"""기존 색인 문서 GraphRAG backfill 대상 선택 회귀 테스트."""
from uuid import uuid4

import scripts.backfill_graphrag as backfill_module


def test_explicit_version_with_chunks_does_not_require_indexed_at(monkeypatch):
    version_id = uuid4()
    captured = {}

    class FakeResult:
        def fetchall(self):
            return [{"version_id": version_id, "title": "구성도", "source_filename": "a.pptx"}]

    class FakeConnection:
        def execute(self, statement, values):
            captured["statement"] = statement
            captured["values"] = values
            return FakeResult()

    class FakeContext:
        def __enter__(self):
            return FakeConnection()

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(backfill_module, "connect", lambda: FakeContext())

    rows = backfill_module._targets(version_id, limit=None, force=True)

    assert rows[0]["version_id"] == version_id
    assert "source_chunk.version_id=v.version_id" in captured["statement"]
    assert "indexed_at" not in captured["statement"]
    assert captured["values"] == (version_id,)
