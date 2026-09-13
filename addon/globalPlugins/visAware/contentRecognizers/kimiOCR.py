# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Kimi OCR through the OpenAI-compatible Chat Completions API."""

from __future__ import annotations

import json
import math
from typing import Any

import addonHandler
from logHandler import log
from PIL import Image

from ..engineGUIHelper import ChoiceEngineSetting, TextInputEngineSetting
from ..exceptions import ApiError, AuthenticationError
from ..kimiAPI import (
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
	getKimiModelChoices,
	getKimiReasoningEffortChoices,
	isKimiK26Model,
	isKimiK3Model,
	isKimiK27Model,
	normalizeKimiReasoningEffort,
	normalizeKimiReasoningEffortForModel,
)
from ..recogHandler import BaseRecognizer, RecognitionRequest

addonHandler.initTranslation()

KIMI_REQUEST_TIMEOUT = 180
KIMI_MAX_COMPLETION_TOKENS = 32_768
NORMALIZED_COORDINATE_SCALE = 1000

OCR_PROMPT = (
	"Perform OCR only. Return every visible text line in natural reading order. "
	"For each line, return localized text runs that can be clicked accurately. "
	"Use exactly one top-level lines array. Each line object must contain exactly one words array. "
	"Each word object must contain exactly a string text and a four-number box2d array. "
	"For scripts with visible word separators, use words. For scripts without visible word separators, "
	"use the smallest contiguous readable text run you can localize reliably. "
	"Do not describe the image and do not add text that is not visible. "
	"Use bounding boxes in the [ymin, xmin, ymax, xmax] format normalized to 0-1000 "
	"relative to this exact input image. If there is no visible text, return an empty lines array. "
	"Return only the JSON object described by the response format."
)

# Translators: An error message for malformed Kimi OCR structured data.
_MALFORMED_STRUCTURED_DATA_MESSAGE = _("Kimi OCR returned malformed structured data.")
# Translators: Reported when OCR coordinates cannot be mapped because the image size is unavailable.
_MISSING_IMAGE_SIZE_MESSAGE = _("Kimi OCR cannot convert coordinates without the uploaded image size.")

# K3 and K2.7 have stable structured-output support. K2.6 is intentionally
# sent JSON mode and is checked again locally because its strict-schema support
# is less predictable for nested arrays.
OCR_RESPONSE_SCHEMA: dict[str, Any] = {
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
									"items": {"type": "number"},
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


class CustomContentRecognizer(BaseRecognizer):
	"""Recognizes text using Kimi's Chat Completions API."""

	name = "kimiOCR"
	supportsAutomaticRecognition = True
	# Translators: The description of the Kimi OCR engine. "Moonshot AI" is a proper noun
	# and should not be translated.
	description = _("Kimi (Moonshot AI) OCR")

	uploadBase64EncodeImage = True
	uploadImageFormat = "JPEG"
	isStreaming = False

	_apiKey: str = ""
	_baseUrl: str = DEFAULT_KIMI_BASE_URL
	_model: str = DEFAULT_KIMI_MODEL
	_reasoningEffort: str = "high"
	_uploadedImageSize: tuple[int, int] | None = None

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

	def _serializeImage(self, pilImage: Image.Image) -> bytes:
		self._uploadedImageSize = pilImage.size
		return super()._serializeImage(pilImage)

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		if not self.apiKey:
			# Translators: An error message if the Kimi API key is missing.
			raise AuthenticationError(
				_("API key is missing. Please configure it in the Kimi OCR engine settings."),
			)
		payload: dict[str, Any] = {
			"model": self.model,
			"max_completion_tokens": KIMI_MAX_COMPLETION_TOKENS,
			"messages": [
				{
					"role": "system",
					"content": "You are an OCR engine. Return only the JSON object described by the user.",
				},
				{
					"role": "user",
					"content": [
						{
							"type": "image_url",
							"image_url": {
								"url": f"data:image/jpeg;base64,{imageContent.decode('ascii')}",
							},
						},
						{"type": "text", "text": OCR_PROMPT},
					],
				},
			],
		}
		if isKimiK3Model(self.model) or isKimiK27Model(self.model):
			payload["response_format"] = {
				"type": "json_schema",
				"json_schema": {
					"name": "kimi_ocr_result",
					"strict": True,
					"schema": OCR_RESPONSE_SCHEMA,
				},
			}
		else:
			payload["response_format"] = {"type": "json_object"}
		applyKimiThinking(payload, self.model, self.reasoningEffort)
		return {
			"method": "POST",
			"url": buildKimiChatCompletionsUrl(self.baseUrl),
			"headers": {
				"Content-Type": "application/json",
				"Authorization": f"Bearer {self.apiKey}",
			},
			"json": payload,
			"timeout": KIMI_REQUEST_TIMEOUT,
		}

	def processApiResult(self, result: bytes) -> str | bool:
		try:
			self._extractStructuredResult(parseKimiResponse(result))
		except ApiError as e:
			return str(e)
		return False

	def extractText(self, apiResult: dict) -> str:
		structuredResult = self._extractStructuredResult(apiResult)
		return "\n".join(
			" ".join(word["text"].strip() for word in line["words"] if word["text"].strip())
			for line in structuredResult["lines"]
			if any(word["text"].strip() for word in line["words"])
		)

	def _convertToLineResultFormat(self, apiResult: dict) -> list:
		structuredResult = self._extractStructuredResult(apiResult)
		lineResult: list[list[dict[str, Any]]] = []
		for line in structuredResult["lines"]:
			lineWords = [
				convertedWord
				for word in line["words"]
				if (convertedWord := self._convertStructuredWord(word)) is not None
			]
			if lineWords:
				lineResult.append(lineWords)
		return lineResult

	def _cleanup(self) -> None:
		self._uploadedImageSize = None
		super()._cleanup()

	def _extractStructuredResult(self, apiResult: dict[str, Any]) -> dict[str, Any]:
		_choice, message = validateKimiCompletion(apiResult, {"stop"})
		text = getKimiMessageText(message).strip()
		if not text:
			# Translators: An error message for an empty Kimi OCR response.
			raise ApiError(_("Kimi OCR returned an empty response."))
		try:
			structuredResult = json.loads(self._stripMarkdownJsonFence(text))
		except json.JSONDecodeError as e:
			# Translators: An error message for malformed Kimi OCR structured JSON.
			raise ApiError(_("Kimi OCR returned invalid JSON.")) from e
		self._validateStructuredResult(structuredResult)
		return structuredResult

	@staticmethod
	def _stripMarkdownJsonFence(text: str) -> str:
		text = text.strip()
		if not text.startswith("```"):
			return text
		lines = text.splitlines()
		if len(lines) >= 3 and lines[-1].strip() == "```":
			return "\n".join(lines[1:-1]).strip()
		return text

	def _validateStructuredResult(self, structuredResult: Any) -> None:
		if not isinstance(structuredResult, dict) or not isinstance(structuredResult.get("lines"), list):
			raise ApiError(_MALFORMED_STRUCTURED_DATA_MESSAGE)
		for line in structuredResult["lines"]:
			if not isinstance(line, dict) or not isinstance(line.get("words"), list):
				raise ApiError(_MALFORMED_STRUCTURED_DATA_MESSAGE)
			for word in line["words"]:
				if not isinstance(word, dict) or not isinstance(word.get("text"), str):
					raise ApiError(_MALFORMED_STRUCTURED_DATA_MESSAGE)
				box = word.get("box2d")
				if not isinstance(box, list) or len(box) != 4:
					raise ApiError(_MALFORMED_STRUCTURED_DATA_MESSAGE)
				if any(isinstance(coordinate, bool) for coordinate in box):
					raise ApiError(_MALFORMED_STRUCTURED_DATA_MESSAGE)
				try:
					coordinates = [float(coordinate) for coordinate in box]
				except (TypeError, ValueError) as e:
					raise ApiError(_MALFORMED_STRUCTURED_DATA_MESSAGE) from e
				if not all(math.isfinite(coordinate) for coordinate in coordinates):
					raise ApiError(_MALFORMED_STRUCTURED_DATA_MESSAGE)

	def _convertStructuredWord(self, word: dict[str, Any]) -> dict[str, Any] | None:
		text = word["text"].strip()
		if not text:
			return None
		if not self._uploadedImageSize:
			raise ApiError(_MISSING_IMAGE_SIZE_MESSAGE)
		imageWidth, imageHeight = self._uploadedImageSize
		if imageWidth <= 0 or imageHeight <= 0:
			raise ApiError(_MISSING_IMAGE_SIZE_MESSAGE)

		yMin, xMin, yMax, xMax = self._normalizeBox(word["box2d"])
		if xMax <= xMin or yMax <= yMin:
			log.debugWarning(f"Kimi OCR skipped a degenerate box: {word!r}")
			return None
		x = round(xMin / NORMALIZED_COORDINATE_SCALE * imageWidth)
		y = round(yMin / NORMALIZED_COORDINATE_SCALE * imageHeight)
		right = round(xMax / NORMALIZED_COORDINATE_SCALE * imageWidth)
		bottom = round(yMax / NORMALIZED_COORDINATE_SCALE * imageHeight)
		x = max(0, min(x, imageWidth - 1))
		y = max(0, min(y, imageHeight - 1))
		right = max(x + 1, min(right, imageWidth))
		bottom = max(y + 1, min(bottom, imageHeight))
		return {"text": text, "x": x, "y": y, "width": right - x, "height": bottom - y}

	@staticmethod
	def _normalizeBox(box: list[Any]) -> tuple[int, int, int, int]:
		values = [max(0, min(round(float(coordinate)), NORMALIZED_COORDINATE_SCALE)) for coordinate in box]
		yMin, xMin, yMax, xMax = values
		if yMax < yMin:
			yMin, yMax = yMax, yMin
		if xMax < xMin:
			xMin, xMax = xMax, xMin
		return yMin, xMin, yMax, xMax
