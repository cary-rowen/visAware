# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An OCR engine that uses the Baimiao Web service."""

from __future__ import annotations

import addonHandler
import gui
from contentRecog import LinesWordsResult, RecogImageInfo, SimpleTextResult
from typing import Any
import wx

from .. import recogHistory
from ..engineGUIHelper import ButtonEngineSetting
from ..exceptions import ApiError
from ..recogHandler import AUTO_RECOGNITION_REQUEST_TIMEOUT, BaseRecognizer, RecognitionRequest
from ._baimiaoGui import BaimiaoLoginDialog
from ._baimiaoWeb import (
	BaimiaoWebClient,
	extractText,
	getStoredAccountSummary,
	hasStoredLogin,
	logoutStoredLoginAsync,
	toLineResult,
)

addonHandler.initTranslation()


class CustomContentRecognizer(BaseRecognizer):
	"""Recognizes text and coordinates with Baimiao OCR."""

	name = "baimiaoOCR"
	# Translators: The description of the Baimiao OCR engine.
	description = _("Baimiao OCR")

	uploadBase64EncodeImage = False
	uploadImageFormat = "PNG"
	supportsStreaming = False
	engineConfigSpec = {"encryptedSession": "string(default='')"}

	@property
	def supportedSettings(self) -> list[Any]:
		"""Returns the account action shown for Baimiao OCR."""

		return [
			ButtonEngineSetting(
				"manageAccount",
				self._accountActionLabel,
			),
		]

	@property
	def manageAccount(self) -> str:
		return ""

	@manageAccount.setter
	def manageAccount(self, _value: str) -> None:
		pass

	@property
	def _accountActionLabel(self) -> str:
		accountSummary = getStoredAccountSummary()
		if accountSummary:
			accountLabel, vip = accountSummary
			accountLabel = accountLabel.replace("&", "&&")
			membership = {
				# Translators: A Baimiao account membership level.
				0: _("Regular user"),
				# Translators: A Baimiao account membership level.
				1: _("Regular member"),
				# Translators: A Baimiao account membership level.
				2: _("Gold member"),
				# Translators: A Baimiao account membership level.
				3: _("Super member"),
			}.get(vip)
			if accountLabel and membership:
				# Translators: The label for a button that logs out of Baimiao. {account} is a
				# nickname, masked login, or user ID; {membership} is the account membership level.
				return _("Log out of Baimiao ({account}, {membership})").format(
					account=accountLabel,
					membership=membership,
				)
			if accountLabel:
				# Translators: The label for a button that logs out of Baimiao. {account} is a
				# nickname, masked login, or user ID.
				return _("Log out of Baimiao ({account})").format(account=accountLabel)
			# Translators: The label for a button that logs out of Baimiao when account details are unavailable.
			return _("Log out of Baimiao (logged in)")
		# Translators: The label for a button that opens Baimiao login.
		return _("Log in to Baimiao...")

	def manageAccountChanger(self, evt: wx.CommandEvent) -> None:
		"""Opens Baimiao login or clears the current local login."""

		parent = evt.GetEventObject().GetParent()
		if hasStoredLogin():
			self._confirmLogout(parent)
		else:
			self._showLoginDialog(parent)
		self._refreshSettingsPanel(parent)

	def _showLoginDialog(self, parent: wx.Window) -> None:
		dialog = BaimiaoLoginDialog(parent)
		try:
			dialog.ShowModal()
		finally:
			dialog.Destroy()

	def _confirmLogout(self, parent: wx.Window) -> None:
		result = gui.messageBox(
			# Translators: A confirmation shown before clearing the Baimiao login saved by Vis Aware.
			_(
				"Are you sure you want to log out of Baimiao in Vis Aware? "
				"This deletes the Baimiao login saved on this computer, but does not automatically sign "
				"this device out of your Baimiao account. If you no longer use this device, sign it out "
				"in the Baimiao mobile app.",
			),
			# Translators: The title of a confirmation dialog shown before logging out of Baimiao.
			_("Log out of Baimiao"),
			wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
			parent,
		)
		if result != wx.YES:
			return
		try:
			logoutStoredLoginAsync()
		except ApiError as e:
			gui.messageBox(
				str(e),
				# Translators: The title of an error shown when Baimiao logout fails.
				_("Baimiao Logout Failed"),
				wx.OK | wx.ICON_ERROR,
				parent,
			)

	@staticmethod
	def _refreshSettingsPanel(parent: wx.Window) -> None:
		if hasattr(parent, "updateDriverSettings"):
			parent.updateDriverSettings()

	@classmethod
	def check(cls) -> bool:
		"""Returns whether the configurable Baimiao OCR service is available."""

		return True

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict[str, Any]:
		"""Builds request-local image data for the multi-step Web client."""

		return {"imageContent": imageContent}

	def _handleStandardResponse(
		self,
		requestParams: dict[str, Any],
		imageInfo: RecogImageInfo,
		cancellationEvent: Any,
		request: RecognitionRequest,
	) -> Any:
		"""Runs Baimiao's session, permission, upload, and polling sequence."""

		imageContent = requestParams.get("imageContent")
		if not isinstance(imageContent, bytes):
			# Translators: An error when Baimiao receives invalid image content.
			raise ApiError(_("Invalid image content for Baimiao."))
		client = BaimiaoWebClient.fromStoredLogin(
			cancellationChecker=lambda: self._checkCancelled(cancellationEvent),
			requestTimeout=AUTO_RECOGNITION_REQUEST_TIMEOUT if request.isAutomaticRecognition else None,
		)
		apiResult = self._recognizeContent(client, imageContent)
		self._checkCancelled(cancellationEvent)
		historyEntry = (
			recogHistory.createEntry(self, self.originalImage, apiResult) if self.originalImage else None
		)
		text = self.extractText(apiResult)
		if not text:
			# Translators: An error message for a blank recognition result.
			raise ApiError(_("Recognition result is blank."))
		if request.textResult:
			return recogHistory.attachEntry(SimpleTextResult(text), historyEntry)
		lineResult = self._convertToLineResultFormat(apiResult)
		if lineResult:
			return recogHistory.attachEntry(LinesWordsResult(lineResult, imageInfo), historyEntry)
		textOnlyResult = SimpleTextResult(text)
		if getattr(self, "forceBrowseableMessage", False):
			textOnlyResult.forceBrowseableMessage = True
		else:
			textOnlyResult.forceVirtualDocument = True
		return recogHistory.attachEntry(textOnlyResult, historyEntry)

	def _recognizeContent(
		self,
		client: BaimiaoWebClient,
		imageContent: bytes,
	) -> dict[str, Any]:
		return client.recognizeImage(imageContent)

	def processApiResult(self, _result: bytes) -> str | bool:
		"""Returns success because the Web client validates each response."""

		return False

	def extractText(self, apiResult: dict[str, Any]) -> str:
		"""Extracts plain text from a Baimiao OCR result."""

		return extractText(apiResult)

	def _convertToLineResultFormat(self, apiResult: dict[str, Any]) -> list[list[dict[str, Any]]]:
		"""Converts Baimiao OCR locations to NVDA's rich OCR format."""

		return toLineResult(apiResult)
