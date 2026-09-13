# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Client helpers for text CAPTCHA recognition services."""

from __future__ import annotations

import addonHandler
from collections.abc import Callable
from dataclasses import dataclass
import json
from logHandler import log
import time
from typing import Any, NoReturn
from urllib.parse import urlencode

from .. import network
from ..exceptions import ApiError, AuthenticationError

addonHandler.initTranslation()

SERVICE_TYPE_RUCAPTCHA = "rucaptcha"
SERVICE_TYPE_2CAPTCHA = "2captcha"

LANGUAGE_UNSPECIFIED = "0"
LANGUAGE_CYRILLIC = "1"
LANGUAGE_LATIN = "2"

ANSWER_TYPE_UNSPECIFIED = "0"
ANSWER_TYPE_NUMBERS_ONLY = "1"
ANSWER_TYPE_LETTERS_ONLY = "2"
ANSWER_TYPE_NUMBERS_OR_LETTERS = "3"
ANSWER_TYPE_NUMBERS_AND_LETTERS = "4"

CAPTCHA_NOT_READY = "CAPCHA_NOT_READY"
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_MAX_WAIT_SECONDS = 120
MAX_INSTRUCTION_LENGTH = 140
MIN_ANSWER_LENGTH = 0
MAX_ANSWER_LENGTH = 20

_SERVICE_BASE_URLS = {
	SERVICE_TYPE_RUCAPTCHA: "https://rucaptcha.com",
	SERVICE_TYPE_2CAPTCHA: "https://2captcha.com",
}

_AUTHENTICATION_ERROR_CODES = {
	"ERROR_WRONG_USER_KEY",
	"ERROR_KEY_DOES_NOT_EXIST",
}


@dataclass(frozen=True)
class CaptchaTextClientOptions:
	"""Options needed to call a text CAPTCHA recognition service."""

	serviceType: str
	apiKey: str
	caseSensitive: bool
	hasMultipleWords: bool
	answerType: str
	isMathCaptcha: bool
	minLength: int
	maxLength: int
	language: str
	instructionText: str


class CaptchaTextClient:
	"""A small HTTP client for RuCaptcha/2Captcha compatible text CAPTCHA APIs."""

	def __init__(
		self,
		options: CaptchaTextClientOptions,
		cancellationChecker: Callable[[], None] | None = None,
		requestTimeout: float | tuple[float, float] | None = None,
	) -> None:
		self.options = options
		self._apiKey = options.apiKey.strip()
		self._cancellationChecker = cancellationChecker
		self._requestTimeout = requestTimeout

	def recognizeImage(self, imageContent: bytes) -> dict[str, Any]:
		"""
		Recognizes a text CAPTCHA image.

		:param imageContent: Base64 encoded image bytes from the recognizer pipeline.
		:returns: A response dictionary containing the recognized text and task id.
		"""
		self._requireApiKey()
		captchaId = self._submitImage(imageContent)
		text = self._pollTextResult(captchaId)
		return {
			"captchaId": captchaId,
			"text": text,
			"serviceType": self.options.serviceType,
		}

	def getBalance(self) -> str:
		"""
		Gets the account balance from the selected CAPTCHA service.

		:returns: The balance returned by the service.
		"""
		self._requireApiKey()
		result = self._requestJson(
			"GET",
			f"{self._getBaseUrl()}/res.php",
			params={
				"key": self._apiKey,
				"action": "getbalance",
				"json": 1,
			},
			timeout=30,
		)
		return self._extractSuccessfulRequest(result)

	def _submitImage(self, imageContent: bytes) -> str:
		body = self._decodeImageContent(imageContent)
		payload: dict[str, Any] = {
			"key": self._apiKey,
			"method": "base64",
			"body": body,
			"json": 1,
			"phrase": int(self.options.hasMultipleWords),
			"regsense": int(self.options.caseSensitive),
			"numeric": self._getAnswerTypeValue(),
			"calc": int(self.options.isMathCaptcha),
			"min_len": self._getAnswerLengthValue(self.options.minLength),
			"max_len": self._getAnswerLengthValue(self.options.maxLength),
			"language": self._getLanguageValue(),
		}
		instructionText = self.options.instructionText.strip()
		if instructionText:
			payload["textinstructions"] = instructionText[:MAX_INSTRUCTION_LENGTH]
		result = self._requestJson(
			"POST",
			f"{self._getBaseUrl()}/in.php",
			headers={"Content-Type": "application/x-www-form-urlencoded"},
			data=urlencode(payload),
			timeout=120,
			retry=False,
		)
		return self._extractSuccessfulRequest(result)

	def _pollTextResult(self, captchaId: str) -> str:
		deadline = time.monotonic() + DEFAULT_MAX_WAIT_SECONDS
		lastResult: dict[str, Any] = {}
		while time.monotonic() < deadline:
			self._checkCancelled()
			network.sleepWithCancellation(DEFAULT_POLL_INTERVAL_SECONDS, self._cancellationChecker)
			self._checkCancelled()
			lastResult = self._requestJson(
				"GET",
				f"{self._getBaseUrl()}/res.php",
				params={
					"key": self._apiKey,
					"action": "get",
					"id": captchaId,
					"json": 1,
				},
				timeout=30,
			)
			request = str(lastResult.get("request", ""))
			if request == CAPTCHA_NOT_READY:
				continue
			return self._extractSuccessfulRequest(lastResult)
		log.debugWarning(f"Text CAPTCHA recognition timed out. Last result: {lastResult!r}")
		# Translators: An error message when a text CAPTCHA task does not finish in time.
		raise ApiError(_("CAPTCHA recognition did not finish before the timeout."))

	def _requestJson(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
		self._checkCancelled()
		if self._cancellationChecker:
			kwargs["cancelCheck"] = self._cancellationChecker
		if self._requestTimeout is not None:
			kwargs["timeout"] = self._requestTimeout
		response = network.sendRequest(method, url, **kwargs)
		try:
			result = json.loads(response.content.decode("utf-8", errors="ignore"))
		except json.JSONDecodeError as e:
			log.debugWarning(f"CAPTCHA service returned invalid JSON: {e}")
			# Translators: An error message for malformed CAPTCHA service response data.
			raise ApiError(_("CAPTCHA service returned malformed response data.")) from e
		if not isinstance(result, dict):
			# Translators: An error message for malformed CAPTCHA service response data.
			raise ApiError(_("CAPTCHA service returned malformed response data."))
		return result

	def _extractSuccessfulRequest(self, result: dict[str, Any]) -> str:
		request = result.get("request")
		if str(result.get("status")) == "1":
			text = str(request or "").strip()
			if text:
				return text
			# Translators: An error message when a CAPTCHA service success response has no result.
			raise ApiError(_("CAPTCHA service returned an empty result."))
		self._raiseForServiceError(str(request or ""), result)

	def _raiseForServiceError(self, errorCode: str, result: dict[str, Any]) -> NoReturn:
		message = self._getErrorMessage(errorCode, result)
		if errorCode in _AUTHENTICATION_ERROR_CODES:
			raise AuthenticationError(message)
		raise ApiError(message)

	def _getErrorMessage(self, errorCode: str, result: dict[str, Any]) -> str:
		errorText = result.get("error_text")
		if isinstance(errorText, str) and errorText.strip():
			return errorText.strip()
		errorMessages = {
			# Translators: An error message when the CAPTCHA API key is missing.
			"ERROR_WRONG_USER_KEY": _("CAPTCHA API key is not specified."),
			# Translators: An error message when the CAPTCHA API key is invalid.
			"ERROR_KEY_DOES_NOT_EXIST": _("The configured CAPTCHA API key does not exist."),
			# Translators: An error message when the CAPTCHA service account has no balance.
			"ERROR_ZERO_BALANCE": _("The CAPTCHA service account balance is zero."),
			# Translators: An error message when the CAPTCHA service has no available workers.
			"ERROR_NO_SLOT_AVAILABLE": _("The CAPTCHA service is busy. Please try again later."),
			# Translators: An error message when a CAPTCHA image is too small.
			"ERROR_ZERO_CAPTCHA_FILESIZE": _("The CAPTCHA image is too small."),
			# Translators: An error message when a CAPTCHA image is too large.
			"ERROR_TOO_BIG_CAPTCHA_FILESIZE": _("The CAPTCHA image is too large."),
			# Translators: An error message when the CAPTCHA image file extension is not supported.
			"ERROR_WRONG_FILE_EXTENSION": _("The CAPTCHA image format is not supported."),
			# Translators: An error message when the CAPTCHA image content type is not supported.
			"ERROR_IMAGE_TYPE_NOT_SUPPORTED": _("The CAPTCHA image type is not supported."),
			# Translators: An error message when a CAPTCHA image upload fails.
			"ERROR_UPLOAD": _("The CAPTCHA image could not be uploaded."),
			# Translators: An error message when CAPTCHA service request parameters are invalid.
			"ERROR_BAD_PARAMETERS": _("The CAPTCHA service request parameters are invalid."),
			# Translators: An error message when the CAPTCHA service receives a malformed task id.
			"ERROR_WRONG_ID_FORMAT": _("The CAPTCHA task id has an invalid format."),
			# Translators: An error message when the CAPTCHA task id does not exist.
			"ERROR_WRONG_CAPTCHA_ID": _("The CAPTCHA task id does not exist."),
			# Translators: An error message when the CAPTCHA service account restricts API access by IP.
			"ERROR_IP_NOT_ALLOWED": _("This IP address is not allowed for the CAPTCHA service account."),
			# Translators: An error message when the CAPTCHA service blocks the current IP address.
			"IP_BANNED": _("This IP address is temporarily blocked by the CAPTCHA service."),
			# Translators: An error message when the CAPTCHA cannot be solved.
			"ERROR_CAPTCHA_UNSOLVABLE": _("The CAPTCHA could not be solved."),
			# Translators: An error message when the CAPTCHA service cannot get enough matching answers.
			"ERROR_BAD_DUPLICATES": _("The CAPTCHA service could not get enough matching answers."),
			# Translators: An error message when the CAPTCHA image is blocked by the service.
			"ERROR_CAPTCHAIMAGE_BLOCKED": _("This CAPTCHA image cannot be recognized."),
			# Translators: An error message when too many bad CAPTCHA images were submitted.
			"TOO_MANY_BAD_IMAGES": _(
				"Too many unrecognizable CAPTCHA images were sent. Please try again later.",
			),
		}
		if errorCode in errorMessages:
			return errorMessages[errorCode]
		# Translators: A generic CAPTCHA service error. {error} is the service error code or message.
		return _("CAPTCHA service returned an error: {error}").format(error=errorCode or result)

	def _getBaseUrl(self) -> str:
		try:
			return _SERVICE_BASE_URLS[self.options.serviceType]
		except KeyError as e:
			# Translators: An error message when the CAPTCHA service setting is invalid.
			raise ApiError(_("Unsupported CAPTCHA service.")) from e

	def _getLanguageValue(self) -> int:
		try:
			return int(self.options.language)
		except ValueError:
			return int(LANGUAGE_UNSPECIFIED)

	def _getAnswerTypeValue(self) -> int:
		try:
			value = int(self.options.answerType)
		except ValueError:
			return int(ANSWER_TYPE_UNSPECIFIED)
		if value < int(ANSWER_TYPE_UNSPECIFIED) or value > int(ANSWER_TYPE_NUMBERS_AND_LETTERS):
			return int(ANSWER_TYPE_UNSPECIFIED)
		return value

	def _getAnswerLengthValue(self, value: int) -> int:
		if value < MIN_ANSWER_LENGTH or value > MAX_ANSWER_LENGTH:
			# Translators: An error message when a CAPTCHA answer length setting is out of range.
			raise ApiError(
				_(
					"CAPTCHA answer length must be between {minLength} and {maxLength}.",
				).format(
					minLength=MIN_ANSWER_LENGTH,
					maxLength=MAX_ANSWER_LENGTH,
				),
			)
		return value

	def _decodeImageContent(self, imageContent: bytes) -> str:
		try:
			return imageContent.decode("ascii")
		except UnicodeDecodeError as e:
			# Translators: An error message when CAPTCHA image data cannot be sent to the service.
			raise ApiError(_("CAPTCHA image data could not be prepared for upload.")) from e

	def _requireApiKey(self) -> None:
		if not self._apiKey:
			# Translators: An error message if the CAPTCHA API key is missing.
			raise AuthenticationError(_("API key is missing. Please configure it in CAPTCHA settings."))

	def _checkCancelled(self) -> None:
		if self._cancellationChecker:
			self._cancellationChecker()
