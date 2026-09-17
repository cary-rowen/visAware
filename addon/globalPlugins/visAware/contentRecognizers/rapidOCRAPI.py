# Copyright (C) 2025 hwf1324 <1398969445@qq.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An OCR engine that uses the Rapid OCR API."""

import json
from typing import Any, override

import addonHandler
from locationHelper import Point, RectLTWH
from logHandler import log

from ..recogHandler import BaseRecognizer, RecognitionRequest
from ..exceptions import ApiError
from ..engineGUIHelper import BooleanEngineSetting, TextInputEngineSetting


addonHandler.initTranslation()


class CustomContentRecognizer(BaseRecognizer):

	name = "rapidOCRAPI"
	# Translators: The description of the Rapid OCR API engine.
	description = _("Rapid OCR API")

	uploadBase64EncodeImage = False
	_serverAddress: str = "http://localhost:9003"
	_useDetection: bool = True
	_useClassification: bool = False
	_useRecognition: bool = True

	@property
	@override
	def supportedSettings(self) -> list[Any]:
		"""
		Define the user-configurable settings for this engine.

		:returns: A list of `EngineSetting` objects.
		"""
		return [
			TextInputEngineSetting(
				"serverAddress",
				# Translators: The label for the text field to enter the OCR Server address in the engine settings.
				_("Server &Address (e.g., localhost:9003)"),
			),
			# BooleanEngineSetting(
			# 	"useDetection",
			# 	# Translators: The label for the checkbox to enable detection in the engine settings.
			# 	_("Use &Detection"),
			# ),
			BooleanEngineSetting(
				"useClassification",
				# Translators: The label for the checkbox to enable classification in the engine settings.
				_("Use &Classification"),
			),
			# BooleanEngineSetting(
			# 	"useRecognition",
			# 	# Translators: The label for the checkbox to enable recognition in the engine settings.
			# 	_("Use &Recognition"),
			# ),
		]

	@property
	def serverAddress(self) -> str:
		return self._serverAddress

	@serverAddress.setter
	def serverAddress(self, value: str) -> None:
		if value and not value.startswith(("http://", "https://")):
			self._serverAddress = "http://" + value
		else:
			self._serverAddress = value

	@property
	def useDetection(self) -> bool:
		return self._useDetection

	@useDetection.setter
	def useDetection(self, value: bool) -> None:
		self._useDetection = value

	@property
	def useClassification(self) -> bool:
		return self._useClassification

	@useClassification.setter
	def useClassification(self, value: bool) -> None:
		self._useClassification = value

	@property
	def useRecognition(self) -> bool:
		return self._useRecognition

	@useRecognition.setter
	def useRecognition(self, value: bool) -> None:
		self._useRecognition = value

	@override
	@classmethod
	def check(cls) -> bool:
		"""
		Checks if the engine is available.

		:returns: Always True for this local network engine.
		"""
		# try:
		# 	response = network.sendRequest("GET", cls.serverAddress)
		# 	data = cls._convertToJson(response.content)
		# 	if "Welcome to RapidOCR API Server!" == data.get("message"):
		# 		return True
		# 	else:
		# 		return False
		# except:
		# 	return False
		return True

	@override
	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict[str, Any]:
		"""
		Builds the parameters dictionary for the network request.

		:param imageContent: The raw byte content of the image.
		:returns: A dictionary of parameters for `requests`.
		:raises ApiError: If the server address is not configured.
		"""
		if not self.serverAddress or not self.serverAddress.startswith("http"):
			# Translators: An error message shown when the OCR Server address is not configured correctly.
			raise ApiError(_("Please configure a valid OCR Server address in the engine settings."))
		url = f"{self.serverAddress.rstrip('/')}/ocr"
		files = {"image_file": ("image.png", imageContent, "image/png")}
		data = {
			"use_det": self.useDetection,
			"use_cls": self.useClassification,
			"use_rec": self.useRecognition,
		}
		return {
			"method": "POST",
			"url": url,
			"files": files,
			"data": data,
		}

	@override
	def processApiResult(self, result: bytes) -> str | bool:
		"""
		Handles error checking from the API response.

		:param result: The raw byte response from the server.
		:returns: An error message string on failure, or False on success.
		"""
		try:
			responseJson = self._convertToJson(result)
			# if not responseJson:
			# 	errorMsg = "No text detected"
			# 	log.debugWarning(f"OCR Server returned a failure message: {errorMsg}")
			# 	# Translators: The OCR Server app reported an error. The placeholder will be replaced with the error message.
			# 	return _("OCR Server Error: {}").format(errorMsg)
			return False  # Indicates success
		except json.JSONDecodeError:
			log.error("OCR Server: Failed to parse JSON from response.")
			# Translators: The OCR Server app returned data that was not in the expected format.
			return _("OCR Server returned an invalid response.")

	@override
	def extractText(self, apiResult: dict[str, Any]) -> str:
		"""
		Extracts the plain text result from the parsed API response.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: The recognized text as a single string.
		"""
		return "\n".join([value["rec_txt"] for value in apiResult.values()])

	@override
	def _convertToLineResultFormat(self, apiResult: dict[str, Any]) -> list[list[dict[str, int | str]]]:
		"""
		Converts the API response into NVDA's rich format with coordinates.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: A list of lines, where each line is a list of word dictionaries.
		"""
		lines: list[list[dict[str, int | str]]] = []
		for value in apiResult.values():
			rect: RectLTWH = RectLTWH.fromCollection(*[Point.fromFloatCollection(*point) for point in value["dt_boxes"]])
			lines.append([{"x": rect.left, "y": rect.top, "width": rect.width, "height": rect.height, "text": value["rec_txt"]}])
		return lines
