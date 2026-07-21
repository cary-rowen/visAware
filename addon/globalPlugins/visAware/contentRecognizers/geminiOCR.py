# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An OCR engine that uses Gemini structured output."""

import json
from typing import Any

import addonHandler
from logHandler import log
from PIL import Image

from ..engineGUIHelper import ChoiceEngineSetting, TextInputEngineSetting
from ..exceptions import ApiError, AuthenticationError
from ..geminiModels import DEFAULT_GEMINI_MODEL, getGeminiLowLatencyThinkingConfig, getGeminiModelChoices
from ..recogHandler import BaseRecognizer, RecognitionRequest

addonHandler.initTranslation()


NORMALIZED_COORDINATE_SCALE = 1000

OCR_RESULT_SCHEMA: dict[str, Any] = {
	"type": "object",
	"properties": {
		"lines": {
			"type": "array",
			"description": "Text lines in natural reading order.",
			"items": {
				"type": "object",
				"properties": {
					"words": {
						"type": "array",
						"description": "Localized OCR text runs in left-to-right or natural script order.",
						"items": {
							"type": "object",
							"properties": {
								"text": {
									"type": "string",
									"description": "The exact visible text in this localized run.",
								},
								"box2d": {
									"type": "array",
									"description": (
										"Bounding box as [ymin, xmin, ymax, xmax], normalized to 0-1000 "
										"relative to the input image."
									),
									"items": {
										"type": "integer",
										"minimum": 0,
										"maximum": NORMALIZED_COORDINATE_SCALE,
									},
									"minItems": 4,
									"maxItems": 4,
								},
							},
							"required": ["text", "box2d"],
							"additionalProperties": False,
							"propertyOrdering": ["text", "box2d"],
						},
					},
				},
				"required": ["words"],
				"additionalProperties": False,
				"propertyOrdering": ["words"],
			},
		},
	},
	"required": ["lines"],
	"additionalProperties": False,
	"propertyOrdering": ["lines"],
}

OCR_PROMPT = (
	"Perform OCR only. Return every visible text line in natural reading order. "
	"For each line, return localized text runs that can be clicked accurately. "
	"For scripts with visible word separators, use words. For scripts without visible word separators, "
	"use the smallest contiguous readable text run you can localize reliably. "
	"Do not describe the image and do not add text that is not visible. "
	"Use bounding boxes in the [ymin, xmin, ymax, xmax] format normalized to 0-1000 "
	"relative to this exact input image. If there is no visible text, return an empty lines array."
)


class CustomContentRecognizer(BaseRecognizer):
	"""Recognizes text using Gemini structured JSON output."""

	name = "geminiOCR"
	# Translators: The description of the Gemini OCR engine.
	description = _("Google Gemini OCR")

	uploadBase64EncodeImage = True
	uploadImageFormat = "JPEG"
	isStreaming = False

	_apiKey: str = ""
	_model: str = DEFAULT_GEMINI_MODEL
	_uploadedImageSize: tuple[int, int] | None = None

	@property
	def supportedSettings(self) -> list[Any]:
		"""
		Define the user-configurable settings for this engine.

		:returns: A list of `EngineSetting` objects.
		"""
		return [
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the text field to enter the Gemini API Key.
				displayNameWithAccelerator=_("API &Key:"),
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for a setting to select the Gemini model.
				displayNameWithAccelerator=_("&Model:"),
				optionsPropertyName="availableModels",
			),
			self.autoRecognitionModelSetting(),
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
		self._model = value

	@property
	def availableModels(self) -> dict:
		"""
		Provides the items for the 'model' choice control in the settings UI.

		:returns: A dictionary of model IDs to display names.
		"""
		return self.generateStringSettings(getGeminiModelChoices())

	@classmethod
	def check(cls) -> bool:
		"""
		Checks if the engine is available.

		:returns: This engine is always considered available.
		"""
		return True

	def _serializeImage(self, pilImage: Image.Image) -> bytes:
		"""
		Serializes the upload image and remembers its dimensions for coordinate conversion.

		:param pilImage: The image being serialized.
		:returns: The serialized image bytes.
		"""
		self._uploadedImageSize = pilImage.size
		return super()._serializeImage(pilImage)

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
				_("API Key is missing. Please configure it in the Gemini OCR engine settings."),
			)

		imageBase64String = imageContent.decode("utf-8")
		url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
		headers = {
			"Content-Type": "application/json",
			"x-goog-api-key": self.apiKey,
		}
		payload = {
			"contents": [
				{
					"parts": [
						{
							"inline_data": {
								"mime_type": "image/jpeg",
								"data": imageBase64String,
							},
						},
						{"text": OCR_PROMPT},
					],
				},
			],
			"generationConfig": {
				"temperature": 0,
				"responseMimeType": "application/json",
				"responseJsonSchema": OCR_RESULT_SCHEMA,
			},
		}
		thinkingConfig = getGeminiLowLatencyThinkingConfig(self.model)
		if thinkingConfig:
			payload["generationConfig"]["thinkingConfig"] = thinkingConfig
		return {
			"method": "POST",
			"url": url,
			"headers": headers,
			"json": payload,
		}

	def processApiResult(self, result: bytes) -> str | bool:
		"""
		Handles error checking from the API response.

		:param result: The raw byte response from the server.
		:returns: An error message string on failure, or False on success.
		"""
		try:
			responseJson = self._convertToJson(result)
			if not isinstance(responseJson, dict):
				# Translators: An error message for malformed Gemini OCR structured data.
				return _("Gemini OCR returned malformed structured data.")
			if "error" in responseJson:
				error = responseJson["error"]
				errorMessage = (
					error.get("message", "Unknown API error") if isinstance(error, dict) else str(error)
				)
				# Translators: An error message returned from the Gemini API.
				return _("Gemini API Error: {}").format(errorMessage)
			self._extractStructuredResult(responseJson)
		except (ApiError, json.JSONDecodeError) as e:
			return str(e)
		return False

	def extractText(self, apiResult: dict) -> str:
		"""
		Extracts the plain text result from the parsed API response.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: The recognized text as a single string.
		"""
		structuredResult = self._extractStructuredResult(apiResult)
		textLines: list[str] = []
		for line in structuredResult["lines"]:
			words = [word["text"].strip() for word in line["words"] if word["text"].strip()]
			if words:
				textLines.append(" ".join(words))
		return "\n".join(textLines)

	def _convertToLineResultFormat(self, apiResult: dict) -> list:
		"""
		Converts the API response into NVDA's rich format with coordinates.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: A list of lines, where each line is a list of word dictionaries.
		"""
		structuredResult = self._extractStructuredResult(apiResult)
		lineResult: list[list[dict[str, Any]]] = []
		for line in structuredResult["lines"]:
			lineWords: list[dict[str, Any]] = []
			for word in line["words"]:
				convertedWord = self._convertStructuredWord(word)
				if convertedWord:
					lineWords.append(convertedWord)
			if lineWords:
				lineResult.append(lineWords)
		return lineResult

	def _cleanup(self) -> None:
		"""Cleans up engine state after recognition."""
		self._uploadedImageSize = None
		super()._cleanup()

	def _extractStructuredResult(self, apiResult: dict) -> dict[str, Any]:
		"""
		Extracts and validates Gemini's schema-constrained OCR JSON.

		:param apiResult: The parsed Gemini response.
		:returns: The parsed structured OCR object.
		:raises ApiError: If the response is empty or does not match the expected structure.
		"""
		if not apiResult.get("candidates"):
			promptFeedback = apiResult.get("promptFeedback", {})
			if promptFeedback.get("blockReason"):
				# Translators: An error message indicating content was blocked by safety filters.
				raise ApiError(_("Content blocked due to safety settings."))
			# Translators: An error message for an empty but successful server response.
			raise ApiError(_("Server returned a successful but empty response."))

		candidate = apiResult["candidates"][0]
		if not isinstance(candidate, dict):
			# Translators: An error message for malformed Gemini OCR structured data.
			raise ApiError(_("Gemini OCR returned malformed structured data."))
		finishReason = candidate.get("finishReason")
		if finishReason == "SAFETY":
			# Translators: An error message indicating content was blocked by safety filters.
			raise ApiError(_("Content blocked due to safety settings."))
		text = self._extractCandidateText(candidate)
		if not text:
			# Translators: An error message for an empty Gemini OCR response.
			raise ApiError(_("Gemini OCR returned an empty response."))
		try:
			structuredResult = json.loads(self._stripMarkdownJsonFence(text))
		except json.JSONDecodeError as e:
			# Translators: An error message for malformed Gemini OCR structured JSON.
			raise ApiError(_("Gemini OCR returned invalid JSON.")) from e
		self._validateStructuredResult(structuredResult)
		return structuredResult

	@staticmethod
	def _extractCandidateText(candidate: dict) -> str:
		"""
		Returns the first text part from a Gemini candidate.

		:param candidate: A candidate object from the Gemini response.
		:returns: The text content.
		"""
		content = candidate.get("content")
		if not isinstance(content, dict):
			return ""
		for part in content.get("parts", []):
			if "text" in part:
				return part["text"]
		return ""

	@staticmethod
	def _stripMarkdownJsonFence(text: str) -> str:
		"""
		Strips Markdown code fences from model output as a defensive fallback.

		:param text: The raw candidate text.
		:returns: JSON text without Markdown fences.
		"""
		text = text.strip()
		if not text.startswith("```"):
			return text
		lines = text.splitlines()
		if lines:
			lines = lines[1:]
		if lines and lines[-1].startswith("```"):
			lines = lines[:-1]
		return "\n".join(lines).strip()

	def _validateStructuredResult(self, structuredResult: Any) -> None:
		"""
		Validates the model output before it is used for NVDA coordinates.

		:param structuredResult: The decoded OCR object.
		:raises ApiError: If the object does not match the expected shape.
		"""
		if not isinstance(structuredResult, dict) or not isinstance(structuredResult.get("lines"), list):
			# Translators: An error message for malformed Gemini OCR structured data.
			raise ApiError(_("Gemini OCR returned malformed structured data."))
		for line in structuredResult["lines"]:
			if not isinstance(line, dict) or not isinstance(line.get("words"), list):
				# Translators: An error message for malformed Gemini OCR structured data.
				raise ApiError(_("Gemini OCR returned malformed structured data."))
			for word in line["words"]:
				if not isinstance(word, dict) or not isinstance(word.get("text"), str):
					# Translators: An error message for malformed Gemini OCR structured data.
					raise ApiError(_("Gemini OCR returned malformed structured data."))
				box = word.get("box2d")
				if not isinstance(box, list) or len(box) != 4:
					# Translators: An error message for malformed Gemini OCR structured data.
					raise ApiError(_("Gemini OCR returned malformed structured data."))
				try:
					[int(coordinate) for coordinate in box]
				except (TypeError, ValueError) as e:
					# Translators: An error message for malformed Gemini OCR structured data.
					raise ApiError(_("Gemini OCR returned malformed structured data.")) from e

	def _convertStructuredWord(self, word: dict[str, Any]) -> dict[str, Any] | None:
		"""
		Converts one Gemini OCR word into NVDA's line/word item format.

		:param word: A structured word object from Gemini.
		:returns: An NVDA word dictionary, or None if the word is empty or degenerate.
		"""
		text = word["text"].strip()
		if not text:
			return None
		if not self._uploadedImageSize:
			# Translators: An error message when coordinate conversion cannot be completed.
			raise ApiError(_("Gemini OCR cannot convert coordinates without the uploaded image size."))
		imageWidth, imageHeight = self._uploadedImageSize
		if imageWidth <= 0 or imageHeight <= 0:
			# Translators: An error message when coordinate conversion cannot be completed.
			raise ApiError(_("Gemini OCR cannot convert coordinates without the uploaded image size."))

		yMin, xMin, yMax, xMax = self._normalizeBox(word["box2d"])
		if xMax <= xMin or yMax <= yMin:
			log.debugWarning(f"Gemini OCR skipped a degenerate box: {word!r}")
			return None

		x = round(xMin / NORMALIZED_COORDINATE_SCALE * imageWidth)
		y = round(yMin / NORMALIZED_COORDINATE_SCALE * imageHeight)
		right = round(xMax / NORMALIZED_COORDINATE_SCALE * imageWidth)
		bottom = round(yMax / NORMALIZED_COORDINATE_SCALE * imageHeight)

		x = max(0, min(x, imageWidth - 1))
		y = max(0, min(y, imageHeight - 1))
		right = max(x + 1, min(right, imageWidth))
		bottom = max(y + 1, min(bottom, imageHeight))
		return {
			"text": text,
			"x": x,
			"y": y,
			"width": right - x,
			"height": bottom - y,
		}

	@staticmethod
	def _normalizeBox(box: list[Any]) -> tuple[int, int, int, int]:
		"""
		Clamps and orders a Gemini normalized box.

		:param box: A [ymin, xmin, ymax, xmax] box.
		:returns: A clamped and ordered box tuple.
		"""
		yMin, xMin, yMax, xMax = [
			max(0, min(int(coordinate), NORMALIZED_COORDINATE_SCALE)) for coordinate in box
		]
		if yMax < yMin:
			yMin, yMax = yMax, yMin
		if xMax < xMin:
			xMin, xMax = xMax, xMin
		return yMin, xMin, yMax, xMax
