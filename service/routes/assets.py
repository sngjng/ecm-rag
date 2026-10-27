"""ECM Asset/Version/Relation HTTP API.

router는 HTTP 상태 코드와 입력 DTO 변환을 담당하고 실제 SQL은 repository로 위임한다.
모든 endpoint에는 router 수준의 ``authorize`` 의존성이 적용된다. 인증 비활성 기간에는
이 의존성이 임시 사용자를 반환하고, 활성화 후에는 HttpOnly 세션을 검증한다.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
from service.dependencies import authorize, authorize_uploader
from service.config import get_settings
from service.markdown_viewer import render_markdown
from service.repositories import assets
from service.schemas import AssetCreate, AssetType, AssetUpdate, RelationCreate


router = APIRouter(prefix="/assets", tags=["assets"], dependencies=[Depends(authorize)])


def _require_document_version(asset_id: UUID, version_id: UUID) -> dict:
    """활성 문서 버전을 조회하고 다운로드·뷰어의 공통 접근 정책을 적용한다."""
    version = assets.get_version(asset_id, version_id)
    if not version:
        raise HTTPException(404, "문서 버전을 찾을 수 없습니다")
    if version["asset_type"] != "document":
        raise HTTPException(400, "Markdown은 문서 자산만 지원합니다")
    return version


def _markdown_artifact_path(version_id: UUID) -> Path | None:
    """허용된 artifact root 안에 보존된 Markdown 파일이 있으면 반환한다."""
    artifact_root = get_settings().paths.artifact_root.resolve()
    markdown_path = (artifact_root / str(version_id) / "document.md").resolve()
    if markdown_path.is_file() and markdown_path.is_relative_to(artifact_root):
        return markdown_path
    return None


def _markdown_from_chunks(asset_id: UUID, version_id: UUID) -> str:
    """별도 MD 파일이 없는 기존 문서를 저장 청크 순서대로 복원한다."""
    chunks = assets.list_version_markdown_chunks(asset_id, version_id)
    contents = [
        str(chunk["display_content"]).strip()
        for chunk in chunks
        if str(chunk.get("display_content") or "").strip()
    ]
    if not contents:
        raise HTTPException(404, "Markdown으로 변환할 문서 내용이 없습니다")
    return "\n\n---\n\n".join(contents) + "\n"


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(authorize_uploader)])
def create_asset(payload: AssetCreate):
    """파일 없이 메타데이터 Asset을 먼저 생성한다."""
    return assets.create_asset(payload)


@router.get("")
def list_assets(
    asset_type: AssetType | None = None,
    system_name: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """삭제되지 않은 Asset 목록을 선택 필터와 페이지 단위로 반환한다."""
    return {"items": assets.list_assets(
        asset_type=asset_type, system_name=system_name, limit=limit, offset=offset
    )}


@router.get("/stats/counts")
def count_assets_by_type():
    """검색 화면에 표시할 활성 문서자산 수를 유형별로 반환한다."""
    counts = assets.count_assets_by_type()
    return {"counts": counts, "total": sum(counts.values())}


@router.get("/{asset_id}")
def get_asset(asset_id: UUID):
    """단일 Asset을 조회한다. soft delete된 Asset은 404로 취급한다."""
    result = assets.get_asset(asset_id)
    if not result:
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    return result


@router.patch("/{asset_id}", dependencies=[Depends(authorize_uploader)])
def update_asset(asset_id: UUID, payload: AssetUpdate):
    """전달된 필드만 변경하는 PATCH endpoint다."""
    result = assets.update_asset(asset_id, payload)
    if not result:
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    return result


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(authorize_uploader)])
def delete_asset(asset_id: UUID):
    """물리 삭제 대신 deleted_at을 기록하고 대기/처리 중 작업을 취소한다."""
    if not assets.soft_delete_asset(asset_id):
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{asset_id}/versions")
def list_versions(asset_id: UUID):
    """Asset의 버전 이력을 반환하되 내부 서버 경로(source_path)는 숨긴다."""
    if not assets.get_asset(asset_id):
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    versions = assets.list_versions(asset_id)
    return {"items": [
        {key: value for key, value in version.items() if key != "source_path"}
        for version in versions
    ]}


@router.get("/{asset_id}/versions/{version_id}/download")
def download_version(asset_id: UUID, version_id: UUID):
    """인증된 사용자에게 등록 당시 원본 파일을 첨부 형태로 전송한다.

    DB의 ``source_path``도 신뢰하지 않고 실제 경로를 해석한 뒤 설정된 업로드 루트
    내부인지 확인한다. 따라서 DB 값이 변조돼도 서버의 다른 파일은 내려받을 수 없다.
    """
    version = assets.get_version(asset_id, version_id)
    if not version:
        raise HTTPException(404, "문서 버전을 찾을 수 없습니다")

    upload_root = get_settings().paths.upload_root.resolve()
    try:
        source = Path(version["source_path"]).resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(404, "원본 파일을 찾을 수 없습니다") from None
    if not source.is_file() or not source.is_relative_to(upload_root):
        raise HTTPException(403, "허용되지 않은 원본 파일 경로입니다")

    return FileResponse(
        source,
        filename=version["source_filename"],
        media_type="application/octet-stream",
    )


@router.get("/{asset_id}/versions/{version_id}/download/markdown")
def download_version_markdown(asset_id: UUID, version_id: UUID):
    """인증된 사용자에게 문서 버전의 Markdown 변환본을 전송한다.

    이 endpoint는 ``document`` 자산만 허용한다. PDF는 Docling 처리 시 보존한
    ``document.md``를 그대로 사용하고, DOCX·TXT처럼 별도 Markdown artifact가 없는
    문서는 PostgreSQL에 저장된 전체 청크를 ``chunk_seq`` 순서로 결합한다. 따라서 이
    기능 추가 전에 처리한 문서도 재업로드 없이 다운로드할 수 있다.
    """
    version = _require_document_version(asset_id, version_id)
    markdown_path = _markdown_artifact_path(version_id)
    markdown_filename = f"{Path(version['source_filename']).stem or 'document'}.md"

    # version_id는 UUID로 검증되지만 artifact root 검사를 추가해 설정 경로나 향후 코드가
    # 바뀌어도 허용된 처리 산출물 밖의 파일이 노출되지 않게 한다.
    if markdown_path:
        return FileResponse(
            markdown_path,
            filename=markdown_filename,
            media_type="text/markdown; charset=utf-8",
        )

    markdown = _markdown_from_chunks(asset_id, version_id)
    encoded_filename = quote(markdown_filename, safe="")
    return Response(
        content=markdown.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"document.md\"; filename*=UTF-8''{encoded_filename}"
            )
        },
    )


@router.get("/{asset_id}/versions/{version_id}/markdown")
def view_version_markdown(asset_id: UUID, version_id: UUID):
    """문서 버전의 Markdown을 별도 뷰어가 표시할 안전한 HTML로 반환한다.

    원본 Markdown이나 변환 HTML에 포함된 임의 script/HTML은 실행되지 않는다. 소스코드,
    로그, 장애자료는 다운로드 endpoint와 동일하게 거부해 화면 우회 호출도 차단한다.
    """
    version = _require_document_version(asset_id, version_id)
    markdown_path = _markdown_artifact_path(version_id)
    if markdown_path:
        try:
            markdown_text = markdown_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise HTTPException(500, "Markdown 문서를 읽을 수 없습니다") from exc
    else:
        markdown_text = _markdown_from_chunks(asset_id, version_id)

    return {
        "asset_id": str(asset_id),
        "version_id": str(version_id),
        "title": version.get("title") or Path(version["source_filename"]).stem,
        "source_filename": version["source_filename"],
        "version_label": version.get("version_label"),
        "html": render_markdown(markdown_text),
    }


@router.get("/{asset_id}/upload-history")
def list_upload_history(asset_id: UUID):
    """사번·성명·부서가 포함된 문서 업로드 감사 이력을 반환한다."""
    if not assets.get_asset(asset_id):
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    return {"items": assets.list_upload_audit(asset_id)}


@router.post("/{asset_id}/relations", status_code=status.HTTP_201_CREATED, dependencies=[Depends(authorize_uploader)])
def create_relation(asset_id: UUID, payload: RelationCreate):
    """두 Asset 사이에 참조·대체·파생 등의 방향성 관계를 만든다."""
    if not assets.get_asset(asset_id) or not assets.get_asset(payload.target_asset_id):
        raise HTTPException(404, "source 또는 target asset을 찾을 수 없습니다")
    try:
        return assets.create_relation(asset_id, payload)
    except (CheckViolation, ForeignKeyViolation, UniqueViolation) as exc:
        raise HTTPException(409, "동일한 관계가 이미 존재하거나 관계가 유효하지 않습니다") from exc


@router.get("/{asset_id}/relations")
def list_relations(asset_id: UUID):
    """Asset이 source 또는 target으로 참여하는 모든 관계를 조회한다."""
    return {"items": assets.list_relations(asset_id)}


@router.delete(
    "/{asset_id}/relations/{target_asset_id}/{relation_type}", status_code=204,
    dependencies=[Depends(authorize_uploader)],
)
def delete_relation(asset_id: UUID, target_asset_id: UUID, relation_type: str):
    """source/target/type이 모두 일치하는 관계 하나를 삭제한다."""
    if not assets.delete_relation(asset_id, target_asset_id, relation_type):
        raise HTTPException(404, "관계를 찾을 수 없습니다")
    return Response(status_code=204)
