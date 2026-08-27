"""Trusted, integrity-checked reads from the GarchOS Artifact API."""

from __future__ import annotations

import hashlib
import hmac
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from garchos_openapi_contract import ContractValidationError, OpenAPIContract


ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class ArtifactAccessError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "ARTIFACT_READ_FAILED",
        status: int = 502,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = int(status)
        self.retryable = bool(retryable)


class ArtifactPolicyError(ValueError):
    pass


class ArtifactIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedArtifact:
    artifact: dict[str, Any]
    content: bytes


class ArtifactReader:
    def __init__(
        self,
        *,
        base_url: str,
        service_secret: str | bytes,
        service_name: str = "dds",
        transport: Any | None = None,
        timeout_seconds: float = 15.0,
        contract: OpenAPIContract | None = None,
        clock: Any = time.time,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        if not self.base_url:
            raise ValueError("base_url is required")
        secret = service_secret.encode("utf-8") if isinstance(service_secret, str) else service_secret
        if not secret:
            raise ValueError("service_secret is required")
        self._secret = bytes(secret)
        self.service_name = str(service_name or "").strip()
        if not self.service_name:
            raise ValueError("service_name is required")
        self.transport = transport or requests.Session()
        self.timeout_seconds = float(timeout_seconds)
        self.contract = contract or OpenAPIContract.from_environment()
        self.clock = clock

    def read(
        self,
        *,
        artifact_id: str,
        owner: str,
        workflow_id: str,
    ) -> VerifiedArtifact:
        self.contract.validate_schema("StableId", artifact_id)
        self.contract.validate_schema("StableId", workflow_id)
        metadata_response = self._request(
            artifact_id=artifact_id,
            view="metadata",
            owner=owner,
            workflow_id=workflow_id,
        )
        try:
            payload = metadata_response.json()
        except Exception as exc:
            raise ArtifactIntegrityError("artifact metadata is not valid JSON") from exc
        artifact = payload.get("artifact") if isinstance(payload, dict) else None
        if not isinstance(artifact, dict):
            raise ArtifactIntegrityError("artifact metadata is missing artifact")
        self._enforce_policy(
            artifact,
            artifact_id=artifact_id,
            workflow_id=workflow_id,
        )
        try:
            self.contract.validate_schema("ArtifactResponse", payload)
        except ContractValidationError as exc:
            raise ArtifactIntegrityError(str(exc)) from exc

        content_response = self._request(
            artifact_id=artifact_id,
            view="content",
            owner=owner,
            workflow_id=workflow_id,
        )
        content_type = self._header(content_response.headers, "Content-Type").split(";", 1)[0].strip().lower()
        if content_type != "application/octet-stream":
            raise ArtifactIntegrityError("artifact content must be application/octet-stream")
        content = bytes(content_response.content)
        actual_size = len(content)
        actual_sha256 = hashlib.sha256(content).hexdigest()
        header_sha256 = self._header(content_response.headers, "X-Content-SHA256").strip().lower()
        header_length = self._header(content_response.headers, "Content-Length").strip()
        try:
            parsed_length = int(header_length)
        except ValueError as exc:
            raise ArtifactIntegrityError("artifact Content-Length is invalid") from exc
        if parsed_length != actual_size or int(artifact["size_bytes"]) != actual_size:
            raise ArtifactIntegrityError("artifact size does not match metadata and content headers")
        if header_sha256 != actual_sha256 or artifact["content_sha256"] != actual_sha256:
            raise ArtifactIntegrityError("artifact SHA-256 does not match metadata and content headers")
        if artifact["media_type"] not in ALLOWED_IMAGE_TYPES:
            raise ArtifactIntegrityError("artifact image MIME is not allowed")
        return VerifiedArtifact(artifact=deepcopy(artifact), content=content)

    def _request(
        self,
        *,
        artifact_id: str,
        view: str,
        owner: str,
        workflow_id: str,
    ):
        timestamp = str(int(self.clock()))
        path = f"/api/v1/artifacts/{quote(artifact_id, safe='')}"
        canonical = "\n".join(
            ["GET", path, view, str(owner), workflow_id, self.service_name, timestamp]
        )
        signature = hmac.new(
            self._secret,
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        response = self.transport.get(
            f"{self.base_url}{path}?view={view}",
            headers={
                "Accept": "application/json" if view == "metadata" else "application/octet-stream",
                "X-GarchOS-Service": self.service_name,
                "X-GarchOS-Owner": str(owner),
                "X-GarchOS-Workflow": workflow_id,
                "X-GarchOS-Timestamp": timestamp,
                "X-GarchOS-Signature": signature,
            },
            allow_redirects=False,
            timeout=self.timeout_seconds,
        )
        status = int(response.status_code)
        if status != 200:
            if status in {401, 403}:
                raise ArtifactAccessError(
                    "artifact access was denied",
                    code="ARTIFACT_ACCESS_DENIED",
                    status=403,
                    retryable=False,
                )
            if status == 404:
                raise ArtifactAccessError(
                    "artifact was not found in the requested owner/workflow scope",
                    code="ARTIFACT_NOT_FOUND",
                    status=404,
                    retryable=False,
                )
            if status == 429 or status >= 500:
                raise ArtifactAccessError(
                    "artifact content channel is temporarily unavailable",
                    code="ARTIFACT_CHANNEL_UNAVAILABLE",
                    status=503,
                    retryable=True,
                )
            raise ArtifactAccessError(
                "artifact request failed",
                code="ARTIFACT_READ_FAILED",
                status=502,
                retryable=status >= 500,
            )
        return response

    @staticmethod
    def _enforce_policy(
        artifact: dict[str, Any], *, artifact_id: str, workflow_id: str
    ) -> None:
        if artifact.get("artifact_id") != artifact_id:
            raise ArtifactPolicyError("artifact_id does not match requested artifact")
        if artifact.get("workflow_id") != workflow_id:
            raise ArtifactPolicyError("artifact workflow_id does not match")
        if artifact.get("producer") != "shibao":
            raise ArtifactPolicyError("only ShibaoAI artifacts may be embedded")
        if artifact.get("kind") != "concept_image":
            raise ArtifactPolicyError("only concept_image artifacts may be embedded")
        if artifact.get("synthetic") is not True:
            raise ArtifactPolicyError("concept images must be marked synthetic")
        try:
            size_bytes = int(artifact.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("artifact size_bytes is invalid") from exc
        if size_bytes > MAX_IMAGE_BYTES:
            raise ArtifactPolicyError("artifact exceeds the 20 MiB image limit")

    @staticmethod
    def _header(headers: Any, name: str) -> str:
        for key, value in dict(headers or {}).items():
            if str(key).lower() == name.lower():
                return str(value)
        return ""
