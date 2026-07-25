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
			self.renderer.renderMarkdownToHtml(
				"```html\n<div>$x$</div>\n```\n\nUse `<button title='$x$'>OK</button>`.",
			),
		)
		self.assertIn("&lt;div&gt;$x$&lt;/div&gt;", htmlText)
		self.assertIn("<code>&lt;button title='$x$'&gt;OK&lt;/button&gt;</code>", htmlText)
		self.assertNotIn("<math", htmlText)

	def test_converts_formula_inside_raw_html_table(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(
			"<table><tr><td>$ \\lim_{x \\to 1} \\frac{x^{2} - 3x + 2}{x - 1} $</td></tr></table>",
		)
		self.assertIn("<math", htmlText)
		self.assertIn("<mfrac>", htmlText)
		self.assertNotIn("$ \\lim", htmlText)
		self.assertIn("<table", htmlText)

	def test_unwraps_only_formula_only_single_cell_table(self) -> None:
		formulaTable = self.renderer.sanitizeRenderedHtml(
			self.renderer.renderMarkdownToHtml(
				"<table border=1><tr><td>$x+1$</td></tr></table>",
			),
		)
		self.assertTrue(formulaTable.startswith("<math"))
		self.assertNotIn("<table", formulaTable)

		realTable = self.renderer.sanitizeRenderedHtml(
			self.renderer.renderMarkdownToHtml(
				"<table><tr><td>$x$</td><td>$y$</td></tr></table>",
			),
		)
		self.assertIn("<table", realTable)
		self.assertEqual(2, realTable.count("<math"))

	def test_preserves_real_table_and_html_attributes(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(
			"<table summary='Result'><tr><td title='$unchanged$'>$x$</td></tr></table>",
		)
		self.assertIn("<table", htmlText)
		self.assertIn("summary='Result'", htmlText)
		self.assertIn("title='$unchanged$'", htmlText)
		self.assertIn("<math", htmlText)

	def test_preserves_mathml_entities_while_converting_raw_html(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(
			"<div><math><mo>&InvisibleTimes;</mo></math><span>$x$</span></div>",
		)
		self.assertIn("&InvisibleTimes;", htmlText)
		self.assertNotIn("&amp;InvisibleTimes;", htmlText)
		self.assertIn("\u2062", self.renderer.sanitizeRenderedHtml(htmlText))

	def test_preserves_character_reference_terminators_while_converting_raw_html(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(
			"<table><tr><td>AT&T &copy; &copy</td><td>$x$</td></tr></table>",
		)
		self.assertIn("<td>AT&T &copy; &copy</td>", htmlText)
		self.assertEqual(1, htmlText.count("<math"))
		sanitizedHtml = self.renderer.sanitizeRenderedHtml(htmlText)
		self.assertIn("AT&amp;T", sanitizedHtml)
		self.assertNotIn("AT&amp;T;", sanitizedHtml)

	def test_escapes_mathml_text_from_raw_html_formula(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(r"<div>$\text{x&lt;y}$ trailing</div>")
		self.assertIn("<mtext>x&lt;y</mtext>", htmlText)
		sanitizedHtml = self.renderer.sanitizeRenderedHtml(htmlText)
		self.assertIn("<mtext>x&lt;y</mtext>", sanitizedHtml)
		self.assertIn(" trailing</div>", sanitizedHtml)

	def test_preserves_latex2mathml_symbols(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(r"<div>$x+1 \to y \le z \alpha$</div>")
		self.assertNotIn("&amp;#", htmlText)
		sanitizedHtml = self.renderer.sanitizeRenderedHtml(htmlText)
		for character in ("+", "→", "≤", "α"):
			self.assertIn(character, sanitizedHtml)

	def test_preserves_literal_numeric_character_reference_text(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(
			r"<div>$\text{&amp;#65; &amp;#x41; &amp;copy;}$</div>",
		)
		sanitizedHtml = self.renderer.sanitizeRenderedHtml(htmlText)
		for literalReference in ("&amp;#65;", "&amp;#x41;", "&amp;copy;"):
			self.assertIn(literalReference, htmlText)
			self.assertIn(literalReference, sanitizedHtml)

	def test_literal_reference_marker_avoids_unescaped_formula_characters(self) -> None:
		marker = chr(self.renderer._PRIVATE_USE_MARKER_START)
		htmlText = self.renderer.renderMarkdownToHtml(
			r"<div>$\text{&#xF0000; &amp;#65;}$</div>",
		)
		self.assertIn(marker, htmlText)
		self.assertEqual(1, htmlText.count("&amp;#65;"))

	def test_literal_reference_marker_does_not_replace_generated_unicode(self) -> None:
		marker = chr(self.renderer._PRIVATE_USE_MARKER_START)
		mathElement = self.renderer.ElementTree.Element("math")
		mathElement.text = f"{marker} &#x{ord(marker):X};"
		self.renderer._decodeMathMlNumericCharacterReferences(
			mathElement,
			{marker: "&amp;#65;"},
		)
		self.assertEqual(f"&#65; {marker}", mathElement.text)

	def test_respects_markdown_escaped_math_delimiters(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(r"Literal \\(x+1\\) and \\[y+1\\]")
		self.assertIn(r"\(x+1\)", htmlText)
		self.assertIn(r"\[y+1\]", htmlText)
		self.assertNotIn("<math", htmlText)

	def test_converts_supported_delimiters_and_skips_existing_math(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(
			r"<div>\(x+1\) \[y+1\] $$z+1$$ <math><mi>$q$</mi></math></div>",
		)
		self.assertEqual(4, htmlText.count("<math"))
		self.assertIn("<mi>$q$</mi>", htmlText)

	def test_converts_adjacent_and_multiline_inline_formulas(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml("<div>$x$$y$ $a\n+b$</div>")
		self.assertEqual(3, htmlText.count("<math"))
		self.assertIn("<mi>x</mi>", htmlText)
		self.assertIn("<mi>y</mi>", htmlText)
		self.assertIn("<mi>a</mi>", htmlText)

	def test_preserves_paired_prices_and_converts_following_formulas(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(r"<td>From $5 to $10; $x$</td>")
		self.assertIn("From $5 to $10; ", htmlText)
		self.assertEqual(1, htmlText.count("<math"))
		self.assertEqual(2, htmlText.count("$"))

	def test_preserves_math_delimiters_in_html_text_only_elements(self) -> None:
		for tag in ("title", "iframe", "noscript", "xmp", "noembed", "noframes"):
			with self.subTest(tag=tag):
				htmlText = self.renderer.renderMarkdownToHtml(
					f"<{tag}>$x$</{tag}><span>$y$</span>",
				)
				self.assertIn("$x$", htmlText)
				self.assertEqual(1, htmlText.count("<math"))
				self.assertNotIn("&lt;math", self.renderer.sanitizeRenderedHtml(htmlText))

	def test_preserves_cdata_while_converting_following_formula(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(
			"<div><math><annotation><![CDATA[$x$]]></annotation></math><span>$y$</span></div>",
		)
		self.assertIn("<![CDATA[$x$]]>", htmlText)
		self.assertEqual(2, htmlText.count("<math"))
		sanitizedHtml = self.renderer.sanitizeRenderedHtml(htmlText)
		self.assertIn("<annotation>$x$</annotation>", sanitizedHtml)
		self.assertIn("<mi>y</mi>", sanitizedHtml)
		self.assertNotIn("&lt;/annotation", sanitizedHtml)

	def test_preserves_unmatched_formula_delimiter(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml("<p>Costs $5 with no closing delimiter.</p>")
		self.assertIn("Costs $5 with no closing delimiter.", htmlText)
		self.assertNotIn("<math", htmlText)

	def test_converts_formula_after_unmatched_different_delimiter(self) -> None:
		htmlText = self.renderer.renderMarkdownToHtml(r"<div>Cost $5; solve \(x+1\)</div>")
		self.assertIn("Cost $5; solve ", htmlText)
		self.assertIn("<math", htmlText)
		self.assertNotIn(r"\(x+1\)", htmlText)


if __name__ == "__main__":
	unittest.main()
