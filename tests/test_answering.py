"""검색 근거 유형에 따른 답변 안내 문구 테스트."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from service.answering import DECOMPILED_LINE_NOTICE, answer_documents
from service.schemas import ChatRequest


class AnswerNoticeTests(unittest.TestCase):
    def _settings(self):
        return SimpleNamespace(
            models=SimpleNamespace(llm=SimpleNamespace(context_top_k=8)),
            graph_rag=SimpleNamespace(
                enabled=False,
                query_max_entities=30,
                query_max_relations=50,
            ),
        )

    def test_decompiled_jar_result_adds_line_number_notice(self):
        result = {
            "kind": "decompiled_code_symbol",
            "metadata": {"decompiled": True},
            "content": "public void load() {}",
        }
        with (
            patch("service.answering.get_settings", return_value=self._settings()),
            patch("service.answering.search", return_value=[result]),
            patch("service.answering.generate_answer", return_value="분석 결과입니다."),
        ):
            response = answer_documents(ChatRequest(query="오류 원인을 알려줘"))

        self.assertIn(DECOMPILED_LINE_NOTICE, response["answer"])
        self.assertEqual(response["notices"], [DECOMPILED_LINE_NOTICE])

    def test_direct_java_result_does_not_add_decompile_notice(self):
        result = {
            "kind": "code_symbol",
            "metadata": {"language": "java"},
            "content": "public void load() {}",
        }
        with (
            patch("service.answering.get_settings", return_value=self._settings()),
            patch("service.answering.search", return_value=[result]),
            patch("service.answering.generate_answer", return_value="분석 결과입니다."),
        ):
            response = answer_documents(ChatRequest(query="오류 원인을 알려줘"))

        self.assertNotIn(DECOMPILED_LINE_NOTICE, response["answer"])
        self.assertEqual(response["notices"], [])

    def test_diagram_is_generated_only_when_requested(self):
        result = {
            "chunk_id": "version:000000",
            "kind": "text",
            "metadata": {},
            "content": "내부망 API 연계 구성",
        }
        expected = {"type": "architecture", "nodes": [], "edges": []}
        settings = self._settings()
        settings.graph_rag.enabled = True
        graph = {"entities": [{"entity_id": "one"}], "relations": []}
        with (
            patch("service.answering.get_settings", return_value=settings),
            patch("service.answering.search", return_value=[result]),
            patch("service.answering.fetch_subgraph", return_value=graph) as fetch,
            patch("service.answering.generate_answer", return_value="구성 답변"),
            patch("service.answering.build_document_diagram", return_value=expected) as build,
        ):
            response = answer_documents(
                ChatRequest(query="구성도를 알려줘", include_diagram=True)
            )

        self.assertEqual(response["diagram"], expected)
        fetch.assert_called_once_with(
            ["version:000000"], max_entities=30, max_relations=50
        )
        build.assert_called_once_with(graph)

    def test_diagram_is_hidden_when_answer_reports_insufficient_evidence(self):
        result = {
            "chunk_id": "version:000000",
            "kind": "text",
            "metadata": {},
            "content": "하이메시징 시스템 성능시험",
        }
        settings = self._settings()
        settings.graph_rag.enabled = True
        graph = {
            "entities": [{"entity_id": "one"}, {"entity_id": "two"}],
            "relations": [{"relation_id": "relation"}],
        }
        answer = (
            '제공된 문서에서 "여신 롤 배치 이관" 관련 내용은 '
            "확인할 수 없습니다."
        )
        with (
            patch("service.answering.get_settings", return_value=settings),
            patch("service.answering.search", return_value=[result]),
            patch("service.answering.fetch_subgraph", return_value=graph),
            patch("service.answering.generate_answer", return_value=answer),
            patch("service.answering.build_document_diagram") as build,
        ):
            response = answer_documents(
                ChatRequest(query="여신 롤 배치 이관 가이드", include_diagram=True)
            )

        self.assertIsNone(response["diagram"])
        build.assert_not_called()

    def test_preferred_documents_are_forwarded_to_hybrid_search(self):
        preferred_asset_id = uuid4()
        result = {
            "asset_id": preferred_asset_id,
            "kind": "text",
            "metadata": {},
            "content": "A서버 재기동 절차",
        }
        with (
            patch("service.answering.get_settings", return_value=self._settings()),
            patch("service.answering.search", return_value=[result]) as search,
            patch("service.answering.generate_answer", return_value="재기동 절차입니다."),
        ):
            response = answer_documents(ChatRequest(
                query="그 문서의 재기동 방법은?",
                preferred_asset_ids=[preferred_asset_id],
            ))

        self.assertEqual(
            search.call_args.kwargs["preferred_asset_ids"],
            [preferred_asset_id],
        )
        self.assertEqual(
            response["preferred_asset_ids_applied"],
            [str(preferred_asset_id)],
        )


if __name__ == "__main__":
    unittest.main()
