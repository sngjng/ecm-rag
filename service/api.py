"""FastAPI 애플리케이션 조립 지점.

이 모듈은 비즈니스 로직을 직접 구현하지 않는다. 설정을 한 번 읽어 애플리케이션을
생성하고, 업무 영역별 router를 공통 API prefix 아래에 연결한다. 이 구조 덕분에 API
프로세스는 HTTP 접수에 집중하고 무거운 문서 처리는 ``service.worker``가 담당한다.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from service.config import get_settings
from service.routes import assets, ingestion, search, system


# get_settings()는 프로세스 안에서 캐시된다. 운영 중 profile이 임의로 바뀌지 않게 하고,
# 테스트에서 환경 변수를 바꿀 때만 reload_settings()를 사용한다.
settings = get_settings()
app = FastAPI(
    # Swagger/OpenAPI 화면에도 프로젝트의 업무 목적을 그대로 표시한다.
    title="ECM Document Assetization Service",
    docs_url="/docs" if settings.server.docs_enabled else None,
    redoc_url=None,
)
app.include_router(system.router)
app.include_router(assets.router, prefix=settings.server.api_prefix)
app.include_router(ingestion.router, prefix=settings.server.api_prefix)
app.include_router(search.router, prefix=settings.server.api_prefix)


@app.get("/", include_in_schema=False)
def index():
    """빌드 과정이 없는 단일 업로드 화면을 반환한다.

    web_root는 설정 로더가 프로젝트 루트 기준 절대 경로로 정규화하므로 실행 디렉터리에
    영향을 받지 않는다.
    """
    return FileResponse(settings.paths.web_root / "upload.html", media_type="text/html")
