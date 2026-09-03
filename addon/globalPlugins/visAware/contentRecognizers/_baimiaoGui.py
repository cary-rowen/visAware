# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Account login dialog for Baimiao Web OCR."""

from __future__ import annotations

import addonHandler
import gui
from gui import guiHelper
from gui.nvdaControls import DPIScaledDialog
from logHandler import log
from threading import Event, Thread
import wx

from ..exceptions import CancellationError, OCRError
from ._baimiaoWeb import BaimiaoWebClient

addonHandler.initTranslation()


class BaimiaoLoginDialog(DPIScaledDialog):
	"""Collects a Baimiao account and performs login off the GUI thread."""

	def __init__(self, parent: wx.Window | None) -> None:
		# Translators: The title of the Baimiao login dialog.
		super().__init__(parent, title=_("Log in to Baimiao"))
		self._cancelEvent = Event()
		self._loginThread: Thread | None = None
		self._isClosing = False
		self._makeSettings()
		self.CentreOnParent()
		self.Bind(wx.EVT_CLOSE, self._onClose)

	def _makeSettings(self) -> None:
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		settingsHelper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
		# Translators: The label for a Baimiao email address or mobile number field.
		self._accountCtrl = settingsHelper.addLabeledControl(_("&Email or mobile number"), wx.TextCtrl)
		# Translators: The label for a Baimiao password field.
		self._passwordCtrl = settingsHelper.addLabeledControl(
			_("&Password"),
			wx.TextCtrl,
			style=wx.TE_PASSWORD,
		)

		buttonHelper = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: The label for a button that submits Baimiao login information.
		self._loginButton = buttonHelper.addButton(self, id=wx.ID_OK, label=_("&Log in"))
		# Translators: The label for a button that cancels Baimiao login.
		self._cancelButton = buttonHelper.addButton(self, id=wx.ID_CANCEL, label=_("&Cancel"))
		settingsHelper.addDialogDismissButtons(buttonHelper, separated=True)
		self.Bind(wx.EVT_BUTTON, self._onLogin, id=wx.ID_OK)
		self.Bind(wx.EVT_BUTTON, self._onCancel, id=wx.ID_CANCEL)

		mainSizer.Add(settingsHelper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL)
		mainSizer.Fit(self)
		self.SetSizer(mainSizer)
		self.AffirmativeId = wx.ID_OK
		self.EscapeId = wx.ID_CANCEL
		self._accountCtrl.SetFocus()

	def _onLogin(self, _evt: wx.CommandEvent) -> None:
		if self._loginThread and self._loginThread.is_alive():
			return
		account = self._accountCtrl.GetValue().strip()
		password = self._passwordCtrl.GetValue()
		if not account or not password:
			gui.messageBox(
				# Translators: An error when a Baimiao login field is empty.
				_("Enter your Baimiao account and password."),
				# Translators: The title of a Baimiao login error dialog.
				_("Baimiao Login Failed"),
				wx.OK | wx.ICON_WARNING,
				self,
			)
			(self._accountCtrl if not account else self._passwordCtrl).SetFocus()
			return
		self._passwordCtrl.ChangeValue("")
		self._cancelEvent.clear()
		self._setControlsEnabled(False)
		self._loginThread = Thread(
			name="BaimiaoLoginThread",
			target=self._loginWorker,
			args=(account, password),
			daemon=True,
		)
		self._loginThread.start()

	def _loginWorker(self, account: str, password: str) -> None:
		try:
			BaimiaoWebClient.login(account, password, cancellationChecker=self._checkCancelled)
		except CancellationError:
			return
		except OCRError as e:
			log.warning("Baimiao login failed.", exc_info=True)
			wx.CallAfter(self._onLoginFailed, str(e), False)
		except Exception:
			log.error("Unexpected Baimiao login failure.", exc_info=True)
			wx.CallAfter(self._onLoginFailed, "", True)
		else:
			wx.CallAfter(self._onLoginSucceeded)

	def _checkCancelled(self) -> None:
		if self._cancelEvent.is_set() or self._isClosing:
			raise CancellationError("Baimiao login cancelled.", self._cancelEvent)

	def _onLoginSucceeded(self) -> None:
		if self._isClosing:
			return
		self._setControlsEnabled(True)
		gui.messageBox(
			# Translators: A message shown when Baimiao login succeeds.
			_("Logged in to Baimiao successfully."),
			# Translators: The title of a dialog shown when Baimiao login succeeds.
			_("Baimiao Login Successful"),
			wx.OK | wx.ICON_INFORMATION,
			self,
		)
		self._isClosing = True
		self.EndModal(wx.ID_OK)

	def _onLoginFailed(self, message: str, unexpected: bool) -> None:
		if self._isClosing:
			return
		self._setControlsEnabled(True)
		if unexpected:
			# Translators: An error when Baimiao login fails unexpectedly.
			message = _("Baimiao login failed with an unexpected error.")
			style = wx.OK | wx.ICON_ERROR
		else:
			style = wx.OK | wx.ICON_WARNING
		gui.messageBox(
			message,
			# Translators: The title of a Baimiao login error dialog.
			_("Baimiao Login Failed"),
			style,
			self,
		)
		self._passwordCtrl.SetFocus()

	def _setControlsEnabled(self, enabled: bool) -> None:
		self._accountCtrl.Enable(enabled)
		self._passwordCtrl.Enable(enabled)
		self._loginButton.Enable(enabled)
		self._cancelButton.Enable(True)

	def _onCancel(self, _evt: wx.CommandEvent) -> None:
		self._isClosing = True
		self._cancelEvent.set()
		self.EndModal(wx.ID_CANCEL)

	def _onClose(self, evt: wx.CloseEvent) -> None:
		self._isClosing = True
		self._cancelEvent.set()
		evt.Skip()

	def Destroy(self) -> bool:
		self._isClosing = True
		self._cancelEvent.set()
		return super().Destroy()
