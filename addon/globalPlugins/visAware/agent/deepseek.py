# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""DeepSeek-backed computer-use agent engine."""

from __future__ import annotations

from copy import deepcopy
import json
import time
from typing import Any

import addonHandler
import config
from logHandler import log

from ..deepseekModels import (
	DEFAULT_DEEPSEEK_BASE_URL,
	DEFAULT_DEEPSEEK_VISION_MODEL,
	buildDeepSeekResponsesUrl,
	getDeepSeekVisionModelChoices,
)
from ..engineGUIHelper import ChoiceEngineSetting, TextInputEngineSetting
from .actions import Screenshot, formatScreenshotPromptContext
from .decision import AGENT_ACTION_SCHEMA, AgentDecision, parseAgentDecision
from .settings import BaseAgentEngine
from .. import network
from ..exceptions import ApiError, AuthenticationError

addonHandler.initTranslation()

AGENT_DECISION_TOOL_NAME = "agent_decision"
MAX_HISTORY_ITEMS = 8
MAX_REQUEST_BYTES = 1_900_000
REQUEST_TIMEOUT = 60


class AgentEngine(BaseAgentEngine):
	"""DeepSeek-backed computer-use agent engine."""

	name = "deepseek"
	# Translators: The description of the DeepSeek Agent engine.
	description = _("DeepSeek")

	_apiKey: str = ""
	_baseUrl: str = DEFAULT_DEEPSEEK_BASE_URL
	_model: str = DEFAULT_DEEPSEEK_VISION_MODEL

	@property
	def supportedSettings(self) -> list[Any]:
		return [
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the DeepSeek API key used by the Agent.
				displayNameWithAccelerator=_("API &key"),
			),
			TextInputEngineSetting(
				name="baseUrl",
				# Translators: The label for the DeepSeek base URL used by the Agent.
				displayNameWithAccelerator=_("Base &URL"),
				refreshSettingsOnChange=True,
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for the DeepSeek model used by the Agent.
				displayNameWithAccelerator=_("&Model"),
				optionsPropertyName="availableModels",
			),
			self._imageQualitySetting(),
		]

	@property
	def apiKey(self) -> str:
		return self._apiKey

	@apiKey.setter
	def apiKey(self, value: str) -> None:
		self._apiKey = value.strip()

	@property
	def baseUrl(self) -> str:
		return self._baseUrl

	@baseUrl.setter
	def baseUrl(self, value: str) -> None:
		self._baseUrl = value.strip().rstrip("/") or DEFAULT_DEEPSEEK_BASE_URL

	@property
	def model(self) -> str:
		return self._model

	@model.setter
	def model(self, value: str) -> None:
		value = value.strip()
		self._model = value if value in self.availableModels else DEFAULT_DEEPSEEK_VISION_MODEL

	@property
	def availableModels(self) -> dict:
		return self.generateStringSettings(getDeepSeekVisionModelChoices(self.baseUrl))

	@classmethod
	def check(cls) -> bool:
		return True

	def createClient(self) -> "DeepSeekAgentClient":
		return DeepSeekAgentClient(
			DeepSeekAgentSettings(
				apiKey=self.apiKey,
				baseUrl=self.baseUrl,
				model=self.model,
				imageQuality=self.imageQuality,
				source="Agent DeepSeek engine settings",
			),
		)


class DeepSeekAgentClient:
	"""DeepSeek-backed Responses agent client."""

	def __init__(self, settings: "DeepSeekAgentSettings") -> None:
		self.apiKey = settings.apiKey
		self.baseUrl = settings.baseUrl.rstrip("/") or DEFAULT_DEEPSEEK_BASE_URL
		self.model = settings.model or DEFAULT_DEEPSEEK_VISION_MODEL
		self.imageQuality = settings.imageQuality
		if not self.apiKey:
			raise AuthenticationError(
				_("API key is missing. Please configure it in the AI Agent settings."),
			)
		log.info(
			f"Vis Aware agent using DeepSeek settings from {settings.source or 'Agent settings'}, "
			f"model={self.model}, baseUrl={self.baseUrl!r}, imageQuality={self.imageQuality}",
		)
		self._inputItems: list[dict[str, Any]] = []
		self._pendingToolCallId: str | None = None

	def nextAction(self, goal: str, screenshot: Screenshot, history: list[str]) -> AgentDecision:
		self._buildInput(goal, screenshot, history)
		payload: dict[str, Any] = {
			"model": self.model,
			"instructions": _buildSystemInstruction(),
			"input": deepcopy(self._inputItems),
			"reasoning": {"effort": "none"},
			"tools": [_buildAgentDecisionTool()],
			"tool_choice": {"type": "function", "name": AGENT_DECISION_TOOL_NAME},
		}
		requestParams = {
			"method": "POST",
			"url": buildDeepSeekResponsesUrl(self.baseUrl),
			"headers": {
				"Content-Type": "application/json",
				"Authorization": f"Bearer {self.apiKey}",
			},
			"json": payload,
			"timeout": REQUEST_TIMEOUT,
		}
		if _verboseDebugLogging():
			log.debug(f"DeepSeek agent request params: {_redactForLog(requestParams)}")
		startTime = time.perf_counter()
		response = network.sendRequest(**requestParams)
		duration = time.perf_counter() - startTime
		apiResult = response.json()
		log.info(
			f"DeepSeek agent response completed: duration={duration:.2f}s, {_formatUsage(apiResult)}",
		)
		if _verboseDebugLogging():
			log.debug(f"DeepSeek agent response: {_redactForLog(apiResult)}")
		return self._parseDecision(apiResult)

	def _buildInput(self, goal: str, screenshot: Screenshot, history: list[str]) -> None:
		if self._pendingToolCallId:
			self._inputItems.append(
				{
					"type": "function_call_output",
					"call_id": self._pendingToolCallId,
					"output": _formatToolResult(history),
				},
			)
			self._inputItems.append(
				{
					"role": "user",
					"content": self._buildUserContent(goal, screenshot, history),
				},
			)
			self._pendingToolCallId = None
			self._trimInput()
			return
		self._inputItems.append(
			{
				"role": "user",
				"content": self._buildUserContent(goal, screenshot, history),
			},
		)
		self._trimInput()

	@staticmethod
	def _buildUserContent(goal: str, screenshot: Screenshot, history: list[str]) -> list[dict[str, Any]]:
		return [
			_buildImageContent(screenshot),
			{
				"type": "input_text",
				"text": (
					f"Task: {goal}.\n"
					f"Foreground App: {screenshot.window.appName}. "
					f"{formatScreenshotPromptContext(screenshot)}\n"
					f"Recent actions and observations:\n{_formatPromptHistory(history)}"
				),
			},
		]

	def _trimInput(self) -> None:
		if len(self._inputItems) <= 1:
			self._checkInputSize()
			return
		turns: list[list[dict[str, Any]]] = []
		for item in self._inputItems:
			if item.get("role") == "user" or not turns:
				turns.append([])
			turns[-1].append(item)
		turns = turns[-4:]
		self._inputItems = [item for turn in turns for item in turn]
		_dropOldScreenshots(self._inputItems)
		while len(turns) > 1 and _messageSize(self._inputItems) > MAX_REQUEST_BYTES:
			turns.pop(0)
			self._inputItems = [item for turn in turns for item in turn]
			_dropOldScreenshots(self._inputItems)
		self._checkInputSize()

	def _checkInputSize(self) -> None:
		if _messageSize(self._inputItems) > MAX_REQUEST_BYTES:
			raise ApiError(_("The current screenshot is too large for the DeepSeek request limit."))

	def _parseDecision(self, apiResult: dict[str, Any]) -> AgentDecision:
		_validateResponseStatus(apiResult)
		toolCalls = _extractAgentToolCalls(apiResult)
		if len(toolCalls) != 1:
			raise ApiError(_("DeepSeek did not return exactly one agent tool call."))
		toolCall = toolCalls[0]
		callId = toolCall.get("call_id")
		if not isinstance(callId, str) or not callId:
			raise ApiError(_("DeepSeek returned malformed agent action data."))
		if any(
			existing.get("type") == "function_call" and existing.get("call_id") == callId
			for existing in self._inputItems
		):
			raise ApiError(_("DeepSeek returned a duplicate agent tool call ID."))
		arguments = toolCall.get("arguments")
		if isinstance(arguments, str):
			try:
				arguments = json.loads(arguments)
			except json.JSONDecodeError as e:
				raise ApiError(_("DeepSeek returned invalid agent action JSON.")) from e
		if not isinstance(arguments, dict):
			raise ApiError(_("DeepSeek returned malformed agent action data."))
		decision = parseAgentDecision(arguments, "DeepSeek")
		self._inputItems.append(
			{
				"type": "function_call",
				"call_id": callId,
				"name": AGENT_DECISION_TOOL_NAME,
				"arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
			},
		)
		self._pendingToolCallId = callId
		return decision


def _validateResponseStatus(apiResult: dict[str, Any]) -> None:
	error = apiResult.get("error")
	if isinstance(error, dict) and error.get("message"):
		raise ApiError(_("DeepSeek API error: {}").format(error["message"]))
	status = apiResult.get("status")
	if status == "failed":
		raise ApiError(_("DeepSeek agent response failed."))
	if status == "incomplete":
		details = apiResult.get("incomplete_details")
		reason = details.get("reason") if isinstance(details, dict) else ""
		if reason == "max_output_tokens":
			raise ApiError(_("DeepSeek agent response was truncated at the token limit."))
		if reason == "content_filter":
			raise ApiError(_("DeepSeek agent response was blocked by content filtering."))
		raise ApiError(_("DeepSeek agent response was incomplete."))
	if status not in {None, "completed"}:
		raise ApiError(_("DeepSeek returned an invalid agent response."))


def _extractAgentToolCalls(apiResult: dict[str, Any]) -> list[dict[str, Any]]:
	output = apiResult.get("output")
	if not isinstance(output, list):
		raise ApiError(_("DeepSeek returned an invalid agent response."))
	return [
		item
		for item in output
		if isinstance(item, dict)
		and item.get("type") == "function_call"
		and item.get("name") == AGENT_DECISION_TOOL_NAME
	]


def _buildAgentDecisionTool() -> dict[str, Any]:
	schema = deepcopy(AGENT_ACTION_SCHEMA)
	schema.pop("propertyOrdering", None)
	return {
		"type": "function",
		"name": AGENT_DECISION_TOOL_NAME,
		"description": "Return the next Windows desktop action for the local agent host.",
		"parameters": schema,
	}


def _buildImageContent(screenshot: Screenshot) -> dict[str, Any]:
	return {
		"type": "input_image",
		"image_url": f"data:{screenshot.mimeType};base64,{screenshot.imageBase64}",
		"detail": "high",
	}


def _buildSystemInstruction() -> str:
	return (
		"You are a Windows operator. STRICT RULES:\n"
		"1. RESPONSE LANGUAGE: Everything MUST be in the user's language.\n"
		"2. OUTPUT: Always call the agent_decision tool exactly once. Do not answer in text.\n"
		'3. STATUS: Use "action" to operate, "finish" only after the visible state confirms completion, '
		'and "ask_user" only when user input or confirmation is required; '
		"the host will pause, ask the user, then continue with the answer.\n"
		'4. FINISHED: For an action that is probably final, set "finished": true; '
		"the host will still verify on the next screenshot before ending.\n"
		"5. ONE ACTION: Return exactly one real UI action.\n"
		"- Use double_click only when the visible target normally requires double activation; "
		"use click for ordinary buttons, menu items, tabs, and links.\n"
		"- Coordinates are normalized from 0 to 1000 relative to the screenshot, not pixels.\n"
		"- For drags and sliders, use drag_by when you know the offset from the handle or object.\n"
		"- For scroll_at, always include x and y inside the visible scrollable list or panel.\n"
		"- For destructive or security-sensitive tasks, do not infer success from completed actions.\n"
		"Ignore 'Vis Aware Agent' or 'NVDA' windows."
	)


def _formatPromptHistory(history: list[str]) -> str:
	if not history:
		return "None."
	return "\n".join(" ".join(str(item).split())[:1000] for item in history[-MAX_HISTORY_ITEMS:])


def _formatToolResult(history: list[str]) -> str:
	result = "The local host handled the previous agent_decision tool call."
	if not history:
		return f"{result} Inspect the current screenshot for the latest state."
	return f"{result}\n" + _formatPromptHistory(history[-2:])


def _messageSize(messages: list[dict[str, Any]]) -> int:
	return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _dropOldScreenshots(inputItems: list[dict[str, Any]]) -> None:
	imageItemIndexes = [
		index
		for index, item in enumerate(inputItems)
		if isinstance(item.get("content"), list)
		and any(isinstance(part, dict) and part.get("type") == "input_image" for part in item["content"])
	]
	for index in imageItemIndexes[:-1]:
		inputItems[index]["content"] = [
			part
			for part in inputItems[index]["content"]
			if not isinstance(part, dict) or part.get("type") != "input_image"
		]


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
	value = _redactDeepSeekImageUrls(value)
	from ..recogHandler import _redactRequestParamsForLog

	return _redactRequestParamsForLog(value)


def _redactDeepSeekImageUrls(value: Any, keyName: str = "") -> Any:
	if isinstance(value, dict):
		return {key: _redactDeepSeekImageUrls(childValue, str(key)) for key, childValue in value.items()}
	if isinstance(value, list):
		return [_redactDeepSeekImageUrls(item, keyName) for item in value]
	if isinstance(value, tuple):
		return tuple(_redactDeepSeekImageUrls(item, keyName) for item in value)
	if keyName == "image_url" and isinstance(value, str):
		return f"<redacted data URL: {len(value)} chars>"
	return value


class DeepSeekAgentSettings:
	"""Settings used to create a DeepSeek agent client."""

	def __init__(self, apiKey: str, baseUrl: str, model: str, imageQuality: int, source: str) -> None:
		self.apiKey = apiKey
		self.baseUrl = baseUrl
		self.model = model
		self.imageQuality = imageQuality
		self.source = source
