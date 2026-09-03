# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""A formula recognition engine that uses the Baimiao Web service."""

from __future__ import annotations

import addonHandler
from typing import Any

from ._baimiaoWeb import BaimiaoWebClient, extractFormulaMarkdown
from .baimiaoOCR import CustomContentRecognizer as BaimiaoOCRRecognizer

addonHandler.initTranslation()


class CustomContentRecognizer(BaimiaoOCRRecognizer):
	"""Recognizes mathematical formulas and text with Baimiao."""

	name = "baimiaoFormula"
	# Translators: The description of the Baimiao formula recognition engine.
	description = _("Baimiao Formula Recognition")
	engineConfigSpec = {}
	forceBrowseableMessage = True

	def _recognizeContent(
		self,
		client: BaimiaoWebClient,
		imageContent: bytes,
	) -> dict[str, Any]:
		return client.recognizeFormula(imageContent)

	def extractText(self, apiResult: dict[str, Any]) -> str:
		"""Returns text and LaTeX using the delimiters understood by Vis Aware."""

		return extractFormulaMarkdown(apiResult)

	def _convertToLineResultFormat(self, apiResult: dict[str, Any]) -> list[list[dict[str, Any]]]:
		"""Uses text output so Vis Aware can render LaTeX as accessible MathML."""

		return []
