# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Kimi (Moonshot AI) client for the Vis Aware computer-use agent."""

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
from ..kimiAPI import KimiStreamState, getKimiApiErrorMessage, parseKimiResponse, validateKimiCompletion
from ..kimiModels import (
	DEFAULT_KIMI_AGENT_MODEL,
	DEFAULT_KIMI_BASE_URL,
	applyKimiThinking,
	buildKimiChatCompletionsUrl,
	buildKimiModelsUrl,
	getKimiModelChoices,
	isKimiK26Model,
	isKimiK3Model,
	isKimiK27Model,
	isOfficialKimiBaseUrl,
	normalizeKimiReasoningEffortForModel,
	requiresKimiReasoningContent,
	requiresPreservedThinking,
)
from .actions import JPEG_QUALITY, Screenshot, formatScreenshotPromptContext
from .decision import AGENT_ACTION_SCHEMA, AgentDecision, parseAgentDecision

addonHandler.initTranslation()

AGENT_DECISION_TOOL_NAME = "agent_decision"
MAX_HISTORY_ITEMS = 8
MAX_CONVERSATION_TURNS = 4
MAX_REQUEST_BYTES = 1_900_000
KIMI_REQUEST_TIMEOUT = 180
MAX_COMPLETION_TOKENS = 16_384

# Translators: An error message for malformed Kimi Agent tool-call data.
_MALFORMED_AGENT_ACTION_MESSAGE = _("Kimi returned malformed agent action data.")


@dataclass(frozen=True)
class KimiAgentSettings:
	apiKey: str = ""
	baseUrl: str = ""
	model: str = ""
	imageQuality: int = JPEG_QUALITY
	source: str = ""
	reasoningEffort: str = "high"


class KimiAgentClient:
	def __init__(self, settings: KimiAgentSettings) -> None:
		self.apiKey = settings.apiKey
		self.baseUrl = (settings.baseUrl or DEFAULT_KIMI_BASE_URL).rstrip("/")
		self.model = settings.model or DEFAULT_KIMI_AGENT_MODEL
		self.imageQuality = settings.imageQuality
		self.reasoningEffort = normalizeKimiReasoningEffortForModel(self.model, settings.reasoningEffort)
		if not self.apiKey:
			# Translators: Reported when no API key is configured for the selected AI Agent.
			raise AuthenticationError(
				_("API key is missing. Please configure it in the AI Agent settings."),
			)
		log.info(
			f"Vis Aware agent using Kimi settings from {settings.source or 'Agent settings'}, "
			f"model={self.model}, baseUrl={self.baseUrl!r}, imageQuality={self.imageQuality}",
		)
		self._messages: list[dict[str, Any]] = []
		self._pendingToolUseId: str | None = None

	def nextAction(self, goal: str, screenshot: Screenshot, history: list[str]) -> AgentDecision:
		self._buildMessages(goal, screenshot, history)
		payload: dict[str, Any] = {
			"model": self.model,
			"max_completion_tokens": MAX_COMPLETION_TOKENS,
			"messages": [
				{"role": "system", "content": _buildSystemInstruction()},
				*deepcopy(self._messages),
			],
			"tools": [_buildAgentDecisionTool()],
		}
		applyKimiThinking(payload, self.model, self.reasoningEffort)
		if self._shouldStream():
			payload["stream"] = True
			payload["stream_options"] = {"include_usage": True}
		if isKimiK3Model(self.model):
			payload["tool_choice"] = "required"
		requestParams = {
			"method": "POST",
			"url": buildKimiChatCompletionsUrl(self.baseUrl),
			"headers": {
				"Content-Type": "application/json",
				"Authorization": f"Bearer {self.apiKey}",
			},
			"json": payload,
			"timeout": KIMI_REQUEST_TIMEOUT,
		}
		if _verboseDebugLogging():
			log.debug(f"Kimi agent request params: {_redactForLog(requestParams)}")
		startTime = time.perf_counter()
		apiResult = self._sendCompletion(requestParams)
		duration = time.perf_counter() - startTime
		finishReason = _getFinishReason(apiResult)
		log.info(
			"Kimi agent response completed: "
			f"duration={duration:.2f}s, "
			f"finishReason={finishReason or 'unknown'}, "
			f"{_formatUsage(apiResult)}",
		)
		if _verboseDebugLogging():
			log.debug(f"Kimi agent response: {_redactForLog(apiResult)}")
		return self._parseDecision(apiResult)

	def _shouldStream(self) -> bool:
		"""Uses Kimi's recommended streaming path for thinking tool loops."""
		return isKimiK27Model(self.model) or (isKimiK26Model(self.model) and self.reasoningEffort != "none")

	@staticmethod
	def _sendCompletion(requestParams: dict[str, Any]) -> dict[str, Any]:
		if requestParams["json"].get("stream"):
			streamState = KimiStreamState()
			for chunk in network.sendStreamingRequest(**requestParams):
				streamState.consume(chunk)
			return streamState.buildResponse()
		response = network.sendRequest(**requestParams)
		return parseKimiResponse(response.content)

	def _buildMessages(self, goal: str, screenshot: Screenshot, history: list[str]) -> None:
		"""Builds the OpenAI-compatible message list for one Agent step."""
		if self._pendingToolUseId:
			self._messages.append(
				{
					"role": "tool",
					"tool_call_id": self._pendingToolUseId,
					"content": _formatToolResult(history),
				},
			)
			self._messages.append(
				{
					"role": "user",
					"content": self._buildUserContent(goal, screenshot, history),
				},
			)
			self._pendingToolUseId = None
			self._trimMessages()
			return

		self._messages.append(
			{
				"role": "user",
				"content": self._buildUserContent(goal, screenshot, history),
			},
		)
		self._trimMessages()

	@staticmethod
	def _buildUserContent(goal: str, screenshot: Screenshot, history: list[str]) -> list[dict[str, Any]]:
		return [
			_buildImageContent(screenshot),
			{
				"type": "text",
				"text": (
					f"Task: {goal}.\n"
					f"Foreground App: {screenshot.window.appName}. "
					f"{formatScreenshotPromptContext(screenshot)}\n"
					f"Recent actions and observations:\n{_formatPromptHistory(history)}"
				),
			},
		]

	def _trimMessages(self) -> None:
		"""Keeps complete recent tool turns within Kimi Code's request-size limit."""
		if len(self._messages) <= 1:
			self._checkMessageSize()
			return
		turns: list[list[dict[str, Any]]] = []
		for message in self._messages:
			if message.get("role") == "user" or not turns:
				turns.append([])
			turns[-1].append(message)
		turns = turns[-MAX_CONVERSATION_TURNS:]
		self._messages = [message for turn in turns for message in turn]
		_dropOldScreenshots(self._messages)
		while len(turns) > 1 and _messageSize(self._messages) > MAX_REQUEST_BYTES:
			turns.pop(0)
			self._messages = [message for turn in turns for message in turn]
			_dropOldScreenshots(self._messages)
		self._checkMessageSize()

	def _checkMessageSize(self) -> None:
		if _messageSize(self._messages) > MAX_REQUEST_BYTES:
			# Translators: Reported when a screenshot makes the Kimi Agent request too large.
			raise ApiError(
				_("The current screenshot is too large for the Kimi request limit."),
			)

	def _parseDecision(self, apiResult: dict[str, Any]) -> AgentDecision:
		"""Parses one OpenAI-compatible tool call into an AgentDecision."""
		apiError = getKimiApiErrorMessage(apiResult)
		if apiError:
			# Translators: An error message returned from the Kimi API.
			raise ApiError(_("Kimi API error: {}").format(apiError))

		choices = apiResult.get("choices")
		if isinstance(choices, list) and choices and isinstance(choices[0], dict):
			if choices[0].get("finish_reason") == "length":
				# Translators: Reported when Kimi cannot finish an Agent action within the token limit.
				raise ApiError(_("Kimi agent response was truncated at the token limit."))
		try:
			_choice, message = validateKimiCompletion(apiResult, {"tool_calls"})
		except ApiError as e:
			raise ApiError(_MALFORMED_AGENT_ACTION_MESSAGE) from e
		toolCalls = message.get("tool_calls")
		if not isinstance(toolCalls, list) or len(toolCalls) != 1 or not isinstance(toolCalls[0], dict):
			# Translators: Reported when Kimi returns zero or multiple actions for one Agent step.
			raise ApiError(_("Kimi did not return exactly one agent tool call."))
		toolCall = toolCalls[0]
		if toolCall.get("type") != "function":
			raise ApiError(_MALFORMED_AGENT_ACTION_MESSAGE)
		function = toolCall.get("function")
		if not isinstance(function, dict):
			raise ApiError(_MALFORMED_AGENT_ACTION_MESSAGE)
		if function.get("name") != AGENT_DECISION_TOOL_NAME:
			# Translators: Reported when Kimi calls a tool unsupported by the desktop Agent.
			raise ApiError(_("Kimi returned an unsupported agent tool call."))
		toolUseId = toolCall.get("id")
		if not isinstance(toolUseId, str) or not toolUseId:
			raise ApiError(_MALFORMED_AGENT_ACTION_MESSAGE)
		if any(
			isinstance(existing.get("tool_calls"), list)
			and any(call.get("id") == toolUseId for call in existing["tool_calls"] if isinstance(call, dict))
			for existing in self._messages
		):
			# Translators: Reported when Kimi reuses an Agent tool-call identifier.
			raise ApiError(_("Kimi returned a duplicate agent tool call ID."))
		arguments = function.get("arguments")
		if isinstance(arguments, str):
			try:
				arguments = json.loads(arguments)
			except json.JSONDecodeError as e:
				# Translators: Reported when Kimi returns invalid JSON for an Agent action.
				raise ApiError(_("Kimi returned invalid agent action JSON.")) from e
		if not isinstance(arguments, dict):
			raise ApiError(_MALFORMED_AGENT_ACTION_MESSAGE)
		if requiresKimiReasoningContent(self.model, self.reasoningEffort) and not isinstance(
			message.get("reasoning_content"),
			str,
		):
			# Translators: Reported when Kimi omits reasoning required for the next Agent step.
			raise ApiError(_("Kimi returned incomplete preserved thinking data."))
		decision = parseAgentDecision(arguments, "Kimi")

		assistantMessage = deepcopy(message)
		assistantMessage["role"] = "assistant"
		if not requiresPreservedThinking(self.model, self.reasoningEffort):
			assistantMessage.pop("reasoning_content", None)
		self._messages.append(assistantMessage)
		self._pendingToolUseId = toolUseId
		return decision

	def listModels(self) -> list[str]:
		"""Fetches available model names from the Kimi API."""
		response = network.sendRequest(
			"GET",
			buildKimiModelsUrl(self.baseUrl),
			headers={"Authorization": f"Bearer {self.apiKey}"},
			timeout=30,
		)
		try:
			apiResult = response.json()
		except ValueError:
			return []
		if not isinstance(apiResult, dict):
			return []
		supportedModels = getKimiModelChoices(self.baseUrl) if isOfficialKimiBaseUrl(self.baseUrl) else None
		modelIds = set()
		models = apiResult.get("data", [])
		if not isinstance(models, list):
			return []
		for model in models:
			if isinstance(model, dict) and isinstance(model.get("id"), str):
				modelId = model["id"].strip()
				if modelId and (supportedModels is None or modelId in supportedModels):
					modelIds.add(modelId)
		return sorted(modelIds)


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


def _buildAgentDecisionTool() -> dict[str, Any]:
	schema = deepcopy(AGENT_ACTION_SCHEMA)
	schema.pop("propertyOrdering", None)
	return {
		"type": "function",
		"function": {
			"name": AGENT_DECISION_TOOL_NAME,
			"description": "Return the next Windows desktop action for the local agent host.",
			"parameters": schema,
		},
	}


def _buildImageContent(screenshot: Screenshot) -> dict[str, Any]:
	return {
		"type": "image_url",
		"image_url": {
			"url": f"data:{screenshot.mimeType};base64,{screenshot.imageBase64}",
		},
	}


def _formatPromptHistory(history: list[str]) -> str:
	if not history:
		return "None."
	return "\n".join(" ".join(str(item).split())[:1000] for item in history[-MAX_HISTORY_ITEMS:])


def _messageSize(messages: list[dict[str, Any]]) -> int:
	return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _dropOldScreenshots(messages: list[dict[str, Any]]) -> None:
	imageMessageIndexes = [
		index
		for index, message in enumerate(messages)
		if isinstance(message.get("content"), list)
		and any(isinstance(part, dict) and part.get("type") == "image_url" for part in message["content"])
	]
	for index in imageMessageIndexes[:-1]:
		messages[index]["content"] = [
			part
			for part in messages[index]["content"]
			if not isinstance(part, dict) or part.get("type") != "image_url"
		]


def _formatToolResult(history: list[str]) -> str:
	result = "The local host handled the previous agent_decision tool call."
	if not history:
		return f"{result} Inspect the current screenshot for the latest state."
	return f"{result}\n" + _formatPromptHistory(history[-2:])


def _getFinishReason(apiResult: dict[str, Any]) -> str:
	choices = apiResult.get("choices")
	if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
		return ""
	return str(choices[0].get("finish_reason") or "")


def _formatUsage(apiResult: dict[str, Any]) -> str:
	usage = apiResult.get("usage")
	if not isinstance(usage, dict):
		return "usage=unknown"
	return (
		f"tokens=input:{usage.get('prompt_tokens', usage.get('input_tokens'))}, "
		f"output:{usage.get('completion_tokens', usage.get('output_tokens'))}"
	)


def _verboseDebugLogging() -> bool:
	try:
		return bool(config.conf["visAwareGeneral"]["verboseDebugLogging"])
	except Exception:
		return False


def _redactForLog(value: Any) -> Any:
	from ..recogHandler import _redactRequestParamsForLog

	return _redactRequestParamsForLog(value)
