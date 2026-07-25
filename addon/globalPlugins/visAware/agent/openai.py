# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""OpenAI Responses API client for the Vis Aware computer-use agent."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import addonHandler
from logHandler import log

from .. import network
from ..exceptions import ApiError, AuthenticationError
from .actions import JPEG_QUALITY, NORMALIZED_SCALE, AgentAction, Screenshot, formatScreenshotPromptContext
from .decision import AgentDecision

addonHandler.initTranslation()

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
MAX_HISTORY_ITEMS = 8
COMPUTER_SCREENSHOT_DETAIL = "original"
MAX_INTERNAL_COMPUTER_TURNS = 3
ASK_USER_TOOL_NAME = "ask_user"


@dataclass(frozen=True)
class OpenAIAgentSettings:
	apiKey: str = ""
	baseUrl: str = ""
	model: str = ""
	imageQuality: int = JPEG_QUALITY
	source: str = ""


class OpenAIAgentClient:
	def __init__(self, settings: OpenAIAgentSettings) -> None:
		self.apiKey = settings.apiKey
		self.baseUrl = (settings.baseUrl or DEFAULT_OPENAI_BASE_URL).rstrip("/")
		self.model = settings.model or DEFAULT_OPENAI_MODEL
		self.imageQuality = settings.imageQuality
		if not self.apiKey:
			raise AuthenticationError(
				_("API key is missing. Please configure it in the AI Agent settings."),
			)
		log.info(
			f"Vis Aware agent using OpenAI settings from {settings.source or 'Agent settings'}, "
			f"model={self.model}, baseUrl={self.baseUrl!r}, imageQuality={self.imageQuality}",
		)
		self._previousResponseId: str | None = None
		self._pendingComputerCallId: str | None = None
		self._pendingAskUserCallId: str | None = None

	def nextAction(self, goal: str, screenshot: Screenshot, history: list[str]) -> AgentDecision:
		for _index in range(MAX_INTERNAL_COMPUTER_TURNS):
			apiResult = self._createResponse(goal, screenshot, history)
			self._setPreviousResponseId(apiResult)
			askUserCall = _extractAskUserCall(apiResult)
			if askUserCall:
				self._pendingAskUserCallId = askUserCall[0]
				return AgentDecision(status="ask_user", message=askUserCall[1])
			computerCall = _extractComputerCall(apiResult)
			if not computerCall:
				return AgentDecision(
					status="finish",
					message=_extractResponseText(apiResult) or _("Agent finished."),
				)
			callId, computerActions = computerCall
			self._pendingComputerCallId = callId
			actions = _computerActionsToAgentActions(computerActions, screenshot)
			if actions:
				message = _formatComputerActionMessage(actions)
				return AgentDecision(status="action", message=message, action=actions[0], actions=actions)
		raise ApiError(_("OpenAI did not return a desktop action."))

	def _createResponse(self, goal: str, screenshot: Screenshot, history: list[str]) -> dict[str, Any]:
		payload = {
			"model": self.model,
			"tools": [_buildComputerTool(), _buildAskUserTool()],
			"input": self._buildInput(goal, screenshot, history),
		}
		if self._previousResponseId:
			payload["previous_response_id"] = self._previousResponseId
		startTime = time.perf_counter()
		response = network.sendRequest(
			"POST",
			f"{self.baseUrl}/responses",
			headers={
				"Authorization": f"Bearer {self.apiKey}",
				"Content-Type": "application/json",
			},
			json=payload,
			timeout=60,
		)
		apiResult = response.json()
		duration = time.perf_counter() - startTime
		log.info(
			"OpenAI agent response completed: "
			f"duration={duration:.2f}s, previous={_shortId(payload.get('previous_response_id')) or 'none'}, "
			f"response={_shortId(apiResult.get('id')) or 'none'}, output={_formatOutputTypes(apiResult)}",
		)
		return apiResult

	def _buildInput(self, goal: str, screenshot: Screenshot, history: list[str]) -> Any:
		if self._pendingAskUserCallId:
			callId = self._pendingAskUserCallId
			self._pendingAskUserCallId = None
			return [
				{
					"type": "function_call_output",
					"call_id": callId,
					"output": _latestUserAnswer(history),
				},
			]
		if self._pendingComputerCallId:
			callId = self._pendingComputerCallId
			self._pendingComputerCallId = None
			return [
				{
					"type": "computer_call_output",
					"call_id": callId,
					"output": _buildComputerScreenshot(screenshot),
				},
			]
		return self._buildPrompt(goal, screenshot, history)

	def _setPreviousResponseId(self, apiResult: dict[str, Any]) -> None:
		responseId = apiResult.get("id")
		if isinstance(responseId, str) and responseId:
			self._previousResponseId = responseId

	def listModels(self) -> list[str]:
		response = network.sendRequest(
			"GET",
			f"{self.baseUrl}/models",
			headers={"Authorization": f"Bearer {self.apiKey}"},
			timeout=30,
		)
		apiResult = response.json()
		modelIds = set()
		models = apiResult.get("data", [])
		if not isinstance(models, list):
			return []
		for model in models:
			if isinstance(model, dict) and isinstance(model.get("id"), str):
				modelId = model["id"].strip()
				if modelId:
					modelIds.add(modelId)
		return sorted(modelIds)

	def _buildPrompt(self, goal: str, screenshot: Screenshot, history: list[str]) -> str:
		historyText = "\n".join(history[-MAX_HISTORY_ITEMS:]) if history else "None."
		return (
			f"Task: {goal}.\n"
			f"Foreground App: {screenshot.window.appName}. {formatScreenshotPromptContext(screenshot)}\n"
			"Use the computer tool for all UI interaction. First request a screenshot before deciding "
			"visual UI actions.\n"
			"STRICT RULES:\n"
			"1. RESPONSE LANGUAGE: Everything MUST be in the user's language.\n"
			"2. OUTPUT: Use computer actions for desktop operation. Do not describe clicks in text when "
			"the computer tool can perform them.\n"
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
			"Ignore 'Vis Aware Agent' or 'NVDA' windows.\n"
			f"- Recent actions:\n{historyText}"
		)


def _buildComputerTool() -> dict[str, str]:
	return {"type": "computer"}


def _buildAskUserTool() -> dict[str, Any]:
	return {
		"type": "function",
		"name": ASK_USER_TOOL_NAME,
		"description": "Ask the local user for missing information or confirmation.",
		"parameters": {
			"type": "object",
			"properties": {
				"question": {
					"type": "string",
					"description": "The exact question to show the user.",
				},
			},
			"required": ["question"],
			"additionalProperties": False,
		},
	}


def _buildComputerScreenshot(screenshot: Screenshot) -> dict[str, str]:
	return {
		"type": "computer_screenshot",
		"image_url": f"data:{screenshot.mimeType};base64,{screenshot.imageBase64}",
		"detail": COMPUTER_SCREENSHOT_DETAIL,
	}


def _extractComputerCall(apiResult: dict[str, Any]) -> tuple[str, list[dict[str, Any]]] | None:
	for item in _iterOutputItems(apiResult):
		if item.get("type") != "computer_call":
			continue
		callId = str(item.get("call_id") or item.get("id") or "")
		actions = item.get("actions")
		if not callId or not isinstance(actions, list):
			raise ApiError(_("OpenAI returned malformed computer action data."))
		return callId, [action for action in actions if isinstance(action, dict)]
	return None


def _extractAskUserCall(apiResult: dict[str, Any]) -> tuple[str, str] | None:
	for item in _iterOutputItems(apiResult):
		if item.get("type") != "function_call" or item.get("name") != ASK_USER_TOOL_NAME:
			continue
		callId = str(item.get("call_id") or item.get("id") or "")
		arguments = item.get("arguments")
		if isinstance(arguments, str):
			try:
				import json

				arguments = json.loads(arguments)
			except ValueError:
				arguments = {}
		if not callId or not isinstance(arguments, dict):
			raise ApiError(_("OpenAI returned malformed user question data."))
		question = str(arguments.get("question") or "").strip()
		if not question:
			raise ApiError(_("OpenAI returned an empty user question."))
		return callId, question
	return None


def _computerActionsToAgentActions(
	computerActions: list[dict[str, Any]],
	screenshot: Screenshot,
) -> list[AgentAction]:
	actions: list[AgentAction] = []
	for computerAction in computerActions:
		actions.extend(_computerActionToAgentActions(computerAction, screenshot))
	return actions


def _computerActionToAgentActions(
	computerAction: dict[str, Any],
	screenshot: Screenshot,
) -> list[AgentAction]:
	actionType = str(computerAction.get("type") or "").lower()
	if actionType == "screenshot":
		return []
	if actionType == "wait":
		return [AgentAction(name="wait", arguments={"seconds": 2}, message=_("Waiting."))]
	if actionType == "keypress":
		return _computerKeysToActions(computerAction.get("keys"))
	if actionType == "type":
		return [
			AgentAction(
				name="type_text_at",
				arguments={"text": str(computerAction.get("text") or ""), "clear_before_typing": False},
				message=_("Typing text."),
			),
		]
	if actionType == "scroll":
		return _withModifiers(
			computerAction.get("keys"),
			[
				AgentAction(
					name="scroll_at",
					arguments=_scrollArgsFromComputerAction(computerAction, screenshot),
					message=_("Scrolling."),
				),
			],
		)
	if actionType == "drag":
		return _withModifiers(
			computerAction.get("keys"),
			_dragActionsFromComputerAction(computerAction, screenshot),
		)
	if actionType == "move":
		x, y = _normalizedPointFromComputerAction(computerAction, screenshot)
		return _withModifiers(
			computerAction.get("keys"),
			[AgentAction(name="hover_at", arguments={"x": x, "y": y}, message=_("Moving mouse."))],
		)
	if actionType in {"click", "double_click"}:
		x, y = _normalizedPointFromComputerAction(computerAction, screenshot)
		name = _clickActionName(actionType, str(computerAction.get("button") or "left").lower())
		return _withModifiers(
			computerAction.get("keys"),
			[AgentAction(name=name, arguments={"x": x, "y": y}, message=_("Clicking."))],
		)
	raise ApiError(_("OpenAI returned an unsupported computer action: {}").format(actionType))


def _clickActionName(actionType: str, button: str) -> str:
	if button == "right":
		return "right_click_at"
	if button == "middle":
		return "middle_click_at"
	if actionType == "double_click":
		return "double_click_at"
	return "click_at"


def _dragActionsFromComputerAction(
	computerAction: dict[str, Any],
	screenshot: Screenshot,
) -> list[AgentAction]:
	path = _normalizeComputerDragPath(computerAction.get("path"), screenshot)
	if len(path) < 2:
		raise ApiError(_("OpenAI returned malformed drag action data."))
	startX, startY = path[0]
	endX, endY = path[-1]
	return [
		AgentAction(
			name="drag_and_drop",
			arguments={
				"x": startX,
				"y": startY,
				"destination_x": endX,
				"destination_y": endY,
			},
			message=_("Dragging."),
		),
	]


def _normalizeComputerDragPath(value: Any, screenshot: Screenshot) -> list[tuple[int, int]]:
	if not isinstance(value, list):
		return []
	points = []
	for item in value:
		if isinstance(item, dict):
			xValue = item.get("x")
			yValue = item.get("y")
		elif isinstance(item, list | tuple) and len(item) >= 2:
			xValue, yValue = item[0], item[1]
		else:
			continue
		points.append(_normalizedPointFromPixels(xValue, yValue, screenshot))
	return points


def _scrollArgsFromComputerAction(
	computerAction: dict[str, Any],
	screenshot: Screenshot,
) -> dict[str, int | str]:
	x, y = _normalizedPointFromComputerAction(computerAction, screenshot)
	scrollX = _numberFromAction(computerAction, "scroll_x", "scrollX")
	scrollY = _numberFromAction(computerAction, "scroll_y", "scrollY")
	if abs(scrollX) > abs(scrollY):
		direction = "right" if scrollX > 0 else "left"
		magnitude = abs(scrollX)
	else:
		direction = "down" if scrollY > 0 else "up"
		magnitude = abs(scrollY)
	return {
		"x": x,
		"y": y,
		"direction": direction,
		"magnitude": min(999, max(1, round(magnitude))),
	}


def _normalizedPointFromComputerAction(
	computerAction: dict[str, Any],
	screenshot: Screenshot,
) -> tuple[int, int]:
	return _normalizedPointFromPixels(computerAction.get("x"), computerAction.get("y"), screenshot)


def _normalizedPointFromPixels(xValue: Any, yValue: Any, screenshot: Screenshot) -> tuple[int, int]:
	x = _pixelToNormalized(xValue, screenshot.width)
	y = _pixelToNormalized(yValue, screenshot.height)
	return x, y


def _pixelToNormalized(value: Any, size: int) -> int:
	try:
		pixels = float(value)
	except (TypeError, ValueError):
		pixels = 0
	if size <= 0:
		return 0
	return min(NORMALIZED_SCALE, max(0, round(pixels / size * NORMALIZED_SCALE)))


def _withModifiers(keys: Any, actions: list[AgentAction]) -> list[AgentAction]:
	modifiers = [_normalizeComputerKey(key) for key in _iterComputerKeys(keys)]
	modifiers = [key for key in modifiers if key in {"control", "alt", "shift", "windows"}]
	return [
		*(
			AgentAction(name="key_down", arguments={"key": key}, message=_("Holding key."))
			for key in modifiers
		),
		*actions,
		*(
			AgentAction(name="key_up", arguments={"key": key}, message=_("Releasing key."))
			for key in reversed(modifiers)
		),
	]


def _computerKeysToActions(keys: Any) -> list[AgentAction]:
	normalizedKeys = [_normalizeComputerKey(key) for key in _iterComputerKeys(keys)]
	if not normalizedKeys:
		raise ApiError(_("OpenAI returned malformed keypress action data."))
	modifiers = [key for key in normalizedKeys[:-1] if key in {"control", "alt", "shift", "windows"}]
	if modifiers:
		keyCombination = "+".join([*modifiers, normalizedKeys[-1]])
		return [
			AgentAction(
				name="key_combination",
				arguments={"keys": keyCombination},
				message=_("Pressing keys."),
			),
		]
	return [
		AgentAction(name="key_combination", arguments={"keys": key}, message=_("Pressing keys."))
		for key in normalizedKeys
	]


def _iterComputerKeys(keys: Any) -> list[str]:
	if isinstance(keys, str):
		return [part for part in keys.replace("-", "+").split("+") if part]
	if isinstance(keys, list | tuple):
		return [str(key) for key in keys if str(key).strip()]
	return []


def _normalizeComputerKey(key: Any) -> str:
	keyText = str(key or "").strip().lower()
	replacements = {
		"ctrl": "control",
		"control": "control",
		"alt": "alt",
		"shift": "shift",
		"meta": "windows",
		"cmd": "windows",
		"command": "windows",
		"win": "windows",
		"windows": "windows",
		"enter": "enter",
		"return": "enter",
		"esc": "escape",
		"escape": "escape",
		"backspace": "backspace",
		"tab": "tab",
		"space": "space",
		"spacebar": "space",
		"delete": "delete",
		"del": "delete",
		"arrowleft": "leftArrow",
		"left": "leftArrow",
		"arrowright": "rightArrow",
		"right": "rightArrow",
		"arrowup": "upArrow",
		"up": "upArrow",
		"arrowdown": "downArrow",
		"down": "downArrow",
		"pageup": "pageUp",
		"pagedown": "pageDown",
		"home": "home",
		"end": "end",
	}
	return replacements.get(keyText, keyText)


def _numberFromAction(computerAction: dict[str, Any], snakeName: str, camelName: str) -> float:
	value = computerAction.get(snakeName, computerAction.get(camelName, 0))
	try:
		return float(value)
	except (TypeError, ValueError):
		return 0.0


def _latestUserAnswer(history: list[str]) -> str:
	for item in reversed(history):
		prefix = "User answered: "
		if item.startswith(prefix):
			return item[len(prefix) :]
	return ""


def _formatComputerActionMessage(actions: list[AgentAction]) -> str:
	if len(actions) == 1:
		return actions[0].message
	return _("Executing {count} computer actions.").format(count=len(actions))


def _extractResponseText(apiResult: dict[str, Any]) -> str:
	error = apiResult.get("error")
	if error:
		raise ApiError(_("OpenAI API error: {}").format(error))
	outputText = apiResult.get("output_text")
	if isinstance(outputText, str) and outputText:
		return outputText
	for item in apiResult.get("output", []):
		if not isinstance(item, dict):
			continue
		for content in item.get("content", []):
			if not isinstance(content, dict):
				continue
			if content.get("type") == "output_text" and content.get("text"):
				return str(content["text"])
			if content.get("type") == "refusal" and content.get("refusal"):
				raise ApiError(str(content["refusal"]))
	return ""


def _iterOutputItems(apiResult: dict[str, Any]) -> list[dict[str, Any]]:
	output = apiResult.get("output", [])
	if not isinstance(output, list):
		return []
	return [item for item in output if isinstance(item, dict)]


def _formatOutputTypes(apiResult: dict[str, Any]) -> str:
	types = [str(item.get("type") or "unknown") for item in _iterOutputItems(apiResult)]
	return ",".join(types) or "none"


def _shortId(value: Any) -> str:
	if isinstance(value, str) and value:
		return value[:12]
	return ""
