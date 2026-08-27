from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = (
    ROOT / "Dockerfile",
    ROOT / "cloud" / "volcengine" / "service" / "Dockerfile",
)
COMPOSE_FILES = (
    ROOT / "cloud" / "volcengine" / "service" / "docker-compose.yml",
    ROOT / "cloud" / "volcengine" / "service" / "docker-compose.full.yml",
)
BUILD_COMPOSE = ROOT / "cloud" / "volcengine" / "service" / "docker-compose.build.yml"
DEPLOYMENT_DOC = ROOT / "cloud" / "volcengine" / "service" / "DEPLOYMENT.md"


def _copy_sources(dockerfile: Path) -> list[str]:
    sources: list[str] = []
    for raw_line in dockerfile.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.upper().startswith("COPY "):
            continue
        line = re.sub(r"^COPY\s+(?:--[A-Za-z-]+=[^\s]+\s+)*", "", line, flags=re.IGNORECASE)
        parts = line.split()
        sources.extend(parts[:-1])
    return sources


def test_dockerignore_fails_closed_for_vault_and_recursive_environment_files() -> None:
    lines = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        "Vault/",
        "**/Vault/",
        ".env*",
        "**/.env*",
        "tests/",
        "**/tests/",
        "**/test_*.py",
        "cloud/volcengine/status/",
        "**/status/",
    } <= lines
    environment_exceptions = {line for line in lines if line.startswith("!") and ".env" in line}
    assert environment_exceptions <= {
        "!.env.example",
        "!**/.env.example",
        "!.env.template",
        "!**/.env.template",
    }


def test_runtime_dockerfiles_use_an_explicit_copy_allowlist() -> None:
    for dockerfile in DOCKERFILES:
        text = dockerfile.read_text(encoding="utf-8")
        sources = _copy_sources(dockerfile)

        assert sources, f"{dockerfile} must declare an auditable COPY allowlist"
        assert not re.search(r"(?im)^\s*COPY(?:\s+--[^\s]+)*\s+\.\s+", text)
        assert all(source not in {".", "./"} for source in sources)
        assert all("Vault" not in source for source in sources)
        assert all(not Path(source).name.startswith(".env") for source in sources)


def test_cloud_image_installs_frontend_dependencies_without_copying_host_node_modules() -> None:
    text = (ROOT / "cloud" / "volcengine" / "service" / "Dockerfile").read_text(encoding="utf-8")

    assert "npm ci --omit=dev" in text
    assert "COPY --from=" in text and "/app/node_modules" in text
    assert not re.search(r"(?im)^\s*COPY\s+node_modules", text)


def test_cloud_compose_requires_an_immutable_image_digest() -> None:
    for compose_path in COMPOSE_FILES:
        text = compose_path.read_text(encoding="utf-8")
        compose = yaml.safe_load(text)
        for service in compose["services"].values():
            image = str(service["image"])
            assert "_IMAGE_REPOSITORY:?" in image
            assert "@${" in image and "_IMAGE_DIGEST:?" in image
        assert ":latest" not in text and ":staging" not in text


def test_runtime_compose_never_builds_and_build_compose_never_runs() -> None:
    for compose_path in COMPOSE_FILES:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        assert all("build" not in service for service in compose["services"].values())

    build_compose = yaml.safe_load(BUILD_COMPOSE.read_text(encoding="utf-8"))
    assert set(build_compose["services"]) == {"dds-image"}
    build_service = build_compose["services"]["dds-image"]
    assert "build" in build_service
    assert not any(key in build_service for key in ("ports", "expose", "volumes", "restart", "command"))


def test_deployment_document_builds_then_runs_an_resolved_digest_without_up_build() -> None:
    document = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    assert "up -d --build" not in document
    assert "up --build" not in document
    assert "dds-app:latest" not in document
    assert "repository@sha256" in document
    assert "DDS_IMAGE_DIGEST" in document
