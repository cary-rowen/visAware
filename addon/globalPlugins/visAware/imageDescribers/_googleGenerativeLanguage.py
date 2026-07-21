# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Small helpers for Google's Generative Language API image describers."""

import addonHandler
import json
from logHandler import log
from typing import Any

addonHandler.initTranslation()

GENERATE_CONTENT_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}"


def buildGenerateContentUrl(model: str, stream: bool) -> str:
	"""
	Builds a Gemini API generateContent URL.

	:param model: The model id.
	:param stream: Whether to use the SSE streaming endpoint.
	:returns: The REST API URL.
	"""
	method = "streamGenerateContent" if stream else "generateContent"
	url = GENERATE_CONTENT_URL_TEMPLATE.format(model=model, method=method)
	if stream:
		return f"{url}?alt=sse"
	return url


def parseSseDataChunk(chunk: bytes, serviceName: str) -> dict[str, Any] | None:
	"""
	Parses one Server-Sent Events data line from streamGenerateContent.

	:param chunk: One raw line from the streaming HTTP response.
	:param serviceName: The service name to include in debug logs.
	:returns: A decoded GenerateContentResponse object, or None for non-data lines.
	"""
	decodedLine = chunk.decode("utf-8", "ignore").strip()
	if not decodedLine.startswith("data:"):
		return None
	payload = decodedLine[len("data:") :].strip()
	if not payload or payload == "[DONE]":
		return None
	try:
		data = json.loads(payload)
	except json.JSONDecodeError:
		log.debugWarning(f"{serviceName} ignored malformed streaming chunk: {decodedLine}")
		return None
	if not isinstance(data, dict):
		log.debugWarning(f"{serviceName} ignored non-object streaming chunk: {decodedLine}")
		return None
	return data


def getApiErrorMessage(apiResult: dict[str, Any]) -> str | None:
	"""
	Extracts a Google API error message from a response object.

	:param apiResult: A decoded GenerateContentResponse-like object.
	:returns: An error message, or None when no API error is present.
	"""
	error = apiResult.get("error")
	if not error:
		return None
	if isinstance(error, dict):
		return error.get("message", "Unknown API error")
	return str(error)


def getPromptFeedbackError(apiResult: dict[str, Any]) -> str | None:
	"""
	Returns an error message from promptFeedback, when present.

	:param apiResult: A decoded GenerateContentResponse object.
	:returns: An error message, or None when promptFeedback is not blocking.
	"""
	promptFeedback = apiResult.get("promptFeedback", {})
	if isinstance(promptFeedback, dict) and promptFeedback.get("blockReason"):
		# Translators: An error message indicating content was blocked by safety filters.
		return _("Content blocked due to safety settings.")
	return None


def getCandidateFinishError(candidate: dict[str, Any], allowMissing: bool = False) -> str | None:
	"""
	Returns a user-visible error message for incomplete Google candidates.

	:param candidate: A candidate object from a GenerateContentResponse.
	:param allowMissing: Whether a missing finishReason is valid for an intermediate stream chunk.
	:returns: An error message, or None when the candidate completed normally.
	"""
	finishReason = candidate.get("finishReason")
	if finishReason == "STOP" or (allowMissing and finishReason is None):
		return None
	if finishReason == "SAFETY":
		# Translators: An error message indicating content was blocked by safety filters.
		return _("Content blocked due to safety settings.")
	# Translators: An error message when the model output was cut short before completion.
	return _("Model response stopped before completion: {}").format(
		finishReason or "FINISH_REASON_UNSPECIFIED",
	)


def extractTextFromCandidate(candidate: dict[str, Any], strip: bool = True) -> str:
	"""
	Extracts plain text from a Google GenerateContent candidate.

	:param candidate: A candidate object from a GenerateContentResponse.
	:param strip: Whether to trim surrounding whitespace.
	:returns: The concatenated text parts.
	"""
	content = candidate.get("content", {})
	if not isinstance(content, dict):
		return ""
	parts = content.get("parts", [])
	if not isinstance(parts, list):
		return ""
	text = "".join(part["text"] for part in parts if isinstance(part, dict) and "text" in part)
	if strip:
		return text.strip()
	return text


def extractTextFromResponse(apiResult: dict[str, Any]) -> str:
	"""
	Extracts plain text from a full GenerateContentResponse or stored streaming result.

	:param apiResult: A GenerateContentResponse-like object.
	:returns: The recognized text.
	"""
	streamedText = apiResult.get("streamed_text")
	if streamedText:
		return str(streamedText)
	try:
		candidate = apiResult["candidates"][0]
		if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
			return ""
		return extractTextFromCandidate(candidate)
	except (KeyError, IndexError, TypeError):
		return ""


def buildConversationContents(
	imagePart: dict[str, Any],
	initialPrompt: str,
	initialAnswer: str,
	turns: list[Any],
	question: str,
) -> list[dict[str, Any]]:
	"""
	Builds GenerateContent contents for a visual follow-up conversation.

	:param imagePart: The image part for the first user turn.
	:param initialPrompt: The original image description prompt.
	:param initialAnswer: The model's original image description.
	:param turns: Follow-up text turns already completed.
	:param question: The new user question.
	:returns: A contents list for generateContent.
	"""
	contents: list[dict[str, Any]] = [
		{
			"role": "user",
			"parts": [
				imagePart,
				{"text": initialPrompt},
			],
		},
	]
	if initialAnswer:
		contents.append(
			{
				"role": "model",
				"parts": [{"text": initialAnswer}],
			},
		)
	for turn in turns:
		role = "model" if getattr(turn, "role", "") == "assistant" else "user"
		text = str(getattr(turn, "text", "")).strip()
		if text:
			contents.append(
				{
					"role": role,
					"parts": [{"text": text}],
				},
			)
	contents.append(
		{
			"role": "user",
			"parts": [{"text": question}],
		},
	)
	return contents
