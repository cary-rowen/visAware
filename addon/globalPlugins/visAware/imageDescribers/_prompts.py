# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Shared prompt text and formatting rules for image description engines."""

import addonHandler

addonHandler.initTranslation()

# Translators: The default prompt sent to image description models.
DEFAULT_IMAGE_DESCRIPTION_PROMPT = _(
	"Describe this image objectively for a screen-reader user. "
	"Return only the final image description. "
	"Include all readable text visible in the image; if there is no readable text, do not mention it. "
	"Do not add introductory phrases, reasoning, planning, task restatements, or subjective speculation. "
	"When mathematical notation or formulas are visible, convert them to LaTeX and enclose each formula "
	"in single dollar signs, for example `$x^2 + y^2 = z^2$`. Do not solve, simplify, or invent missing parts. "
	"If the image contains only a mathematical formula, output only the formula in LaTeX enclosed in single "
	"dollar signs. Do not describe the background, layout, colors, or introduce the formula.",
)

# Translators: The default prompt sent to automatic image description models.
DEFAULT_AUTO_RECOGNITION_PROMPT = _(
	"Briefly describe the main subject of the image in 20 to 30 words. "
	"Output one sentence only. Avoid Markdown, LaTeX, headings, lists, introductions, and explanations. "
	"Do not make subjective guesses or transcribe text unrelated to the main subject.",
)

# Translators: The instruction appended when image description results are shown in a browsable message.
_MARKDOWN_OUTPUT_INSTRUCTION = _(
	"Use simple Markdown where helpful. When the image contains a table, represent it as a standard Markdown table, "
	"not a list. Never wrap the response in code fences. Keep LaTeX formulas enclosed in single dollar signs.",
)

# Translators: The instruction appended when image description results are announced as plain text.
_PLAIN_TEXT_OUTPUT_INSTRUCTION = _(
	"Return plain text only. "
	"Do not use Markdown formatting, headings, lists, tables, code spans, or code fences. "
	"Keep LaTeX formulas enclosed in single dollar signs.",
)

# Translators: The instruction appended to ordinary image description prompts for mathematical formulas.
_FORMULA_INSTRUCTION = _(
	"When mathematical notation or formulas are visible, convert them to LaTeX and enclose each formula "
	"in single dollar signs, for example `$x^2 + y^2 = z^2$`. "
	"Do not solve, simplify, or invent missing parts of a formula. "
	"If the image contains only a mathematical formula, output only the formula in LaTeX enclosed in single "
	"dollar signs. Do not describe the background, layout, colors, or introduce the formula.",
)


def buildImageDescriptionPrompt(prompt: str, useMarkdown: bool) -> str:
	"""Adds formula and result-format instructions to an ordinary image prompt."""
	prompt = prompt.strip()
	instructions: list[str] = []
	if "LaTeX" not in prompt and "$...$" not in prompt:
		instructions.append(_FORMULA_INSTRUCTION)
	instructions.append(_MARKDOWN_OUTPUT_INSTRUCTION if useMarkdown else _PLAIN_TEXT_OUTPUT_INSTRUCTION)
	return "\n\n".join((prompt, *instructions))
