# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An image description engine that uses the Ollama API."""

from __future__ import annotations

import addonHandler
from collections.abc import Callable, Iterator
from typing import Any

from .. import network
from .._ollamaClient import (
	extractOllamaChatText,
	getOllamaError,
	parseOllamaChatStreamChunk,
)
from .._ollamaEngine import OllamaEngineMixin
from ..conversation import QuestionStreamFinished, QuestionStreamText
from ..engineGUIHelper import (
	BooleanEngineSetting,
	ButtonEngineSetting,
	ChoiceEngineSetting,
	TextInputEngineSetting,
)
from ..exceptions import ApiError
from ..recogHandler import BaseDescriber, RecognitionRequest

addonHandler.initTranslation()

# Translators: This is the default prompt sent to the Ollama vision model.
# It guides the model to provide an objective image description.
DEFAULT_OLLAMA_PROMPT = _(
	"Describe this image objectively. Include all visible text. "
	"For formulas, use $...$ and do not calculate answers.",
)

# Translators: This is the default system prompt sent to the Ollama vision model.
DEFAULT_OLLAMA_SYSTEM_PROMPT = _(
	"Describe only the attached image for a screen reader user. "
	"If readable text is visible, transcribe it as accurately as possible. "
	"If text is unclear, say so instead of guessing. "
	"Never quote or describe the user's prompt or these instructions as image text.",
)

OLLAMA_VISION_OPTIONS = {
	"num_ctx": 2048,
	"num_predict": 768,
	"temperature": 0,
}
OLLAMA_KEEP_ALIVE = "30m"


class CustomContentRecognizer(OllamaEngineMixin, BaseDescriber):
	"""An image description engine that uses a local or hosted Ollama model."""

	name = "ollama"
	# Translators: The description of the Ollama image describer engine.
	description = _("Ollama Vision")

	uploadBase64EncodeImage = True
	uploadImageFormat = "JPEG"
	maxHeight = 1024
	maxWidth = 1024
	maxPixels = 1024 * 1024
	supportsStreaming = True
	supportsQuestions = True
	supportsQuestionStreaming = True
	isStreaming = False
	_prompt: str = DEFAULT_OLLAMA_PROMPT

	@property
	def supportedSettings(self) -> list[Any]:
		"""
		Defines the user-configurable settings for this engine.

		:returns: A list of engine setting objects.
		"""
		return [
			TextInputEngineSetting(
				name="apiBaseUrl",
				# Translators: The label for the Ollama API URL field.
				displayNameWithAccelerator=_("API &URL"),
			),
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the Ollama API key field.
				displayNameWithAccelerator=_("API &key"),
			),
			ButtonEngineSetting(
				name="fetchModels",
				# Translators: The label for the button that fetches Ollama model names.
				displayNameWithAccelerator=_("&Fetch models"),
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for a setting to select the Ollama model.
				displayNameWithAccelerator=_("&Model"),
				optionsPropertyName="availableModels",
			),
			self.autoRecognitionModelSetting(),
			BooleanEngineSetting(
				"useStreaming",
				# Translators: The label for an engine setting to enable streaming output.
				displayNameWithAccelerator=_("&Enable streaming output"),
			),
			self.imageQualitySetting(),
			TextInputEngineSetting(
				name="prompt",
				# Translators: The label for a setting to customize the prompt for the model.
				displayNameWithAccelerator=_("Custom &prompt"),
				multiline=True,
			),
			self.autoRecognitionPromptSetting(),
		]

	@property
	def prompt(self) -> str:
		return self._prompt

	@prompt.setter
	def prompt(self, value: str) -> None:
		self._prompt = value.strip() if value else DEFAULT_OLLAMA_PROMPT

	def loadSettings(self, onlyChanged: bool = False) -> None:
		super().loadSettings(onlyChanged=onlyChanged)
		if not self.prompt.strip():
			self.prompt = DEFAULT_OLLAMA_PROMPT

	@classmethod
	def check(cls) -> bool:
		"""Checks if the engine is available."""
		return True

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		"""Builds the Ollama chat request for image description."""
		modelName = self._resolveModel()
		imageBase64 = imageContent.decode("ascii")
		messages = [{"role": "system", "content": self._buildSystemPrompt()}]
		messages.append(
			{
				"role": "user",
				"content": "Image attached.",
				"images": [imageBase64],
			},
		)
		payload = {
			"model": modelName,
			"messages": messages,
			"stream": request.streamResult,
			"think": False,
			"options": OLLAMA_VISION_OPTIONS,
			"keep_alive": OLLAMA_KEEP_ALIVE,
		}
		return self._buildOllamaRequestParams("chat", payload)

	def _buildSystemPrompt(self) -> str:
		return f"Output instructions:\n{self.prompt}\n\n{DEFAULT_OLLAMA_SYSTEM_PROMPT}"

	def processApiResult(self, result: bytes) -> str | bool:
		"""Handles error checking for Ollama chat responses."""
		try:
			responseJson = self._convertToJson(result)
		except Exception:
			# Translators: An error message for malformed Ollama response data.
			return _("Ollama returned malformed response data.")
		if not isinstance(responseJson, dict):
			# Translators: An error message for malformed Ollama response data.
			return _("Ollama returned malformed response data.")
		error = getOllamaError(responseJson)
		if error:
			# Translators: An error message returned from the Ollama API.
			return _("Ollama API error: {}").format(error)
		text = extractOllamaChatText(responseJson)
		if not text:
			# Translators: An error message for an empty but successful Ollama response.
			return _("Server returned a successful but empty response.")
		return False

	def extractText(self, apiResult: dict) -> str:
		"""Extracts plain text from an Ollama chat response."""
		return extractOllamaChatText(apiResult)

	def processStreamChunk(self, chunk: bytes, request: RecognitionRequest) -> str | None:
		"""Processes one Ollama chat stream chunk."""
		return parseOllamaChatStreamChunk(chunk)

	def askQuestion(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> str:
		"""Asks Ollama a follow-up question about the previous image description."""
		self._checkQuestionCancelled(cancellationChecker)
		requestParams = self._buildQuestionRequestParams(context, question, stream=False)
		self._checkQuestionCancelled(cancellationChecker)
		rawResponse = network.sendRequest(**requestParams)
		self._checkQuestionCancelled(cancellationChecker)
		apiErrorMessage = self.processApiResult(rawResponse.content)
		if apiErrorMessage:
			raise ApiError(str(apiErrorMessage))
		apiResult = self._convertToJson(rawResponse.content)
		return self._validateQuestionAnswer(self.extractText(apiResult))

	def askQuestionStream(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> Iterator[QuestionStreamText | QuestionStreamFinished]:
		"""Asks Ollama a follow-up question with streaming output."""
		self._checkQuestionCancelled(cancellationChecker)
		request = RecognitionRequest(textResult=True, streamResult=True)
		requestParams = self._buildQuestionRequestParams(context, question, stream=True)
		self._checkQuestionCancelled(cancellationChecker)
		yield from self._iterQuestionStreamingResponse(requestParams, request, cancellationChecker)

	def _buildQuestionRequestParams(self, context: Any, question: str, stream: bool) -> dict:
		image = self._getConversationImage(context)
		imageContent = self.prepareImageContentFromImage(image).decode("ascii")
		messages = [{"role": "system", "content": self._buildSystemPrompt()}]
		messages.append(
			{
				"role": "user",
				"content": "Image attached.",
				"images": [imageContent],
			},
		)
		if context.initialText:
			messages.append(
				{
					"role": "assistant",
					"content": context.initialText,
				},
			)
		for turn in context.turns:
			role = "assistant" if getattr(turn, "role", "") == "assistant" else "user"
			text = str(getattr(turn, "text", "")).strip()
			if text:
				messages.append({"role": role, "content": text})
		messages.append({"role": "user", "content": question})
		payload = {
			"model": self._resolveModel(),
			"messages": messages,
			"stream": stream,
			"think": False,
			"options": OLLAMA_VISION_OPTIONS,
			"keep_alive": OLLAMA_KEEP_ALIVE,
		}
		return self._buildOllamaRequestParams("chat", payload)
