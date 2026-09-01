# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Shared Kimi model presets and Chat Completions request helpers."""

from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

import addonHandler

addonHandler.initTranslation()

# Kimi Code exposes this endpoint and the same OpenAI-compatible protocol as
# the public Kimi API. Public platform endpoints use api.moonshot.cn or
# api.moonshot.ai, depending on the platform where the key was created.
DEFAULT_KIMI_BASE_URL = "https://api.kimi.com/coding/v1"
DEFAULT_KIMI_MODEL = "k3-256k"
DEFAULT_KIMI_AGENT_MODEL = "k3-256k"
DEFAULT_KIMI_PUBLIC_MODEL = "kimi-k3"

KIMI_REASONING_EFFORTS = OrderedDict(
	{
		# Translators: A choice that disables Kimi model reasoning.
		"none": _("Disabled"),
		# Translators: A low Kimi model reasoning effort.
		"low": _("Low"),
		# Translators: A high Kimi model reasoning effort.
		"high": _("High"),
		# Translators: The maximum Kimi model reasoning effort.
		"max": _("Maximum"),
	},
)
KIMI_K3_REASONING_EFFORTS = OrderedDict(
	{
		# Translators: A low Kimi K3 reasoning effort.
		"low": _("Low"),
		# Translators: A high Kimi K3 reasoning effort.
		"high": _("High"),
		# Translators: The maximum Kimi K3 reasoning effort.
		"max": _("Maximum"),
	},
)
KIMI_K26_REASONING_EFFORTS = OrderedDict(
	{
		# Translators: A choice that disables reasoning for a Kimi K2.x model.
		"none": _("Disabled"),
		# Translators: A choice that enables reasoning for a Kimi K2.x model.
		"high": _("Enabled"),
	},
)

_K3_MODELS = {"k3", "k3-256k", "kimi-k3"}
_MILLION_TOKEN_CONTEXT_MODELS = {"k3", "kimi-k3"}
_K27_MODELS = {
	"kimi-for-coding",
	"kimi-for-coding-highspeed",
	"kimi-k2.7-code",
	"kimi-k2.7-code-highspeed",
}
_K26_MODELS = {"kimi-k2.6"}


def _normalizedModel(model: str | None) -> str:
	return (model or "").strip().lower()


def isKimiK3Model(model: str | None) -> bool:
	"""Returns whether a model uses Kimi K3's reasoning-effort API."""
	return _normalizedModel(model) in _K3_MODELS


def isKimiK27Model(model: str | None) -> bool:
	"""Returns whether a model is Kimi K2.7 Code."""
	return _normalizedModel(model) in _K27_MODELS


def isKimiK26Model(model: str | None) -> bool:
	"""Returns whether a model uses Kimi K2.6-style thinking controls."""
	return _normalizedModel(model) in _K26_MODELS


def getKimiContextWindow(model: str | None) -> int:
	"""Returns the model context window, defaulting custom models conservatively to 256K."""
	return 1_048_576 if _normalizedModel(model) in _MILLION_TOKEN_CONTEXT_MODELS else 262_144


def requiresPreservedThinking(model: str | None, reasoningEffort: str | None) -> bool:
	"""Returns whether assistant reasoning must be retained for later turns."""
	if isKimiK3Model(model) or isKimiK27Model(model):
		return True
	return isKimiK26Model(model) and normalizeKimiReasoningEffort(reasoningEffort) != "none"


def requiresKimiReasoningContent(model: str | None, reasoningEffort: str | None) -> bool:
	"""Returns whether a response must contain reasoning content for continuation."""
	return isKimiK27Model(model) or (
		isKimiK26Model(model) and normalizeKimiReasoningEffort(reasoningEffort) != "none"
	)


def normalizeKimiReasoningEffort(value: str | None, default: str = "high") -> str:
	"""Normalizes a configured effort value without guessing unknown values."""
	value = (value or "").strip().lower()
	return value if value in KIMI_REASONING_EFFORTS else default


def normalizeKimiReasoningEffortForModel(model: str | None, value: str | None) -> str:
	"""Normalizes reasoning effort to a value supported by the selected model."""
	effort = normalizeKimiReasoningEffort(value)
	if isKimiK3Model(model):
		return effort if effort in {"low", "high", "max"} else "high"
	if isKimiK27Model(model):
		return "high"
	if isKimiK26Model(model):
		return effort if effort in {"none", "high"} else "high"
	return effort


def getKimiReasoningEffortChoices(model: str | None) -> OrderedDict[str, str]:
	"""Returns the controls valid for the selected model family."""
	if isKimiK3Model(model):
		return KIMI_K3_REASONING_EFFORTS
	if isKimiK26Model(model):
		return KIMI_K26_REASONING_EFFORTS
	return OrderedDict()


def applyKimiThinking(payload: dict[str, Any], model: str, reasoningEffort: str | None) -> None:
	"""Adds only the thinking fields supported by the selected Kimi model.

	K3 uses top-level ``reasoning_effort``. K2.7 always thinks and therefore
	omits both controls. K2.6 uses ``thinking`` and can preserve reasoning for
	multi-turn calls.
	"""
	effort = normalizeKimiReasoningEffortForModel(model, reasoningEffort)
	payload.pop("reasoning_effort", None)
	payload.pop("thinking", None)
	if isKimiK3Model(model):
		payload["reasoning_effort"] = effort
	elif isKimiK26Model(model):
		payload["thinking"] = (
			{"type": "disabled"}
			if effort == "none"
			else {
				"type": "enabled",
				"keep": "all",
			}
		)


def buildKimiChatCompletionsUrl(baseUrl: str) -> str:
	"""Builds the OpenAI-compatible Chat Completions endpoint URL."""
	baseUrl = baseUrl.rstrip("/")
	if baseUrl.endswith("/chat/completions"):
		return baseUrl
	if baseUrl.endswith("/v1"):
		return f"{baseUrl}/chat/completions"
	return f"{baseUrl}/v1/chat/completions"


def buildKimiModelsUrl(baseUrl: str) -> str:
	"""Builds the model-list endpoint URL."""
	baseUrl = baseUrl.rstrip("/")
	if baseUrl.endswith("/models"):
		return baseUrl
	if baseUrl.endswith("/chat/completions"):
		baseUrl = baseUrl[: -len("/chat/completions")]
	if baseUrl.endswith("/v1"):
		return f"{baseUrl}/models"
	return f"{baseUrl}/v1/models"


_KIMI_CODE_MODEL_CHOICES = OrderedDict(
	{
		# Translators: The display name for a Kimi model preset.
		"k3-256k": _("Kimi K3 (256K context)"),
		# Translators: The display name for a Kimi model preset.
		"k3": _("Kimi K3 (1M context)"),
		# Translators: The display name for a Kimi model preset.
		"kimi-for-coding": _("Kimi K2.7 Code"),
		# Translators: The display name for a Kimi model preset.
		"kimi-for-coding-highspeed": _("Kimi K2.7 Code (high speed)"),
	},
)
_KIMI_PUBLIC_MODEL_CHOICES = OrderedDict(
	{
		# Translators: The display name for a Kimi model preset.
		"kimi-k3": _("Kimi K3 (public API)"),
		# Translators: The display name for a Kimi model preset.
		"kimi-k2.7-code": _("Kimi K2.7 Code (public API)"),
		# Translators: The display name for a Kimi model preset.
		"kimi-k2.7-code-highspeed": _("Kimi K2.7 Code (public API high speed)"),
		# Translators: The display name for a Kimi model preset.
		"kimi-k2.6": _("Kimi K2.6 (public API)"),
	},
)


def _kimiApiHost(baseUrl: str) -> str:
	return (urlsplit(baseUrl).hostname or "").lower()


def isOfficialKimiBaseUrl(baseUrl: str) -> bool:
	"""Returns whether a URL targets an official Kimi API host."""
	return _kimiApiHost(baseUrl) in {"api.kimi.com", "api.moonshot.ai", "api.moonshot.cn"}


def getKimiModelChoices(baseUrl: str = DEFAULT_KIMI_BASE_URL) -> OrderedDict[str, str]:
	"""Returns model presets supported by the configured official endpoint."""
	host = _kimiApiHost(baseUrl)
	if host == "api.kimi.com":
		return _KIMI_CODE_MODEL_CHOICES.copy()
	if host in {"api.moonshot.ai", "api.moonshot.cn"}:
		return _KIMI_PUBLIC_MODEL_CHOICES.copy()
	return _KIMI_CODE_MODEL_CHOICES | _KIMI_PUBLIC_MODEL_CHOICES


def getDefaultKimiModel(baseUrl: str) -> str:
	"""Returns a valid default model for an official Kimi endpoint."""
	if _kimiApiHost(baseUrl) in {"api.moonshot.ai", "api.moonshot.cn"}:
		return DEFAULT_KIMI_PUBLIC_MODEL
	return DEFAULT_KIMI_MODEL
