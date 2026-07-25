# Copyright (C) 2025 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An OCR engine that uses the Vivo Cloud OCR API via NVDACN."""

import addonHandler
import urllib.parse
from collections import OrderedDict
from typing import List, Any

from ..recogHandler import BaseRecognizer, RecognitionRequest
from . import _vivo_auth
from ..engineGUIHelper import ChoiceEngineSetting

addonHandler.initTranslation()


class CustomContentRecognizer(BaseRecognizer):
	"""An OCR engine that uses the Vivo Cloud OCR API via NVDACN."""

	name = "vivoOCR"
	# Translators: The description of the Vivo OCR engine.
	description = _("Vivo OCR (NVDACN)")

	_domain = "api-ai.vivo.com.cn"
	_uri = "/ocr/general_recognition"
	_method = "POST"
	isStreaming = False

	# Constants for recognition modes.
	BUSINESS_ID_ADVANCED = "1990173156ceb8a09eee80c293135279"
	BUSINESS_ID_STANDARD = "8bf312e702043779ad0f2760b37a0806"

	@property
	def supportedSettings(self) -> List[Any]:
		"""
		Define the user-configurable settings for this engine.

		:returns: A list of `EngineSetting` objects.
		"""
		return [
			ChoiceEngineSetting(
				"recognitionMode",
				# Translators: The label for the recognition mode setting.
				_("Recognition &mode"),
				optionsPropertyName="availableRecognitionModes",
			),
		]

	_recognitionMode: str = BUSINESS_ID_ADVANCED

	@property
	def recognitionMode(self) -> str:
		return self._recognitionMode

	@recognitionMode.setter
	def recognitionMode(self, value: str) -> None:
		self._recognitionMode = value

	@property
	def availableRecognitionModes(self) -> dict:
		"""
		Provides the list of available recognition modes for the settings UI.

		:returns: A dictionary of mode IDs to display names.
		"""
		modes = OrderedDict(
			{
				# Translators: A recognition mode for Vivo OCR.
				self.BUSINESS_ID_ADVANCED: _("Advanced (supports rotation)"),
				# Translators: A recognition mode for Vivo OCR.
				self.BUSINESS_ID_STANDARD: _("Standard (faster)"),
			},
		)
		return self.generateStringSettings(modes)

	@classmethod
	def check(cls) -> bool:
		"""
		Checks if the engine is available.

		:returns: Always True as it's a cloud service.
		"""
		return True

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		"""
		Builds the parameters dictionary for the network request.

		:param imageContent: The raw byte content of the image, base64 encoded.
		:returns: A dictionary of parameters for `requests`.
		:raises AuthenticationError: If NVDACN credentials are not configured.
		"""
		user, password = _vivo_auth.getNvdacnCredentials()
		headers = _vivo_auth.genSignHeaders(user, password, self._method, self._uri, {})
		headers["Content-Type"] = "application/x-www-form-urlencoded"

		postData = {
			"image": imageContent.decode("utf-8"),
			"pos": 1,
			"businessid": self.recognitionMode,
		}
		payload = urllib.parse.urlencode(postData).encode("utf-8")

		return {
			"method": self._method,
			"url": f"https://{self._domain}{self._uri}",
			"headers": headers,
			"data": payload,
		}

	def processApiResult(self, result: bytes) -> str | bool:
		"""
		Handles error checking from the API response.

		:param result: The raw byte response from the server.
		:returns: An error message string on failure, or False on success.
		"""
		responseJson = self._convertToJson(result)
		if responseJson.get("error_code") != 0:
			errorMsg = responseJson.get("error_msg", "Unknown Vivo API error")
			# Translators: A Vivo API error. {message} is the service message and {code} is the error code.
			return _("Vivo API error: {message} (Code: {code})").format(
				message=errorMsg,
				code=responseJson.get("error_code"),
			)
		return False

	def extractText(self, apiResult: dict) -> str:
		"""
		Extracts the plain text result from the parsed API response.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: The recognized text as a single string.
		"""
		lines = []
		ocrItems = apiResult.get("result", {}).get("OCR", [])
		for item in ocrItems:
			lines.append(item.get("words", ""))
		return "\n".join(lines)

	def _convertToLineResultFormat(self, apiResult: dict) -> list:
		"""
		Converts the API response into NVDA's rich format with coordinates.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: A list of lines, where each line is a list of word dictionaries.
		"""
		lines = []
		ocrItems = apiResult.get("result", {}).get("OCR", [])
		for item in ocrItems:
			text = item.get("words")
			loc = item.get("location")
			if not text or not loc:
				continue
			topLeft = loc.get("top_left", {})
			topRight = loc.get("top_right", {})
			downLeft = loc.get("down_left", {})
			x = int(topLeft.get("x", 0))
			y = int(topLeft.get("y", 0))
			width = int(topRight.get("x", 0)) - x
			height = int(downLeft.get("y", 0)) - y
			word = {"text": text, "x": x, "y": y, "width": max(0, width), "height": max(0, height)}
			lines.append([word])
		return lines
