"""Java stack trace와 code_symbols 정확 검색 키 변환 테스트."""
import unittest

from service.search import _qualified_vector_ranking, _stack_frame_keys


class StackFrameKeyTest(unittest.TestCase):
    def test_stack_frame_keys_preserve_package_class_and_method(self):
        query = (
            "java.lang.NullPointerException\n"
            "  at com.company.payment.PaymentService.pay(PaymentService.java:143)\n"
            "  at com.company.payment.PaymentController.handle(PaymentController.java:51)"
        )

        self.assertEqual(_stack_frame_keys(query), [
            ("com.company.payment", ["PaymentService"], ["pay"]),
            ("com.company.payment", ["PaymentController"], ["handle"]),
        ])

    def test_inner_class_and_constructor_create_ast_compatible_candidates(self):
        query = "at com.company.Batch$Worker.<init>(Batch.java:20)"

        self.assertEqual(_stack_frame_keys(query), [
            (
                "com.company",
                ["Batch$Worker", "Batch", "Worker"],
                ["<init>", "Batch$Worker", "Batch", "Worker"],
            )
        ])

    def test_unrelated_vector_candidates_are_removed_by_distance(self):
        ranking, distances = _qualified_vector_ranking(
            [
                {"chunk_id": "relevant", "vector_distance": 0.31},
                {"chunk_id": "unrelated", "vector_distance": 0.82},
            ],
            max_distance=0.55,
        )

        self.assertEqual(ranking, ["relevant"])
        self.assertEqual(distances["unrelated"], 0.82)


if __name__ == "__main__":
    unittest.main()
