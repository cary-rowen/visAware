# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""A dialog for asking follow-up questions about recognition results."""

from __future__ import annotations

from threading import Event, Thread

import addonHandler
import ui
import wx
from gui import guiHelper
from logHandler import log

from .conversation import ROLE_ASSISTANT, ConversationContext, QuestionStreamFinished, QuestionStreamText
from .exceptions import CancellationError, OCRError
from .markdownRenderer import showMarkdownBrowseableMessage
from .streamingSpeech import StreamingSpeechPresenter

addonHandler.initTranslation()


class AskQuestionFrame(wx.Frame):
	"""A reusable frame that asks follow-up questions in the background."""

	FRAME_SIZE = (720, 560)
	MIN_FRAME_SIZE = (650, 500)
	MESSAGE_MIN_SIZE = (620, 360)
	QUESTION_MIN_SIZE = (460, 80)

	def __init__(self, parent: wx.Window | None, context: ConversationContext) -> None:
		# Translators: The title of the follow-up question dialog.
		super().__init__(parent=parent, title=_("Ask a question"))
		self.SetName("visAwareAskQuestionFrame")
		self._context = context
		self._cancellationEvent: Event | None = None
		self._requestSequence = 0
		self._activeRequestSequence: int | None = None
		self._streamingSpeechPresenter = StreamingSpeechPresenter()
		self._streamingAnswerRequestSequence: int | None = None
		self._streamingAnswerTextStartPosition: int | None = None
		self._streamingAnswerText = ""
		self._latestAnswerText = ""
		self._makeControls()
		self.setContext(context)
		self.SetMinSize(self.MIN_FRAME_SIZE)
		self.SetSize(self.FRAME_SIZE)
		self.CenterOnScreen()
		self.Bind(wx.EVT_CLOSE, self._onClose)

	def _makeControls(self) -> None:
		panel = wx.Panel(self, style=wx.TAB_TRAVERSAL)
		mainSizer = wx.BoxSizer(wx.VERTICAL)

		conversationSizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: The label for the conversation history read-only text field in the follow-up dialog.
		conversationLabel = wx.StaticText(panel, label=_("Conversation &history:"))
		conversationSizer.Add(conversationLabel)
		conversationSizer.AddSpacer(guiHelper.SPACE_BETWEEN_ASSOCIATED_CONTROL_VERTICAL)
		self._messagesText = wx.TextCtrl(
			panel,
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
		)
		self._messagesText.SetMinSize(self.MESSAGE_MIN_SIZE)
		conversationSizer.Add(self._messagesText, proportion=1, flag=wx.EXPAND)
		mainSizer.Add(
			conversationSizer,
			proportion=1,
			flag=wx.EXPAND | wx.ALL,
			border=guiHelper.BORDER_FOR_DIALOGS,
		)

		questionSizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: The label for the follow-up question edit field.
		questionLabel = wx.StaticText(panel, label=_("&Question:"))
		questionSizer.Add(questionLabel)
		questionSizer.AddSpacer(guiHelper.SPACE_BETWEEN_ASSOCIATED_CONTROL_VERTICAL)
		self._questionText = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_PROCESS_ENTER)
		self._questionText.SetMinSize(self.QUESTION_MIN_SIZE)
		questionSizer.Add(self._questionText, flag=wx.EXPAND)
		mainSizer.Add(
			questionSizer,
			flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
			border=guiHelper.BORDER_FOR_DIALOGS,
		)

		buttonHelper = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: The label for a button that sends a follow-up question.
		self._sendButton = buttonHelper.addButton(panel, label=_("&Send"))
		self._sendButton.Bind(wx.EVT_BUTTON, self._onSend)
		# Translators: The label for a button that opens the latest answer rendered as Markdown.
		self._renderedAnswerButton = buttonHelper.addButton(panel, label=_("Open rendered &answer"))
		self._renderedAnswerButton.Bind(wx.EVT_BUTTON, self._onOpenRenderedAnswer)
		# Translators: The label for a button that closes the follow-up question dialog.
		self._closeButton = buttonHelper.addButton(panel, label=_("&Close"))
		self._closeButton.Bind(wx.EVT_BUTTON, self._onClose)
		mainSizer.Add(
			buttonHelper.sizer,
			flag=wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM,
			border=guiHelper.BORDER_FOR_DIALOGS,
		)

		panel.SetSizer(mainSizer)
		frameSizer = wx.BoxSizer(wx.VERTICAL)
		frameSizer.Add(panel, proportion=1, flag=wx.EXPAND)
		self.SetSizer(frameSizer)
		self.Bind(wx.EVT_CHAR_HOOK, self._onCharHook)

	def setContext(self, context: ConversationContext) -> None:
		"""
		Loads a new conversation context into the frame.

		:param context: The context to display and use for future questions.
		"""
		self._cancelWorker()
		self._context = context
		engineDescription = context.engineDescription or context.engineName
		if engineDescription:
			self.SetTitle(f"{_('Ask a question')} - {engineDescription}")
		else:
			self.SetTitle(_("Ask a question"))
		self._messagesText.SetValue("")
		self._latestAnswerText = ""
		# Translators: The sender label for the original image description in the follow-up dialog.
		self._appendMessage(_("Image description"), context.initialText, report=False)
		for turn in context.turns:
			if turn.role == "user":
				# Translators: The sender label for the user in the follow-up dialog.
				sender = _("You")
			else:
				sender = context.engineDescription
				if turn.role == ROLE_ASSISTANT:
					self._latestAnswerText = turn.text
			self._appendMessage(sender, turn.text, report=False)
		self._questionText.SetValue("")
		self._setSendButtonEnabled(True)
		self.Layout()

	def focusQuestionInput(self) -> None:
		"""Moves focus to the question edit field."""
		self._questionText.SetFocus()

	def _appendMessage(self, sender: str, text: str, report: bool = True) -> None:
		if not text:
			return
		currentText = self._messagesText.GetValue()
		displayMessageText = f"{sender}:\n{text}"
		if currentText:
			self._messagesText.AppendText(f"\n\n{displayMessageText}")
		else:
			self._messagesText.SetValue(displayMessageText)
		self._messagesText.SetInsertionPointEnd()
		if report:
			ui.message(f"{sender}: {text}")

	def _onSend(self, evt: wx.CommandEvent | wx.KeyEvent) -> None:
		if isinstance(evt, wx.CommandEvent):
			evt.Skip()
		if self._activeRequestSequence is not None:
			# Translators: Reported while a follow-up question is still being answered.
			ui.message(_("Waiting for answer."))
			return
		self._cancelStreamingAnswer()
		question = self._questionText.GetValue().strip()
		if not question:
			# Translators: Reported when the user tries to send an empty follow-up question.
			ui.message(_("Question is blank."))
			self._questionText.SetFocus()
			return
		self._questionText.SetValue("")
		# Translators: The sender label for the user in the follow-up dialog.
		self._appendMessage(_("You"), question, report=False)
		self._requestSequence += 1
		requestSequence = self._requestSequence
		context = self._context
		self._activeRequestSequence = requestSequence
		self._setSendButtonEnabled(False)
		# Translators: Reported while a follow-up question is being answered.
		ui.message(_("Waiting for answer."))
		self._cancellationEvent = Event()
		thread = Thread(
			name="VisAwareAskQuestionThread",
			target=self._askWorker,
			args=(requestSequence, context, question, self._cancellationEvent),
			daemon=True,
		)
		thread.start()

	def _askWorker(
		self,
		requestSequence: int,
		context: ConversationContext,
		question: str,
		cancellationEvent: Event,
	) -> None:
		try:
			for event in context.engine.askQuestionEvents(
				context,
				question,
				lambda: self._checkCancelled(cancellationEvent),
			):
				self._checkCancelled(cancellationEvent)
				if isinstance(event, QuestionStreamText):
					wx.CallAfter(self._onAskTextReceived, requestSequence, event.text, event.replace)
				elif isinstance(event, QuestionStreamFinished):
					wx.CallAfter(
						self._onAskSucceeded,
						requestSequence,
						question,
						event.text,
						event.incompleteReason,
					)
					return
			raise RuntimeError("Question answer stream ended without a final answer.")
		except CancellationError:
			wx.CallAfter(self._onAskCancelled, requestSequence)
		except OCRError as e:
			log.warning("Follow-up question failed.", exc_info=True)
			wx.CallAfter(self._onAskFailed, requestSequence, str(e))
		except Exception:
			log.error("Unexpected follow-up question failure.", exc_info=True)
			# Translators: Reported when a follow-up question fails unexpectedly.
			wx.CallAfter(self._onAskFailed, requestSequence, _("Question failed with an unexpected error."))

	def _checkCancelled(self, cancellationEvent: Event) -> None:
		if cancellationEvent.is_set():
			raise CancellationError("Question was cancelled.", cancellationEvent)

	def _onAskTextReceived(self, requestSequence: int, text: str, replace: bool = False) -> None:
		if requestSequence != self._activeRequestSequence or not text:
			return
		if self._streamingAnswerRequestSequence != requestSequence:
			self._startStreamingAnswer(requestSequence)
		if replace:
			self._replaceStreamingAnswerText(text)
			self._streamingSpeechPresenter.cancel()
			self._streamingSpeechPresenter.start()
			self._streamingSpeechPresenter.addText(text)
			return
		self._messagesText.AppendText(text)
		self._messagesText.SetInsertionPointEnd()
		self._streamingAnswerText += text
		self._streamingSpeechPresenter.addText(text)

	def _startStreamingAnswer(self, requestSequence: int) -> None:
		currentText = self._messagesText.GetValue()
		messagePrefix = "\n\n" if currentText else ""
		self._messagesText.AppendText(f"{messagePrefix}{self._context.engineDescription}:\n")
		self._messagesText.SetInsertionPointEnd()
		self._streamingAnswerRequestSequence = requestSequence
		self._streamingAnswerTextStartPosition = self._messagesText.GetLastPosition()
		self._streamingAnswerText = ""
		self._streamingSpeechPresenter.start()

	def _replaceStreamingAnswerText(self, text: str) -> None:
		if self._streamingAnswerTextStartPosition is None:
			return
		currentText = self._messagesText.GetValue()
		self._messagesText.SetValue(f"{currentText[: self._streamingAnswerTextStartPosition]}{text}")
		self._messagesText.SetInsertionPointEnd()
		self._streamingAnswerText = text

	def _finishStreamingAnswer(self, requestSequence: int, answer: str) -> bool:
		if self._streamingAnswerRequestSequence != requestSequence:
			return False
		if answer:
			missingText = ""
			if not self._streamingAnswerText:
				missingText = answer
			elif answer.startswith(self._streamingAnswerText):
				missingText = answer[len(self._streamingAnswerText) :]
			if missingText:
				self._messagesText.AppendText(missingText)
				self._messagesText.SetInsertionPointEnd()
				self._streamingAnswerText += missingText
				self._streamingSpeechPresenter.addText(missingText)
		self._streamingSpeechPresenter.finish()
		self._clearStreamingAnswer()
		return True

	def _cancelStreamingAnswer(self) -> None:
		if self._streamingAnswerRequestSequence is not None or self._streamingSpeechPresenter.isActive:
			self._streamingSpeechPresenter.cancel()
		self._clearStreamingAnswer()

	def _clearStreamingAnswer(self) -> None:
		self._streamingAnswerRequestSequence = None
		self._streamingAnswerTextStartPosition = None
		self._streamingAnswerText = ""

	def _onAskSucceeded(
		self,
		requestSequence: int,
		question: str,
		answer: str,
		incompleteReason: str | None = None,
	) -> None:
		wasStreaming = self._streamingAnswerRequestSequence == requestSequence
		if not self._finishRequest(requestSequence):
			return
		answerForContext = answer
		if incompleteReason:
			answerForContext = f"{answer}\n\n{incompleteReason}"
		self._context.addExchange(question, answerForContext)
		self._latestAnswerText = answerForContext
		if wasStreaming:
			self._finishStreamingAnswer(requestSequence, answer)
		else:
			self._appendMessage(self._context.engineDescription, answer)
		if incompleteReason:
			log.warning(f"Follow-up streaming answer may be incomplete. reason={incompleteReason}")
			# Translators: The sender label for an error shown in the follow-up dialog.
			self._appendMessage(_("Error"), incompleteReason)
		self._setSendButtonEnabled(True)

	def _onAskFailed(self, requestSequence: int, message: str) -> None:
		if not self._finishRequest(requestSequence):
			return
		self._cancelStreamingAnswer()
		# Translators: The sender label for an error shown in the follow-up dialog.
		self._appendMessage(_("Error"), message)
		self._setSendButtonEnabled(True)

	def _onAskCancelled(self, requestSequence: int) -> None:
		if not self._finishRequest(requestSequence):
			return
		self._cancelStreamingAnswer()
		self._setSendButtonEnabled(True)

	def _finishRequest(self, requestSequence: int) -> bool:
		if requestSequence != self._requestSequence or requestSequence != self._activeRequestSequence:
			return False
		self._activeRequestSequence = None
		self._cancellationEvent = None
		return True

	def _setSendButtonEnabled(self, enabled: bool) -> None:
		self._sendButton.Enable(enabled)
		self._updateRenderedAnswerButton()

	def _updateRenderedAnswerButton(self) -> None:
		if hasattr(self, "_renderedAnswerButton"):
			self._renderedAnswerButton.Enable(
				bool(self._latestAnswerText) and self._activeRequestSequence is None,
			)

	def _onOpenRenderedAnswer(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		if not self._latestAnswerText:
			# Translators: Reported when there is no follow-up answer to show in rendered form.
			ui.message(_("There is no answer to render."))
			return
		# Translators: The title for the browseable message window showing a rendered follow-up answer.
		showMarkdownBrowseableMessage(
			self._latestAnswerText,
			title=_("Rendered answer"),
			closeButton=True,
			copyButton=True,
		)

	def _cancelWorker(self) -> None:
		self._requestSequence += 1
		if self._cancellationEvent:
			self._cancellationEvent.set()
			self._cancellationEvent = None
		self._activeRequestSequence = None
		if hasattr(self, "_streamingSpeechPresenter"):
			self._cancelStreamingAnswer()
		if hasattr(self, "_questionText"):
			self._setSendButtonEnabled(True)

	def _onCharHook(self, evt: wx.KeyEvent) -> None:
		key = evt.GetKeyCode()
		if evt.GetModifiers() == wx.MOD_CONTROL and key == wx.WXK_RETURN:
			self._onSend(evt)
			return
		if key == wx.WXK_ESCAPE:
			self._onClose(evt)
			return
		evt.Skip()

	def _onClose(self, evt: wx.Event) -> None:
		self._cancelWorker()
		self.Hide()

	def Destroy(self) -> bool:
		self._cancelWorker()
		return super().Destroy()
