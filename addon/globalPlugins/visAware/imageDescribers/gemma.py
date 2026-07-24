# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An image description engine that uses the Google Gemma API."""

from collections import OrderedDict
from collections.abc import Callable
import json
from typing import Any

import addonHandler
import config
from logHandler import log

from .. import network
from ..engineGUIHelper import (
	ChoiceEngineSetting,
	TextInputEngineSetting,
)
from ..exceptions import ApiError, AuthenticationError
from ..recogHandler import BaseDescriber, RecognitionRequest
from ._googleGenerativeLanguage import (
	buildConversationContents,
	buildGenerateContentUrl,
	extractTextFromCandidate,
	extractTextFromResponse,
	getApiErrorMessage,
	getCandidateFinishError,
	getPromptFeedbackError,
)

addonHandler.initTranslation()


INLINE_IMAGE_MIME_TYPE = "image/jpeg"
RECOMMENDED_TEMPERATURE = 100
RECOMMENDED_TOP_P = 95
RECOMMENDED_TOP_K = 64
LOG_TEXT_PREVIEW_CHARS = 500
RESPONSE_SCHEMA = {
	"type": "object",
	"properties": {
		"answer": {
			"type": "string",
		},
	},
	"required": [
		"answer",
	],
}
SYSTEM_INSTRUCTION = (
	"You are an image description engine for a screen reader. "
	"Return only the final user-facing answer in the JSON answer field. "
	"Do not include reasoning, planning, drafts, internal notes, task restatements, constraints lists, "
	"translations for internal understanding, or analysis labels. "
	"Do not say what you need to do. "
	"Follow the language of the user's image-description request."
)
LEGACY_DEFAULT_PROMPT_EN = (
	"Describe this image objectively. Include all visible text (if none, do not mention it). "
	"Do not include any introductory phrases. "
	"Avoid subjective descriptions like 'it seems...' or 'it gives a feeling of...'."
)
LEGACY_DEFAULT_PROMPT = _(LEGACY_DEFAULT_PROMPT_EN)
if not LEGACY_DEFAULT_PROMPT.strip():
	LEGACY_DEFAULT_PROMPT = LEGACY_DEFAULT_PROMPT_EN
DEFAULT_PROMPT_EN = (
	"Describe this image objectively. Output only the final image description. "
	"Do not include reasoning, planning, internal notes, task restatements, constraints lists, "
	"translations for internal understanding, or introductory phrases. "
	"Include all visible text (if none, do not mention it). "
	"Avoid subjective descriptions like 'it seems...' or 'it gives a feeling of...'."
)
DEFAULT_PROMPT = _(
	"Describe this image objectively. Output only the final image description. "
	"Do not include reasoning, planning, internal notes, task restatements, constraints lists, "
	"translations for internal understanding, or introductory phrases. "
	"Include all visible text (if none, do not mention it). "
	"Avoid subjective descriptions like 'it seems...' or 'it gives a feeling of...'.",
)
if not DEFAULT_PROMPT.strip():
	DEFAULT_PROMPT = DEFAULT_PROMPT_EN


def _isVerboseDebugLoggingEnabled() -> bool:
	try:
		return bool(config.conf["visAwareGeneral"]["verboseDebugLogging"])
	except (KeyError, AttributeError, TypeError):
		return False


def _previewTextForLog(text: str, limit: int = LOG_TEXT_PREVIEW_CHARS) -> str:
	text = text.replace("\r", "\\r").replace("\n", "\\n")
	if len(text) <= limit:
		return text
	return f"{text[:limit]}...[{len(text)} chars]"


def _buildSystemInstruction() -> dict[str, Any]:
	return {
		"parts": [
			{
				"text": SYSTEM_INSTRUCTION,
			},
		],
	}


class CustomContentRecognizer(BaseDescriber):
	"""An image description engine that uses the Google Gemma API."""

	name = "gemma"
	# Translators: The description of the Google Gemma engine.
	description = _("Google Gemma")

	# --- API Configuration ---
	uploadBase64EncodeImage = True
	supportsStreaming = False
	supportsQuestions = True
	supportsQuestionStreaming = False
	isStreaming = False
	uploadImageFormat = "JPEG"

	# --- Engine Default Settings ---
	_apiKey: str = ""
	_model: str = "gemma-4-26b-a4b-it"
	_thinkingLevel: str = "off"
	# Translators: This is the default prompt sent to the Gemma model.
	# It guides the model to provide only an objective final description.
	_prompt: str = DEFAULT_PROMPT

	@property
	def supportedSettings(self) -> list[Any]:
		"""
		Define the user-configurable settings for this engine.

		:returns: A list of `EngineSetting` objects.
		"""
		return [
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the text field to enter the Gemma API Key.
				displayNameWithAccelerator=_("API &Key"),
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for a setting to select the Gemma model.
				displayNameWithAccelerator=_("&Model"),
				optionsPropertyName="availableModels",
			),
			self.autoRecognitionModelSetting(),
			ChoiceEngineSetting(
				name="thinkingLevel",
				# Translators: The label for a setting to control Gemma thinking mode.
				displayNameWithAccelerator=_("Thinking &Level"),
				optionsPropertyName="availableThinkingLevels",
			),
			self.imageQualitySetting(),
			TextInputEngineSetting(
				name="prompt",
				# Translators: The label for a setting to customize the prompt for the vision model.
				displayNameWithAccelerator=_("&Custom Prompt"),
			),
			self.autoRecognitionPromptSetting(),
		]

	@property
	def apiKey(self) -> str:
		return self._apiKey

	@apiKey.setter
	def apiKey(self, value: str) -> None:
		self._apiKey = value

	@property
	def model(self) -> str:
		return self._model

	@model.setter
	def model(self, value: str) -> None:
		if value in self.availableModels:
			self._model = value

	@property
	def thinkingLevel(self) -> str:
		return self._thinkingLevel

	@thinkingLevel.setter
	def thinkingLevel(self, value: str) -> None:
		if value in self.availableThinkingLevels:
			self._thinkingLevel = value

	@property
	def prompt(self) -> str:
		return self._prompt

	@prompt.setter
	def prompt(self, value: str) -> None:
		self._prompt = value

	@property
	def availableModels(self) -> dict:
		"""
		Provides the items for the 'model' choice control in the settings UI.

		:returns: A dictionary of model IDs to display names.
		"""
		models = OrderedDict(
			{
				"gemma-4-26b-a4b-it": _("Gemma 4 26B A4B IT (Recommended, faster)"),
				"gemma-4-31b-it": _("Gemma 4 31B IT (Higher quality, slower)"),
			},
		)
		return self.generateStringSettings(models)

	@property
	def availableThinkingLevels(self) -> dict:
		"""
		Provides the items for the thinking level choice control in the settings UI.

		:returns: A dictionary of thinking level IDs to display names.
		"""
		levels = OrderedDict(
			{
				"off": _("Off (faster)"),
				"high": _("High (better reasoning, slower)"),
			},
		)
		return self.generateStringSettings(levels)

	@classmethod
	def check(cls) -> bool:
		"""
		Checks if the engine is available.

		:returns: This engine is always considered available.
		"""
		return True

	def loadSettings(self, onlyChanged: bool = False) -> None:
		super().loadSettings(onlyChanged=onlyChanged)
		if not self.prompt.strip() or self.prompt in {LEGACY_DEFAULT_PROMPT_EN, LEGACY_DEFAULT_PROMPT}:
			self.prompt = DEFAULT_PROMPT

	def _logRequestDiagnostics(
		self,
		imageContent: bytes,
		mimeType: str,
		payload: dict[str, Any],
		stream: bool,
		purpose: str,
	) -> None:
		if not _isVerboseDebugLoggingEnabled():
			return
		promptPreview = _previewTextForLog(self.prompt)
		promptKind = "custom" if self.prompt != DEFAULT_PROMPT else "default"
		log.debug(
			"Gemma request details: "
			f"purpose={purpose}, model={self.model}, stream={stream}, mimeType={mimeType}, "
			f"base64ImageBytes={len(imageContent)}, generationConfig={payload.get('generationConfig')!r}, "
			f"systemInstructionChars={len(SYSTEM_INSTRUCTION)}, promptKind={promptKind}, "
			f"promptChars={len(self.prompt)}, "
			f"promptPreview={promptPreview!r}",
		)

	def _logResponseDiagnostics(self, responseJson: dict[str, Any]) -> None:
		if not _isVerboseDebugLoggingEnabled():
			return
		candidates = responseJson.get("candidates")
		promptFeedback = responseJson.get("promptFeedback")
		if not isinstance(candidates, list) or not candidates:
			log.debug(
				"Gemma response details: "
				f"candidates=0, promptFeedback={promptFeedback!r}, keys={list(responseJson.keys())!r}",
			)
			return
		candidate = candidates[0]
		if not isinstance(candidate, dict):
			log.debug(
				"Gemma response details: "
				f"candidates={len(candidates)}, firstCandidateType={type(candidate).__name__}",
			)
			return
		text = extractTextFromCandidate(candidate, strip=False)
		content = candidate.get("content", {})
		parts = content.get("parts", []) if isinstance(content, dict) else []
		partCount = len(parts) if isinstance(parts, list) else 0
		log.debug(
			"Gemma response details: "
			f"candidates={len(candidates)}, finishReason={candidate.get('finishReason')!r}, "
			f"partCount={partCount}, textChars={len(text)}, "
			f"textPreview={_previewTextForLog(text)!r}",
		)

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		"""
		Builds the parameters dictionary for the network request.

		:param imageContent: The base64 encoded byte content of the image.
		:returns: A dictionary of parameters for `requests`.
		:raises AuthenticationError: If the API key is not configured.
		"""
		if not self.apiKey:
			# Translators: An error message if the Gemma API key is missing.
			raise AuthenticationError(
				_("API Key is missing. Please configure it in the Gemma engine settings."),
			)

		url = buildGenerateContentUrl(self.model, request.streamResult)
		headers = {
			"Content-Type": "application/json",
			"x-goog-api-key": self.apiKey,
		}
		imagePart = self._buildInlineImagePart(imageContent)
		payload: dict[str, Any] = {
			"systemInstruction": _buildSystemInstruction(),
			"contents": [
				{
					"parts": [
						imagePart,
						{"text": self.prompt},
					],
				},
			],
			"generationConfig": self._buildGenerationConfig(),
		}

		log.debug(
			f"Gemma request prepared for model {self.model}: "
			f"thinkingLevel={self.thinkingLevel}, generationConfig={payload['generationConfig']!r}",
		)
		self._logRequestDiagnostics(
			imageContent=imageContent,
			mimeType=INLINE_IMAGE_MIME_TYPE,
			payload=payload,
			stream=request.streamResult,
			purpose="recognition",
		)

		return {
			"method": "POST",
			"url": url,
			"headers": headers,
			"json": payload,
		}

	def _buildInlineImagePart(self, imageContent: bytes) -> dict[str, Any]:
		"""
		Builds a Gemini API inline image part from base64 encoded image content.

		:param imageContent: The base64 encoded byte content of the image.
		:returns: A GenerateContent image part using inline_data.
		"""
		return {
			"inline_data": {
				"mime_type": INLINE_IMAGE_MIME_TYPE,
				"data": imageContent.decode("utf-8"),
			},
		}

	def askQuestion(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> str:
		"""
		Asks Gemma a follow-up question about the previous image description.

		:param context: The conversation context created from recognition history.
		:param question: The user's follow-up question.
		:param cancellationChecker: Optional callback that raises when cancelled.
		:returns: Gemma's answer.
		"""
		if not self.apiKey:
			# Translators: An error message if the Gemma API key is missing.
			raise AuthenticationError(
				_("API Key is missing. Please configure it in the Gemma engine settings."),
			)
		self._checkQuestionCancelled(cancellationChecker)
		requestParams = self._buildQuestionRequestParams(context, question, stream=False)
		self._checkQuestionCancelled(cancellationChecker)
		response = network.sendRequest(**requestParams)
		self._checkQuestionCancelled(cancellationChecker)
		apiErrorMessage = self.processApiResult(response.content)
		if apiErrorMessage:
			raise ApiError(str(apiErrorMessage))
		apiResult = self._convertToJson(response.content)
		return self._validateQuestionAnswer(self.extractText(apiResult))

	def _buildQuestionRequestParams(self, context: Any, question: str, stream: bool) -> dict:
		if not self.apiKey:
			# Translators: An error message if the Gemma API key is missing.
			raise AuthenticationError(
				_("API Key is missing. Please configure it in the Gemma engine settings."),
			)
		image = self._getConversationImage(context)
		imageContent = self.prepareImageContentFromImage(image)
		imagePart = self._buildInlineImagePart(imageContent)
		headers = {
			"Content-Type": "application/json",
			"x-goog-api-key": self.apiKey,
		}
		payload = {
			"systemInstruction": _buildSystemInstruction(),
			"contents": buildConversationContents(
				imagePart,
				self.prompt,
				context.initialText,
				context.turns,
				question,
			),
			"generationConfig": self._buildGenerationConfig(),
		}
		self._logRequestDiagnostics(
			imageContent=imageContent,
			mimeType=INLINE_IMAGE_MIME_TYPE,
			payload=payload,
			stream=stream,
			purpose="follow-up",
		)
		return {
			"method": "POST",
			"url": buildGenerateContentUrl(self.model, stream=stream),
			"headers": headers,
			"json": payload,
		}

	def _buildGenerationConfig(self) -> dict[str, Any]:
		"""
		Builds the generationConfig payload for Gemma.

		:returns: A JSON-serializable generation configuration dictionary.
		"""
		generationConfig: dict[str, Any] = {
			"temperature": RECOMMENDED_TEMPERATURE / 100.0,
			"topP": RECOMMENDED_TOP_P / 100.0,
			"topK": RECOMMENDED_TOP_K,
			"responseMimeType": "application/json",
			"responseSchema": RESPONSE_SCHEMA,
		}
		if self.thinkingLevel == "high":
			generationConfig["thinkingConfig"] = {
				"thinkingLevel": "high",
			}
		return generationConfig

	def processApiResult(self, result: bytes) -> str | bool:
		"""
		Handles error checking from the API response.

		:param result: The raw byte response from the server.
		:returns: An error message string on failure, or False on success.
		"""
		try:
			responseJson = self._convertToJson(result)
		except json.JSONDecodeError:
			# Translators: An error message for malformed Gemma response data.
			return _("Server returned an invalid response.")
		if not isinstance(responseJson, dict):
			# Translators: An error message for malformed Gemma response data.
			return _("Server returned an invalid response.")
		self._logResponseDiagnostics(responseJson)
		if "error" in responseJson:
			errorMessage = getApiErrorMessage(responseJson) or "Unknown API error"
			# Translators: An error message returned from the Gemma API.
			return _("Gemma API Error: {}").format(errorMessage)
		if not responseJson.get("candidates"):
			promptFeedbackError = getPromptFeedbackError(responseJson)
			if promptFeedbackError:
				return promptFeedbackError
			# Translators: An error message for an empty but successful server response.
			return _("Server returned a successful but empty response.")
		candidate = responseJson["candidates"][0]
		if isinstance(candidate, dict):
			finishError = getCandidateFinishError(candidate)
			if finishError:
				return finishError
		return False  # Indicates success

	def extractText(self, apiResult: dict) -> str:
		"""
		Extracts the plain text result from the parsed API response.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: The recognized text as a single string.
		"""
		text = extractTextFromResponse(apiResult)
		try:
			structuredResult = json.loads(text)
		except json.JSONDecodeError:
			return text
		if not isinstance(structuredResult, dict):
			return text
		answer = structuredResult.get("answer")
		if isinstance(answer, str):
			return answer.strip()
		return text
