# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""A text CAPTCHA recognition engine."""

from __future__ import annotations

import addonHandler
from contentRecog import RecogImageInfo, SimpleTextResult
import threading
from collections.abc import Callable
from typing import Any

import ui
import wx

from .. import recogHistory
from ..engineGUIHelper import (
	BooleanEngineSetting,
	ButtonEngineSetting,
	ChoiceEngineSetting,
	NumericEngineSetting,
	ReadOnlyEngineSetting,
	TextInputEngineSetting,
)
from ..exceptions import ApiError, AuthenticationError
from ..recogHandler import BaseRecognizer, RecognitionRequest
from ._captchaTextClient import (
	ANSWER_TYPE_LETTERS_ONLY,
	ANSWER_TYPE_NUMBERS_AND_LETTERS,
	ANSWER_TYPE_NUMBERS_ONLY,
	ANSWER_TYPE_NUMBERS_OR_LETTERS,
	ANSWER_TYPE_UNSPECIFIED,
	CaptchaTextClient,
	CaptchaTextClientOptions,
	LANGUAGE_CYRILLIC,
	LANGUAGE_LATIN,
	LANGUAGE_UNSPECIFIED,
	MAX_ANSWER_LENGTH,
	MIN_ANSWER_LENGTH,
	SERVICE_TYPE_2CAPTCHA,
	SERVICE_TYPE_RUCAPTCHA,
)

addonHandler.initTranslation()


class CustomContentRecognizer(BaseRecognizer):
	"""Recognizes text CAPTCHA images through CAPTCHA solving services."""

	name = "captchaText"
	supportsAutomaticRecognition = False
	# Translators: The description of the text CAPTCHA recognition engine.
	description = _("Text CAPTCHA recognition")

	uploadBase64EncodeImage = True
	uploadImageFormat = "PNG"
	isStreaming = False
	maxWidth = 1000
	maxHeight = 1000
	maxSize = 100 * 1024

	_serviceType: str = SERVICE_TYPE_RUCAPTCHA
	caseSensitive: bool = False
	hasMultipleWords: bool = False
	_answerType: str = ANSWER_TYPE_UNSPECIFIED
	isMathCaptcha: bool = False
	_minLength: int = MIN_ANSWER_LENGTH
	_maxLength: int = MIN_ANSWER_LENGTH
	_language: str = LANGUAGE_UNSPECIFIED
	_instructionText: str = ""
	_balanceByCredential: dict[tuple[str, str], str]

	@property
	def supportedSettings(self) -> list[Any]:
		"""
		Defines the user-configurable settings for this engine.

		:returns: A list of engine setting objects.
		"""
		return [
			ChoiceEngineSetting(
				name="serviceType",
				# Translators: The label for selecting the CAPTCHA recognition service.
				displayNameWithAccelerator=_("CAPTCHA &service:"),
				optionsPropertyName="availableServiceTypes",
			),
			TextInputEngineSetting(
				name="apikey",
				# Translators: The label for the CAPTCHA service API key field.
				displayNameWithAccelerator=_("API &key:"),
				refreshSettingsOnChange=True,
			),
			BooleanEngineSetting(
				name="caseSensitive",
				# Translators: The label for a CAPTCHA setting to request case-sensitive recognition.
				displayNameWithAccelerator=_("&Case-sensitive recognition"),
			),
			BooleanEngineSetting(
				name="hasMultipleWords",
				# Translators: The label for a CAPTCHA setting to request recognition of answers with multiple words.
				displayNameWithAccelerator=_("Answer has multiple &words"),
			),
			ChoiceEngineSetting(
				name="answerType",
				# Translators: The label for selecting the expected CAPTCHA answer type.
				displayNameWithAccelerator=_("Answer &type:"),
				optionsPropertyName="availableAnswerTypes",
			),
			BooleanEngineSetting(
				name="isMathCaptcha",
				# Translators: The label for a CAPTCHA setting to solve math captcha images as calculations.
				displayNameWithAccelerator=_("Solve as a &math CAPTCHA"),
			),
			ChoiceEngineSetting(
				name="language",
				# Translators: The label for selecting the CAPTCHA image language.
				displayNameWithAccelerator=_("Image &language:"),
				optionsPropertyName="availableLanguages",
			),
			self._makeAnswerLengthSetting(
				"minLength",
				# Translators: The label for the minimum expected CAPTCHA answer length.
				_("Minimum answer &length:"),
			),
			self._makeAnswerLengthSetting(
				"maxLength",
				# Translators: The label for the maximum expected CAPTCHA answer length.
				_("Maximum answer l&ength:"),
			),
			TextInputEngineSetting(
				name="instructionText",
				# Translators: The label for optional instructions sent to CAPTCHA service workers.
				displayNameWithAccelerator=_("Worker &instruction (max 140 characters):"),
			),
			ReadOnlyEngineSetting(
				name="balance",
				# Translators: The label for the CAPTCHA service account balance field.
				displayNameWithAccelerator=_("Account &balance:"),
			),
			ButtonEngineSetting(
				name="checkBalance",
				# Translators: The label for a button that checks CAPTCHA service account balance.
				displayNameWithAccelerator=_("Check account &balance"),
			),
		]

	@property
	def serviceType(self) -> str:
		return self._serviceType

	@serviceType.setter
	def serviceType(self, value: str) -> None:
		if value in self.availableServiceTypes:
			self._serviceType = value

	@property
	def answerType(self) -> str:
		return self._answerType

	@answerType.setter
	def answerType(self, value: str) -> None:
		if value in self.availableAnswerTypes:
			self._answerType = value

	@property
	def language(self) -> str:
		return self._language

	@language.setter
	def language(self, value: str) -> None:
		if value in self.availableLanguages:
			self._language = value

	@property
	def instructionText(self) -> str:
		return self._instructionText

	@instructionText.setter
	def instructionText(self, value: str) -> None:
		self._instructionText = value.strip()

	@property
	def apikey(self) -> str:
		return self._apiKey

	@apikey.setter
	def apikey(self, value: str) -> None:
		self._apiKey = value.strip() if isinstance(value, str) else ""

	@property
	def minLength(self) -> int:
		return self._minLength

	@minLength.setter
	def minLength(self, value: int) -> None:
		self._minLength = self._normalizeAnswerLength(value)

	@property
	def maxLength(self) -> int:
		return self._maxLength

	@maxLength.setter
	def maxLength(self, value: int) -> None:
		self._maxLength = self._normalizeAnswerLength(value)

	@property
	def balance(self) -> str:
		balance = self._getBalanceByCredential().get(self._getBalanceCredentialKey())
		if balance is not None:
			return balance
		# Translators: The CAPTCHA account balance value shown before it has been checked.
		return _("Not checked")

	@balance.setter
	def balance(self, value: str) -> None:
		self._getBalanceByCredential()[self._getBalanceCredentialKey()] = value

	@property
	def availableServiceTypes(self) -> dict:
		"""
		Provides supported CAPTCHA service choices for the settings UI.

		:returns: A dictionary of service IDs to display names.
		"""
		serviceTypes = {
			SERVICE_TYPE_RUCAPTCHA: _("RuCaptcha"),
			SERVICE_TYPE_2CAPTCHA: _("2Captcha"),
		}
		return self.generateStringSettings(serviceTypes)

	@property
	def availableAnswerTypes(self) -> dict:
		"""
		Provides supported CAPTCHA answer type hints for the settings UI.

		:returns: A dictionary of answer type IDs to display names.
		"""
		answerTypes = {
			# Translators: A CAPTCHA answer type setting option.
			ANSWER_TYPE_UNSPECIFIED: _("Unspecified"),
			# Translators: A CAPTCHA answer type setting option.
			ANSWER_TYPE_NUMBERS_ONLY: _("Numbers only"),
			# Translators: A CAPTCHA answer type setting option.
			ANSWER_TYPE_LETTERS_ONLY: _("Letters only"),
			# Translators: A CAPTCHA answer type setting option.
			ANSWER_TYPE_NUMBERS_OR_LETTERS: _("Numbers or letters"),
			# Translators: A CAPTCHA answer type setting option.
			ANSWER_TYPE_NUMBERS_AND_LETTERS: _("Numbers and letters"),
		}
		return self.generateStringSettings(answerTypes)

	@property
	def availableLanguages(self) -> dict:
		"""
		Provides supported CAPTCHA language hints for the settings UI.

		:returns: A dictionary of language IDs to display names.
		"""
		languages = {
			# Translators: A CAPTCHA language setting option.
			LANGUAGE_UNSPECIFIED: _("Unspecified"),
			# Translators: A CAPTCHA language setting option.
			LANGUAGE_CYRILLIC: _("Cyrillic only"),
			# Translators: A CAPTCHA language setting option.
			LANGUAGE_LATIN: _("Latin only"),
		}
		return self.generateStringSettings(languages)

	@staticmethod
	def _makeAnswerLengthSetting(name: str, displayNameWithAccelerator: str) -> NumericEngineSetting:
		setting = NumericEngineSetting(
			name=name,
			displayNameWithAccelerator=displayNameWithAccelerator,
			minStep=1,
			largeStep=5,
		)
		setting.minVal = MIN_ANSWER_LENGTH
		setting.maxVal = MAX_ANSWER_LENGTH
		setting.configSpec = f"integer(default=0,min={MIN_ANSWER_LENGTH},max={MAX_ANSWER_LENGTH})"
		return setting

	@classmethod
	def check(cls) -> bool:
		"""
		Checks if the engine is available.

		:returns: Always True because this is a configurable HTTP engine.
		"""
		return True

	def checkBalanceChanger(self, evt: wx.CommandEvent) -> None:
		"""
		Checks the configured CAPTCHA service account balance.

		:param evt: The button event.
		"""
		evt.Skip()
		parent = evt.GetEventObject().GetParent()
		threading.Thread(
			name="VisAwareCaptchaBalanceThread",
			target=self._checkBalanceWorker,
			args=(parent, self._getBalanceCredentialKey()),
			daemon=True,
		).start()

	def _checkBalanceWorker(self, parent: wx.Window, credential: tuple[str, str]) -> None:
		try:
			balance = self._makeClient(serviceType=credential[0], apiKey=credential[1]).getBalance()
		except Exception as e:
			wx.CallAfter(self._onBalanceFailed, str(e), credential)
			return
		wx.CallAfter(self._onBalanceChecked, parent, balance, credential)

	def _onBalanceChecked(
		self,
		parent: wx.Window,
		balance: str,
		credential: tuple[str, str],
	) -> None:
		self._getBalanceByCredential()[credential] = balance
		if credential != self._getBalanceCredentialKey():
			return
		# Translators: Reported after retrieving the CAPTCHA service account balance.
		ui.message(_("CAPTCHA balance: {}").format(balance))
		self._refreshSettingsPanel(parent)

	def _onBalanceFailed(self, message: str, credential: tuple[str, str]) -> None:
		if credential != self._getBalanceCredentialKey():
			return
		# Translators: Reported when retrieving CAPTCHA service account balance fails. {} is the error message.
		ui.message(_("Could not get CAPTCHA balance: {}").format(message))

	def _refreshSettingsPanel(self, parent: wx.Window) -> None:
		try:
			if hasattr(parent, "updateDriverSettings"):
				parent.updateDriverSettings()
		except RuntimeError:
			pass

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict[str, Any]:
		"""
		Builds request state consumed by the CAPTCHA client.

		:param imageContent: The base64 encoded serialized image bytes.
		:param request: The request-local recognition options.
		:returns: A small request dictionary for `_handleStandardResponse`.
		"""
		self._validateSettings()
		return {"imageContent": imageContent}

	def _handleStandardResponse(
		self,
		requestParams: dict[str, Any],
		imageInfo: RecogImageInfo,
		cancellationEvent: Any,
		request: RecognitionRequest,
	) -> Any:
		"""Handles text CAPTCHA submission and polling through the private client."""
		client = self._makeClient(
			cancellationChecker=lambda: self._checkCancelled(cancellationEvent),
			requestTimeout=requestParams.get("timeout"),
		)
		apiResult = client.recognizeImage(requestParams["imageContent"])
		self._checkCancelled(cancellationEvent)
		historyEntry = (
			recogHistory.createEntry(self, self.originalImage, apiResult) if self.originalImage else None
		)
		text = self._validateTextResult(apiResult)
		textResult = SimpleTextResult(text)
		if not request.textResult:
			textResult.forceVirtualDocument = True
		return recogHistory.attachEntry(textResult, historyEntry)

	def processApiResult(self, _result: bytes) -> str | bool:
		"""
		Handles error checking from the service response.

		The CAPTCHA client performs request-specific error handling, so this method
		is present only to satisfy the recognizer interface.
		"""
		return False

	def extractText(self, apiResult: dict[str, Any]) -> str:
		"""
		Extracts recognized CAPTCHA text from a stored response.

		:param apiResult: The parsed CAPTCHA response.
		:returns: Recognized CAPTCHA text.
		"""
		return str(apiResult.get("text", "")).strip()

	def _convertToLineResultFormat(self, apiResult: dict[str, Any]) -> list[list[dict[str, Any]]]:
		"""
		Returns no coordinate result for text CAPTCHA recognition.

		Text CAPTCHA services return the answer text, not screen OCR coordinates.
		"""
		return []

	def _validateSettings(self) -> None:
		if not self.apikey.strip():
			# Translators: An error message if the CAPTCHA API key is missing.
			raise AuthenticationError(_("API key is missing. Please configure it in CAPTCHA settings."))
		if self.maxLength and self.minLength > self.maxLength:
			# Translators: An error message when the configured CAPTCHA answer length range is invalid.
			raise ApiError(_("Minimum answer length cannot be greater than maximum answer length."))

	def _makeClient(
		self,
		cancellationChecker: Callable[[], None] | None = None,
		*,
		requestTimeout: float | tuple[float, float] | None = None,
		serviceType: str | None = None,
		apiKey: str | None = None,
	) -> CaptchaTextClient:
		return CaptchaTextClient(
			CaptchaTextClientOptions(
				serviceType=serviceType if serviceType is not None else self.serviceType,
				apiKey=apiKey if apiKey is not None else self.apikey,
				caseSensitive=self.caseSensitive,
				hasMultipleWords=self.hasMultipleWords,
				answerType=self.answerType,
				isMathCaptcha=self.isMathCaptcha,
				minLength=self.minLength,
				maxLength=self.maxLength,
				language=self.language,
				instructionText=self.instructionText,
			),
			cancellationChecker=cancellationChecker,
			requestTimeout=requestTimeout,
		)

	def _normalizeAnswerLength(self, value: int) -> int:
		try:
			length = int(value)
		except (TypeError, ValueError):
			return MIN_ANSWER_LENGTH
		return min(max(length, MIN_ANSWER_LENGTH), MAX_ANSWER_LENGTH)

	def _getBalanceCredentialKey(self) -> tuple[str, str]:
		return (self.serviceType, self.apikey)

	def _getBalanceByCredential(self) -> dict[tuple[str, str], str]:
		balances = self.__dict__.get("_balanceByCredential")
		if balances is None:
			balances = {}
			self._balanceByCredential = balances
		return balances

	def _validateTextResult(self, apiResult: dict[str, Any]) -> str:
		text = self.extractText(apiResult)
		if not text:
			# Translators: An error message for a blank CAPTCHA recognition result.
			raise ApiError(_("CAPTCHA recognition result is blank."))
		return text
