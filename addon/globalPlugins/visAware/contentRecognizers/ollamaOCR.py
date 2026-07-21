# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An OCR engine that uses the Ollama API."""

from __future__ import annotations

import addonHandler
import json
from logHandler import log
from PIL import Image
from typing import Any

from .._ollamaClient import (
	extractOllamaGenerateText,
	getOllamaError,
	parseOllamaGenerateStreamChunk,
)
from .._ollamaEngine import OllamaEngineMixin
from ..engineGUIHelper import (
	BooleanEngineSetting,
	ButtonEngineSetting,
	ChoiceEngineSetting,
	TextInputEngineSetting,
)
from ..exceptions import ApiError
from ..recogHandler import BaseRecognizer, RecognitionRequest

addonHandler.initTranslation()

NORMALIZED_COORDINATE_SCALE = 1000

# Translators: This is the default prompt sent to the Ollama OCR model.
# It guides the model to extract only visible text.
DEFAULT_OLLAMA_OCR_PROMPT = _(
	"Extract only the visible text from this image. Preserve line breaks. "
	"Do not describe the image or add any extra words.",
)

# Translators: This is the default system prompt sent to the Ollama OCR model.
DEFAULT_OLLAMA_OCR_SYSTEM_PROMPT = _(
	"You are an OCR engine. Follow the requested output format exactly. "
	"Do not describe the image, infer hidden text, add explanations, or use Markdown.",
)

OLLAMA_OCR_RESULT_SCHEMA: dict[str, Any] = {
	"type": "object",
	"properties": {
		"lines": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"words": {
						"type": "array",
						"items": {
							"type": "object",
							"properties": {
								"text": {"type": "string"},
								"box2d": {
									"type": "array",
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
						},
					},
				},
				"required": ["words"],
				"additionalProperties": False,
			},
		},
	},
	"required": ["lines"],
	"additionalProperties": False,
}

OLLAMA_OCR_STRUCTURED_PROMPT = (
	"Perform OCR only. Return only valid JSON matching exactly this schema: "
	'{"lines":[{"words":[{"text":"visible text","box2d":[0,0,100,100]}]}]}. '
	"Use natural reading order. For scripts with spaces, split into words. For scripts without spaces, "
	"use the smallest contiguous readable text runs you can localize reliably. "
	"Coordinates must be integers in [ymin, xmin, ymax, xmax] order, normalized from 0 to 1000 "
	'relative to the exact input image. If there is no visible text, return exactly {"lines":[]}. '
	"Do not include Markdown fences, explanations, confidence scores, or any keys outside the schema."
)


class CustomContentRecognizer(OllamaEngineMixin, BaseRecognizer):
	"""Recognizes text using a local or hosted Ollama vision model."""

	name = "ollamaOCR"
	# Translators: The description of the Ollama OCR engine.
	description = _("Ollama OCR")

	uploadBase64EncodeImage = True
	uploadImageFormat = "JPEG"
	isStreaming = False
	supportsStreaming = True
	_prompt: str = DEFAULT_OLLAMA_OCR_PROMPT
	_uploadedImageSize: tuple[int, int] | None = None

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
				displayNameWithAccelerator=_("API &URL:"),
			),
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the Ollama API key field.
				displayNameWithAccelerator=_("API &Key:"),
			),
			ButtonEngineSetting(
				name="fetchModels",
				# Translators: The label for the button that fetches Ollama model names.
				displayNameWithAccelerator=_("&Fetch models"),
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for a setting to select the Ollama model.
				displayNameWithAccelerator=_("&Model:"),
				optionsPropertyName="availableModels",
			),
			self.autoRecognitionModelSetting(),
			BooleanEngineSetting(
				name="useStreaming",
				# Translators: The label for an engine setting to enable streaming output.
				displayNameWithAccelerator=_("&Enable streaming output"),
			),
			self.imageQualitySetting(),
			TextInputEngineSetting(
				name="prompt",
				# Translators: The label for a setting to customize the OCR prompt.
				displayNameWithAccelerator=_("&Custom Prompt:"),
			),
			self.autoRecognitionPromptSetting(),
		]

	@property
	def prompt(self) -> str:
		return self._prompt

	@prompt.setter
	def prompt(self, value: str) -> None:
		self._prompt = value.strip() if value else DEFAULT_OLLAMA_OCR_PROMPT

	def loadSettings(self, onlyChanged: bool = False) -> None:
		super().loadSettings(onlyChanged=onlyChanged)
		if not self.prompt.strip():
			self.prompt = DEFAULT_OLLAMA_OCR_PROMPT

	@classmethod
	def check(cls) -> bool:
		"""Checks if the engine is available."""
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
		"""Builds the Ollama generate request for OCR."""
		modelName = self._resolveModel()
		imageBase64 = imageContent.decode("ascii")
		structuredResult = not request.textResult
		payload = {
			"model": modelName,
			"system": DEFAULT_OLLAMA_OCR_SYSTEM_PROMPT,
			"prompt": self._buildStructuredPrompt() if structuredResult else self.prompt,
			"images": [imageBase64],
			"stream": request.streamResult and not structuredResult,
		}
		if structuredResult:
			payload["format"] = OLLAMA_OCR_RESULT_SCHEMA
		return self._buildOllamaRequestParams("generate", payload)

	def processApiResult(self, result: bytes) -> str | bool:
		"""Handles error checking for Ollama generate responses."""
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
			return _("Ollama API Error: {}").format(error)
		text = extractOllamaGenerateText(responseJson)
		if not text:
			# Translators: An error message for an empty but successful Ollama response.
			return _("Server returned a successful but empty response.")
		return False

	def extractText(self, apiResult: dict) -> str:
		"""Extracts text from an Ollama generate response."""
		rawText = extractOllamaGenerateText(apiResult)
		try:
			structuredResult = self._extractStructuredResult(apiResult)
		except ApiError:
			return rawText
		textLines: list[str] = []
		for line in structuredResult["lines"]:
			words = [word["text"].strip() for word in line["words"] if word["text"].strip()]
			if words:
				textLines.append(" ".join(words))
		return "\n".join(textLines)

	def _convertToLineResultFormat(self, apiResult: dict) -> list:
		"""
		Converts structured Ollama OCR JSON into NVDA's rich format with coordinates.

		:param apiResult: The parsed Ollama generate response.
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

	def processStreamChunk(self, chunk: bytes, request: RecognitionRequest) -> str | None:
		"""Processes one Ollama generate stream chunk."""
		return parseOllamaGenerateStreamChunk(chunk)

	def _cleanup(self) -> None:
		"""Cleans up engine state after recognition."""
		self._uploadedImageSize = None
		super()._cleanup()

	def _buildStructuredPrompt(self) -> str:
		customPrompt = self.prompt.strip()
		if not customPrompt or customPrompt == DEFAULT_OLLAMA_OCR_PROMPT:
			return OLLAMA_OCR_STRUCTURED_PROMPT
		return (
			f"{OLLAMA_OCR_STRUCTURED_PROMPT}\n"
			"Additional OCR instruction, only if it does not change the JSON format: "
			f"{customPrompt}"
		)

	def _extractStructuredResult(self, apiResult: dict) -> dict[str, Any]:
		"""
		Extracts and validates Ollama OCR structured JSON.

		:param apiResult: The parsed Ollama generate response.
		:returns: The parsed structured OCR object.
		:raises ApiError: If the response is empty or does not match the expected structure.
		"""
		text = extractOllamaGenerateText(apiResult)
		if not text:
			# Translators: An error message for an empty Ollama OCR response.
			raise ApiError(_("Ollama OCR returned an empty response."))
		try:
			structuredResult = json.loads(self._stripMarkdownJsonFence(text))
		except json.JSONDecodeError as e:
			# Translators: An error message for malformed Ollama OCR structured JSON.
			raise ApiError(_("Ollama OCR returned invalid JSON.")) from e
		self._validateStructuredResult(structuredResult)
		return structuredResult

	@staticmethod
	def _stripMarkdownJsonFence(text: str) -> str:
		"""
		Strips Markdown code fences from model output as a defensive fallback.

		:param text: The raw model text.
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
			# Translators: An error message for malformed Ollama OCR structured data.
			raise ApiError(_("Ollama OCR returned malformed structured data."))
		for line in structuredResult["lines"]:
			if not isinstance(line, dict) or not isinstance(line.get("words"), list):
				# Translators: An error message for malformed Ollama OCR structured data.
				raise ApiError(_("Ollama OCR returned malformed structured data."))
			for word in line["words"]:
				if not isinstance(word, dict) or not isinstance(word.get("text"), str):
					# Translators: An error message for malformed Ollama OCR structured data.
					raise ApiError(_("Ollama OCR returned malformed structured data."))
				box = word.get("box2d")
				if not isinstance(box, list) or len(box) != 4:
					# Translators: An error message for malformed Ollama OCR structured data.
					raise ApiError(_("Ollama OCR returned malformed structured data."))
				try:
					[int(coordinate) for coordinate in box]
				except (TypeError, ValueError) as e:
					# Translators: An error message for malformed Ollama OCR structured data.
					raise ApiError(_("Ollama OCR returned malformed structured data.")) from e

	def _convertStructuredWord(self, word: dict[str, Any]) -> dict[str, Any] | None:
		"""
		Converts one structured OCR word into NVDA's line/word item format.

		:param word: A structured word object from Ollama.
		:returns: An NVDA word dictionary, or None if the word is empty or degenerate.
		"""
		text = word["text"].strip()
		if not text:
			return None
		if not self._uploadedImageSize:
			# Translators: An error message when coordinate conversion cannot be completed.
			raise ApiError(_("Ollama OCR cannot convert coordinates without the uploaded image size."))
		imageWidth, imageHeight = self._uploadedImageSize
		if imageWidth <= 0 or imageHeight <= 0:
			# Translators: An error message when coordinate conversion cannot be completed.
			raise ApiError(_("Ollama OCR cannot convert coordinates without the uploaded image size."))

		yMin, xMin, yMax, xMax = self._normalizeBox(word["box2d"])
		if xMax <= xMin or yMax <= yMin:
			log.debugWarning(f"Ollama OCR skipped a degenerate box: {word!r}")
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
		Clamps and orders a normalized [ymin, xmin, ymax, xmax] box.

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
