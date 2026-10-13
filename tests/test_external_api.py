"""외부 질의응답 입력 형식의 네트워크 비의존 단위 테스트."""
import unittest

from fastapi import HTTPException

from service.routes.external import parse_external_question


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


if __name__ == "__main__":
    unittest.main()
