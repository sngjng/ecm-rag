"""검색 근거 유형에 따른 답변 안내 문구 테스트."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
