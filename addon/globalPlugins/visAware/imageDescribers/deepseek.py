# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""DeepSeek image description through the Chat Completions API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import addonHandler

from .. import network
from ..deepseekModels import (
	DEFAULT_DEEPSEEK_BASE_URL,
	DEFAULT_DEEPSEEK_VISION_MODEL,
	buildDeepSeekChatCompletionsUrl,
	getDeepSeekVisionModelChoices,
	redactDeepSeekImageUrlsForLog,
)
from ..engineGUIHelper import ChoiceEngineSetting, TextInputEngineSetting
from ..exceptions import ApiError, AuthenticationError
from ..recogHandler import BaseDescriber, RecognitionRequest
from ._prompts import DEFAULT_IMAGE_DESCRIPTION_PROMPT

addonHandler.initTranslation()

REQUEST_TIMEOUT = 60
MAX_COMPLETION_TOKENS = 2_048

# Translators: The default prompt sent to the DeepSeek image describer.
DEFAULT_DEEPSEEK_PROMPT = DEFAULT_IMAGE_DESCRIPTION_PROMPT


class CustomContentRecognizer(BaseDescriber):
	"""An image description engine that uses the DeepSeek Chat Completions API."""

	name = "deepseek"
	# Translators: The description of the DeepSeek image describer engine.
	description = _("DeepSeek")

	uploadBase64EncodeImage = True
	supportsStreaming = False
	supportsQuestions = True
	supportsQuestionStreaming = False
	isStreaming = False
	uploadImageFormat = "JPEG"

	_apiKey: str = ""
	_baseUrl: str = DEFAULT_DEEPSEEK_BASE_URL
	_model: str = DEFAULT_DEEPSEEK_VISION_MODEL
	_prompt: str = DEFAULT_DEEPSEEK_PROMPT

	@property
	def supportedSettings(self) -> list[Any]:
		return [
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the DeepSeek API key used by the image describer.
				displayNameWithAccelerator=_("API &key"),
			),
			TextInputEngineSetting(
				name="baseUrl",
				# Translators: The label for the DeepSeek base URL used by the image describer.
				displayNameWithAccelerator=_("Base &URL"),
				refreshSettingsOnChange=True,
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for the DeepSeek model used by the image describer.
				displayNameWithAccelerator=_("&Model"),
				optionsPropertyName="availableModels",
			),
			self.imageQualitySetting(),
			TextInputEngineSetting(
				name="prompt",
				# Translators: The label for the DeepSeek prompt used by the image describer.
				displayNameWithAccelerator=_("Custom &prompt"),
				multiline=True,
				configKey="promptV2",
			),
			self.autoRecognitionPromptSetting(),
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
		if self.model not in getDeepSeekVisionModelChoices(self._baseUrl):
			self._model = DEFAULT_DEEPSEEK_VISION_MODEL

	@property
	def model(self) -> str:
		return self._model

	@model.setter
	def model(self, value: str) -> None:
		value = value.strip()
		self._model = value if value in self.availableModels else DEFAULT_DEEPSEEK_VISION_MODEL

	@property
	def prompt(self) -> str:
		return self._prompt

	@prompt.setter
	def prompt(self, value: str) -> None:
		self._prompt = value.strip() or DEFAULT_DEEPSEEK_PROMPT

	@property
	def availableModels(self) -> dict:
		return self.generateStringSettings(getDeepSeekVisionModelChoices(self.baseUrl))

	@classmethod
	def check(cls) -> bool:
		return True

	def _buildHeaders(self) -> dict[str, str]:
		return {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {self.apiKey}",
		}

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		if not self.apiKey:
			raise AuthenticationError(
				_("API key is missing. Please configure it in the DeepSeek engine settings."),
			)
		payload: dict[str, Any] = {
			"model": self.model,
			"messages": self._buildMessages(
				imageContent,
				prompt=getattr(request, "prompt", None),
			),
			"thinking": {"type": "disabled"},
			"max_tokens": MAX_COMPLETION_TOKENS,
		}
		return {
			"method": "POST",
			"url": buildDeepSeekChatCompletionsUrl(self.baseUrl),
			"headers": self._buildHeaders(),
			"json": payload,
			"timeout": REQUEST_TIMEOUT,
		}

	def _buildMessages(
		self,
		imageContent: bytes,
		prompt: str | None = None,
		context: Any | None = None,
		question: str | None = None,
	) -> list[dict[str, Any]]:
		messages: list[dict[str, Any]] = [
			{
				"role": "user",
				"content": [
					{
						"type": "text",
						"text": prompt if prompt else self.prompt,
					},
					_buildImageContent(imageContent),
				],
			},
		]
		if context is not None:
			messages.append({"role": "assistant", "content": context.initialText})
			for turn in getattr(context, "turns", []):
				role = getattr(turn, "role", "")
				text = str(getattr(turn, "text", "")).strip()
				if not text:
					continue
				messages.append({"role": role if role == "assistant" else "user", "content": text})
			if question is not None:
				messages.append({"role": "user", "content": question})
		return messages

	def _buildQuestionRequestParams(self, context: Any, question: str) -> dict:
		if not self.apiKey:
			raise AuthenticationError(
				_("API key is missing. Please configure it in the DeepSeek engine settings."),
			)
		image = self._getConversationImage(context)
		imageContent = self.prepareImageContentFromImage(image)
		payload: dict[str, Any] = {
			"model": self.model,
			"messages": self._buildMessages(
				imageContent,
				context=context,
				question=question,
			),
			"thinking": {"type": "disabled"},
			"max_tokens": MAX_COMPLETION_TOKENS,
		}
		return {
			"method": "POST",
			"url": buildDeepSeekChatCompletionsUrl(self.baseUrl),
			"headers": self._buildHeaders(),
			"json": payload,
			"timeout": REQUEST_TIMEOUT,
		}

	def askQuestion(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> str:
		self._checkQuestionCancelled(cancellationChecker)
		requestParams = self._buildQuestionRequestParams(context, question)
		self._checkQuestionCancelled(cancellationChecker)
		response = network.sendRequest(**requestParams)
		self._checkQuestionCancelled(cancellationChecker)
		apiErrorMessage = self.processApiResult(response.content)
		if apiErrorMessage:
			raise ApiError(str(apiErrorMessage))
		apiResult = self._convertToJson(response.content)
		return self._validateQuestionAnswer(self.extractText(apiResult))

	def processApiResult(self, result: bytes) -> str | bool:
		try:
			apiResult = self._convertToJson(result)
			if not isinstance(apiResult, dict):
				return _("Server returned an invalid response.")
			_validateResponseStatus(apiResult)
			if not _extractResponseText(apiResult).strip():
				return _("Server returned a successful but empty response.")
		except ApiError as e:
			return str(e)
		except Exception:
			return _("Invalid response from server.")
		return False

	def extractText(self, apiResult: dict) -> str:
		return _extractResponseText(apiResult)


def _buildImageContent(imageContent: bytes) -> dict[str, Any]:
	return {
		"type": "image_url",
		"image_url": {
			"url": f"data:image/jpeg;base64,{imageContent.decode('ascii')}",
			"detail": "high",
		},
	}


def _validateResponseStatus(apiResult: dict[str, Any]) -> None:
	error = apiResult.get("error")
	if isinstance(error, dict) and error.get("message"):
		raise ApiError(_("DeepSeek API error: {}").format(error["message"]))
	choices = apiResult.get("choices")
	if not isinstance(choices, list) or not choices:
		raise ApiError(_("DeepSeek returned an invalid response."))
	choice = choices[0]
	if not isinstance(choice, dict):
		raise ApiError(_("DeepSeek returned an invalid response."))
	finishReason = choice.get("finish_reason")
	if finishReason == "length":
		raise ApiError(_("DeepSeek response was truncated at the token limit."))
	if finishReason == "content_filter":
		raise ApiError(_("DeepSeek response was blocked by content filtering."))
	if finishReason not in {None, "stop"}:
		raise ApiError(_("DeepSeek returned an invalid response."))


def _extractResponseText(apiResult: dict[str, Any]) -> str:
	choices = apiResult.get("choices")
	if not isinstance(choices, list) or not choices:
		return ""
	choice = choices[0]
	if not isinstance(choice, dict):
		return ""
	message = choice.get("message")
	if not isinstance(message, dict):
		return ""
	content = message.get("content")
	if isinstance(content, str):
		return content.strip()
	if not isinstance(content, list):
		return ""
	text = "".join(
		str(part.get("text") or "")
		for part in content
		if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
	).strip()
	return text


def _redactForLog(value: Any) -> Any:
	value = redactDeepSeekImageUrlsForLog(value)
	from ..recogHandler import _redactRequestParamsForLog

	return _redactRequestParamsForLog(value)
