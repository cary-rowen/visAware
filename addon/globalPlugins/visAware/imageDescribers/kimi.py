# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Kimi image description through the OpenAI-compatible Chat API."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
import json
from typing import Any

import addonHandler

from .. import network
from ..conversation import QuestionStreamFinished, QuestionStreamText
from ..engineGUIHelper import BooleanEngineSetting, ChoiceEngineSetting, TextInputEngineSetting
from ..exceptions import ApiError, AuthenticationError, StreamIncompleteError
from ..kimiAPI import (
	KimiStreamState,
	extractKimiAssistantMessage,
	getKimiMessageText,
	parseKimiResponse,
	validateKimiCompletion,
)
from ..kimiModels import (
	DEFAULT_KIMI_BASE_URL,
	DEFAULT_KIMI_MODEL,
	KIMI_REASONING_EFFORTS,
	applyKimiThinking,
	buildKimiChatCompletionsUrl,
	getDefaultKimiModel,
	getKimiContextWindow,
	getKimiModelChoices,
	getKimiReasoningEffortChoices,
	isKimiK26Model,
	isKimiK3Model,
	normalizeKimiReasoningEffort,
	normalizeKimiReasoningEffortForModel,
	requiresKimiReasoningContent,
	requiresPreservedThinking,
)
from ..recogHandler import BaseDescriber, RecognitionRequest
from ._prompts import DEFAULT_IMAGE_DESCRIPTION_PROMPT

addonHandler.initTranslation()

KIMI_REQUEST_TIMEOUT = 180
KIMI_MAX_COMPLETION_TOKENS = 32_768
KIMI_CONTEXT_SAFETY_TOKENS = 4_096
KIMI_FALLBACK_IMAGE_TOKENS = 32_768

# Translators: The default prompt sent to Kimi for an image description.
DEFAULT_KIMI_PROMPT = DEFAULT_IMAGE_DESCRIPTION_PROMPT


class CustomContentRecognizer(BaseDescriber):
	"""An image description engine that uses Kimi's Chat Completions API."""

	name = "kimi"
	# Translators: The description of the Kimi engine. "Moonshot AI" is a proper noun
	# and should not be translated.
	description = _("Kimi (Moonshot AI)")

	uploadBase64EncodeImage = True
	supportsStreaming = True
	supportsQuestions = True
	supportsQuestionStreaming = True
	isStreaming = False
	uploadImageFormat = "JPEG"

	_apiKey: str = ""
	_baseUrl: str = DEFAULT_KIMI_BASE_URL
	_useStreaming: bool = False
	_model: str = DEFAULT_KIMI_MODEL
	_reasoningEffort: str = "high"
	_prompt: str = DEFAULT_KIMI_PROMPT

	@property
	def supportedSettings(self) -> list[Any]:
		return [
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the text field to enter the Kimi API key.
				displayNameWithAccelerator=_("API &key"),
			),
			TextInputEngineSetting(
				name="baseUrl",
				# Translators: The label for the text field to enter the Kimi API base URL.
				displayNameWithAccelerator=_("Base &URL"),
				refreshSettingsOnChange=True,
			),
			BooleanEngineSetting(
				"useStreaming",
				# Translators: The label for an engine setting to enable streaming output.
				_("&Enable streaming output (if available)"),
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for a setting to select the Kimi model.
				displayNameWithAccelerator=_("&Model"),
				optionsPropertyName="availableModels",
			),
			self.autoRecognitionModelSetting(),
			ChoiceEngineSetting(
				name="reasoningEffort",
				# Translators: The label for Kimi thinking effort.
				displayNameWithAccelerator=_("&Thinking effort"),
				optionsPropertyName="reasoningEffortChoices",
			),
			self.imageQualitySetting(),
			TextInputEngineSetting(
				name="prompt",
				# Translators: The label for a setting to customize the Kimi prompt.
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
		self._baseUrl = value.strip().rstrip("/") or DEFAULT_KIMI_BASE_URL
		if self.model not in getKimiModelChoices(self._baseUrl):
			self._model = getDefaultKimiModel(self._baseUrl)
			self._normalizeReasoningEffort()

	@property
	def useStreaming(self) -> bool:
		return self._useStreaming

	@useStreaming.setter
	def useStreaming(self, value: bool) -> None:
		self._useStreaming = bool(value)
		self.isStreaming = self._useStreaming

	@property
	def model(self) -> str:
		return self._model

	@model.setter
	def model(self, value: str) -> None:
		value = value.strip()
		self._model = value if value in self.availableModels else getDefaultKimiModel(self.baseUrl)
		self._normalizeReasoningEffort()

	@property
	def reasoningEffort(self) -> str:
		return self._reasoningEffort

	@reasoningEffort.setter
	def reasoningEffort(self, value: str) -> None:
		self._reasoningEffort = normalizeKimiReasoningEffort(value)
		self._normalizeReasoningEffort()

	@property
	def prompt(self) -> str:
		return self._prompt

	@prompt.setter
	def prompt(self, value: str) -> None:
		self._prompt = value.strip() or DEFAULT_KIMI_PROMPT

	@property
	def availableModels(self) -> dict:
		return self.generateStringSettings(getKimiModelChoices(self.baseUrl))

	@property
	def reasoningEffortChoices(self) -> dict:
		choices = getKimiReasoningEffortChoices(self.model)
		return self.generateStringSettings(choices or KIMI_REASONING_EFFORTS)

	@classmethod
	def check(cls) -> bool:
		return True

	def isSupported(self, settingName: str) -> bool:
		if settingName == "reasoningEffort":
			return isKimiK3Model(self.model) or isKimiK26Model(self.model)
		return super().isSupported(settingName)

	def _normalizeReasoningEffort(self) -> None:
		self._reasoningEffort = normalizeKimiReasoningEffortForModel(self.model, self._reasoningEffort)

	def _buildHeaders(self) -> dict[str, str]:
		return {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {self.apiKey}",
		}

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		if not self.apiKey:
			# Translators: An error message if the Kimi API key is missing.
			raise AuthenticationError(
				_("API key is missing. Please configure it in the Kimi engine settings."),
			)
		payload: dict[str, Any] = {
			"model": self.model,
			"max_completion_tokens": KIMI_MAX_COMPLETION_TOKENS,
			"messages": [self._buildInitialUserMessage(imageContent, getattr(request, "prompt", None))],
		}
		applyKimiThinking(payload, self.model, self.reasoningEffort)
		if request.streamResult:
			payload["stream"] = True
			payload["stream_options"] = {"include_usage": True}
		return {
			"method": "POST",
			"url": buildKimiChatCompletionsUrl(self.baseUrl),
			"headers": self._buildHeaders(),
			"json": payload,
			"timeout": KIMI_REQUEST_TIMEOUT,
		}

	def _buildInitialUserMessage(self, imageContent: bytes, prompt: str | None = None) -> dict[str, Any]:
		return {
			"role": "user",
			"content": [
				self._imageBlock(imageContent),
				{"type": "text", "text": prompt if prompt is not None else self.prompt},
			],
		}

	@staticmethod
	def _imageBlock(imageContent: bytes) -> dict[str, Any]:
		return {
			"type": "image_url",
			"image_url": {
				"url": f"data:image/jpeg;base64,{imageContent.decode('ascii')}",
			},
		}

	def _resetStreamingState(self) -> None:
		self._streamState = KimiStreamState()
		self._lastQuestionResponse = None

	def processStreamChunk(self, chunk: bytes, request: RecognitionRequest) -> str | None:
		return self._streamState.consume(chunk)

	def _getStreamingResponse(self, fullResponseText: str) -> dict[str, Any]:
		response = self._streamState.buildResponse()
		_choice, message = validateKimiCompletion(response, {"stop"})
		if not getKimiMessageText(message).strip():
			# Translators: Reported when Kimi completes an image description without any text.
			raise StreamIncompleteError(
				_("Kimi returned a successful but empty response."),
				partialText=fullResponseText,
			)
		self._lastQuestionResponse = deepcopy(response)
		return response

	def _buildQuestionRequestParams(self, context: Any, question: str, stream: bool) -> dict:
		if not self.apiKey:
			# Translators: An error message if the Kimi API key is missing.
			raise AuthenticationError(
				_("API key is missing. Please configure it in the Kimi engine settings."),
			)
		imageContent = self.prepareImageContentFromImage(self._getConversationImage(context))
		initialResponse = getattr(context, "response", None)
		initialUserMessage = self._buildInitialUserMessage(imageContent)
		initialAssistantMessage = self._assistantMessage(
			initialResponse,
			getattr(context, "initialText", ""),
		)
		questionMessage = {"role": "user", "content": question}
		historyTokenBudget = (
			getKimiContextWindow(self.model)
			- KIMI_MAX_COMPLETION_TOKENS
			- KIMI_CONTEXT_SAFETY_TOKENS
			- self._initialExchangeTokenCount(initialResponse, initialAssistantMessage)
			- self._estimatedMessageTokens(questionMessage)
		)
		if historyTokenBudget < 0:
			# Translators: Reported when the required Kimi follow-up context exceeds the model limit.
			raise ApiError(_("The Kimi conversation is too large for the selected model."))
		messages = [initialUserMessage, initialAssistantMessage]
		messages.extend(self._historyMessages(getattr(context, "turns", []), historyTokenBudget))
		messages.append(questionMessage)
		payload: dict[str, Any] = {
			"model": self.model,
			"max_completion_tokens": KIMI_MAX_COMPLETION_TOKENS,
			"messages": messages,
		}
		applyKimiThinking(payload, self.model, self.reasoningEffort)
		if stream:
			payload["stream"] = True
			payload["stream_options"] = {"include_usage": True}
		return {
			"method": "POST",
			"url": buildKimiChatCompletionsUrl(self.baseUrl),
			"headers": self._buildHeaders(),
			"json": payload,
			"timeout": KIMI_REQUEST_TIMEOUT,
		}

	def _assistantMessage(self, response: Any, text: str) -> dict[str, Any]:
		message = extractKimiAssistantMessage(response)
		if message is not None and isinstance(message.get("content"), (str, list)):
			if requiresKimiReasoningContent(self.model, self.reasoningEffort) and not isinstance(
				message.get("reasoning_content"),
				str,
			):
				# Translators: Reported when Kimi cannot continue without preserved reasoning.
				raise ApiError(
					_("Kimi follow-up history is missing the complete assistant response."),
				)
			if not requiresPreservedThinking(self.model, self.reasoningEffort):
				message.pop("reasoning_content", None)
				message.pop("tool_calls", None)
			return message
		if requiresPreservedThinking(self.model, self.reasoningEffort):
			# Translators: Reported when Kimi cannot continue without preserved reasoning.
			raise ApiError(_("Kimi follow-up history is missing the complete assistant response."))
		return {"role": "assistant", "content": str(text)}

	@staticmethod
	def _getHistoryPairs(turns: Any) -> list[tuple[Any, Any]]:
		pairs: list[tuple[Any, Any]] = []
		pendingUser: Any = None
		sourceTurns = turns if isinstance(turns, list) else []
		for turn in sourceTurns:
			role = getattr(turn, "role", "")
			if role == "user":
				pendingUser = turn
			elif role == "assistant" and pendingUser is not None:
				pairs.append((pendingUser, turn))
				pendingUser = None
		return pairs

	@staticmethod
	def _usageTokenCount(response: Any, name: str) -> int | None:
		"""Returns one valid token count from a Kimi response."""
		usage = response.get("usage") if isinstance(response, dict) else None
		value = usage.get(name) if isinstance(usage, dict) else None
		return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

	@staticmethod
	def _estimatedMessageTokens(message: dict[str, Any]) -> int:
		"""Conservatively estimates tokens when Kimi omitted usage."""
		return len(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

	def _assistantTokenCount(self, response: Any, message: dict[str, Any]) -> int:
		completionTokens = self._usageTokenCount(response, "completion_tokens")
		return completionTokens if completionTokens is not None else self._estimatedMessageTokens(message)

	def _initialExchangeTokenCount(self, response: Any, assistantMessage: dict[str, Any]) -> int:
		promptTokens = self._usageTokenCount(response, "prompt_tokens")
		if promptTokens is None:
			promptTokens = KIMI_FALLBACK_IMAGE_TOKENS + self._estimatedMessageTokens(
				{"role": "user", "content": self.prompt},
			)
		return promptTokens + self._assistantTokenCount(response, assistantMessage)

	def _historyMessages(self, turns: Any, tokenBudget: int) -> list[dict[str, Any]]:
		selectedPairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
		for userTurn, assistantTurn in reversed(self._getHistoryPairs(turns)):
			userMessage = {"role": "user", "content": str(getattr(userTurn, "text", ""))}
			assistantResponse = getattr(assistantTurn, "response", None)
			assistantMessage = self._assistantMessage(
				assistantResponse,
				getattr(assistantTurn, "text", ""),
			)
			pairTokens = self._estimatedMessageTokens(userMessage) + self._assistantTokenCount(
				assistantResponse,
				assistantMessage,
			)
			if pairTokens > tokenBudget:
				break
			tokenBudget -= pairTokens
			selectedPairs.append((userMessage, assistantMessage))
		return [message for pair in reversed(selectedPairs) for message in pair]

	def askQuestion(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> str:
		self._checkQuestionCancelled(cancellationChecker)
		response = network.sendRequest(**self._buildQuestionRequestParams(context, question, stream=False))
		self._checkQuestionCancelled(cancellationChecker)
		apiResult = parseKimiResponse(response.content)
		_choice, message = validateKimiCompletion(apiResult, {"stop"})
		answer = self._validateQuestionAnswer(getKimiMessageText(message))
		self._lastQuestionResponse = deepcopy(apiResult)
		return answer

	def _consumeQuestionResponse(self) -> dict[str, Any] | None:
		response = getattr(self, "_lastQuestionResponse", None)
		self._lastQuestionResponse = None
		return deepcopy(response) if isinstance(response, dict) else None

	def askQuestionStream(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> Iterator[QuestionStreamText | QuestionStreamFinished]:
		self._checkQuestionCancelled(cancellationChecker)
		requestParams = self._buildQuestionRequestParams(context, question, stream=True)
		yield from self._iterQuestionStreamingResponse(
			requestParams,
			RecognitionRequest(textResult=True, streamResult=True),
			cancellationChecker,
		)

	def processApiResult(self, result: bytes) -> str | bool:
		try:
			apiResult = parseKimiResponse(result)
			_choice, message = validateKimiCompletion(apiResult, {"stop"})
			if not getKimiMessageText(message).strip():
				# Translators: An error message for an empty response from an image-description server.
				return _("Server returned a successful but empty response.")
		except ApiError as e:
			return str(e)
		return False

	def extractText(self, apiResult: dict) -> str:
		message = extractKimiAssistantMessage(apiResult)
		return getKimiMessageText(message) if message else ""
