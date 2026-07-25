# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Shared Gemini model and media-resolution presets."""

from collections import OrderedDict

import addonHandler

addonHandler.initTranslation()

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_GEMINI_MEDIA_RESOLUTION = "MEDIA_RESOLUTION_HIGH"


def getGeminiModelChoices() -> OrderedDict[str, str]:
	"""
	Returns supported Gemini model presets for vision-capable requests.

	:returns: An ordered mapping of model IDs to display names.
	"""
	return OrderedDict(
		{
			# Translators: The display name for a Gemini model preset.
			"gemini-3.6-flash": _("Gemini 3.6 Flash (recommended)"),
			# Translators: The display name for a Gemini model preset.
			"gemini-3.5-flash-lite": _("Gemini 3.5 Flash-Lite (fast, lower cost)"),
			# Translators: The display name for a Gemini model preset.
			"gemini-3.5-flash": _("Gemini 3.5 Flash"),
			# Translators: The display name for a Gemini model preset.
			"gemini-flash-latest": _("Gemini Flash Latest"),
			# Translators: The display name for a Gemini model preset.
			"gemini-3.1-pro-preview": _("Gemini 3.1 Pro Preview (higher reasoning, slower)"),
			# Translators: The display name for a Gemini model preset.
			"gemini-pro-latest": _("Gemini Pro Latest (higher reasoning)"),
			# Translators: The display name for a Gemini model preset.
			"gemini-3-flash-preview": _("Gemini 3 Flash Preview (agentic preview)"),
			# Translators: The display name for a Gemini model preset.
			"gemini-3.1-flash-lite": _("Gemini 3.1 Flash-Lite (fast, lower cost)"),
			# Translators: The display name for a Gemini model preset.
			"gemini-flash-lite-latest": _("Gemini Flash-Lite Latest (fast, lower cost)"),
			# Translators: The display name for a Gemini model preset.
			"gemini-2.5-flash-lite": _("Gemini 2.5 Flash-Lite (stable low cost)"),
		},
	)


def getGeminiMediaResolutionChoices() -> OrderedDict[str, str]:
	"""
	Returns Gemini media-resolution presets.

	:returns: An ordered mapping of API values to display names.
	"""
	return OrderedDict(
		{
			# Translators: The display name for Gemini's default media resolution.
			"MEDIA_RESOLUTION_UNSPECIFIED": _("Automatic (model default)"),
			# Translators: The display name for high Gemini media resolution.
			"MEDIA_RESOLUTION_HIGH": _("High (best detail, slower)"),
			# Translators: The display name for medium Gemini media resolution.
			"MEDIA_RESOLUTION_MEDIUM": _("Medium (balanced)"),
			# Translators: The display name for low Gemini media resolution.
			"MEDIA_RESOLUTION_LOW": _("Low (faster, less detail)"),
		},
	)


def getGeminiLowLatencyThinkingConfig(model: str) -> dict[str, int | str] | None:
	"""
	Returns the low-latency thinking configuration for known Gemini models.

	:param model: The Gemini model ID.
	:returns: A Gemini thinkingConfig object, or None for models without a known low-latency setting.
	"""
	model = model.lower()
	if model == "gemini-flash-latest" or model.startswith("gemini-3.6"):
		return {"thinkingLevel": "medium"}
	if model == "gemini-pro-latest" or model.startswith("gemini-3.1-pro") or model.startswith("gemini-3-pro"):
		return {"thinkingLevel": "low"}
	if model == "gemini-flash-lite-latest" or model.startswith("gemini-3"):
		return {"thinkingLevel": "minimal"}
	if model.startswith("gemini-2.5-flash"):
		return {"thinkingBudget": 0}
	return None
