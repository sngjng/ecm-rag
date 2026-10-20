"""외부 CFR 실행을 모의해 JAR→Java 심볼 변환 경로를 검증한다."""
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from service.jar_parser import parse_jar
from service.parsers import parse_code


class JarParserTest(unittest.TestCase):
    def test_jar_is_decompiled_and_indexed_as_java_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "payment.jar"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("com/company/payment/PaymentService.class", b"bytecode")
            cfr = root / "cfr.jar"
            cfr.write_bytes(b"tool")

            def fake_run(command, **kwargs):
                output = Path(command[command.index("--outputdir") + 1])
                java = output / "com/company/payment/PaymentService.java"
                java.parent.mkdir(parents=True)
                java.write_text(
                    "package com.company.payment;\n"
                    "public class PaymentService {\n"
                    "  public void pay(String id) { System.out.println(id); }\n"
                    "}\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="")

            settings = SimpleNamespace(
                cfr_jar_path=str(cfr), java_command="java", timeout_seconds=30,
                max_class_files=10, max_uncompressed_mb=10, max_java_file_kb=128,
            )
            progress_events = []
            with patch("service.jar_parser.subprocess.run", side_effect=fake_run):
                items, report = parse_jar(
                    source,
                    root / "out",
                    "payment",
                    "main",
                    "lib/payment.jar",
                    settings,
                    lambda stage, detail, processed, total: progress_events.append(
                        (stage, processed, total)
                    ),
                )

            pay = next(
                item for item in items
                if item.symbol and item.symbol["symbol_name"] == "pay"
            )
            self.assertEqual(pay.kind, "decompiled_code_symbol")
            self.assertEqual(pay.symbol["package_name"], "com.company.payment")
            self.assertEqual(pay.symbol["class_name"], "PaymentService")
            self.assertEqual(pay.metadata["jar_path"], "lib/payment.jar")
            self.assertGreaterEqual(report["symbols"], 2)
            self.assertEqual(progress_events[0][0], "inspecting_jar")
            self.assertIn(("decompiling_jar", 0, 1), progress_events)
            self.assertIn(("parsing_jar", 1, 1), progress_events)

    def test_jar_path_does_not_load_native_java_parser(self):
        """JAR 분석 중 native parser가 호출되면 segmentation fault가 재발할 수 있다."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java = root / "Example.java"
            java.write_text(
                "package sample;\n"
                "public class Example {\n"
                "  public String hello(String name) { return name; }\n"
                "}\n",
                encoding="utf-8",
            )
            with patch(
                "service.parsers._get_java_parser",
                side_effect=AssertionError("native parser must not be loaded"),
            ):
                items, report = parse_code(
                    java,
                    "sample",
                    "main",
                    "sample.jar!/sample/Example.java",
                    safe_java_parser=True,
                )

            hello = next(item for item in items if item.identifier == "hello")
            self.assertEqual(hello.symbol["package_name"], "sample")
            self.assertEqual(hello.symbol["class_name"], "Example")
            self.assertGreaterEqual(report["symbols"], 2)


if __name__ == "__main__":
    unittest.main()
