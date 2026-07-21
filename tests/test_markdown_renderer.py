from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


def load_renderer():
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "markdownRenderer.py"
	)
	spec = importlib.util.spec_from_file_location("visAware_markdownRenderer", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load markdownRenderer.py")
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class MarkdownRendererTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.renderer = load_renderer()

	def test_sanitizes_untrusted_html(self) -> None:
		htmlText = self.renderer.sanitizeRenderedHtml(
			self.renderer.renderMarkdownToHtml(
				'<script>alert(1)</script><a href="javascript:alert(2)" onclick="evil()">link</a>',
			),
		)
		self.assertNotIn("<script>", htmlText)
		self.assertNotIn("alert(1)", htmlText)
		self.assertNotIn("javascript:", htmlText)
		self.assertNotIn("onclick=", htmlText)
		self.assertIn("link", htmlText)

	def test_does_not_allow_image_resource_loads(self) -> None:
		for source in (
			"![tracker](https://example.invalid/pixel.png)",
			'<img src="https://example.invalid/raw.png">',
		):
			with self.subTest(source=source):
				htmlText = self.renderer.sanitizeRenderedHtml(
					self.renderer.renderMarkdownToHtml(source),
				)
				self.assertNotIn("<img", htmlText)
				self.assertNotIn("src=", htmlText)
				self.assertNotIn("https://example.invalid", htmlText)

	def test_preserves_html_source_inside_code(self) -> None:
		htmlText = self.renderer.sanitizeRenderedHtml(
			self.renderer.renderMarkdownToHtml("```html\n<div>x</div>\n```\n\nUse `<button>OK</button>`."),
		)
		self.assertIn("&lt;div&gt;x&lt;/div&gt;", htmlText)
		self.assertIn("<code>&lt;button&gt;OK&lt;/button&gt;</code>", htmlText)


if __name__ == "__main__":
	unittest.main()
