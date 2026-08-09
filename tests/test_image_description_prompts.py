from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path
import sys
import types
import unittest


def load_prompt_module():
	builtins._ = lambda value: value
	addonHandlerModule = types.ModuleType("addonHandler")
	addonHandlerModule.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandlerModule
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon/globalPlugins/visAware/imageDescribers/_prompts.py"
	)
	spec = importlib.util.spec_from_file_location("visAwareImageDescriptionPrompts", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load image description prompts")
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class ImageDescriptionPromptsTestCase(unittest.TestCase):
	def test_markdown_instruction_is_selected(self) -> None:
		module = load_prompt_module()

		prompt = module.buildImageDescriptionPrompt("Describe the image.", useMarkdown=True)

		self.assertIn("LaTeX", prompt)
		self.assertIn("single dollar signs", prompt)
		self.assertIn("simple Markdown", prompt)
		self.assertIn("standard Markdown table", prompt)
		self.assertIn("not a list", prompt)
		self.assertIn("Never wrap the response in code fences", prompt)
		self.assertNotIn("Return plain text only", prompt)

	def test_plain_text_instruction_is_selected(self) -> None:
		module = load_prompt_module()

		prompt = module.buildImageDescriptionPrompt("Describe the image.", useMarkdown=False)

		self.assertIn("LaTeX", prompt)
		self.assertIn("Return plain text only", prompt)
		self.assertIn("Do not use Markdown formatting", prompt)
		self.assertNotIn("standard Markdown table", prompt)
		self.assertNotIn("Never wrap the response in code fences", prompt)
		self.assertNotIn("simple Markdown", prompt)

	def test_default_prompt_does_not_repeat_formula_instruction(self) -> None:
		module = load_prompt_module()

		prompt = module.buildImageDescriptionPrompt(
			module.DEFAULT_IMAGE_DESCRIPTION_PROMPT,
			useMarkdown=True,
		)

		self.assertEqual(1, prompt.count("When mathematical notation or formulas are visible"))

	def test_formula_only_images_return_only_latex(self) -> None:
		module = load_prompt_module()

		self.assertIn(
			"If the image contains only a mathematical formula", module.DEFAULT_IMAGE_DESCRIPTION_PROMPT
		)
		self.assertIn(
			"Do not describe the background, layout, colors", module.DEFAULT_IMAGE_DESCRIPTION_PROMPT
		)

	def test_automatic_prompt_is_concise_localizable_text(self) -> None:
		module = load_prompt_module()

		self.assertIn("20 to 30 words", module.DEFAULT_AUTO_RECOGNITION_PROMPT)
		self.assertIn("Avoid Markdown", module.DEFAULT_AUTO_RECOGNITION_PROMPT)
		self.assertIn("LaTeX", module.DEFAULT_AUTO_RECOGNITION_PROMPT)


if __name__ == "__main__":
	unittest.main()
