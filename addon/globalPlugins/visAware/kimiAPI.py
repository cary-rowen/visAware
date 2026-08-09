# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Small, shared helpers for Kimi's OpenAI-compatible Chat API."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any

import addonHandler

from .exceptions import ApiError, StreamIncompleteError

addonHandler.initTranslation()

SUPPORTED_FINISH_REASONS = {"stop", "tool_calls"}

# Translators: An error message for invalid data returned by Kimi.
_INVALID_RESPONSE_MESSAGE = _("Kimi returned an invalid response.")
# Translators: An error message for malformed Kimi Chat Completion data.
_MALFORMED_COMPLETION_MESSAGE = _("Kimi returned malformed completion data.")
# Translators: An error message for malformed data in a Kimi streaming response.
_MALFORMED_STREAM_MESSAGE = _("Kimi streaming response contained malformed data.")


def parseKimiResponse(result: bytes | str) -> dict[str, Any]:
	"""Decodes a successful Kimi JSON response."""
	try:
		if isinstance(result, bytes):
			result = result.decode("utf-8")
		response = json.loads(result)
	except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as e:
		raise ApiError(_INVALID_RESPONSE_MESSAGE) from e
	if not isinstance(response, dict):
		raise ApiError(_INVALID_RESPONSE_MESSAGE)
	return response


def getKimiApiErrorMessage(response: dict[str, Any]) -> str | None:
	"""Extracts an API error object without exposing credentials."""
	error = response.get("error")
	if error is None and response.get("type") == "error":
		error = response
	if error is None:
		return None
	if isinstance(error, dict):
		# Translators: A fallback message when a Kimi API error has no details.
		message = error.get("message") or error.get("code") or _("Unknown API error")
	else:
		message = error
	return str(message)[:500]


def validateKimiCompletion(
	response: dict[str, Any],
	allowedFinishReasons: set[str] = SUPPORTED_FINISH_REASONS,
) -> tuple[dict[str, Any], dict[str, Any]]:
	"""Validates one non-streaming Chat Completion and returns choice/message."""
	apiError = getKimiApiErrorMessage(response)
	if apiError:
		# Translators: An error returned by Kimi. The placeholder contains the API's message.
		raise ApiError(_("Kimi API error: {}").format(apiError))
	choices = response.get("choices")
	if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
		raise ApiError(_MALFORMED_COMPLETION_MESSAGE)
	choice = choices[0]
	finishReason = choice.get("finish_reason")
	if finishReason == "length":
		# Translators: Reported when Kimi stops because the output token limit was reached.
		raise ApiError(_("Kimi response was truncated at the token limit."))
	if not isinstance(finishReason, str) or finishReason not in allowedFinishReasons:
		# Translators: An error for an unsupported Kimi completion finish reason.
		raise ApiError(_("Kimi returned an unsupported completion result."))
	message = choice.get("message")
	if not isinstance(message, dict) or message.get("role") != "assistant":
		raise ApiError(_MALFORMED_COMPLETION_MESSAGE)
	toolCalls = message.get("tool_calls")
	if toolCalls is not None and not isinstance(toolCalls, list):
		raise ApiError(_MALFORMED_COMPLETION_MESSAGE)
	if finishReason == "tool_calls" and not toolCalls:
		raise ApiError(_MALFORMED_COMPLETION_MESSAGE)
	if finishReason != "tool_calls" and toolCalls:
		raise ApiError(_MALFORMED_COMPLETION_MESSAGE)
	return choice, message


def getKimiMessageText(message: dict[str, Any]) -> str:
	"""Extracts visible text from a Chat Completion assistant message."""
	content = message.get("content")
	if isinstance(content, str):
		return content
	if not isinstance(content, list):
		return ""
	return "".join(
		str(part.get("text", ""))
		for part in content
		if isinstance(part, dict) and isinstance(part.get("text"), str)
	)


def extractKimiAssistantMessage(response: Any) -> dict[str, Any] | None:
	"""Gets an assistant message from either a raw response or message object."""
	if not isinstance(response, dict):
		return None
	if response.get("role") == "assistant" and (
		"content" in response or "tool_calls" in response or "reasoning_content" in response
	):
		return deepcopy(response)
	choices = response.get("choices")
	if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
		message = choices[0].get("message")
		if isinstance(message, dict):
			return deepcopy(message)
	return None


@dataclass
class KimiStreamState:
	"""Reconstructs one OpenAI-compatible Kimi SSE completion."""

	metadata: dict[str, Any] = field(default_factory=dict)
	message: dict[str, Any] = field(default_factory=lambda: {"role": "assistant"})
	_textParts: list[str] = field(default_factory=list)
	_reasoningParts: list[str] = field(default_factory=list)
	_toolCalls: list[dict[str, Any]] = field(default_factory=list)
	_finishReason: str | None = None
	_sawReasoningField: bool = False
	_sawDone: bool = False
	_malformed: bool = False
	_usage: dict[str, Any] | None = None

	@property
	def text(self) -> str:
		return "".join(self._textParts)

	def consume(self, chunk: bytes) -> str | None:
		"""Consumes one SSE line and returns only visible text."""
		try:
			line = chunk.decode("utf-8").strip()
		except UnicodeDecodeError:
			self._malformed = True
			return None
		if not line or line.startswith((":", "event:", "id:", "retry:")):
			return None
		if not line.startswith("data:"):
			self._malformed = True
			return None
		data = line[len("data:") :].strip()
		if data == "[DONE]":
			if self._sawDone:
				self._malformed = True
				return None
			self._sawDone = True
			return None
		if self._sawDone:
			self._malformed = True
			return None
		try:
			event = json.loads(data)
		except json.JSONDecodeError:
			self._malformed = True
			return None
		if not isinstance(event, dict):
			self._malformed = True
			return None
		apiError = getKimiApiErrorMessage(event)
		if apiError:
			# Translators: An error returned by Kimi. The placeholder contains the API's message.
			raise ApiError(_("Kimi API error: {}").format(apiError))
		for key in ("id", "object", "created", "model"):
			if key not in event:
				continue
			if key in self.metadata and self.metadata[key] != event[key]:
				self._malformed = True
			else:
				self.metadata[key] = deepcopy(event[key])
		if isinstance(event.get("usage"), dict):
			self._usage = deepcopy(event["usage"])
		choices = event.get("choices")
		if choices == []:
			return None
		if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
			self._malformed = True
			return None
		if self._finishReason is not None:
			self._malformed = True
			return None
		choice = choices[0]
		if choice.get("index") not in (None, 0):
			self._malformed = True
		finishReason = choice.get("finish_reason")
		if finishReason is not None:
			if not isinstance(finishReason, str):
				self._malformed = True
			else:
				self._finishReason = finishReason
		delta = choice.get("delta", {})
		if not isinstance(delta, dict):
			self._malformed = True
			return None
		role = delta.get("role")
		if role is not None:
			if role != "assistant":
				self._malformed = True
			else:
				self.message["role"] = role
		if "reasoning_content" in delta:
			self._sawReasoningField = True
			reasoning = delta.get("reasoning_content")
			if isinstance(reasoning, str):
				self._reasoningParts.append(reasoning)
			elif reasoning is not None:
				self._malformed = True
		content = delta.get("content")
		text = ""
		if isinstance(content, str):
			text = content
		elif isinstance(content, list):
			for part in content:
				if isinstance(part, dict) and isinstance(part.get("text"), str):
					text += part["text"]
				else:
					self._malformed = True
		elif content is not None:
			self._malformed = True
		if text:
			self._textParts.append(text)
		toolCalls = delta.get("tool_calls")
		if toolCalls is not None:
			if not isinstance(toolCalls, list):
				self._malformed = True
			else:
				self._mergeToolCalls(toolCalls)
		return text or None

	def _mergeToolCalls(self, toolCalls: list[Any]) -> None:
		for call in toolCalls:
			if not isinstance(call, dict):
				self._malformed = True
				continue
			index = call.get("index", len(self._toolCalls))
			if isinstance(index, bool) or not isinstance(index, int) or index < 0:
				self._malformed = True
				continue
			while len(self._toolCalls) <= index:
				self._toolCalls.append({"type": "function", "function": {}})
			stored = self._toolCalls[index]
			for key in ("id", "type"):
				if key in call and not isinstance(call[key], str):
					self._malformed = True
				elif isinstance(call.get(key), str):
					if key in stored and stored[key] != call[key]:
						self._malformed = True
					else:
						stored[key] = call[key]
			if call.get("type") not in (None, "function"):
				self._malformed = True
			function = call.get("function")
			if not isinstance(function, dict):
				self._malformed = True
				continue
			storedFunction = stored.setdefault("function", {})
			if "name" in function:
				value = function["name"]
				if not isinstance(value, str):
					self._malformed = True
				elif "name" in storedFunction and storedFunction["name"] != value:
					self._malformed = True
				else:
					storedFunction["name"] = value
			value = function.get("arguments")
			if value is not None:
				if not isinstance(value, str):
					self._malformed = True
				else:
					storedFunction["arguments"] = f"{storedFunction.get('arguments', '')}{value}"

	def _validateToolCalls(self) -> None:
		for call in self._toolCalls:
			if call.get("type") != "function":
				self._malformed = True
				continue
			function = call.get("function")
			if (
				not isinstance(call.get("id"), str)
				or not call["id"]
				or not isinstance(function, dict)
				or not isinstance(function.get("name"), str)
				or not function["name"]
				or not isinstance(function.get("arguments"), str)
			):
				self._malformed = True

	def buildResponse(self) -> dict[str, Any]:
		"""Returns the reconstructed provider response or raises for a partial stream."""
		if self._malformed:
			raise StreamIncompleteError(_MALFORMED_STREAM_MESSAGE)
		if not self._sawDone:
			# Translators: Reported when a Kimi stream ends without its completion marker.
			raise StreamIncompleteError(_("Kimi streaming response ended before completion."))
		if self._finishReason is None:
			# Translators: Reported when a Kimi stream does not provide a finish reason.
			raise StreamIncompleteError(_("Kimi streaming response did not include a finish reason."))
		if self._finishReason == "length":
			# Translators: Reported when Kimi stops because the output token limit was reached.
			raise StreamIncompleteError(_("Kimi response was truncated at the token limit."))
		if self._finishReason not in SUPPORTED_FINISH_REASONS:
			# Translators: An error for an unsupported Kimi completion finish reason.
			raise StreamIncompleteError(_("Kimi returned an unsupported completion result."))
		if self._finishReason == "tool_calls":
			if not self._toolCalls:
				# Translators: Reported when a Kimi stream claims to call a tool but supplies none.
				raise StreamIncompleteError(_("Kimi streaming response contained no tool call."))
		else:
			if self._toolCalls:
				# Translators: Reported when a Kimi stream unexpectedly includes a tool call.
				raise StreamIncompleteError(_("Kimi streaming response returned an unexpected tool call."))
		self._validateToolCalls()
		if self._malformed:
			raise StreamIncompleteError(_MALFORMED_STREAM_MESSAGE)
		message = deepcopy(self.message)
		message["content"] = self.text
		if self._sawReasoningField:
			message["reasoning_content"] = "".join(self._reasoningParts)
		if self._toolCalls:
			message["tool_calls"] = deepcopy(self._toolCalls)
		response = deepcopy(self.metadata)
		response["choices"] = [
			{"index": 0, "message": message, "finish_reason": self._finishReason},
		]
		if self._usage is not None:
			response["usage"] = deepcopy(self._usage)
		return response
