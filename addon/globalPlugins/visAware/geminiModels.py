# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Shared Gemini model and media-resolution presets."""

from collections import OrderedDict

import addonHandler

addonHandler.initTranslation()

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_GEMINI_MEDIA_RESOLUTION = "MEDIA_RESOLUTION_HIGH"

_GEMINI_3_THINKING_LEVELS = {
	"gemini-3.8-flash": "low",
	"gemini-3.7-flash": "low",
	"gemini-3.6-flash": "medium",
	"gemini-3.5-flash-lite": "minimal",
	"gemini-3.5-flash": "minimal",
	"gemini-flash-latest": "medium",
	"gemini-3.1-pro-preview": "low",
	"gemini-pro-latest": "low",
	"gemini-3-flash-preview": "minimal",
	"gemini-3.1-flash-lite": "minimal",
	"gemini-flash-lite-latest": "minimal",
}


def getGeminiModelChoices() -> OrderedDict[str, str]:
	"""
	Returns supported Gemini model presets for vision-capable requests.

	:returns: An ordered mapping of model IDs to display names.
	"""
	return OrderedDict(
		{
			# Translators: The display name for a Gemini model preset.
			"gemini-3.8-flash": _("Gemini 3.8 Flash (latest)"),
			# Translators: The display name for a Gemini model preset.
			"gemini-3.7-flash": _("Gemini 3.7 Flash"),
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


def getGeminiAgentModelChoices() -> OrderedDict[str, str]:
	modelChoices = getGeminiModelChoices()
	return OrderedDict(
		(model, modelChoices[model])
		for model in (
			"gemini-3.8-flash",
			"gemini-3.7-flash",
			"gemini-3.6-flash",
			"gemini-3.5-flash-lite",
			"gemini-3.5-flash",
			"gemini-flash-latest",
			"gemini-3-flash-preview",
			"gemini-3.1-flash-lite",
			"gemini-flash-lite-latest",
			"gemini-2.5-flash-lite",
		)
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
	thinkingLevel = _GEMINI_3_THINKING_LEVELS.get(model)
	if thinkingLevel:
		return {"thinkingLevel": thinkingLevel}
	if model == "gemini-2.5-flash-lite":
		return {"thinkingBudget": 0}
	return None


def supportsGeminiPerImageResolution(model: str) -> bool:
	# Per-image resolution is supported only by Gemini 3, unlike the global GenerateContent setting.
	return model.lower() in _GEMINI_3_THINKING_LEVELS
