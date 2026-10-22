"""외부 질의응답 입력 형식의 네트워크 비의존 단위 테스트."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from service.routes.external import answer, parse_external_question, prepare_external_answer
from service.stacktrace import analyze_stack_trace, build_stack_trace_query


class ExternalQuestionParsingTests(unittest.TestCase):
    def test_accepts_json_object(self):
        request = parse_external_question(
            '{"query":"장애 대응 절차를 알려줘","limit":3}'.encode(),
            "application/json; charset=utf-8",
        )
        self.assertEqual(request.query, "장애 대응 절차를 알려줘")
        self.assertEqual(request.limit, 3)

    def test_accepts_json_string(self):
        request = parse_external_question(
            '"복리후생 기준을 알려줘"'.encode(),
            "application/json",
        )
        self.assertEqual(request.query, "복리후생 기준을 알려줘")

    def test_accepts_plain_string(self):
        request = parse_external_question(
            "문서 보존 기간은?".encode(),
            "text/plain; charset=utf-8",
        )
        self.assertEqual(request.query, "문서 보존 기간은?")

    def test_rejects_invalid_json(self):
        with self.assertRaises(HTTPException) as caught:
            parse_external_question(b'{"query":', "application/json")
        self.assertEqual(caught.exception.status_code, 400)

    def test_analyzes_java_stack_trace_for_exact_code_search(self):
        trace = (
            "java.lang.RuntimeException: payment failed PAY1001\n"
            "  at com.company.payment.PaymentService.pay(PaymentService.java:143)\n"
            "  at org.springframework.web.DispatcherServlet.doDispatch(DispatcherServlet.java:933)\n"
            "Caused by: java.sql.SQLException: ORA-00942: table or view does not exist\n"
            "  at com.company.payment.PaymentRepository.load(PaymentRepository.java:81)"
        )

        analysis = analyze_stack_trace(trace)

        self.assertTrue(analysis["detected"])
        self.assertEqual(analysis["primary_exception"], "java.lang.RuntimeException")
        self.assertEqual(analysis["root_cause"]["exception_class"], "java.sql.SQLException")
        self.assertIn("PAY1001", analysis["error_codes"])
        self.assertIn("ORA-00942", analysis["error_codes"])
        self.assertEqual(analysis["frame_count"], 3)
        self.assertEqual(analysis["application_frame_count"], 2)
        self.assertEqual(
            analysis["application_frames"][0]["method_name"], "pay"
        )
        query = build_stack_trace_query(trace, analysis)
        self.assertIn("com.company.payment.PaymentService.pay", query)
        self.assertLessEqual(len(query), 2000)

    def test_plain_question_is_not_classified_as_stack_trace(self):
        analysis = analyze_stack_trace("A서버 설정 관련 문서를 찾아줘")
        self.assertFalse(analysis["detected"])

    def test_stack_trace_json_field_builds_valid_chat_request(self):
        request, analysis = prepare_external_answer(
            (
                '{"query":"원인과 조치 방법을 알려줘","stack_trace":'
                '"java.lang.NullPointerException: value\\n'
                ' at com.company.OrderService.load(OrderService.java:42)"}'
            ).encode(),
            "application/json",
        )

        self.assertTrue(analysis["detected"])
        self.assertIn("com.company.OrderService.load", request.query)
        self.assertIn("원인과 조치 방법", request.query)

    def test_external_request_defaults_to_including_chunks(self):
        request, _ = prepare_external_answer(
            b'{"query":"document question"}', "application/json"
        )
        self.assertTrue(request.include_chunks)

    def test_external_request_accepts_answer_only_mode(self):
        request, _ = prepare_external_answer(
            b'{"query":"document question","include_chunks":false}',
            "application/json",
        )
        self.assertFalse(request.include_chunks)

    def test_plain_stack_trace_is_detected_without_json_wrapper(self):
        request, analysis = prepare_external_answer(
            (
                "java.lang.IllegalStateException: invalid state\n"
                " at com.company.Job.run(Job.java:10)\n"
                " at com.company.Main.main(Main.java:20)"
            ).encode(),
            "text/plain",
        )

        self.assertTrue(analysis["detected"])
        self.assertIn("com.company.Job.run", request.query)


class ExternalAnswerRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_returns_structured_stack_trace_analysis(self):
        class RequestStub:
            headers = {"content-type": "application/json"}

            async def body(self):
                return (
                    '{"stack_trace":"java.lang.IllegalStateException: invalid\\n'
                    ' at com.company.Job.run(Job.java:10)"}'
                ).encode()

        with patch(
            "service.routes.external.answer_documents",
            return_value={
                "answer": "관련 소스를 확인했습니다.",
                "generation_status": "completed",
                "results": [],
            },
        ):
            response = await answer(RequestStub())

        self.assertEqual(response["request_type"], "stack_trace")
        self.assertEqual(
            response["stack_trace_analysis"]["primary_exception"],
            "java.lang.IllegalStateException",
        )
        self.assertTrue(response["chunks_included"])

    async def test_answer_only_response_omits_results(self):
        class RequestStub:
            headers = {"content-type": "application/json"}

            async def body(self):
                return b'{"query":"document question","include_chunks":false}'

        with patch(
            "service.routes.external.answer_documents",
            return_value={
                "answer": "문서 근거 답변입니다.",
                "generation_status": "completed",
                "notices": [],
                "results": [{"chunk_id": "internal-result"}],
            },
        ):
            response = await answer(RequestStub())

        self.assertEqual(response["request_type"], "question")
        self.assertFalse(response["chunks_included"])
        self.assertNotIn("results", response)


if __name__ == "__main__":
    unittest.main()
