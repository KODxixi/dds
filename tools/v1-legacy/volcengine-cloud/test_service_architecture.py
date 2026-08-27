from __future__ import annotations

from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parent / "service"


def test_dds_runtime_is_bound_to_ecs_loopback_only():
    compose = (SERVICE_DIR / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:8080:8080"' in compose
    assert '"8080:8080"' not in compose.replace('"127.0.0.1:8080:8080"', "")
    assert "dds-data-out:/app/data_out" in compose
    assert "dds-data-out:" in compose


def test_production_docs_keep_garchos_caddy_as_the_only_public_entry():
    deployment = (SERVICE_DIR / "DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "GarchOS" in deployment
    assert "Caddy" in deployment
    assert "127.0.0.1:8080" in deployment
    assert "DDS 不直接开放公网端口" in deployment


def test_legacy_nginx_stack_cannot_claim_host_port_80():
    compose = (SERVICE_DIR / "docker-compose.full.yml").read_text(encoding="utf-8")

    assert '"80:80"' not in compose
    assert '"0.0.0.0:80:80"' not in compose
