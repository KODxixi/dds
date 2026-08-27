"""Server-only Ark runtime with bounded retries and sanitized failures.

This module is the only production model-provider boundary in DDS.  It accepts
stable Ark endpoint aliases from ``DDS_ARK_*`` environment variables and never
publishes credentials, base URLs, or aliases in returned metadata or logs.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit


log = logging.getLogger("dds.ark")

LEGACY_PROVIDER_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "APIYI_KEY",
    "APIYI_BASE_URL",
    "APIYI_MODEL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "GEMINI_API_KEY",
)
_DEFAULT_RETRY_DELAYS = (30.0, 120.0, 300.0)
_OFFICIAL_ARK_HOST_RE = re.compile(r"^ark\.[a-z0-9-]+\.volces\.com$", re.ASCII)


class ArkConfigurationError(RuntimeError):
    """Ark configuration is missing or violates the production boundary."""


class ArkUnavailable(RuntimeError):
    """Ark could not produce a complete response; details are intentionally hidden."""


class ArkStreamInterrupted(ArkUnavailable):
    """A stream failed after output had started and therefore cannot be replayed."""


@dataclass(frozen=True, repr=False)
class ArkRuntimeConfig:
    api_key: str
    model_aliases: tuple[str, ...]
    base_url: str | None = None
    timeout_seconds: float = 90.0
    max_output_tokens: int = 6000
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if not str(self.api_key or "").strip():
            raise ArkConfigurationError("DDS Ark runtime is not configured")
        if not self.model_aliases or any(not str(alias or "").strip() for alias in self.model_aliases):
            raise ArkConfigurationError("DDS Ark model alias is required")
        if self.timeout_seconds <= 0 or self.max_output_tokens < 256:
            raise ArkConfigurationError("DDS Ark numeric configuration is invalid")
        if not 1 <= self.max_attempts <= 3:
            raise ArkConfigurationError("DDS Ark attempts must be between one and three")
        object.__setattr__(self, "base_url", _validated_base_url(str(self.base_url or "").strip()))

    def __repr__(self) -> str:
        return (
            "ArkRuntimeConfig(api_key='[redacted]', model_aliases='[redacted]', "
            f"base_url='[redacted]', timeout_seconds={self.timeout_seconds!r}, "
            f"max_output_tokens={self.max_output_tokens!r}, max_attempts={self.max_attempts!r})"
        )

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ArkRuntimeConfig":
        values = os.environ if environ is None else environ
        if str(values.get("DDS_MODE") or "local").strip().lower() == "cloud":
            if any(str(values.get(key) or "").strip() for key in LEGACY_PROVIDER_ENV_KEYS):
                raise ArkConfigurationError("Legacy model-provider configuration is forbidden in cloud mode")

        api_key = str(values.get("DDS_ARK_API_KEY") or "").strip()
        primary = str(values.get("DDS_ARK_MODEL_ALIAS") or "").strip()
        if not api_key or not primary:
            raise ArkConfigurationError("DDS Ark runtime is not configured")

        aliases: list[str] = []
        for value in (
            primary,
            *str(values.get("DDS_ARK_FALLBACK_MODEL_ALIASES") or "").split(","),
        ):
            alias = str(value or "").strip()
            if alias and alias not in aliases:
                aliases.append(alias)

        try:
            timeout_seconds = float(values.get("DDS_ARK_TIMEOUT_SECONDS") or 90)
            max_output_tokens = int(values.get("DDS_ARK_MAX_OUTPUT_TOKENS") or 6000)
            max_attempts = int(values.get("DDS_ARK_MAX_ATTEMPTS") or 3)
        except (TypeError, ValueError) as exc:
            raise ArkConfigurationError("DDS Ark numeric configuration is invalid") from exc
        if timeout_seconds <= 0:
            raise ArkConfigurationError("DDS Ark timeout must be positive")
        if max_output_tokens < 256:
            raise ArkConfigurationError("DDS Ark output limit is too small")
        if not 1 <= max_attempts <= 3:
            raise ArkConfigurationError("DDS Ark attempts must be between one and three")

        base_url = _validated_base_url(str(values.get("DDS_ARK_BASE_URL") or "").strip())
        return cls(
            api_key=api_key,
            model_aliases=tuple(aliases),
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
            max_attempts=max_attempts,
        )


class ArkRuntime:
    """Responses API adapter shared by report, chat, OCR, and scenario paths."""

    def __init__(
        self,
        config: ArkRuntimeConfig,
        *,
        client: Any | None = None,
        client_factory: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        connection_error_types: Sequence[type[BaseException]] | None = None,
    ) -> None:
        self._config = config
        self._sleep = sleep
        self._connection_error_types = tuple(connection_error_types or _sdk_connection_errors())
        if client is None:
            if client_factory is None:
                from volcenginesdkarkruntime import Ark

                client_factory = Ark
            kwargs: dict[str, Any] = {
                "api_key": config.api_key,
                "timeout": config.timeout_seconds,
                "max_retries": 0,
            }
            if config.base_url:
                kwargs["base_url"] = config.base_url
            client = client_factory(**kwargs)
        self._client = client

    def __repr__(self) -> str:
        return "ArkRuntime(provider='ark', configuration='[redacted]')"

    def public_metadata(self) -> dict[str, Any]:
        return {"provider": "ark", "configured": True}

    def complete(
        self,
        input_data: Any,
        *,
        instructions: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
        json_object: bool = False,
        timeout_seconds: float | None = None,
    ) -> str:
        last_reason = "unavailable"
        for attempt in range(self._config.max_attempts):
            alias = self._alias_for_attempt(attempt)
            try:
                response = self._client.responses.create(
                    **self._request_kwargs(
                        alias=alias,
                        input_data=input_data,
                        instructions=instructions,
                        max_output_tokens=max_output_tokens,
                        temperature=temperature,
                        json_object=json_object,
                        timeout_seconds=timeout_seconds,
                        stream=False,
                    )
                )
                return response_output_text(response)
            except Exception as exc:
                retryable, last_reason = self._classify(exc)
                log.warning(
                    "[ARK] request failed capability=text attempt=%d reason=%s",
                    attempt + 1,
                    last_reason,
                )
                if not retryable or attempt + 1 >= self._config.max_attempts:
                    break
                self._sleep(_DEFAULT_RETRY_DELAYS[attempt])
        raise ArkUnavailable(f"Ark request unavailable ({last_reason})")

    def web_search(
        self,
        input_data: Any,
        *,
        instructions: str | None = None,
        max_output_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Return an unvalidated Responses API web-search result.

        Callers must treat this object as candidate evidence only. Promotion to
        DDS evidence remains a separate deterministic validation step.
        """
        last_reason = "unavailable"
        for attempt in range(self._config.max_attempts):
            alias = self._alias_for_attempt(attempt)
            try:
                return self._client.responses.create(
                    **self._request_kwargs(
                        alias=alias,
                        input_data=input_data,
                        instructions=instructions,
                        max_output_tokens=max_output_tokens,
                        temperature=0.0,
                        json_object=False,
                        timeout_seconds=timeout_seconds,
                        stream=False,
                        tools=[{"type": "web_search"}],
                    )
                )
            except Exception as exc:
                retryable, last_reason = self._classify(exc)
                log.warning(
                    "[ARK] request failed capability=web_search attempt=%d reason=%s",
                    attempt + 1,
                    last_reason,
                )
                if not retryable or attempt + 1 >= self._config.max_attempts:
                    break
                self._sleep(_DEFAULT_RETRY_DELAYS[attempt])
        raise ArkUnavailable(f"Ark web search unavailable ({last_reason})")

    def stream(
        self,
        input_data: Any,
        *,
        instructions: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
        timeout_seconds: float | None = None,
    ) -> Iterable[str]:
        last_reason = "unavailable"
        for attempt in range(self._config.max_attempts):
            emitted = False
            alias = self._alias_for_attempt(attempt)
            try:
                stream = self._client.responses.create(
                    **self._request_kwargs(
                        alias=alias,
                        input_data=input_data,
                        instructions=instructions,
                        max_output_tokens=max_output_tokens,
                        temperature=temperature,
                        json_object=False,
                        timeout_seconds=timeout_seconds,
                        stream=True,
                    )
                )
                for event in stream:
                    delta = response_stream_delta(event)
                    if delta:
                        emitted = True
                        yield delta
                if emitted:
                    return
                raise _EmptyArkResponse("empty stream")
            except Exception as exc:
                retryable, last_reason = self._classify(exc)
                log.warning(
                    "[ARK] stream failed capability=text attempt=%d emitted=%s reason=%s",
                    attempt + 1,
                    emitted,
                    last_reason,
                )
                if emitted:
                    raise ArkStreamInterrupted("Ark stream interrupted after output started") from None
                if not retryable or attempt + 1 >= self._config.max_attempts:
                    break
                self._sleep(_DEFAULT_RETRY_DELAYS[attempt])
        raise ArkUnavailable(f"Ark stream unavailable ({last_reason})")

    def _alias_for_attempt(self, attempt: int) -> str:
        return self._config.model_aliases[attempt % len(self._config.model_aliases)]

    def _request_kwargs(
        self,
        *,
        alias: str,
        input_data: Any,
        instructions: str | None,
        max_output_tokens: int | None,
        temperature: float,
        json_object: bool,
        timeout_seconds: float | None,
        stream: bool,
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": alias,
            "input": input_data,
            "max_output_tokens": min(
                self._config.max_output_tokens,
                max(1, int(max_output_tokens or self._config.max_output_tokens)),
            ),
            "temperature": float(temperature),
            "store": False,
            "stream": stream,
        }
        if instructions:
            kwargs["instructions"] = str(instructions)
        if tools:
            kwargs["tools"] = [dict(tool) for tool in tools]
        if json_object:
            kwargs["text"] = {"format": {"type": "json_object"}}
        if timeout_seconds is not None:
            kwargs["timeout"] = max(0.1, float(timeout_seconds))
        return kwargs

    def _classify(self, exc: BaseException) -> tuple[bool, str]:
        name = type(exc).__name__.lower()
        if "contentfilter" in name or "safety" in name:
            return False, "safety"
        status = getattr(exc, "status_code", None)
        if status is None:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
        try:
            status_int = int(status) if status is not None else None
        except (TypeError, ValueError):
            status_int = None
        if status_int == 429:
            return True, "rate_limit"
        if status_int is not None and 500 <= status_int <= 599:
            return True, "server"
        if status_int is not None:
            return False, "request_rejected"
        if self._connection_error_types and isinstance(exc, self._connection_error_types):
            return True, "network"
        if "connection" in name or "timeout" in name:
            return True, "network"
        return False, "invalid_response"


class _EmptyArkResponse(ValueError):
    pass


def _validated_base_url(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ArkConfigurationError("DDS Ark endpoint is invalid") from exc
    host = str(parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not _OFFICIAL_ARK_HOST_RE.fullmatch(host)
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/api/v3"
    ):
        raise ArkConfigurationError("DDS Ark endpoint must be an official HTTPS Ark API root")
    return value.rstrip("/")


def response_output_text(response: Any) -> str:
    direct = str(getattr(response, "output_text", "") or "").strip()
    if direct:
        return direct
    texts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for part in getattr(item, "content", []) or []:
            if getattr(part, "type", None) == "output_text":
                texts.append(str(getattr(part, "text", "") or ""))
    text = "".join(texts).strip()
    if not text:
        raise _EmptyArkResponse("Ark returned no output text")
    return text


def response_stream_delta(event: Any) -> str:
    event_type = str(getattr(event, "type", "") or "")
    if event_type not in {"response.output_text.delta", "output_text.delta"}:
        return ""
    delta = getattr(event, "delta", "")
    if isinstance(delta, str):
        return delta
    return str(getattr(delta, "text", "") or "")


def _sdk_connection_errors() -> tuple[type[BaseException], ...]:
    try:
        from volcenginesdkarkruntime._exceptions import (
            ArkAPIConnectionError,
            ArkAPITimeoutError,
        )

        return (ArkAPIConnectionError, ArkAPITimeoutError)
    except Exception:
        return ()


_default_lock = threading.Lock()
_default_runtime: ArkRuntime | None = None


def get_ark_runtime() -> ArkRuntime:
    global _default_runtime
    if _default_runtime is None:
        with _default_lock:
            if _default_runtime is None:
                _default_runtime = ArkRuntime(ArkRuntimeConfig.from_environment())
    return _default_runtime


def reset_ark_runtime_for_tests() -> None:
    global _default_runtime
    with _default_lock:
        _default_runtime = None
