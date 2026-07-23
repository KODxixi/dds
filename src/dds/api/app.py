"""FastAPI surface for the DDS Research Control Center."""

from __future__ import annotations

from importlib.resources import files
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from dds.config import Settings
from dds.product import ProductSettings, ResearchJobService
from dds.research import ResearchSource, sources_from_environment
from dds.services.scenario_service import ScenarioService


class ResearchJobRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=160)
    city: str = Field(min_length=1, max_length=80)
    district: str = Field(default="", max_length=80)
    address: str = Field(default="", max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    project_type: str = Field(default="", max_length=80)
    decision_question: str = Field(default="", max_length=1000)
    requested_level: int | None = Field(default=None, ge=1, le=3)
    constraint_sources: list[dict[str, Any]] = Field(default_factory=list)
    schemes: list[dict[str, Any]] = Field(default_factory=list)
    core_development_boundaries_ready: bool = False


class AnalysisSimulationRequest(BaseModel):
    requested_level: int = Field(ge=1, le=3)
    input_profile: dict[str, Any]
    payload: dict[str, Any]


def _default_settings() -> ProductSettings:
    configured = os.getenv("DDS_PRODUCT_ROOT")
    root = Path(configured).expanduser() if configured else Settings.from_env().projects_root / "research-control-center"
    return ProductSettings(root=root.resolve())


def create_app(
    settings: ProductSettings | None = None,
    *,
    research_sources: tuple[ResearchSource, ...] | None = None,
) -> FastAPI:
    """Create a localhost-first product API.

    Authentication is intentionally not invented here. Operators must keep the
    service bound to localhost until an authenticated deployment layer exists.
    """

    app = FastAPI(title="DDS Research Control Center", version="2.1.0")
    service = ResearchJobService(
        settings or _default_settings(),
        research_sources if research_sources is not None else sources_from_environment(),
    )
    app.state.research_jobs = service

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return (
            files("dds.web")
            .joinpath("research_control_center.html")
            .read_text(encoding="utf-8")
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "sources": service.source_status()}

    @app.get("/api/research-sources")
    def sources() -> list[dict[str, object]]:
        return service.source_status()

    @app.post("/api/analysis/simulate")
    def simulate_analysis(
        request: AnalysisSimulationRequest,
    ) -> dict[str, Any]:
        try:
            return ScenarioService().run(
                requested_level=request.requested_level,
                input_profile=request.input_profile,
                payload=request.payload,
            ).to_dict()
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/research-jobs", status_code=status.HTTP_201_CREATED)
    def create_job(request: ResearchJobRequest) -> dict[str, Any]:
        return service.summary(service.create(request.model_dump()))

    @app.get("/api/research-jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return [service.summary(item) for item in service.list()]

    @app.get("/api/research-jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            return service.get(job_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="研究任务不存在") from exc

    @app.get("/api/research-jobs/{job_id}/logs")
    def get_logs(job_id: str) -> list[dict[str, Any]]:
        try:
            return service.get(job_id).get("logs", [])
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="研究任务不存在") from exc

    @app.post("/api/research-jobs/{job_id}/materials", status_code=status.HTTP_201_CREATED)
    async def upload_material(
        request: Request,
        job_id: str,
        filename: str = Query(min_length=1, max_length=255),
    ) -> dict[str, Any]:
        try:
            body = await request.body()
            return service.upload(
                job_id,
                filename,
                body,
                request.headers.get("content-type", "application/octet-stream"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="研究任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

    @app.post("/api/research-jobs/{job_id}/run")
    def run_job(job_id: str) -> dict[str, Any]:
        try:
            return service.run(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="研究任务不存在") from exc

    return app


app = create_app()


__all__ = [
    "AnalysisSimulationRequest",
    "ResearchJobRequest",
    "app",
    "create_app",
]
