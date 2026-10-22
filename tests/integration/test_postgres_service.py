"""PostgreSQL을 사용하는 API/worker/search 통합 테스트.

로컬 단위 테스트에서는 DB를 요구하지 않도록 ``RAG_DB_DSN``이 설정된 경우에만
실행한다. 테스트 DB의 ``rag`` 스키마에는 ``sql/001_pgvector.sql``이 먼저 적용되어
있어야 한다.
"""
from __future__ import annotations

import os
import shutil

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("RAG_DB_DSN"),
    reason="PostgreSQL 통합 테스트는 RAG_DB_DSN이 필요합니다",
)


@pytest.fixture(autouse=True)
def clean_test_state():
    """각 테스트가 독립적으로 실행되도록 DB와 테스트 산출물을 초기화한다."""
    from service.config import get_settings
    from service.db import connect

    settings = get_settings()
    with connect() as connection:
        connection.execute("TRUNCATE TABLE auth_sessions, assets CASCADE")

    for directory in (settings.paths.upload_root, settings.paths.artifact_root):
        shutil.rmtree(directory, ignore_errors=True)

    yield

    with connect() as connection:
        connection.execute("TRUNCATE TABLE auth_sessions, assets CASCADE")

    for directory in (settings.paths.upload_root, settings.paths.artifact_root):
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from service.api import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client) -> dict[str, str]:
    """로그인 미구현 임시 모드는 세션 쿠키 없이 같은 endpoint를 사용한다."""
    return {}


def test_health_auth_and_asset_crud(client, auth_headers):
    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok", "profile": "test"}

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "database": "postgresql"}

    assert client.get("/api/v1/assets").status_code == 200

    me = client.get("/api/v1/auth/me", headers=auth_headers)
    assert me.status_code == 200
    assert me.json()["employee_id"] == "temporary"
    assert me.json()["temporary_access"] is True
    assert me.json()["authentication_enabled"] is False

    created = client.post(
        "/api/v1/assets",
        headers=auth_headers,
        json={
            "asset_type": "document",
            "subtype": "operation_guide",
            "title": "ECM 통합 테스트 문서",
            "owner_org": "테스트부서",
            "metadata": {"source": "pytest"},
        },
    )
    assert created.status_code == 201
    asset_id = created.json()["asset_id"]

    listed = client.get("/api/v1/assets", headers=auth_headers)
    assert listed.status_code == 200
    assert [item["asset_id"] for item in listed.json()["items"]] == [asset_id]

    updated = client.patch(
        f"/api/v1/assets/{asset_id}",
        headers=auth_headers,
        json={"title": "수정된 ECM 통합 테스트 문서"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "수정된 ECM 통합 테스트 문서"

    deleted = client.delete(f"/api/v1/assets/{asset_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/assets/{asset_id}", headers=auth_headers).status_code == 404


def test_upload_worker_pgvector_search_roundtrip(client, auth_headers):
    from service.repositories import jobs
    from service.worker import process

    content = (
        "전자결재 연계 운영지침\n\n"
        "ECM 문서 자산은 원본, 메타데이터, 처리 이력과 검색 청크를 함께 보존한다."
    )
    uploaded = client.post(
        "/api/v1/ingestion/uploads",
        headers=auth_headers,
        data={
            "asset_type": "document",
            "subtype": "operation_guide",
            "title": "전자결재 연계 운영지침",
            "lifecycle_status": "approved",
            "version_label": "1.0",
        },
        files={"file": ("ecm-guide.txt", content.encode("utf-8"), "text/plain")},
    )
    assert uploaded.status_code == 202, uploaded.text
    job_id = uploaded.json()["job_id"]

    claimed = jobs.claim_jobs("pytest-worker", limit=1, lease_seconds=30)
    assert [str(job["job_id"]) for job in claimed] == [job_id]
    process(job_id, "pytest-worker")

    completed = client.get(f"/api/v1/ingestion/jobs/{job_id}", headers=auth_headers)
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["chunk_count"] >= 1

    searched = client.post(
        "/api/v1/search",
        headers=auth_headers,
        json={"query": "전자결재 ECM 문서 자산", "limit": 5},
    )
    assert searched.status_code == 200, searched.text
    results = searched.json()["results"]
    assert results
    assert results[0]["title"] == "전자결재 연계 운영지침"
    assert "문서 자산" in results[0]["content"]

    # 실제 pgvector 컬럼에 설정 차원과 동일한 벡터가 저장됐는지 확인한다.
    from service.config import get_settings
    from service.db import connect

    with connect() as connection:
        row = connection.execute(
            "SELECT vector_dims(embedding) AS dimensions FROM chunks LIMIT 1"
        ).fetchone()
    assert row["dimensions"] == get_settings().models.embedding.dimension


def test_upload_requires_confirmation_before_adding_existing_asset_version(client, auth_headers):
    """같은 제목/파일명 후보는 자동 병합하지 않고 사용자 확인 후 버전으로 연결한다."""
    common_data = {
        "asset_type": "document",
        "subtype": "operation_guide",
        "title": "전자결재 운영지침",
        "system_name": "전자결재",
        "version_label": "1.0",
    }
    first = client.post(
        "/api/v1/ingestion/uploads",
        headers=auth_headers,
        data=common_data,
        files={"file": ("approval-guide.txt", b"first version", "text/plain")},
    )
    assert first.status_code == 202, first.text
    asset_id = first.json()["asset_id"]

    candidate_response = client.post(
        "/api/v1/ingestion/uploads",
        headers=auth_headers,
        data={**common_data, "version_label": "2.0"},
        files={"file": ("approval-guide.txt", b"updated second version", "text/plain")},
    )
    assert candidate_response.status_code == 409, candidate_response.text
    body = candidate_response.json()
    assert body["code"] == "duplicate_confirmation_required"
    assert body["candidates"][0]["asset_id"] == asset_id
    assert "동일한 제목" in body["candidates"][0]["match_reasons"]

    # 확인 응답 전에는 새 Version이 만들어지지 않는다.
    versions_before = client.get(
        f"/api/v1/assets/{asset_id}/versions", headers=auth_headers
    )
    assert len(versions_before.json()["items"]) == 1

    confirmed = client.post(
        "/api/v1/ingestion/uploads",
        headers=auth_headers,
        data={
            **common_data,
            "version_label": "2.0",
            "duplicate_action": "new_version",
            "confirmed_asset_id": asset_id,
        },
        files={"file": ("approval-guide.txt", b"updated second version", "text/plain")},
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["asset_id"] == asset_id

    versions_after = client.get(
        f"/api/v1/assets/{asset_id}/versions", headers=auth_headers
    )
    assert len(versions_after.json()["items"]) == 2

    from service.db import connect
    with connect() as connection:
        audit = connection.execute(
            """SELECT action, employee_id, employee_name, department
                 FROM upload_audit WHERE asset_id=%s ORDER BY created_at""",
            (asset_id,),
        ).fetchall()
    assert [row["action"] for row in audit] == ["new_asset", "new_version"]
    assert {row["employee_id"] for row in audit} == {"temporary"}
    assert {row["department"] for row in audit} == {"IDC본부"}

    history = client.get(
        f"/api/v1/assets/{asset_id}/upload-history", headers=auth_headers
    )
    assert history.status_code == 200
    assert len(history.json()["items"]) == 2
    assert history.json()["items"][0]["employee_name"] == "임시 사용자"


def test_similar_title_alone_does_not_require_duplicate_confirmation(client, auth_headers):
    """builder.jar와 builder-api.jar는 이름이 비슷해도 별도 Asset으로 등록한다."""
    first = client.post(
        "/api/v1/ingestion/uploads",
        headers=auth_headers,
        data={"asset_type": "source_code", "title": "builder.jar"},
        files={"file": ("builder.jar", b"first jar bytes", "application/java-archive")},
    )
    assert first.status_code == 202, first.text

    second = client.post(
        "/api/v1/ingestion/uploads",
        headers=auth_headers,
        data={"asset_type": "source_code", "title": "builder-api.jar"},
        files={"file": ("builder-api.jar", b"second jar bytes", "application/java-archive")},
    )
    assert second.status_code == 202, second.text
    assert second.json()["asset_id"] != first.json()["asset_id"]


def test_exact_failed_upload_reuses_and_requeues_existing_job(client, auth_headers):
    """파싱 실패한 동일 원본 재업로드는 중복 Asset 대신 기존 job을 재접수한다."""
    payload = {
        "asset_type": "source_code",
        "title": "재처리 대상 JAR",
        "repository": "builder.jar",
    }
    first = client.post(
        "/api/v1/ingestion/uploads",
        headers=auth_headers,
        data=payload,
        files={"file": ("builder.jar", b"same-test-jar-content", "application/java-archive")},
    )
    assert first.status_code == 202, first.text
    original = first.json()

    from service.db import connect
    with connect() as connection:
        connection.execute(
            """UPDATE ingestion_jobs
                  SET status='failed', attempts=max_attempts, error_detail='CFR 설정 오류',
                      completed_at=now(), updated_at=now()
                WHERE job_id=%s""",
            (original["job_id"],),
        )

    retried = client.post(
        "/api/v1/ingestion/uploads",
        headers=auth_headers,
        data=payload,
        files={"file": ("builder.jar", b"same-test-jar-content", "application/java-archive")},
    )
    assert retried.status_code == 202, retried.text
    body = retried.json()
    assert body["reused_existing_upload"] is True
    assert body["asset_id"] == original["asset_id"]
    assert body["version_id"] == original["version_id"]
    assert body["job_id"] == original["job_id"]
    assert body["status"] == "queued"

    with connect() as connection:
        counts = connection.execute(
            """SELECT
                 (SELECT count(*) FROM assets WHERE title='재처리 대상 JAR') AS assets,
                 (SELECT count(*) FROM asset_versions WHERE asset_id=%s) AS versions""",
            (original["asset_id"],),
        ).fetchone()
    assert counts == {"assets": 1, "versions": 1}


def test_admin_asset_and_pgvector_chunk_crud(client, auth_headers):
    """관리 화면 API가 자산 soft delete와 검색 청크 CRUD를 일관되게 수행한다."""
    from service.repositories import jobs
    from service.worker import process

    uploaded = client.post(
        "/api/v1/ingestion/uploads",
        headers=auth_headers,
        data={"asset_type": "document", "title": "관리자 CRUD 시험 문서"},
        files={"file": ("admin-test.txt", "초기 검색 본문".encode(), "text/plain")},
    )
    assert uploaded.status_code == 202, uploaded.text
    upload_body = uploaded.json()
    claimed = jobs.claim_jobs("admin-test-worker", limit=1, lease_seconds=30)
    assert [str(row["job_id"]) for row in claimed] == [upload_body["job_id"]]
    process(upload_body["job_id"], "admin-test-worker")

    stats = client.get("/api/v1/admin/stats", headers=auth_headers)
    assert stats.status_code == 200
    assert stats.json()["assets"] == 1
    assert stats.json()["chunks"] >= 1

    created = client.post(
        "/api/v1/admin/chunks",
        headers=auth_headers,
        json={
            "version_id": upload_body["version_id"],
            "kind": "admin_note",
            "display_content": "관리자가 추가한 검색 지식",
            "identifier": "ADMIN-NOTE-1",
            "metadata": {"source": "admin-test"},
        },
    )
    assert created.status_code == 201, created.text
    chunk_id = created.json()["chunk_id"]
    assert created.json()["embedding_dimensions"] == 1024

    updated = client.patch(
        f"/api/v1/admin/chunks/{chunk_id}",
        headers=auth_headers,
        json={"display_content": "수정된 관리자 검색 지식"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["embedding_content"] == "수정된 관리자 검색 지식"

    listed = client.get(
        "/api/v1/admin/chunks",
        headers=auth_headers,
        params={"q": "수정된 관리자", "version_id": upload_body["version_id"]},
    )
    assert listed.status_code == 200
    assert [row["chunk_id"] for row in listed.json()["items"]] == [chunk_id]

    deleted_chunk = client.delete(
        f"/api/v1/admin/chunks/{chunk_id}", headers=auth_headers
    )
    assert deleted_chunk.status_code == 204

    deleted_asset = client.delete(
        f"/api/v1/admin/assets/{upload_body['asset_id']}", headers=auth_headers
    )
    assert deleted_asset.status_code == 204
    deleted_list = client.get(
        "/api/v1/admin/assets",
        headers=auth_headers,
        params={"include_deleted": True},
    ).json()["items"]
    assert deleted_list[0]["deleted_at"] is not None

    restored = client.post(
        f"/api/v1/admin/assets/{upload_body['asset_id']}/restore", headers=auth_headers
    )
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None
