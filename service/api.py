"""업로드와 처리 상태, 검색 API. 서비스 내부망 배포 시 인증 헤더를 필수로 설정한다."""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from service import config, store
from service.search import search

app = FastAPI(title='보험약관 AI 자산 수집', docs_url=None, redoc_url=None)
ALLOWED = {
    'document': {'.pdf','.docx','.txt','.md'},
    'incident': {'.txt','.md'},
    'error_trace': {'.log','.txt'},
    'source_code': {'.py','.java','.sql','.js','.sh','.yml','.yaml'},
}


def authorize(x_api_key: str | None = Header(default=None)):
    if not config.API_KEY:
        raise HTTPException(503, 'RAG_API_KEY 환경 변수를 먼저 설정하세요')
    import hmac
    if not x_api_key or not hmac.compare_digest(x_api_key, config.API_KEY):
        raise HTTPException(401, '잘못된 API 키')


@app.get('/')
def index():
    return FileResponse(config.ROOT / 'web' / 'upload.html', media_type='text/html')


@app.post('/api/uploads', dependencies=[Depends(authorize)], status_code=202)
async def upload(file: UploadFile = File(...), content_type: str = Form(...),
                 subtype: str = Form('general'), title: str = Form(''),
                 system_name: str = Form(''), version_label: str = Form(''),
                 repository: str = Form(''), branch: str = Form(''), logical_path: str = Form('')):
    suffix = Path(file.filename or '').suffix.lower()
    if content_type not in ALLOWED or suffix not in ALLOWED[content_type]:
        raise HTTPException(422, '허용하지 않은 유형/확장자 조합')
    name = Path(file.filename or 'upload').name
    name = re.sub(r'[^\w.\-가-힣]', '_', name)[:160]
    if not name or name.startswith('.'):
        raise HTTPException(422, '파일명이 올바르지 않습니다')
    if logical_path and (logical_path.startswith('/') or '..' in Path(logical_path).parts or '\\' in logical_path):
        raise HTTPException(422, '상대 경로만 입력할 수 있습니다')
    if content_type == 'source_code' and not repository.strip():
        raise HTTPException(422, '소스코드는 저장소 이름이 필요합니다')
    config.UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    directory = config.UPLOAD_ROOT / str(uuid4())
    directory.mkdir(mode=0o700)
    target = directory / name
    digest = hashlib.sha256()
    size = 0
    try:
        with target.open('xb') as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, '용량 제한 초과')
                digest.update(chunk)
                handle.write(chunk)
        if size == 0: raise HTTPException(422, '빈 파일')
        asset_id = store.create_asset(name, content_type, subtype[:100],
            (title or name)[:300], system_name[:100], version_label[:100], repository[:200],
            branch[:200], digest.hexdigest(), str(target), logical_path[:500])
        return {'asset_id': asset_id, 'status': 'queued', 'size': size}
    except Exception:
        target.unlink(missing_ok=True)
        directory.rmdir()
        raise
    finally:
        await file.close()


@app.get('/api/uploads/{asset_id}', dependencies=[Depends(authorize)])
def status(asset_id: UUID):
    asset = store.get_asset(str(asset_id))
    if not asset: raise HTTPException(404, '업로드 없음')
    # 서버 내부의 파일 절대 경로는 API 응답에서 숨긴다.
    return {key: value for key, value in asset.items() if key != 'source_path'}


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    content_type: str | None = None
    system_name: str | None = None
    limit: int = Field(default=10, ge=1, le=30)


@app.post('/api/search', dependencies=[Depends(authorize)])
def query(request: SearchRequest):
    if request.content_type and request.content_type not in ALLOWED:
        raise HTTPException(422, 'content_type이 잘못되었습니다')
    return {'results': search(request.query, request.content_type, request.system_name, request.limit)}
