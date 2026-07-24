# Copyright (C) 2025 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An image description engine that uses the Google Gemini API."""

import addonHandler
from collections.abc import Callable, Iterator
from typing import List, Any

from .. import network
from ..conversation import QuestionStreamFinished, QuestionStreamText
from ..engineGUIHelper import BooleanEngineSetting, ChoiceEngineSetting, TextInputEngineSetting
from ..exceptions import ApiError, AuthenticationError, StreamIncompleteError
from ._googleGenerativeLanguage import (
	buildConversationContents,
	buildGenerateContentUrl,
	extractTextFromCandidate,
	extractTextFromResponse,
	getApiErrorMessage,
	getCandidateFinishError,
	getPromptFeedbackError,
	parseSseDataChunk,
)
from ..recogHandler import BaseDescriber, RecognitionRequest
from ..geminiModels import (
	DEFAULT_GEMINI_MEDIA_RESOLUTION,
	DEFAULT_GEMINI_MODEL,
	getGeminiMediaResolutionChoices,
	getGeminiModelChoices,
)

addonHandler.initTranslation()


class CustomContentRecognizer(BaseDescriber):
	"""An image description engine that uses the Google Gemini."""

	name = "gemini"
	# Translators: The description of the Google Gemini engine.
	description = _("Google Gemini")

	# --- API Configuration ---
	uploadBase64EncodeImage = True
	supportsStreaming = True
	supportsQuestions = True
	supportsQuestionStreaming = True
	isStreaming = False
	uploadImageFormat = "JPEG"

	# --- Engine Default Settings ---
	_apiKey: str = ""
	_useStreaming: bool = False
	_model: str = DEFAULT_GEMINI_MODEL
	_mediaResolution: str = DEFAULT_GEMINI_MEDIA_RESOLUTION
	# Translators: This is the default prompt sent to the Gemini model.
	# It guides the model to provide an objective description.
	_prompt: str = _(
		"Describe this image objectively. Include all visible text (if none, do not mention it). "
		"Do not include any introductory phrases. "
		"Avoid subjective descriptions like 'it seems...' or 'it gives a feeling of...'.",
	)

	@property
	def supportedSettings(self) -> List[Any]:
		"""
		Define the user-configurable settings for this engine.

		:returns: A list of `EngineSetting` objects.
		"""
		return [
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the text field to enter the Gemini API Key.
				displayNameWithAccelerator=_("API &Key"),
			),
			BooleanEngineSetting(
				"useStreaming",
				# Translators: The label for an engine setting to enable streaming output.
				_("&Enable streaming output (if available)"),
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for a setting to select the Gemini model.
				displayNameWithAccelerator=_("&Model"),
				optionsPropertyName="availableModels",
			),
			self.autoRecognitionModelSetting(),
			ChoiceEngineSetting(
				name="mediaResolution",
				# Translators: The label for a setting to control image detail sent to Gemini.
				displayNameWithAccelerator=_("Media &resolution"),
				optionsPropertyName="availableMediaResolutions",
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
	def useStreaming(self) -> bool:
		return self._useStreaming

	@useStreaming.setter
	def useStreaming(self, value: bool) -> None:
		"""
		Sets whether streaming is enabled for simple text output.

		:param value: The new boolean value.
		"""
		self._useStreaming = value
		self.isStreaming = value

	@property
	def model(self) -> str:
		return self._model

	@model.setter
	def model(self, value: str) -> None:
		if value in self.availableModels:
			self._model = value

	@property
	def prompt(self) -> str:
		return self._prompt

	@prompt.setter
	def prompt(self, value: str) -> None:
		self._prompt = value

	@property
	def mediaResolution(self) -> str:
		return self._mediaResolution

	@mediaResolution.setter
	def mediaResolution(self, value: str) -> None:
		if value in self.availableMediaResolutions:
			self._mediaResolution = value

	@property
	def availableModels(self) -> dict:
		"""
		Provides the items for the 'model' choice control in the settings UI.

		:returns: A dictionary of model IDs to display names.
		"""
		return self.generateStringSettings(getGeminiModelChoices())

	@property
	def availableMediaResolutions(self) -> dict:
		"""
		Provides the items for the media resolution choice control in the settings UI.

		:returns: A dictionary of media resolution IDs to display names.
		"""
		return self.generateStringSettings(getGeminiMediaResolutionChoices())

	@classmethod
	def check(cls) -> bool:
		"""
		Checks if the engine is available.

		:returns: This engine is always considered available.
		"""
		return True

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		"""
		Builds the parameters dictionary for the network request.

		:param imageContent: The raw byte content of the image, base64 encoded.
		:returns: A dictionary of parameters for `requests`.
		:raises AuthenticationError: If the API key is not configured.
		"""
		if not self.apiKey:
			# Translators: An error message if the Gemini API key is missing.
			raise AuthenticationError(
				_("API Key is missing. Please configure it in the Gemini engine settings."),
			)

		url = buildGenerateContentUrl(self.model, request.streamResult)
		headers = {
			"Content-Type": "application/json",
			"x-goog-api-key": self.apiKey,
		}

		imageBase64String = imageContent.decode("utf-8")

		payload = {
			"contents": [
				{
					"parts": [
						{"inline_data": {"mime_type": "image/jpeg", "data": imageBase64String}},
						{"text": self.prompt},
					],
				},
			],
			"generationConfig": {
				"mediaResolution": self.mediaResolution,
			},
		}

		return {
			"method": "POST",
			"url": url,
			"headers": headers,
			"json": payload,
		}

	def processStreamChunk(self, chunk: bytes, request: RecognitionRequest) -> str | None:
		"""
		Extracts text from one Gemini streaming response line.

		:param chunk: One raw line from the streaming HTTP response.
		:param request: The request-local recognition options.
		:returns: New model text, or None when the line does not contain text.
		:raises ApiError: If the stream reports a service error.
		"""
		apiResult = parseSseDataChunk(chunk, "Gemini")
		if not apiResult:
			return None
		apiError = getApiErrorMessage(apiResult)
		if apiError:
			# Translators: An error message returned from the Gemini API.
			raise ApiError(_("Gemini API Error: {}").format(apiError))
		if not apiResult.get("candidates"):
			promptFeedbackError = getPromptFeedbackError(apiResult)
			if promptFeedbackError:
				raise ApiError(promptFeedbackError)
			return None
		candidate = apiResult["candidates"][0]
		if not isinstance(candidate, dict):
			return None
		text = extractTextFromCandidate(candidate, strip=False)
		finishError = getCandidateFinishError(candidate, allowMissing=True)
		if finishError:
			raise StreamIncompleteError(finishError, partialText=text)
		return text

	def _buildQuestionRequestParams(self, context: Any, question: str, stream: bool) -> dict:
		if not self.apiKey:
			# Translators: An error message if the Gemini API key is missing.
			raise AuthenticationError(
				_("API Key is missing. Please configure it in the Gemini engine settings."),
			)
		image = self._getConversationImage(context)
		imageContent = self.prepareImageContentFromImage(image)
		imageBase64String = imageContent.decode("utf-8")
		headers = {
			"Content-Type": "application/json",
			"x-goog-api-key": self.apiKey,
		}
		payload = {
			"contents": buildConversationContents(
				{"inline_data": {"mime_type": "image/jpeg", "data": imageBase64String}},
				self.prompt,
				context.initialText,
				context.turns,
				question,
			),
			"generationConfig": {
				"mediaResolution": self.mediaResolution,
			},
		}
		return {
			"method": "POST",
			"url": buildGenerateContentUrl(self.model, stream=stream),
			"headers": headers,
			"json": payload,
		}

	def askQuestion(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> str:
		"""
		Asks Gemini a follow-up question about the previous image description.

		:param context: The conversation context created from recognition history.
		:param question: The user's follow-up question.
		:param cancellationChecker: Optional callback that raises when cancelled.
		:returns: Gemini's answer.
		"""
		if not self.apiKey:
			# Translators: An error message if the Gemini API key is missing.
			raise AuthenticationError(
				_("API Key is missing. Please configure it in the Gemini engine settings."),
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

	def askQuestionStream(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> Iterator[QuestionStreamText | QuestionStreamFinished]:
		self._checkQuestionCancelled(cancellationChecker)
		request = RecognitionRequest(textResult=True, streamResult=True)
		requestParams = self._buildQuestionRequestParams(context, question, stream=True)
		self._checkQuestionCancelled(cancellationChecker)
		yield from self._iterQuestionStreamingResponse(requestParams, request, cancellationChecker)

	def processApiResult(self, result: bytes) -> str | bool:
		"""
		Handles error checking from the API response.

		:param result: The raw byte response from the server.
		:returns: An error message string on failure, or False on success.
		"""
		responseJson = self._convertToJson(result)
		if not isinstance(responseJson, dict):
			# Translators: An error message for malformed Gemini response data.
			return _("Server returned an invalid response.")
		if "error" in responseJson:
			errorMessage = getApiErrorMessage(responseJson) or "Unknown API error"
			# Translators: An error message returned from the Gemini API.
			return _("Gemini API Error: {}").format(errorMessage)
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
		return extractTextFromResponse(apiResult)

	def _convertToLineResultFormat(self, apiResult: dict) -> list:
		"""
		Converts the API response into NVDA's rich format.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: A list of lines, where each line contains a single word dictionary.
		"""
		text = self.extractText(apiResult)
		if not text:
			return []
		# Image describers return a single block of text, so we wrap it in the required format.
		return [[{"x": 0, "y": 0, "width": 1, "height": 1, "text": text}]]
