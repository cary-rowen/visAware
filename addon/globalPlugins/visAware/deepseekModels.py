# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Shared DeepSeek vision model presets and request helpers."""

from collections import OrderedDict

import addonHandler

addonHandler.initTranslation()

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_VISION_MODEL = "deepseek-v4-flash-vision-exp"

_DEEPSEEK_VISION_MODEL_CHOICES = OrderedDict(
	{
		# Translators: The display name for the DeepSeek vision model preset.
		"deepseek-v4-flash-vision-exp": _("DeepSeek V4 Flash Vision"),
	},
)


def buildDeepSeekResponsesUrl(baseUrl: str) -> str:
	"""Builds the DeepSeek Responses endpoint URL."""
	baseUrl = baseUrl.rstrip("/")
	if baseUrl.endswith("/responses"):
		return baseUrl
	return f"{baseUrl}/responses"


def getDeepSeekVisionModelChoices(baseUrl: str = DEFAULT_DEEPSEEK_BASE_URL) -> OrderedDict[str, str]:
	"""Returns supported DeepSeek vision model presets."""
	del baseUrl
	return _DEEPSEEK_VISION_MODEL_CHOICES.copy()


def getDefaultDeepSeekVisionModel(baseUrl: str = DEFAULT_DEEPSEEK_BASE_URL) -> str:
	"""Returns the default DeepSeek vision model."""
	del baseUrl
	return DEFAULT_DEEPSEEK_VISION_MODEL
