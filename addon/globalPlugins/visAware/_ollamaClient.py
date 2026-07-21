# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Helpers for talking to the Ollama HTTP API."""

from __future__ import annotations

import addonHandler
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import network
from .exceptions import ApiError

addonHandler.initTranslation()

DEFAULT_OLLAMA_API_BASE_URL = "http://localhost:11434/api"
_KNOWN_ENDPOINTS = {
	"chat",
	"generate",
	"tags",
}


def normalizeOllamaApiBaseUrl(value: str) -> str:
	"""
	Normalizes an Ollama API base URL entered by the user.

	:param value: A URL or host/path string such as ``localhost:11434`` or ``host:11434/api``.
	:returns: A normalized API root URL without a trailing slash.
	"""
	value = value.strip() if value else DEFAULT_OLLAMA_API_BASE_URL
	if not value:
		value = DEFAULT_OLLAMA_API_BASE_URL
	if "://" not in value:
		value = f"http://{value}"
	parts = urlsplit(value)
	path = parts.path or "/api"
	path = path.rstrip("/") or "/api"
	lastPathPart = path.rsplit("/", 1)[-1]
	if lastPathPart in _KNOWN_ENDPOINTS:
		path = path.rsplit("/", 1)[0] or "/api"
	if path == "/":
		path = "/api"
	return urlunsplit((parts.scheme or "http", parts.netloc, path.rstrip("/"), "", ""))


def buildOllamaUrl(apiBaseUrl: str, endpoint: str) -> str:
	"""
	Builds an Ollama API endpoint URL from the configured API root.

	:param apiBaseUrl: The configured API root URL.
	:param endpoint: Endpoint name without a leading slash.
	:returns: The full endpoint URL.
	"""
	return f"{normalizeOllamaApiBaseUrl(apiBaseUrl)}/{endpoint.lstrip('/')}"


def buildOllamaHeaders(apiKey: str = "") -> dict[str, str]:
	"""
	Builds headers for an Ollama JSON request.

	:param apiKey: Optional bearer token for hosted or proxied Ollama deployments.
	:returns: Headers suitable for requests.
	"""
	headers = {
		"Accept": "application/json",
		"Content-Type": "application/json",
	}
	apiKey = apiKey.strip()
	if apiKey:
		headers["Authorization"] = f"Bearer {apiKey}"
	return headers


class OllamaClient:
	"""Small wrapper around the Ollama HTTP API."""

	def __init__(self, apiBaseUrl: str, apiKey: str = "") -> None:
		"""
		Initializes the client.

		:param apiBaseUrl: Ollama API root URL.
		:param apiKey: Optional bearer token.
		"""
		self.apiBaseUrl = normalizeOllamaApiBaseUrl(apiBaseUrl)
		self.apiKey = apiKey

	def buildRequestParams(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
		"""
		Builds request parameters for an Ollama JSON POST request.

		:param endpoint: Ollama endpoint name.
		:param payload: JSON payload.
		:returns: Parameters for ``network.sendRequest`` or ``network.sendStreamingRequest``.
		"""
		return {
			"method": "POST",
			"url": buildOllamaUrl(self.apiBaseUrl, endpoint),
			"headers": buildOllamaHeaders(self.apiKey),
			"json": payload,
		}

	def getModelNames(self) -> list[str]:
		"""
		Returns local Ollama model names.

		:returns: Model names in Ollama's returned order.
		:raises ApiError: If Ollama returns malformed model list data.
		"""
		response = network.sendRequest(
			"GET",
			buildOllamaUrl(self.apiBaseUrl, "tags"),
			headers=buildOllamaHeaders(self.apiKey),
			timeout=20,
		)
		payload = decodeOllamaJson(response.content)
		error = getOllamaError(payload)
		if error:
			# Translators: An error message returned from the Ollama API.
			raise ApiError(_("Ollama API Error: {}").format(error))
		modelItems = payload.get("models")
		if not isinstance(modelItems, list):
			# Translators: An error message when Ollama returns invalid model list data.
			raise ApiError(_("Ollama returned an invalid model list."))
		models: list[str] = []
		seenModelNames: set[str] = set()
		for item in modelItems:
			modelName = _extractModelName(item)
			if not modelName or modelName in seenModelNames:
				continue
			seenModelNames.add(modelName)
			models.append(modelName)
		return models


def _extractModelName(item: Any) -> str:
	if not isinstance(item, dict):
		return ""
	name = item.get("name") or item.get("model")
	return str(name).strip() if name else ""


def decodeOllamaJson(data: bytes) -> dict[str, Any]:
	"""
	Decodes an Ollama JSON response.

	:param data: Raw response content.
	:returns: Decoded JSON object.
	:raises ApiError: If the response is not a JSON object.
	"""
	try:
		payload = json.loads(data.decode("utf-8", errors="ignore"))
	except json.JSONDecodeError as e:
		# Translators: An error message when Ollama returns malformed JSON.
		raise ApiError(_("Ollama returned malformed response data.")) from e
	if not isinstance(payload, dict):
		# Translators: An error message when Ollama returns malformed JSON.
		raise ApiError(_("Ollama returned malformed response data."))
	return payload


def getOllamaError(apiResult: dict[str, Any]) -> str | None:
	"""
	Returns an Ollama API error message, if present.

	:param apiResult: Decoded Ollama response.
	:returns: Error message or ``None``.
	"""
	error = apiResult.get("error")
	if not error:
		return None
	return str(error)


def extractOllamaChatText(apiResult: dict[str, Any]) -> str:
	"""
	Extracts text from an Ollama chat response.

	:param apiResult: Decoded Ollama chat response or stored streaming result.
	:returns: Assistant text.
	"""
	streamedText = apiResult.get("streamed_text")
	if streamedText:
		return str(streamedText).strip()
	message = apiResult.get("message")
	if not isinstance(message, dict):
		return ""
	content = message.get("content")
	return str(content).strip() if content else ""


def extractOllamaGenerateText(apiResult: dict[str, Any]) -> str:
	"""
	Extracts text from an Ollama generate response.

	:param apiResult: Decoded Ollama generate response or stored streaming result.
	:returns: Generated text.
	"""
	streamedText = apiResult.get("streamed_text")
	if streamedText:
		return str(streamedText).strip()
	response = apiResult.get("response")
	return str(response).strip() if response else ""


def parseOllamaChatStreamChunk(chunk: bytes) -> str | None:
	"""
	Parses one Ollama chat streaming chunk.

	:param chunk: One JSON line from Ollama.
	:returns: New assistant text, or ``None`` for a final metadata chunk.
	:raises ApiError: If the stream chunk reports an error or is malformed.
	"""
	payload = decodeOllamaJson(chunk)
	error = getOllamaError(payload)
	if error:
		# Translators: An error message returned from the Ollama API.
		raise ApiError(_("Ollama API Error: {}").format(error))
	if payload.get("done"):
		return None
	message = payload.get("message")
	if not isinstance(message, dict):
		return None
	content = message.get("content")
	return str(content) if content else None


def parseOllamaGenerateStreamChunk(chunk: bytes) -> str | None:
	"""
	Parses one Ollama generate streaming chunk.

	:param chunk: One JSON line from Ollama.
	:returns: New response text, or ``None`` for a final metadata chunk.
	:raises ApiError: If the stream chunk reports an error or is malformed.
	"""
	payload = decodeOllamaJson(chunk)
	error = getOllamaError(payload)
	if error:
		# Translators: An error message returned from the Ollama API.
		raise ApiError(_("Ollama API Error: {}").format(error))
	if payload.get("done"):
		return None
	response = payload.get("response")
	return str(response) if response else None
