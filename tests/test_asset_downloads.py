"""원문과 Markdown 다운로드 endpoint의 파일 선택·fallback을 검증한다."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from service.routes.assets import download_version_markdown, view_version_markdown


class MarkdownDownloadTests(unittest.TestCase):
    def setUp(self):
        self.asset_id = uuid4()
        self.version_id = uuid4()
        self.version = {
            "asset_type": "document",
            "title": "운영 지침",
            "version_label": "1",
            "source_filename": "운영 지침.pdf",
            "source_path": "/not/exposed/source.pdf",
        }

    def test_preserved_pdf_markdown_is_downloaded(self):
        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            markdown = artifact_root / str(self.version_id) / "document.md"
            markdown.parent.mkdir(parents=True)
            markdown.write_text("# 변환 문서\n", encoding="utf-8")
            settings = SimpleNamespace(
                paths=SimpleNamespace(artifact_root=artifact_root)
            )
            with (
                patch("service.routes.assets.get_settings", return_value=settings),
                patch("service.routes.assets.assets.get_version", return_value=self.version),
                patch(
                    "service.routes.assets.assets.list_version_markdown_chunks"
                ) as chunks,
            ):
                response = download_version_markdown(self.asset_id, self.version_id)

            self.assertIsInstance(response, FileResponse)
            self.assertEqual(Path(response.path), markdown)
            self.assertIn(".md", response.headers["content-disposition"])
            chunks.assert_not_called()

    def test_stored_chunks_are_returned_when_artifact_is_missing(self):
        with TemporaryDirectory() as directory:
            settings = SimpleNamespace(
                paths=SimpleNamespace(artifact_root=Path(directory))
            )
            rows = [
                {"chunk_seq": 0, "kind": "text", "display_content": "첫 번째 내용"},
                {"chunk_seq": 1, "kind": "text", "display_content": "두 번째 내용"},
            ]
            with (
                patch("service.routes.assets.get_settings", return_value=settings),
                patch("service.routes.assets.assets.get_version", return_value=self.version),
                patch(
                    "service.routes.assets.assets.list_version_markdown_chunks",
                    return_value=rows,
                ),
            ):
                response = download_version_markdown(self.asset_id, self.version_id)

            self.assertIsInstance(response, Response)
            self.assertEqual(
                response.body.decode("utf-8"),
                "첫 번째 내용\n\n---\n\n두 번째 내용\n",
            )
            self.assertIn("filename*=UTF-8''", response.headers["content-disposition"])

    def test_missing_version_returns_404(self):
        with patch("service.routes.assets.assets.get_version", return_value=None):
            with self.assertRaises(HTTPException) as error:
                download_version_markdown(self.asset_id, self.version_id)
        self.assertEqual(error.exception.status_code, 404)

    def test_non_document_asset_returns_400(self):
        source_code_version = {
            **self.version,
            "asset_type": "source_code",
            "source_filename": "sample.java",
        }
        with patch(
            "service.routes.assets.assets.get_version",
            return_value=source_code_version,
        ):
            with self.assertRaises(HTTPException) as error:
                download_version_markdown(self.asset_id, self.version_id)
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("문서 자산", error.exception.detail)

        with patch(
            "service.routes.assets.assets.get_version",
            return_value=source_code_version,
        ):
            with self.assertRaises(HTTPException) as view_error:
                view_version_markdown(self.asset_id, self.version_id)
        self.assertEqual(view_error.exception.status_code, 400)

    def test_version_without_artifact_or_chunks_returns_404(self):
        with TemporaryDirectory() as directory:
            settings = SimpleNamespace(
                paths=SimpleNamespace(artifact_root=Path(directory))
            )
            with (
                patch("service.routes.assets.get_settings", return_value=settings),
                patch("service.routes.assets.assets.get_version", return_value=self.version),
                patch(
                    "service.routes.assets.assets.list_version_markdown_chunks",
                    return_value=[],
                ),
            ):
                with self.assertRaises(HTTPException) as error:
                    download_version_markdown(self.asset_id, self.version_id)
        self.assertEqual(error.exception.status_code, 404)

    def test_markdown_view_returns_safe_rendered_html(self):
        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            markdown = artifact_root / str(self.version_id) / "document.md"
            markdown.parent.mkdir(parents=True)
            markdown.write_text(
                "# 운영 지침\n\n<script>alert('xss')</script>\n\n"
                "[위험 링크](javascript:alert(1))\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                paths=SimpleNamespace(artifact_root=artifact_root)
            )
            with (
                patch("service.routes.assets.get_settings", return_value=settings),
                patch("service.routes.assets.assets.get_version", return_value=self.version),
            ):
                response = view_version_markdown(self.asset_id, self.version_id)

        self.assertEqual(response["title"], "운영 지침")
        self.assertIn("<h1>운영 지침</h1>", response["html"])
        self.assertIn("&lt;script&gt;", response["html"])
        self.assertNotIn("<script", response["html"])
        self.assertNotIn("javascript:", response["html"])


if __name__ == "__main__":
    unittest.main()
