"""Java stack trace와 code_symbols 정확 검색 키 변환 테스트."""
import unittest

from service.search import _stack_frame_keys


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


if __name__ == "__main__":
    unittest.main()
