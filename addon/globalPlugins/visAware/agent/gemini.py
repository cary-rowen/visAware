# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Gemini client for the Vis Aware computer-use agent."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import time
from typing import Any

import addonHandler
import config
from logHandler import log

from .. import network
from ..exceptions import ApiError, AuthenticationError
from ..geminiModels import getGeminiLowLatencyThinkingConfig
from .actions import JPEG_QUALITY, Screenshot, formatScreenshotPromptContext
from .decision import AGENT_ACTION_SCHEMA, AgentDecision, parseAgentDecision

addonHandler.initTranslation()

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1/interactions"
INTERACTIONS_API_REVISION = "2026-05-20"
AGENT_DECISION_TOOL_NAME = "agent_decision"
MAX_HISTORY_ITEMS = 8
FUNCTION_RESULT_HISTORY_ITEMS = 2


@dataclass(frozen=True)
class GeminiAgentSettings:
	apiKey: str
	model: str
	imageQuality: int = JPEG_QUALITY
	mediaResolution: str = ""
	source: str = ""


class GeminiAgentClient:
	def __init__(self, settings: GeminiAgentSettings) -> None:
		self.apiKey = settings.apiKey
		self.model = settings.model
		self.imageQuality = settings.imageQuality
		self.mediaResolution = settings.mediaResolution
		if not self.apiKey:
			raise AuthenticationError(
				_("API Key is missing. Please configure it in the Agent settings."),
			)
		log.info(
			f"Vis Aware agent using Gemini settings from {settings.source or 'Agent settings'}, "
			f"model={self.model}, imageQuality={self.imageQuality}, mediaResolution={self.mediaResolution!r}",
		)
		self._previousInteractionId: str | None = None
		self._pendingFunctionCallId: str | None = None

	def nextAction(self, goal: str, screenshot: Screenshot, history: list[str]) -> AgentDecision:
		payload = {
			"model": self.model,
			"system_instruction": _buildSystemInstruction(),
			"tools": [_buildAgentDecisionTool()],
			"generation_config": _buildGenerationConfig(self.model),
			"input": self._buildInputSteps(goal, screenshot, history),
			"store": True,
		}
		if self._previousInteractionId:
			payload["previous_interaction_id"] = self._previousInteractionId
		requestParams = {
			"method": "POST",
			"url": INTERACTIONS_URL,
			"headers": {
				"Content-Type": "application/json",
				"Api-Revision": INTERACTIONS_API_REVISION,
				"x-goog-api-key": self.apiKey,
			},
			"json": payload,
			"timeout": 60,
		}
		if _verboseDebugLogging():
			log.debug(f"Gemini agent request params: {_redactForLog(requestParams)}")
		startTime = time.perf_counter()
		response = network.sendRequest(**requestParams)
		apiResult = response.json()
		duration = time.perf_counter() - startTime
		log.info(
			"Gemini agent interaction completed: "
			f"duration={duration:.2f}s, "
			f"previous={_shortId(payload.get('previous_interaction_id')) or 'none'}, "
			f"functionResult={bool(self._pendingFunctionCallId)}, "
			f"response={_shortId(apiResult.get('id')) or 'none'}, "
			f"status={apiResult.get('status')!r}, "
			f"steps={_formatStepTypes(apiResult)}, "
			f"{_formatUsage(apiResult)}",
		)
		if _verboseDebugLogging():
			log.debug(f"Gemini agent response: {_redactForLog(apiResult)}")
		return self._parseDecision(apiResult)

	def _buildInputSteps(
		self,
		goal: str,
		screenshot: Screenshot,
		history: list[str],
	) -> list[dict[str, Any]]:
		steps = []
		functionResult = self._buildFunctionResultStep(history)
		if functionResult:
			steps.append(functionResult)
		steps.append(
			{
				"type": "user_input",
				"content": [
					{"type": "text", "text": self._buildInputText(goal, screenshot, history)},
					self._buildImageContent(screenshot),
				],
			},
		)
		return steps

	def _buildFunctionResultStep(self, history: list[str]) -> dict[str, Any] | None:
		if not self._pendingFunctionCallId:
			return None
		return {
			"type": "function_result",
			"call_id": self._pendingFunctionCallId,
			"name": AGENT_DECISION_TOOL_NAME,
			"result": [
				{
					"type": "text",
					"text": _formatFunctionResult(history),
				},
			],
		}

	def _buildInputText(self, goal: str, screenshot: Screenshot, history: list[str]) -> str:
		historyText = "\n".join(history[-MAX_HISTORY_ITEMS:]) if history else "None."
		return (
			f"Foreground App: {screenshot.window.appName}. "
			f"{formatScreenshotPromptContext(screenshot)} "
			f"Task: {goal}.\n"
			f"Recent actions and observations:\n{historyText}"
		)

	def _buildImageContent(self, screenshot: Screenshot) -> dict[str, Any]:
		imageContent: dict[str, Any] = {
			"type": "image",
			"data": screenshot.imageBase64,
			"mime_type": screenshot.mimeType,
		}
		resolution = _normalizeMediaResolution(self.mediaResolution)
		if resolution:
			imageContent["resolution"] = resolution
		return imageContent

	def _parseDecision(self, apiResult: dict[str, Any]) -> AgentDecision:
		self._setPreviousInteractionId(apiResult)
		callId, arguments = _extractToolCall(apiResult)
		self._pendingFunctionCallId = callId
		return parseAgentDecision(arguments, "Gemini")

	def _setPreviousInteractionId(self, apiResult: dict[str, Any]) -> None:
		interactionId = apiResult.get("id")
		if isinstance(interactionId, str) and interactionId:
			self._previousInteractionId = interactionId


def _buildSystemInstruction() -> str:
	return (
		"You are a Windows operator. STRICT RULES:\n"
		"1. RESPONSE LANGUAGE: Everything MUST be in the user's language.\n"
		"2. OUTPUT: Always call the agent_decision tool exactly once. Do not answer in text.\n"
		'3. STATUS: Use "action" to operate, "finish" only after the visible state confirms completion, '
		'and "ask_user" only when user input or confirmation is required; '
		"the host will pause, ask the user, then continue with the answer. "
		"Do not ask the user to inspect visual details such as drag position, target location, "
		"or whether the visual state changed; decide those from screenshots.\n"
		'4. FINISHED: For an action that is probably final, set "finished": true; '
		"the host will still verify on the next screenshot before ending.\n"
		"5. ONE ACTION: Return exactly one real UI action. The message must describe only that action, "
		"not a multi-step plan.\n"
		"- To press Enter after typing, set \"press_enter\": true or append '\\n' to the end of "
		'the "text" parameter.\n'
		"- Use double_click only when the visible target normally requires double activation; "
		"use click for ordinary buttons, menu items, tabs, and links.\n"
		"- Coordinates are normalized from 0 to 1000 relative to the screenshot, not pixels. "
		"When operating inside the foreground app, keep x/y inside the foreground window bounds; "
		"click taskbar, desktop, or another window only when the task requires it. "
		"Target the center of the actual control, not a generic screen point. "
		"For menus, lists, sidebars, tabs, buttons, and dropdowns, click the center of the "
		"visible clickable row or button, not the text baseline, border, separator, or the "
		"space between two rows. "
		"If a click, mouse_down, or drag does not visibly affect the intended control, "
		"assume the coordinate missed the control and retarget the visible center, especially "
		"the vertical center of small handles or buttons. "
		"Each coordinate field must be one integer, not an array. "
		"For drag_by only, delta_x and delta_y are screen pixels, not normalized values. "
		"Use JSON null for empty optional fields, never None. "
		"- For drags and sliders, use drag_by when you know the offset from the handle or object, "
		"or drag_and_drop/drag_to when you know the release point. Use the visible draggable center "
		"as x/y. Estimate the target position and relative offset from the current screenshot instead "
		"of blind trial-and-error. "
		"After releasing the slider, use wait_for_change or inspect the next screenshot before "
		"dragging again. "
		"Never finish only because the mouse was released.\n"
		"- If a previous drag changed the screen but did not finish the task, re-estimate from the "
		"current screenshot instead of repeating the same start point and distance.\n"
		"- For difficult drags, you may use mouse_down, hover_at/move, wait, and mouse_up as separate "
		"actions to adjust the pointer step by step.\n"
		"- For scroll_at, always include x and y inside the visible scrollable list or panel. "
		"If repeated scrolling does not reveal new content, switch strategy: search/filter field, "
		"PageDown, first-letter navigation, or ask_user.\n"
		"- For long lists, prefer a visible search/filter field before repeated wheel scrolling.\n"
		"- For ordered visual targets, click only the next visible target in the sequence and do not "
		"claim later targets were clicked.\n"
		"- If history says the previous action caused no visible screen change, do not repeat the same "
		"coordinates and distance. Change the target, drag distance, or strategy.\n"
		"- For destructive or security-sensitive tasks such as uninstall, delete, login, payment, "
		"or verification, do not infer success from completed actions. Finish only when a "
		"visible success state, page transition, vanished challenge, or explicit confirmation is shown. "
		"Ask the user before irreversible confirmation if unsure, but not for visual challenge judgment.\n"
		"Ignore 'Vis Aware Agent' or 'NVDA' windows."
	)


def _buildGenerationConfig(model: str) -> dict[str, Any]:
	generationConfig: dict[str, Any] = {
		"thinking_summaries": "none",
		"tool_choice": {
			"allowed_tools": {
				"mode": "any",
				"tools": [AGENT_DECISION_TOOL_NAME],
			},
		},
	}
	thinkingLevel = _getInteractionsThinkingLevel(model)
	if thinkingLevel:
		generationConfig["thinking_level"] = thinkingLevel
	return generationConfig


def _getInteractionsThinkingLevel(model: str) -> str | None:
	thinkingConfig = getGeminiLowLatencyThinkingConfig(model)
	if not thinkingConfig:
		return None
	thinkingLevel = thinkingConfig.get("thinkingLevel")
	if isinstance(thinkingLevel, str) and thinkingLevel:
		return thinkingLevel.lower()
	if thinkingConfig.get("thinkingBudget") == 0:
		return "minimal"
	return None


def _buildAgentDecisionTool() -> dict[str, Any]:
	schema = deepcopy(AGENT_ACTION_SCHEMA)
	schema.pop("propertyOrdering", None)
	return {
		"type": "function",
		"name": AGENT_DECISION_TOOL_NAME,
		"description": "Return the next Windows desktop action for the local agent host.",
		"parameters": schema,
	}


def _normalizeMediaResolution(value: str) -> str:
	normalized = (value or "").strip().lower()
	if normalized.startswith("media_resolution_"):
		normalized = normalized.removeprefix("media_resolution_")
	if normalized == "unspecified":
		return ""
	if normalized in {"low", "medium", "high", "ultra_high"}:
		return normalized
	return ""


def _formatFunctionResult(history: list[str]) -> str:
	result = "The local host handled the previous agent_decision tool call."
	if not history:
		return f"{result} Inspect the current screenshot for the latest state."
	return f"{result}\n" + "\n".join(history[-FUNCTION_RESULT_HISTORY_ITEMS:])


def _extractToolCall(apiResult: dict[str, Any]) -> tuple[str, dict[str, Any]]:
	apiError = _getApiErrorMessage(apiResult)
	if apiError:
		# Translators: An error message returned from the Gemini API.
		raise ApiError(_("Gemini API Error: {}").format(apiError))
	steps = apiResult.get("steps")
	if not isinstance(steps, list):
		raise ApiError(_("Gemini returned malformed agent action data."))
	for step in reversed(steps):
		if not isinstance(step, dict) or step.get("type") != "function_call":
			continue
		if str(step.get("name") or "") != AGENT_DECISION_TOOL_NAME:
			raise ApiError(_("Gemini returned an unsupported agent tool call."))
		callId = step.get("id")
		if not isinstance(callId, str) or not callId:
			raise ApiError(_("Gemini returned malformed agent action data."))
		return callId, _parseToolArguments(step.get("arguments"))
	raise ApiError(_("Gemini did not return an agent tool call."))


def _parseToolArguments(arguments: Any) -> dict[str, Any]:
	if isinstance(arguments, dict):
		return arguments
	if not isinstance(arguments, str) or not arguments.strip():
		raise ApiError(_("Gemini returned malformed agent action data."))
	try:
		parsed = json.loads(arguments)
	except json.JSONDecodeError as e:
		log.debugWarning(f"Invalid Gemini agent tool arguments: {arguments!r}")
		raise ApiError(_("Gemini returned invalid agent action JSON.")) from e
	if not isinstance(parsed, dict):
		raise ApiError(_("Gemini returned malformed agent action data."))
	return parsed


def _getApiErrorMessage(apiResult: dict[str, Any]) -> str | None:
	error = apiResult.get("error")
	if not error:
		return None
	if isinstance(error, dict):
		return str(error.get("message") or error)
	return str(error)


def _shortId(value: Any) -> str:
	if isinstance(value, str) and value:
		return value[:12]
	return ""


def _formatStepTypes(apiResult: dict[str, Any]) -> str:
	steps = apiResult.get("steps")
	if not isinstance(steps, list):
		return "unknown"
	return ",".join(
		str(step.get("type") if isinstance(step, dict) else type(step).__name__) for step in steps
	)


def _formatUsage(apiResult: dict[str, Any]) -> str:
	usage = apiResult.get("usage")
	if not isinstance(usage, dict):
		return "usage=unknown"
	return (
		f"tokens=input:{usage.get('total_input_tokens')}, "
		f"cached:{usage.get('total_cached_tokens')}, "
		f"output:{usage.get('total_output_tokens')}, "
		f"thought:{usage.get('total_thought_tokens')}, "
		f"total:{usage.get('total_tokens')}"
	)


def _verboseDebugLogging() -> bool:
	try:
		return bool(config.conf["visAwareGeneral"]["verboseDebugLogging"])
	except Exception:
		return False


def _redactForLog(value: Any) -> Any:
	from ..recogHandler import _redactRequestParamsForLog

	return _redactRequestParamsForLog(value)
