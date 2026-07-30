# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An OCR engine that uses the 'OCR Server' iOS app on the local network."""

import json
import urllib.parse
from typing import Any, override

import addonHandler
from logHandler import log

from ..engineGUIHelper import TextInputEngineSetting
from ..exceptions import ApiError
from ..recogHandler import BaseRecognizer, RecognitionRequest

addonHandler.initTranslation()


class CustomContentRecognizer(BaseRecognizer):
	"""Recognizes text using the "OCR Server" iOS app on the local network."""

	name = "appleOCRServer"
	# Translators: The description of the Apple Vision (OCR Server) engine.
	description = _("Apple Vision (OCR Server)")

	uploadBase64EncodeImage = False
	isStreaming = False
	_serverAddress: str = ""

	@property
	@override
	def supportedSettings(self) -> list[Any]:
		"""
		Defines the user-configurable settings for this engine.

		:returns: A list of `EngineSetting` objects.
		"""
		return [
			TextInputEngineSetting(
				name="serverAddress",
				# Translators: The label for the OCR Server address field in the engine settings.
				displayNameWithAccelerator=_("Server &address (e.g., 192.168.1.10:8000)"),
			),
		]

	@property
	def serverAddress(self) -> str:
		"""The configured OCR Server base address."""
		return self._serverAddress

	@serverAddress.setter
	def serverAddress(self, value: str) -> None:
		value = value.strip()
		if value and "://" not in value:
			value = "http://" + value
		self._serverAddress = value

	@classmethod
	@override
	def check(cls) -> bool:
		"""Checks if the engine is available."""
		return True

	@override
	def _buildRequestParams(
		self,
		imageContent: bytes,
		request: RecognitionRequest,
	) -> dict[str, Any]:
		"""
		Builds the parameters dictionary for the network request.

		:param imageContent: The raw byte content of the image.
		:param request: The request-local recognition options.
		:returns: A dictionary of parameters for `requests`.
		:raises ApiError: If the server address is not configured.
		"""
		try:
			parsedAddress = urllib.parse.urlsplit(self.serverAddress)
			hasValidPort = parsedAddress.port is None or parsedAddress.port > 0
		except ValueError:
			parsedAddress = None
			hasValidPort = False
		if (
			parsedAddress is None
			or parsedAddress.scheme.lower() not in ("http", "https")
			or not parsedAddress.hostname
			or not hasValidPort
			or parsedAddress.username is not None
			or parsedAddress.password is not None
			or any(character.isspace() for character in parsedAddress.netloc)
			or parsedAddress.path.rstrip("/") not in ("", "/upload")
			or parsedAddress.query
			or parsedAddress.fragment
		):
			# Translators: An error message shown when the OCR Server address is not
			# configured correctly.
			raise ApiError(_("Please configure a valid OCR Server address in the engine settings."))
		url = urllib.parse.urlunsplit(
			(parsedAddress.scheme.lower(), parsedAddress.netloc, "/upload", "", ""),
		)
		files = {"file": ("image.png", imageContent, "image/png")}
		return {
			"method": "POST",
			"url": url,
			"files": files,
			"headers": {"Accept": "application/json"},
			"timeout": 60,
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
		except json.JSONDecodeError:
			# Translators: The OCR Server app returned data that was not in the expected format.
			return _("OCR Server returned an invalid response.")
		if not isinstance(responseJson, dict):
			# Translators: The OCR Server app returned data that was not in the expected format.
			return _("OCR Server returned an invalid response.")
		if responseJson.get("success") is not True:
			serverMessage = responseJson.get("message")
			if not isinstance(serverMessage, str) or not serverMessage:
				# Translators: A fallback error message when the OCR Server app provides
				# no error details.
				serverMessage = _("Unknown server error")
			# Translators: The OCR Server app reported an error.
			# The placeholder will be replaced with the error message.
			return _("OCR Server error: {}").format(serverMessage)
		if not isinstance(responseJson.get("ocr_result"), str) or not isinstance(
			responseJson.get("ocr_boxes"),
			list,
		):
			# Translators: The OCR Server app returned data that was not in the expected format.
			return _("OCR Server returned an invalid response.")
		return False

	@override
	def extractText(self, apiResult: dict[str, Any]) -> str:
		"""
		Extracts the plain text result from the parsed API response.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: The recognized text as a single string.
		"""
		text = apiResult.get("ocr_result")
		return text if isinstance(text, str) else ""

	@override
	def _convertToLineResultFormat(self, apiResult: dict[str, Any]) -> list[list[dict[str, Any]]]:
		"""
		Converts the API response into NVDA's rich format with coordinates.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: A list of lines, where each line is a list of word dictionaries.
		"""
		lines: list[list[dict[str, Any]]] = []
		ocrBoxes = apiResult.get("ocr_boxes")
		if not isinstance(ocrBoxes, list):
			# Translators: The OCR Server app returned data that was not in the expected format.
			raise ApiError(_("OCR Server returned an invalid response."))
		skippedBoxCount = 0
		for box in ocrBoxes:
			if not isinstance(box, dict):
				skippedBoxCount += 1
				continue
			text = box.get("text")
			if not isinstance(text, str) or not text.strip():
				skippedBoxCount += 1
				continue
			try:
				word = {
					"text": text,
					"x": int(float(box["x"])),
					"y": int(float(box["y"])),
					"width": int(float(box["w"])),
					"height": int(float(box["h"])),
				}
			except (KeyError, OverflowError, TypeError, ValueError):
				skippedBoxCount += 1
				continue
			if word["x"] < 0 or word["y"] < 0 or word["width"] <= 0 or word["height"] <= 0:
				skippedBoxCount += 1
				continue
			# Treat each recognized box as a separate line.
			lines.append([word])
		if skippedBoxCount:
			log.debugWarning(f"OCR Server ignored {skippedBoxCount} malformed or empty OCR boxes.")
		return lines
