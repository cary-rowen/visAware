# Copyright (C) 2025 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An OCR engine that uses the Baidu OCR API."""

import addonHandler
from collections import OrderedDict
import json
from typing import Any

from ..recogHandler import BaseRecognizer, RecognitionRequest
from ..exceptions import ApiError, AuthenticationError, NetworkError
from .. import network
from ..engineGUIHelper import ChoiceEngineSetting, BooleanEngineSetting, TextInputEngineSetting

addonHandler.initTranslation()


class CustomContentRecognizer(BaseRecognizer):
	"""An OCR engine that uses the Baidu OCR API."""

	name = "baiduOCR"
	# Translators: The description of the Baidu OCR engine.
	description = _("Baidu OCR")

	_accessToken: str = ""
	textResult: bool = False
	_language: str = "CHN_ENG"
	_timeout: int = 100
	_accurate: bool = False
	_recognizeGranularity: bool = True
	_detectLanguage: bool = False
	_detectDirection: bool = False

	@property
	def supportedSettings(self) -> list[Any]:
		"""
		Define the user-configurable settings for this engine.

		:returns: A list of `EngineSetting` objects.
		"""
		return [
			ChoiceEngineSetting(
				"language",
				# Translators: The label for the recognition language setting.
				_("Recognition Language"),
				optionsPropertyName="availableLanguages",
			),
			BooleanEngineSetting(
				"detectDirection",
				# Translators: The label for the setting to detect image direction.
				_("&Detect image direction"),
			),
			BooleanEngineSetting(
				"recognizeGranularity",
				# Translators: The label for the setting to get the position of every character.
				_("&Get position of every character"),
			),
			BooleanEngineSetting(
				"accurate",
				# Translators: The label for the setting to use a more accurate but slower API.
				_("&Use Accurate API (Slower)"),
			),
			TextInputEngineSetting("apikey", _("API &Key")),
			TextInputEngineSetting("apisecret", _("API &Secret Key")),
		]

	@property
	def timeout(self) -> int:
		return self._timeout

	@timeout.setter
	def timeout(self, value: int) -> None:
		self._timeout = value

	@property
	def accurate(self) -> bool:
		return self._accurate

	@accurate.setter
	def accurate(self, value: bool) -> None:
		self._accurate = value

	@property
	def recognizeGranularity(self) -> bool:
		return self._recognizeGranularity

	@recognizeGranularity.setter
	def recognizeGranularity(self, value: bool) -> None:
		self._recognizeGranularity = value

	@property
	def language(self) -> str:
		return self._language

	@language.setter
	def language(self, value: str) -> None:
		self._language = value

	@property
	def detectLanguage(self) -> bool:
		return self._detectLanguage

	@detectLanguage.setter
	def detectLanguage(self, value: bool) -> None:
		self._detectLanguage = value

	@property
	def detectDirection(self) -> bool:
		return self._detectDirection

	@detectDirection.setter
	def detectDirection(self, value: bool) -> None:
		self._detectDirection = value

	@property
	def availableLanguages(self):
		"""
		Provides the list of available languages for the settings UI.

		:returns: A dictionary of language codes to display names.
		"""
		languages = OrderedDict(
			{
				# Translators: The name for the language option in Baidu OCR settings.
				"CHN_ENG": _("Chinese and English"),
				"ENG": _("English"),
				"POR": _("Portuguese"),
				"FRE": _("French"),
				"GER": _("German"),
				"ITA": _("Italian"),
				"SPA": _("Spanish"),
				"RUS": _("Russian"),
				"JAP": _("Japanese"),
				"KOR": _("Korean"),
			},
		)
		return self.generateStringSettings(languages)

	@classmethod
	def check(cls) -> bool:
		"""
		Checks if the engine is available.

		:returns: Always True as it's a cloud service.
		"""
		return True

	def _refreshToken(self) -> None:
		"""
		Refreshes the Baidu API access token.

		:raises AuthenticationError: If token refresh fails due to network or credential issues.
		:raises ApiError: If the server response is invalid.
		"""
		url = "https://aip.baidubce.com/oauth/2.0/token"
		params = {
			"grant_type": "client_credentials",
			"client_id": self.apikey,
			"client_secret": self.apisecret,
		}
		try:
			response = network.sendRequest("POST", url, params=params, timeout=10)
			result = response.json()
			if "access_token" in result:
				self._accessToken = result["access_token"]
			elif "error" in result:
				errorDesc = result.get("error_description", "Unknown OAuth error")
				raise AuthenticationError(
					# Translators: A Baidu OAuth error. {error} is the error code and {description} is its description.
					_("Baidu OAuth Error: {error} - {description}").format(
						error=result["error"],
						description=errorDesc,
					),
				)
			else:
				# Translators: An unexpected response from Baidu OAuth. {response} is the returned response.
				raise ApiError(_("Unexpected response from Baidu OAuth: {response}").format(response=result))
		except (NetworkError, ApiError) as e:
			# Translators: An error message when the token cannot be refreshed.
			raise AuthenticationError(
				_("Cannot refresh token. Please check your network connection or credentials."),
			) from e
		except (json.JSONDecodeError, KeyError) as e:
			# Translators: An error message for an invalid server response during token refresh.
			raise ApiError(_("Cannot refresh token. Invalid response from server.")) from e

	def processApiResult(self, result: bytes) -> str | bool:
		"""
		Handles error checking from the API response.

		:param result: The raw byte response from the server.
		:returns: An error message string on failure, or False on success.
		"""
		apiResult = self._convertToJson(result)
		errorCode = apiResult.get("error_code")
		if not errorCode:
			return False  # Success
		if errorCode in (110, 111):  # Token invalid or expired
			try:
				self._refreshToken()
				# After a successful refresh, signal that a retry is needed.
				# Translators: A message indicating the token was expired and to try again.
				return _("Token was expired, please try again.")
			except (ApiError, AuthenticationError) as e:
				return str(e)
		# Translators: An unknown Baidu API error. {code} is the error code returned by the service.
		return self.CODE_TO_ERROR_MESSAGE.get(
			errorCode, _("Unknown error code: {code}").format(code=errorCode)
		)

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		"""
		Builds the parameters dictionary for the network request.

		:param imageContent: The raw byte content of the image, base64 encoded.
		:returns: A dictionary of parameters for `requests`.
		"""
		if not self._accessToken:
			self._refreshToken()
		apiEndpoint = "accurate" if self.accurate else "general"
		if request.textResult:
			apiEndpoint += "_basic"
		url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/{apiEndpoint}?access_token={self._accessToken}"
		payload: dict[str, Any] = {
			"image": imageContent.decode("utf-8"),
			"detect_direction": str(self.detectDirection).lower(),
			"detect_language": str(self.detectLanguage).lower(),
			"language_type": self.language,
			"probability": "false",
		}
		if not request.textResult:
			payload.update(
				{
					"recognize_granularity": "small" if self.recognizeGranularity else "big",
					"vertexes_location": str(self.recognizeGranularity).lower(),
				},
			)
		return {
			"method": "POST",
			"url": url,
			"data": payload,
			"headers": {"Content-Type": "application/x-www-form-urlencoded"},
		}

	def extractText(self, apiResult: dict) -> str:
		"""
		Extracts the plain text result from the parsed API response.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: The recognized text as a single string.
		"""
		words = [item["words"] for item in apiResult.get("words_result", [])]
		return "\n".join(words)

	def _convertToLineResultFormat(self, apiResult: dict) -> list:
		"""
		Converts the API response into NVDA's rich format with coordinates.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: A list of lines, where each line is a list of word/char dictionaries.
		"""
		lineResult: list[list[dict[str, Any]]] = []
		wordsResult = apiResult.get("words_result", [])
		if self.recognizeGranularity:
			for wordsInfo in wordsResult:
				charList = wordsInfo.get("chars", [])
				if charList:
					line = []
					for charInfo in charList:
						loc = charInfo["location"]
						line.append(
							{
								"text": charInfo["char"],
								"x": loc["left"],
								"y": loc["top"],
								"width": loc["width"],
								"height": loc["height"],
							},
						)
					lineResult.append(line)
		else:
			for item in wordsResult:
				loc = item["location"]
				lineResult.append(
					[
						{
							"text": item["words"],
							"x": loc["left"],
							"y": loc["top"],
							"width": loc["width"],
							"height": loc["height"],
						},
					],
				)
		return lineResult

	CODE_TO_ERROR_MESSAGE: dict[int, str] = {
		# Translators: An error message for Baidu OCR.
		1: _("Unknown error"),
		2: _("Service temporarily unavailable"),
		3: _("Unsupported API endpoint"),
		4: _("API request limit reached"),
		17: _("Daily request limit reached"),
		18: _("QPS request limit reached"),
		19: _("Total request limit reached"),
		100: _("Invalid parameter"),
		110: _("Access token invalid or expired"),
		111: _("Access token expired"),
		216200: _("Empty image"),
		216201: _("Image format error"),
		216202: _("Image size error"),
		282810: _("Image recognize error"),
	}
