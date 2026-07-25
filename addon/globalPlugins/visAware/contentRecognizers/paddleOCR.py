# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An OCR engine that uses PaddleOCR and PaddleOCR-VL services."""

from __future__ import annotations

import addonHandler
from collections import OrderedDict
import config
from contentRecog import LinesWordsResult, RecogImageInfo, SimpleTextResult
import json
from logHandler import log
from PIL import Image
from typing import Any

from ..engineGUIHelper import BooleanEngineSetting, ChoiceEngineSetting, TextInputEngineSetting
from ..exceptions import ApiError, AuthenticationError
from .. import recogHistory
from ..recogHandler import BaseRecognizer, RecognitionRequest
from ._paddleOCRClient import (
	DEFAULT_AISTUDIO_ASYNC_URL,
	MODEL_PADDLEOCR_VL,
	MODEL_PADDLEOCR_VL_1_5,
	MODEL_PADDLEOCR_VL_1_6,
	MODEL_PP_OCR_V5,
	MODEL_PP_OCR_V6,
	MODEL_PP_STRUCTURE_V3,
	PaddleOCRClient,
	PaddleOCRClientOptions,
	SERVICE_TYPE_AISTUDIO_ASYNC,
	SERVICE_TYPE_AISTUDIO_SYNC,
	SERVICE_TYPE_SELF_HOSTED,
)

addonHandler.initTranslation()

TEXT_KEYS = ("block_content", "content", "text", "rec_text", "words")
BOX_KEYS = (
	"bbox",
	"box",
	"block_bbox",
	"rec_box",
	"rec_bbox",
	"poly",
	"polygon",
	"points",
	"dt_poly",
	"dt_polys",
	"location",
)
BOX_LIST_KEYS = ("rec_boxes", "rec_polys", "dt_polys", "polys", "boxes")
IMAGE_FIELD_NAMES = {
	"image",
	"inputImage",
	"ocrImage",
	"layoutImage",
	"outputImage",
	"outputImages",
	"pageImage",
	"pageImages",
}
MAX_HISTORY_STRING_LENGTH = 20000


class PaddleOCRResultParser:
	"""Extracts plain text and coordinate results from PaddleOCR response variants."""

	@classmethod
	def extractText(cls, apiResult: dict[str, Any]) -> str:
		"""
		Extracts plain text from PaddleOCR results.

		:param apiResult: A decoded PaddleOCR result dictionary.
		:returns: Plain text in reading order where possible.
		"""
		knownTextParts = cls._extractKnownTextParts(apiResult)
		if knownTextParts:
			return "\n".join(cls._cleanTextParts(knownTextParts))
		fallbackParts: list[str] = []
		cls._collectGenericText(apiResult, fallbackParts)
		return "\n".join(cls._cleanTextParts(fallbackParts))

	@classmethod
	def toLineResult(cls, apiResult: dict[str, Any]) -> list[list[dict[str, Any]]]:
		"""
		Converts a PaddleOCR result into NVDA's line/word result format.

		:param apiResult: A decoded PaddleOCR result dictionary.
		:returns: A list of lines containing clickable OCR words or blocks.
		"""
		lines: list[list[dict[str, Any]]] = []
		seen: set[tuple[str, int, int, int, int]] = set()
		for text, box in cls._iterTextBoxes(apiResult):
			word = cls._makeWord(text, box)
			if not word:
				continue
			signature = (
				word["text"],
				word["x"],
				word["y"],
				word["width"],
				word["height"],
			)
			if signature in seen:
				continue
			seen.add(signature)
			lines.append([word])
		if lines:
			return lines
		return []

	@classmethod
	def _extractKnownTextParts(cls, apiResult: dict[str, Any]) -> list[str]:
		parts: list[str] = []
		result = cls._getResultContainer(apiResult)
		parts.extend(cls._extractOCRTextParts(result))
		for item in cls._iterResultItems(result, "layoutParsingResults"):
			markdownText = cls._extractMarkdownText(item)
			if markdownText:
				parts.append(markdownText)
				continue
			prunedResult = cls._getPrunedResult(item)
			parts.extend(cls._extractParsingTextParts(prunedResult))
			parts.extend(cls._extractOCRTextParts(prunedResult.get("overall_ocr_res")))
		for item in cls._iterResultItems(result, "ocrResults"):
			parts.extend(cls._extractOCRTextParts(cls._getPrunedResult(item)))
		for item in cls._iterResultItems(result, "results"):
			prunedResult = cls._getPrunedResult(item)
			parts.extend(cls._extractOCRTextParts(prunedResult))
			parts.extend(cls._extractParsingTextParts(prunedResult))
		return parts

	@classmethod
	def _getResultContainer(cls, apiResult: dict[str, Any]) -> dict[str, Any]:
		result = apiResult.get("result")
		return result if isinstance(result, dict) else apiResult

	@classmethod
	def _iterResultItems(cls, result: dict[str, Any], key: str) -> list[dict[str, Any]]:
		items = result.get(key)
		if isinstance(items, dict):
			items = [items]
		if not isinstance(items, list):
			return []
		resultItems: list[dict[str, Any]] = []
		for item in items:
			if not isinstance(item, dict):
				continue
			resultItems.append(item)
		return resultItems

	@staticmethod
	def _getPrunedResult(item: dict[str, Any]) -> dict[str, Any]:
		prunedResult = item.get("prunedResult")
		return prunedResult if isinstance(prunedResult, dict) else item

	@staticmethod
	def _extractMarkdownText(item: dict[str, Any]) -> str:
		markdown = item.get("markdown") or item.get("md")
		if isinstance(markdown, str):
			return markdown
		if not isinstance(markdown, dict):
			return ""
		for key in ("text", "markdown", "content"):
			value = markdown.get(key)
			if isinstance(value, str) and value.strip():
				return value
		return ""

	@classmethod
	def _extractParsingTextParts(cls, item: dict[str, Any]) -> list[str]:
		parsingItems = item.get("parsing_res_list") or item.get("parsingResults")
		if not isinstance(parsingItems, list):
			return []
		parts: list[str] = []
		for parsingItem in parsingItems:
			if not isinstance(parsingItem, dict):
				continue
			text = cls._extractTextFromDict(parsingItem)
			if text:
				parts.append(text)
		return parts

	@staticmethod
	def _extractOCRTextParts(item: Any) -> list[str]:
		if not isinstance(item, dict):
			return []
		texts = item.get("rec_texts") or item.get("texts")
		if not isinstance(texts, list):
			return []
		return [str(text) for text in texts if str(text).strip()]

	@classmethod
	def _collectGenericText(cls, value: Any, parts: list[str]) -> None:
		if isinstance(value, dict):
			text = cls._extractTextFromDict(value)
			if text:
				parts.append(text)
			for key, child in value.items():
				if key in IMAGE_FIELD_NAMES:
					continue
				cls._collectGenericText(child, parts)
		elif isinstance(value, list):
			for child in value:
				cls._collectGenericText(child, parts)

	@classmethod
	def _iterTextBoxes(cls, apiResult: dict[str, Any]) -> list[tuple[str, Any]]:
		items: list[tuple[str, Any]] = []
		for item in cls._iterDictionaries(apiResult):
			texts = item.get("rec_texts") or item.get("texts")
			if isinstance(texts, list):
				boxes = cls._getBoxList(item)
				for index, text in enumerate(texts):
					if index >= len(boxes):
						break
					textString = str(text).strip()
					if textString:
						items.append((textString, boxes[index]))
			text = cls._extractTextFromDict(item)
			box = cls._extractBoxFromDict(item)
			if text and box is not None:
				items.append((text, box))
		return items

	@classmethod
	def _iterDictionaries(cls, value: Any) -> list[dict[str, Any]]:
		items: list[dict[str, Any]] = []
		if isinstance(value, dict):
			items.append(value)
			for key, child in value.items():
				if key in IMAGE_FIELD_NAMES:
					continue
				items.extend(cls._iterDictionaries(child))
		elif isinstance(value, list):
			for child in value:
				items.extend(cls._iterDictionaries(child))
		return items

	@staticmethod
	def _getBoxList(item: dict[str, Any]) -> list[Any]:
		for key in BOX_LIST_KEYS:
			value = item.get(key)
			if isinstance(value, list):
				return value
		return []

	@staticmethod
	def _extractTextFromDict(item: dict[str, Any]) -> str:
		for key in TEXT_KEYS:
			value = item.get(key)
			if isinstance(value, str):
				return value.strip()
			if isinstance(value, list) and all(isinstance(part, str) for part in value):
				return "\n".join(part.strip() for part in value if part.strip())
		return ""

	@staticmethod
	def _extractBoxFromDict(item: dict[str, Any]) -> Any:
		for key in BOX_KEYS:
			value = item.get(key)
			if value is not None:
				return value
		return None

	@classmethod
	def _makeWord(cls, text: str, box: Any) -> dict[str, Any] | None:
		rect = cls._boxToRect(box)
		if not rect:
			return None
		left, top, right, bottom = rect
		width = right - left
		height = bottom - top
		if width <= 0 or height <= 0:
			log.debugWarning(f"PaddleOCR skipped a degenerate box: {box!r}")
			return None
		return {
			"text": text.strip(),
			"x": left,
			"y": top,
			"width": width,
			"height": height,
		}

	@classmethod
	def _boxToRect(cls, box: Any) -> tuple[int, int, int, int] | None:
		if isinstance(box, dict):
			return cls._dictBoxToRect(box)
		if not isinstance(box, list):
			return None
		if len(box) == 4 and all(cls._isNumber(value) for value in box):
			left, top, right, bottom = [round(float(value)) for value in box]
			return cls._normalizeRect(left, top, right, bottom)
		if len(box) == 8 and all(cls._isNumber(value) for value in box):
			points = [[box[index], box[index + 1]] for index in range(0, len(box), 2)]
			return cls._pointsToRect(points)
		if all(cls._isPoint(value) for value in box):
			return cls._pointsToRect(box)
		return None

	@classmethod
	def _dictBoxToRect(cls, box: dict[str, Any]) -> tuple[int, int, int, int] | None:
		if {"left", "top", "width", "height"}.issubset(box):
			left = round(float(box["left"]))
			top = round(float(box["top"]))
			right = left + round(float(box["width"]))
			bottom = top + round(float(box["height"]))
			return cls._normalizeRect(left, top, right, bottom)
		if {"x", "y", "width", "height"}.issubset(box):
			left = round(float(box["x"]))
			top = round(float(box["y"]))
			right = left + round(float(box["width"]))
			bottom = top + round(float(box["height"]))
			return cls._normalizeRect(left, top, right, bottom)
		for key in ("points", "poly", "polygon", "vertices"):
			value = box.get(key)
			if isinstance(value, list):
				return cls._boxToRect(value)
		return None

	@classmethod
	def _pointsToRect(cls, points: list[Any]) -> tuple[int, int, int, int] | None:
		xValues: list[float] = []
		yValues: list[float] = []
		for point in points:
			if not cls._isPoint(point):
				return None
			xValues.append(float(point[0]))
			yValues.append(float(point[1]))
		if not xValues or not yValues:
			return None
		return cls._normalizeRect(
			round(min(xValues)),
			round(min(yValues)),
			round(max(xValues)),
			round(max(yValues)),
		)

	@staticmethod
	def _normalizeRect(left: int, top: int, right: int, bottom: int) -> tuple[int, int, int, int]:
		if right < left:
			left, right = right, left
		if bottom < top:
			top, bottom = bottom, top
		return left, top, right, bottom

	@staticmethod
	def _isNumber(value: Any) -> bool:
		return isinstance(value, (int, float)) or (
			isinstance(value, str) and value.replace(".", "", 1).replace("-", "", 1).isdigit()
		)

	@classmethod
	def _isPoint(cls, value: Any) -> bool:
		return (
			isinstance(value, list)
			and len(value) >= 2
			and cls._isNumber(value[0])
			and cls._isNumber(value[1])
		)

	@staticmethod
	def _cleanTextParts(parts: list[str]) -> list[str]:
		cleanedParts: list[str] = []
		for part in parts:
			part = str(part).strip()
			if not part or part.startswith("data:image/"):
				continue
			cleanedParts.append(part)
		return cleanedParts


class CustomContentRecognizer(BaseRecognizer):
	"""Recognizes text and document layout using PaddleOCR services."""

	name = "paddleOCR"
	# Translators: The description of the PaddleOCR engine.
	description = _("PaddleOCR / PaddleOCR-VL")

	uploadBase64EncodeImage = True
	uploadImageFormat = "PNG"
	isStreaming = False
	maxSize = 10 * 1024 * 1024

	_serviceType: str = SERVICE_TYPE_AISTUDIO_ASYNC
	_aistudioAsyncApiUrl: str = DEFAULT_AISTUDIO_ASYNC_URL
	_aistudioAsyncToken: str = ""
	_aistudioAsyncModel: str = MODEL_PADDLEOCR_VL_1_5
	_aistudioSyncApiUrl: str = ""
	_aistudioSyncToken: str = ""
	_aistudioSyncModel: str = MODEL_PADDLEOCR_VL_1_5
	_selfHostedApiUrl: str = ""
	_selfHostedToken: str = ""
	_selfHostedModel: str = MODEL_PADDLEOCR_VL_1_6
	_useDocOrientationClassify: bool = False
	_useDocUnwarping: bool = False
	_useTextlineOrientation: bool = False
	_useChartRecognition: bool = False
	_uploadedImageSize: tuple[int, int] | None = None
	_PROFILE_SETTINGS = {
		SERVICE_TYPE_AISTUDIO_ASYNC: {
			"apiUrl": "_aistudioAsyncApiUrl",
			"token": "_aistudioAsyncToken",
			"model": "_aistudioAsyncModel",
		},
		SERVICE_TYPE_AISTUDIO_SYNC: {
			"apiUrl": "_aistudioSyncApiUrl",
			"token": "_aistudioSyncToken",
			"model": "_aistudioSyncModel",
		},
		SERVICE_TYPE_SELF_HOSTED: {
			"apiUrl": "_selfHostedApiUrl",
			"token": "_selfHostedToken",
			"model": "_selfHostedModel",
		},
	}

	@property
	def supportedSettings(self) -> list[Any]:
		"""
		Defines the user-configurable settings for this engine.

		:returns: A list of engine setting objects.
		"""
		settings = [
			ChoiceEngineSetting(
				name="serviceType",
				# Translators: The label for selecting the PaddleOCR service type.
				displayNameWithAccelerator=_("Service &type"),
				optionsPropertyName="availableServiceTypes",
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for selecting a PaddleOCR model.
				displayNameWithAccelerator=_("&Model"),
				optionsPropertyName="availableModels",
			),
			self.autoRecognitionModelSetting(),
			TextInputEngineSetting(
				name="apiUrl",
				# Translators: The label for the PaddleOCR API URL field.
				displayNameWithAccelerator=_("API &URL"),
			),
			TextInputEngineSetting(
				name="token",
				# Translators: The label for the PaddleOCR API token field.
				displayNameWithAccelerator=_("API &token"),
			),
		]
		settings.extend(
			[
				BooleanEngineSetting(
					name="useDocOrientationClassify",
					# Translators: The label for enabling PaddleOCR document orientation detection.
					displayNameWithAccelerator=_("&Detect document orientation"),
				),
				BooleanEngineSetting(
					name="useDocUnwarping",
					# Translators: The label for enabling PaddleOCR document unwarping.
					displayNameWithAccelerator=_("Enable document &unwarping"),
				),
			],
		)
		if self._isCurrentOCRModel():
			settings.append(
				BooleanEngineSetting(
					name="useTextlineOrientation",
					# Translators: The label for enabling PaddleOCR text line orientation detection.
					displayNameWithAccelerator=_("Detect text line &orientation"),
				),
			)
		else:
			settings.append(
				BooleanEngineSetting(
					name="useChartRecognition",
					# Translators: The label for enabling PaddleOCR chart recognition.
					displayNameWithAccelerator=_("Enable &chart recognition"),
				),
			)
		return settings

	def getConfigSpec(self) -> dict:
		"""
		Returns the full configuration spec, including inactive service profiles.

		Inactive profile fields are not shown in the UI, but still need a config
		spec so official and self-hosted credentials can be saved side by side.
		"""
		spec = super().getConfigSpec()
		for name, _attr in self._iterProfileConfigItems():
			spec[name] = "string(default=None)"
		return spec

	def saveSettings(self) -> None:
		"""Saves all PaddleOCR service profiles without letting them overwrite each other."""
		if not self.configSectionName:
			return
		conf = config.conf[self.configSectionName][self.name]
		conf["serviceType"] = self.serviceType
		conf["autoRecognitionModel"] = self.autoRecognitionModel
		for name, attr in self._iterProfileConfigItems():
			conf[name] = getattr(self, attr)
		for name in (
			"useDocOrientationClassify",
			"useDocUnwarping",
			"useTextlineOrientation",
			"useChartRecognition",
		):
			conf[name] = getattr(self, name)

	def loadSettings(self, onlyChanged: bool = False) -> None:
		"""Loads all PaddleOCR service profiles, then selects the active one."""
		if not self.configSectionName:
			return
		conf = config.conf[self.configSectionName][self.name]
		serviceType = conf.get("serviceType")
		if serviceType in self.availableServiceTypes:
			self._setLoadedSetting("_serviceType", serviceType, onlyChanged)
		if "autoRecognitionModel" in conf:
			value = str(conf["autoRecognitionModel"] or "").strip()
			self._setLoadedSetting("_autoRecognitionModel", value, onlyChanged)
		for name, attr in self._iterProfileConfigItems():
			if name in conf:
				value = str(conf[name] or "")
				if attr.endswith("ApiUrl"):
					value = value.strip()
				self._setLoadedSetting(attr, value, onlyChanged)
		for name in (
			"useDocOrientationClassify",
			"useDocUnwarping",
			"useTextlineOrientation",
			"useChartRecognition",
		):
			if name not in conf:
				continue
			value = self._coerceBoolean(conf[name])
			if value is not None:
				self._setLoadedSetting(f"_{name}", value, onlyChanged)
		self._ensureValidModel()

	@classmethod
	def _iterProfileConfigItems(cls):
		for profile in cls._PROFILE_SETTINGS.values():
			for attr in profile.values():
				yield attr[1:], attr

	def _setLoadedSetting(self, attr: str, value: Any, onlyChanged: bool) -> None:
		if onlyChanged and getattr(self, attr, None) == value:
			return
		setattr(self, attr, value)

	@staticmethod
	def _coerceBoolean(value: Any) -> bool | None:
		if isinstance(value, bool):
			return value
		normalized = str(value).strip().lower()
		if normalized in ("1", "true", "yes", "on"):
			return True
		if normalized in ("0", "false", "no", "off"):
			return False
		return None

	def _currentProfileAttr(self, name: str) -> str:
		return self._PROFILE_SETTINGS[self.serviceType][name]

	@property
	def apiUrl(self) -> str:
		return getattr(self, self._currentProfileAttr("apiUrl"))

	@apiUrl.setter
	def apiUrl(self, value: str) -> None:
		setattr(self, self._currentProfileAttr("apiUrl"), value.strip())

	@property
	def token(self) -> str:
		return getattr(self, self._currentProfileAttr("token"))

	@token.setter
	def token(self, value: str) -> None:
		setattr(self, self._currentProfileAttr("token"), value)

	@property
	def model(self) -> str:
		return getattr(self, self._currentProfileAttr("model"))

	@model.setter
	def model(self, value: str) -> None:
		if value in self.availableModels:
			setattr(self, self._currentProfileAttr("model"), value)

	@property
	def serviceType(self) -> str:
		return self._serviceType

	@serviceType.setter
	def serviceType(self, value: str) -> None:
		if value in self.availableServiceTypes:
			self._serviceType = value
			self._ensureValidModel()

	def _ensureValidModel(self) -> None:
		if self.model in self.availableModels:
			return
		self.model = next(iter(self.availableModels))

	def _isCurrentOCRModel(self) -> bool:
		return self.model in (MODEL_PP_OCR_V5, MODEL_PP_OCR_V6)

	@property
	def useDocOrientationClassify(self) -> bool:
		return self._useDocOrientationClassify

	@useDocOrientationClassify.setter
	def useDocOrientationClassify(self, value: bool) -> None:
		self._useDocOrientationClassify = value

	@property
	def useDocUnwarping(self) -> bool:
		return self._useDocUnwarping

	@useDocUnwarping.setter
	def useDocUnwarping(self, value: bool) -> None:
		self._useDocUnwarping = value

	@property
	def useTextlineOrientation(self) -> bool:
		return self._useTextlineOrientation

	@useTextlineOrientation.setter
	def useTextlineOrientation(self, value: bool) -> None:
		self._useTextlineOrientation = value

	@property
	def useChartRecognition(self) -> bool:
		return self._useChartRecognition

	@useChartRecognition.setter
	def useChartRecognition(self, value: bool) -> None:
		self._useChartRecognition = value

	@property
	def availableServiceTypes(self) -> dict:
		"""
		Provides the supported PaddleOCR service types for the settings UI.

		:returns: A dictionary of service type IDs to display names.
		"""
		serviceTypes = OrderedDict(
			{
				SERVICE_TYPE_AISTUDIO_ASYNC: _("AI Studio hosted task API (recommended)"),
				SERVICE_TYPE_AISTUDIO_SYNC: _("AI Studio deployed service"),
				SERVICE_TYPE_SELF_HOSTED: _("Self-hosted PaddleOCR service"),
			},
		)
		return self.generateStringSettings(serviceTypes)

	@property
	def availableModels(self) -> dict:
		"""
		Provides the supported PaddleOCR model names for the settings UI.

		:returns: A dictionary of model IDs to display names.
		"""
		models = OrderedDict(
			(
				(MODEL_PADDLEOCR_VL_1_6, _("PaddleOCR-VL 1.6 (document understanding)")),
				(MODEL_PP_OCR_V6, _("PP-OCRv6 (fast text OCR)")),
			)
			if self.serviceType == SERVICE_TYPE_SELF_HOSTED
			else (
				(MODEL_PADDLEOCR_VL_1_5, _("PaddleOCR-VL 1.5 (recommended)")),
				(MODEL_PADDLEOCR_VL, _("PaddleOCR-VL")),
				(MODEL_PP_OCR_V5, _("PP-OCRv5")),
				(MODEL_PP_STRUCTURE_V3, _("PP-StructureV3")),
			),
		)
		return self.generateStringSettings(models)

	@classmethod
	def check(cls) -> bool:
		"""
		Checks if the engine is available.

		:returns: Always True because this is a configurable HTTP engine.
		"""
		return True

	def _serializeImage(self, pilImage: Image.Image) -> bytes:
		"""
		Serializes the upload image and records its dimensions for diagnostics.

		:param pilImage: The image being serialized.
		:returns: The serialized image bytes.
		"""
		self._uploadedImageSize = pilImage.size
		return super()._serializeImage(pilImage)

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		"""
		Builds request state consumed by the PaddleOCR client.

		:param imageContent: The base64 encoded serialized image bytes.
		:param request: The request-local recognition options.
		:returns: A small request dictionary for `_handleStandardResponse`.
		"""
		self._validateSettings()
		return {"imageContent": imageContent}

	def _handleStandardResponse(
		self,
		requestParams: dict,
		imageInfo: RecogImageInfo,
		cancellationEvent: Any,
		request: RecognitionRequest,
	) -> Any:
		"""Handles PaddleOCR's sync and async service variants through the private client."""
		client = PaddleOCRClient(
			self._buildClientOptions(),
			cancellationChecker=lambda: self._checkCancelled(cancellationEvent),
		)
		apiResult = client.recognizeImage(requestParams["imageContent"])
		self._checkCancelled(cancellationEvent)
		historyEntry = (
			recogHistory.createEntry(self, self.originalImage, self._pruneResultForHistory(apiResult))
			if self.originalImage
			else None
		)
		if request.textResult:
			ocrResult = self.extractText(apiResult)
			if not ocrResult or ocrResult.isspace():
				# Translators: An error message for a blank recognition result.
				raise ApiError(_("Recognition result is blank."))
			return recogHistory.attachEntry(SimpleTextResult(ocrResult), historyEntry)
		lineResult = self._convertToLineResultFormat(apiResult)
		if lineResult:
			return recogHistory.attachEntry(LinesWordsResult(lineResult, imageInfo), historyEntry)
		ocrResult = self.extractText(apiResult)
		if not ocrResult or ocrResult.isspace():
			# Translators: An error message for a blank recognition result.
			raise ApiError(_("Recognition result is blank."))
		textOnlyResult = SimpleTextResult(ocrResult)
		textOnlyResult.forceVirtualDocument = True
		return recogHistory.attachEntry(textOnlyResult, historyEntry)

	def _validateSettings(self) -> None:
		if self.serviceType in (SERVICE_TYPE_AISTUDIO_ASYNC, SERVICE_TYPE_AISTUDIO_SYNC) and not self.token:
			# Translators: An error message if the PaddleOCR token is missing.
			raise AuthenticationError(_("API token is missing. Please configure it in PaddleOCR settings."))
		if self.serviceType != SERVICE_TYPE_AISTUDIO_ASYNC and (
			not self.apiUrl or self.apiUrl == DEFAULT_AISTUDIO_ASYNC_URL
		):
			# Translators: An error message if the PaddleOCR API URL is missing.
			raise AuthenticationError(_("API URL is missing. Please configure it in PaddleOCR settings."))

	def _buildClientOptions(self) -> PaddleOCRClientOptions:
		return PaddleOCRClientOptions(
			serviceType=self.serviceType,
			apiUrl=self.apiUrl,
			token=self.token,
			model=self.model,
			useDocOrientationClassify=self.useDocOrientationClassify,
			useDocUnwarping=self.useDocUnwarping,
			useTextlineOrientation=self.useTextlineOrientation,
			useChartRecognition=self.useChartRecognition,
		)

	def processApiResult(self, result: bytes) -> str | bool:
		"""
		Handles error checking for direct PaddleOCR responses.

		:param result: The raw byte response from the server.
		:returns: False on success, otherwise a user-visible error message.
		"""
		try:
			responseJson = self._convertToJson(result)
			error = responseJson.get("error")
			if error:
				return str(error)
			errorCode = responseJson.get("errorCode") or responseJson.get("error_code")
			if errorCode not in (None, 0, "0"):
				return str(responseJson.get("errorMsg") or responseJson.get("error_msg") or errorCode)
		except (json.JSONDecodeError, AttributeError) as e:
			return str(e)
		return False

	def extractText(self, apiResult: dict) -> str:
		"""
		Extracts the plain text result from PaddleOCR variants.

		:param apiResult: The parsed PaddleOCR response.
		:returns: Recognized text as a single string.
		"""
		return PaddleOCRResultParser.extractText(apiResult)

	def _convertToLineResultFormat(self, apiResult: dict) -> list:
		"""
		Converts PaddleOCR response data into NVDA's rich OCR result format.

		:param apiResult: The parsed PaddleOCR response.
		:returns: A list of lines where each line contains word dictionaries.
		"""
		return PaddleOCRResultParser.toLineResult(apiResult)

	def _cleanup(self) -> None:
		"""Cleans up engine state after recognition."""
		self._uploadedImageSize = None
		super()._cleanup()

	@classmethod
	def _pruneResultForHistory(cls, value: Any) -> Any:
		if isinstance(value, dict):
			pruned: dict[str, Any] = {}
			for key, child in value.items():
				if key in IMAGE_FIELD_NAMES:
					continue
				pruned[key] = cls._pruneResultForHistory(child)
			return pruned
		if isinstance(value, list):
			return [cls._pruneResultForHistory(child) for child in value]
		if isinstance(value, str) and len(value) > MAX_HISTORY_STRING_LENGTH:
			return value[:MAX_HISTORY_STRING_LENGTH] + "...[omitted]"
		return value
