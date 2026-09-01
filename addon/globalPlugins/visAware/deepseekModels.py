# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Shared DeepSeek vision model presets and request helpers."""

from collections import OrderedDict
from typing import Any

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


def buildDeepSeekChatCompletionsUrl(baseUrl: str) -> str:
	"""Builds the DeepSeek Chat Completions endpoint URL."""
	baseUrl = baseUrl.rstrip("/")
	if baseUrl.endswith("/chat/completions"):
		return baseUrl
	return f"{baseUrl}/chat/completions"


def getDeepSeekVisionModelChoices() -> OrderedDict[str, str]:
	"""Returns supported DeepSeek vision model presets."""
	return _DEEPSEEK_VISION_MODEL_CHOICES.copy()


def redactDeepSeekImageUrlsForLog(value: Any, keyName: str = "") -> Any:
	"""Redacts DeepSeek image data URLs before verbose logging."""
	if isinstance(value, dict):
		redacted = {
			key: redactDeepSeekImageUrlsForLog(childValue, str(key)) for key, childValue in value.items()
		}
		if keyName == "image_url" and isinstance(value.get("url"), str):
			redacted["url"] = f"<redacted data URL: {len(value['url'])} chars>"
		return redacted
	if isinstance(value, list):
		return [redactDeepSeekImageUrlsForLog(item, keyName) for item in value]
	if isinstance(value, tuple):
		return tuple(redactDeepSeekImageUrlsForLog(item, keyName) for item in value)
	if keyName == "image_url" and isinstance(value, str):
		return f"<redacted data URL: {len(value)} chars>"
	return value
