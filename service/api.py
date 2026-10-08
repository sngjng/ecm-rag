"""FastAPI 조립 지점. endpoint 구현은 service.routes 아래에 분리한다."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from service.config import get_settings
from service.routes import assets, ingestion, search, system


settings = get_settings()
app = FastAPI(
    title="ECM RAG PostgreSQL Service",
    docs_url="/docs" if settings.server.docs_enabled else None,
    redoc_url=None,
)
app.include_router(system.router)
app.include_router(assets.router, prefix=settings.server.api_prefix)
app.include_router(ingestion.router, prefix=settings.server.api_prefix)
app.include_router(search.router, prefix=settings.server.api_prefix)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(settings.paths.web_root / "upload.html", media_type="text/html")
