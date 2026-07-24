# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Shared Agent action schema and decision parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import addonHandler

from ..exceptions import ApiError
from .actions import AgentAction

addonHandler.initTranslation()

ACTION_NAMES = [
	"none",
	"click_at",
	"click",
	"double_click_at",
	"double_click",
	"triple_click_at",
	"triple_click",
	"right_click_at",
	"right_click",
	"middle_click_at",
	"middle_click",
	"hover_at",
	"move_to",
	"move",
	"type_text_at",
	"type",
	"key_combination",
	"hotkey",
	"press_key",
	"key_down",
	"key_up",
	"scroll_at",
	"scroll_document",
	"scroll",
	"drag_and_drop",
	"drag_to",
	"drag_by",
	"mouse_down",
	"mouse_up",
	"wait",
	"wait_for_change",
	"go_back",
	"go_forward",
	"navigate",
]

ACTION_ALIASES = {
	"click": "click_at",
	"double_click": "double_click_at",
	"triple_click": "triple_click_at",
	"right_click": "right_click_at",
	"middle_click": "middle_click_at",
	"move_to": "hover_at",
	"move": "hover_at",
	"type": "type_text_at",
	"hotkey": "key_combination",
	"press_key": "key_combination",
	"scroll": "scroll_at",
	"drag_to": "drag_and_drop",
}

AGENT_ACTION_SCHEMA: dict[str, Any] = {
	"type": "object",
	"properties": {
		"status": {
			"type": "string",
			"enum": ["action", "finish", "ask_user"],
			"description": "Use action to operate, finish when the task is done, ask_user when user input is required.",
		},
		"message": {
			"type": "string",
			"description": "Short explanation in the user's language.",
		},
		"action": {
			"type": "string",
			"enum": ACTION_NAMES,
			"description": "The next single action to execute. Use none unless status is action.",
		},
		"finished": {
			"type": "boolean",
			"description": "Whether the action is expected to complete the task. Use false when visual verification is needed.",
		},
		"x": {
			"type": "integer",
			"minimum": 0,
			"maximum": 1000,
			"description": "X coordinate relative to the screenshot, normalized from 0 to 1000.",
		},
		"y": {
			"type": "integer",
			"minimum": 0,
			"maximum": 1000,
			"description": "Y coordinate relative to the screenshot, normalized from 0 to 1000.",
		},
		"destination_x": {
			"type": "integer",
			"minimum": 0,
			"maximum": 1000,
			"description": "Destination X coordinate for drag_and_drop or drag_to, normalized from 0 to 1000.",
		},
		"destination_y": {
			"type": "integer",
			"minimum": 0,
			"maximum": 1000,
			"description": "Destination Y coordinate for drag_and_drop or drag_to, normalized from 0 to 1000.",
		},
		"delta_x": {
			"type": "integer",
			"minimum": -5000,
			"maximum": 5000,
			"description": "Horizontal drag offset for drag_by, in screen pixels.",
		},
		"delta_y": {
			"type": "integer",
			"minimum": -5000,
			"maximum": 5000,
			"description": "Vertical drag offset for drag_by, in screen pixels.",
		},
		"text": {
			"type": "string",
			"description": "Text for type_text_at.",
		},
		"url": {
			"type": "string",
			"description": "URL for navigate.",
		},
		"keys": {
			"type": "string",
			"description": "Key or key combination, such as enter or control+a.",
		},
		"key": {
			"type": "string",
			"description": "Single key for key_down or key_up.",
		},
		"direction": {
			"type": "string",
			"enum": ["up", "down", "left", "right"],
			"description": "Scroll direction.",
		},
		"magnitude": {
			"type": "integer",
			"minimum": 0,
			"maximum": 999,
			"description": "Scroll amount on a 0-999 scale.",
		},
		"seconds": {
			"type": "integer",
			"minimum": 1,
			"maximum": 10,
			"description": "Seconds to wait.",
		},
		"press_enter": {
			"type": "boolean",
			"description": "Whether to press Enter after typing text.",
		},
		"clear_before_typing": {
			"type": "boolean",
			"description": "Whether to clear the target field before typing.",
		},
	},
	"required": ["status", "message", "action", "finished"],
	"additionalProperties": False,
	"propertyOrdering": [
		"status",
		"message",
		"action",
		"finished",
		"x",
		"y",
		"destination_x",
		"destination_y",
		"delta_x",
		"delta_y",
		"text",
		"url",
		"keys",
		"key",
		"direction",
		"magnitude",
		"seconds",
		"press_enter",
		"clear_before_typing",
	],
}


@dataclass
class AgentDecision:
	status: str
	message: str
	action: AgentAction | None = None
	actions: list[AgentAction] | None = None
	finishAfterAction: bool = False


def parseAgentDecision(data: dict[str, Any], providerName: str) -> AgentDecision:
	status = str(data.get("status") or "")
	message = str(data.get("message") or "")
	actionName = str(data.get("action") or "none")
	finished = bool(data.get("finished", False))
	if status not in {"action", "finish", "ask_user"}:
		raise ApiError(_("{} returned an unknown agent status.").format(providerName))
	if status == "finish":
		return AgentDecision(status="finish", message=message)
	if status == "ask_user":
		return AgentDecision(status=status, message=message)
	if actionName == "none":
		raise ApiError(_("{} did not return an agent action.").format(providerName))
	normalizedActionName = ACTION_ALIASES.get(actionName, actionName)
	if actionName not in ACTION_NAMES or normalizedActionName == "none":
		raise ApiError(_("{} returned an unsupported agent action: {}").format(providerName, actionName))
	arguments = {
		key: value
		for key, value in data.items()
		if key not in {"status", "message", "action", "finished"} and value is not None
	}
	_normalizeCoordinateArguments(arguments)
	_normalizeScalarArguments(arguments, ("delta_x", "delta_y"))
	if normalizedActionName == "type_text_at":
		text = str(arguments.get("text") or "")
		hasTrailingNewline = text.endswith(("\r", "\n"))
		arguments["press_enter"] = bool(arguments.get("press_enter", False)) or hasTrailingNewline
		if text.endswith("\r\n"):
			text = text[:-2]
		elif hasTrailingNewline:
			text = text[:-1]
		arguments["text"] = text
	return AgentDecision(
		status="action",
		message=message,
		action=AgentAction(
			name=normalizedActionName,
			arguments=arguments,
			message=message,
		),
		finishAfterAction=finished,
	)


def _normalizeCoordinateArguments(arguments: dict[str, Any]) -> None:
	for xName, yName in (("x", "y"), ("destination_x", "destination_y")):
		if xName not in arguments and yName not in arguments:
			continue
		point = _pointFromCoordinateList(arguments.get(xName), arguments.get(yName))
		if point:
			arguments[xName], arguments[yName] = point
			continue
		if xName in arguments:
			arguments[xName] = _coordinateScalar(arguments[xName])
		if yName in arguments:
			arguments[yName] = _coordinateScalar(arguments[yName])


def _pointFromCoordinateList(xValue: Any, yValue: Any) -> tuple[Any, Any] | None:
	if not isinstance(xValue, list | tuple) or len(xValue) < 2:
		return None
	if yValue is None or yValue == xValue:
		return xValue[0], xValue[1]
	return None


def _coordinateScalar(value: Any) -> Any:
	if not isinstance(value, list | tuple):
		return value
	values = []
	for item in value:
		try:
			values.append(float(item))
		except (TypeError, ValueError):
			pass
	if not values:
		return value
	return int(sum(values) / len(values))


def _normalizeScalarArguments(arguments: dict[str, Any], names: tuple[str, ...]) -> None:
	for name in names:
		if name in arguments:
			arguments[name] = _coordinateScalar(arguments[name])
