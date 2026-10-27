"""Markdown 웹 렌더러가 문서 구조를 유지하고 실행 코드를 제거하는지 검증한다."""
import unittest

from service.markdown_viewer import render_markdown


class MarkdownViewerTests(unittest.TestCase):
    def test_headings_tables_and_code_are_rendered(self):
        rendered = render_markdown(
            "# 제목\n\n| 항목 | 값 |\n| --- | --- |\n| 상태 | 정상 |\n\n"
            "```python\nprint('ok')\n```"
        )
        self.assertIn("<h1>제목</h1>", rendered)
        self.assertIn("<table>", rendered)
        self.assertIn("<pre>", rendered)
        self.assertIn("<code>", rendered)

    def test_raw_html_and_dangerous_link_are_not_executable(self):
        rendered = render_markdown(
            '<img src=x onerror="alert(1)">\n\n'
            '<script>alert("x")</script>\n\n'
            "[실행](javascript:alert(1))"
        )
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<script", rendered)
        self.assertNotIn("javascript:", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_http_link_opens_without_opener_access(self):
        rendered = render_markdown("[안내](https://example.com/guide)")
        self.assertIn('href="https://example.com/guide"', rendered)
        self.assertIn('rel="noopener noreferrer"', rendered)


if __name__ == "__main__":
    unittest.main()
