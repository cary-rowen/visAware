# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""A dialog for asking follow-up questions about recognition results."""

from __future__ import annotations

from threading import Event, Thread
from typing import Any, Callable, TYPE_CHECKING

import addonHandler
import ui
import wx
from gui import guiHelper
from gui.dpiScalingHelper import DpiScalingHelperMixinWithoutInit
from logHandler import log

from .conversation import ROLE_ASSISTANT, ConversationContext, QuestionStreamFinished, QuestionStreamText
from .exceptions import CancellationError, OCRError
from .markdownRenderer import showMarkdownBrowseableMessage
from .streamingSpeech import StreamingSpeechPresenter

if TYPE_CHECKING:
	from ._taskCues import TaskCueManager

addonHandler.initTranslation()

# Translators: The sender label for assistant answers in the follow-up dialog.
ANSWER_SENDER = _("Answer")


class AskQuestionFrame(DpiScalingHelperMixinWithoutInit, wx.Frame):
	"""A reusable frame that asks follow-up questions in the background."""

	FRAME_SIZE = (720, 560)
	MIN_FRAME_SIZE = (650, 500)
	MESSAGE_MIN_SIZE = (620, 360)
	QUESTION_MIN_SIZE = (460, 80)

	def __init__(
		self,
		parent: wx.Window | None,
		context: ConversationContext | None,
		taskCues: TaskCueManager,
	) -> None:
		# Translators: The title of the follow-up question dialog.
		super().__init__(parent=parent, title=_("Ask a Follow-up Question"))
		self.SetName("visAwareAskQuestionFrame")
		self._taskCues = taskCues
		self._cueHandle: Event | None = None
		self._cancellationEvent: Event | None = None
		self._requestSequence = 0
		self._activeRequestSequence: int | None = None
		self._streamingSpeechPresenter = StreamingSpeechPresenter()
		self._streamingAnswerRequestSequence: int | None = None
		self._streamingAnswerTextStartPosition: int | None = None
		self._streamingAnswerText = ""
		self._formattedContent = ""
		# First-question pending mode: when set, the next send triggers an image
		# recognition via the callback instead of a follow-up model call, and the
		# recognition result is back-filled as the answer to this question.
		self._pendingDescribe: ConversationContext | None = None
		self._onPendingDescribeSend: Callable[[str], None] | None = None
		self._makeControls()
		self._setContext(context)
		self.SetMinSize(self.scaleSize(self.MIN_FRAME_SIZE))
		self.SetSize(self.scaleSize(self.FRAME_SIZE))
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
		self._messagesText.SetMinSize(self.scaleSize(self.MESSAGE_MIN_SIZE))
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
		self._questionText.SetMinSize(self.scaleSize(self.QUESTION_MIN_SIZE))
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
		# Translators: The label for a button that opens the latest content rendered as Markdown.
		self._renderedContentButton = buttonHelper.addButton(panel, label=_("View &formatted content"))
		self._renderedContentButton.Bind(wx.EVT_BUTTON, self._onOpenRenderedContent)
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

	def _setContext(self, context: ConversationContext | None) -> None:
		"""
		Loads a new conversation context into the frame and resets pending-first-question mode.

		:param context: The context to display and use for future questions, or ``None``
			to start in an empty (placeholder) state.
		"""
		self._cancelWorker()
		self._context = context
		# Loading a context always leaves pending-first-question mode; use
		# setPendingDescribe to enter it separately.
		self._pendingDescribe = None
		self._onPendingDescribeSend = None
		self._messagesText.SetValue("")
		self._formattedContent = context.initialText if context else ""
		if context:
			# Translators: The sender label for the original image description in the follow-up dialog.
			self._appendMessage(_("Image description"), context.initialText, report=False)
			for turn in context.turns:
				if turn.role == "user":
					# Translators: The sender label for the user in the follow-up dialog.
					sender = _("You")
				else:
					sender = ANSWER_SENDER
					if turn.role == ROLE_ASSISTANT:
						self._formattedContent = turn.text
				self._appendMessage(sender, turn.text, report=False)
		self._questionText.SetValue("")
		self._setSendButtonEnabled(True)
		self.Layout()

	def setContext(self, context: ConversationContext | None) -> None:
		"""Public wrapper for :meth:`_setContext` (used by the global plugin)."""
		self._setContext(context)

	@property
	def pendingDescribeActive(self) -> bool:
		"""Whether the frame is waiting for a describe-before question to be sent."""
		return self._pendingDescribe is not None

	def setPendingDescribe(
		self,
		context: ConversationContext,
		onSend: Callable[[str], None],
	) -> None:
		"""
		Puts the frame into "pending first question" mode for a describe-before question.

		While pending, the conversation history shows the image description as the
		original context, but sending a question does NOT start a follow-up model call.
		Instead the question becomes the describe prompt (via the one-shot
		``engine._initialPromptForDescribe``) and the callback is invoked to trigger an
		image recognition. Once ``backfillPendingDescribe`` is called with the result,
		the frame switches to normal follow-up mode.

		:param context: The context that will be used for follow-up questions after
			the recognition completes. It may carry the to-be-described image.
		:param onSend: Called on the main thread with the question text when the user
			presses send while the frame is still pending.
		"""
		# Show an empty conversation history until the first question is answered.
		self._setContext(None)
		self._pendingDescribe = context
		self._onPendingDescribeSend = onSend

	def backfillPendingDescribe(self, question: str, result: Any) -> None:
		"""
		Back-fills the result of a pending describe-before question into the frame.

		This is called by the recognition result handler (on the main thread) once the
		image recognition triggered by a pending first question has finished. The
		question/answer pair is appended to the conversation history as the first
		exchange, a real context for future follow-ups is established, and the frame
		switches out of pending first-question mode.

		:param question: The question that was sent while the frame was pending.
		:param result: The recognition result whose text is used as the answer.
		"""
		pendingContext = self._pendingDescribe
		if pendingContext is None:
			return
		answer = getattr(result, "text", None) or ""
		if not isinstance(answer, str) or not answer.strip():
			# An unusable result: lift the in-flight guard but stay in pending mode
			# so the user can send a new question.
			self._activeRequestSequence = None
			self._cancellationEvent = None
			self._setSendButtonEnabled(True)
			return
		self._pendingDescribe = None
		self._onPendingDescribeSend = None
		if not self._context:
			self._context = pendingContext
		if self._context is pendingContext:
			pendingContext.initialText = pendingContext.initialText or answer
			pendingContext.addExchange(question, answer)
			self._formattedContent = answer
		# The "You" half of the exchange was already appended on send; add only the answer.
		# Speech is left to the normal result display chain, which announces the text.
		self._appendMessage(ANSWER_SENDER, answer, report=False)
		# The pending send was answered by the recognition back-fill, so lift the
		# in-flight guard and let the next send go through immediately.
		self._activeRequestSequence = None
		self._cancellationEvent = None
		self._setSendButtonEnabled(True)

	def failPendingDescribe(self, message: str, report: bool = True) -> None:
		"""
		Reports a failed or cancelled describe-before recognition in the frame.

		The frame stays in pending mode, so sending a new question re-triggers the
		recognition via the original callback. The error is shown in the history and
		sending is re-enabled.

		:param message: The error message to show as the failed answer.
		:param report: Whether to announce the error via ``ui.message`` as well.
		"""
		if self._pendingDescribe is None:
			return
		self._activeRequestSequence = None
		self._cancellationEvent = None
		if message:
			# Translators: The sender label for an error shown in the follow-up dialog.
			self._appendMessage(_("Error"), message, report=report)
		self._setSendButtonEnabled(True)

	def focusQuestionInput(self) -> None:
		"""Moves focus to the question edit field."""
		self._questionText.SetFocus()

	def _restoreHistoryCaret(self, caret: int) -> None:
		"""Puts the history caret back where the user left it, anchored in view."""
		self._messagesText.SetInsertionPoint(caret)
		self._messagesText.ShowPosition(caret)

	def _appendToHistory(self, text: str) -> None:
		"""Appends text to the conversation history without moving the caret.

		``AppendText`` implicitly moves the insertion point to the end of the
		control (dragging the view along with it), so the caret is restored
		afterwards to wherever the user left it. Freshly arrived text simply
		grows below the reading position, like an overflowing browser page.
		"""
		caret = self._messagesText.GetInsertionPoint()
		self._messagesText.Freeze()
		try:
			self._messagesText.AppendText(text)
			self._restoreHistoryCaret(caret)
		finally:
			self._messagesText.Thaw()

	def _appendMessage(self, sender: str, text: str, report: bool = True) -> None:
		if not text:
			return
		currentText = self._messagesText.GetValue()
		displayMessageText = f"{sender}:\n{text}"
		if currentText:
			self._appendToHistory(f"\n\n{displayMessageText}")
		else:
			self._messagesText.SetValue(displayMessageText)
		# Keep the caret where the user left it (no auto-scroll to the end).
		if report:
			ui.message(f"{sender}: {text}")

	def _onSend(self, evt: wx.CommandEvent | wx.KeyEvent) -> None:
		if isinstance(evt, wx.CommandEvent):
			evt.Skip()
		if self._activeRequestSequence is not None:
			# Translators: Reported while a follow-up question is still being answered.
			ui.message(_("Waiting for answer."))
			return
		if self._pendingDescribe is not None:
			callback = self._onPendingDescribeSend
			if callback is None:
				# Translators: Reported while a follow-up question is still being answered.
				ui.message(_("Waiting for answer."))
				return
			question = self._questionText.GetValue().strip()
			if not question:
				# Translators: Reported when the user tries to send an empty follow-up question.
				ui.message(_("Enter a question."))
				self._questionText.SetFocus()
				return
			self._questionText.SetValue("")
			# Translators: The sender label for the user in the follow-up dialog.
			self._appendMessage(_("You"), question, report=False)
			self._activeRequestSequence = self._requestSequence + 1
			self._requestSequence = self._activeRequestSequence
			self._setSendButtonEnabled(False)
			# Translators: Reported while a follow-up question is being answered.
			ui.message(_("Waiting for answer."))
			callback(question)
			return
		self._cancelStreamingAnswer()
		question = self._questionText.GetValue().strip()
		if not question:
			# Translators: Reported when the user tries to send an empty follow-up question.
			ui.message(_("Enter a question."))
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
		self._cueHandle = self._taskCues.start()
		try:
			thread = Thread(
				name="VisAwareAskQuestionThread",
				target=self._askWorker,
				args=(requestSequence, context, question, self._cancellationEvent),
				daemon=True,
			)
			thread.start()
		except Exception:
			log.error("Could not start follow-up question worker.", exc_info=True)
			self._onAskFailed(requestSequence, _("Question failed with an unexpected error."))

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
						event.response,
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
		if text.strip():
			self._taskCues.stop(self._cueHandle)
		if self._streamingAnswerRequestSequence != requestSequence:
			self._startStreamingAnswer(requestSequence)
		if replace:
			self._replaceStreamingAnswerText(text)
			self._streamingSpeechPresenter.cancel()
			self._streamingSpeechPresenter.start()
			self._streamingSpeechPresenter.addText(text)
			return
		self._appendToHistory(text)
		self._streamingAnswerText += text
		self._streamingSpeechPresenter.addText(text)

	def _startStreamingAnswer(self, requestSequence: int) -> None:
		currentText = self._messagesText.GetValue()
		messagePrefix = "\n\n" if currentText else ""
		self._appendToHistory(f"{messagePrefix}{ANSWER_SENDER}:\n")
		self._streamingAnswerRequestSequence = requestSequence
		self._streamingAnswerTextStartPosition = self._messagesText.GetLastPosition()
		self._streamingAnswerText = ""
		self._streamingSpeechPresenter.start()

	def _replaceStreamingAnswerText(self, text: str) -> None:
		if self._streamingAnswerTextStartPosition is None:
			return
		currentText = self._messagesText.GetValue()
		caret = self._messagesText.GetInsertionPoint()
		self._messagesText.SetValue(f"{currentText[: self._streamingAnswerTextStartPosition]}{text}")
		# SetValue resets the caret to the start; keep it where the user left it.
		self._messagesText.SetInsertionPoint(min(caret, self._messagesText.GetLastPosition()))
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
				self._appendToHistory(missingText)
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
		response: dict[str, Any] | None = None,
	) -> None:
		wasStreaming = self._streamingAnswerRequestSequence == requestSequence
		if not self._finishRequest(requestSequence):
			return
		answerForContext = answer
		if incompleteReason:
			answerForContext = f"{answer}\n\n{incompleteReason}"
		self._context.addExchange(question, answerForContext, response=response)
		self._formattedContent = answerForContext
		if wasStreaming:
			self._finishStreamingAnswer(requestSequence, answer)
		else:
			self._appendMessage(ANSWER_SENDER, answer)
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
		self._taskCues.stop(self._cueHandle)
		self._cueHandle = None
		self._activeRequestSequence = None
		self._cancellationEvent = None
		return True

	def _setSendButtonEnabled(self, enabled: bool) -> None:
		self._sendButton.Enable(enabled)
		self._updateRenderedContentButton()

	def _updateRenderedContentButton(self) -> None:
		if hasattr(self, "_renderedContentButton"):
			self._renderedContentButton.Enable(
				bool(self._formattedContent) and self._activeRequestSequence is None,
			)

	def _onOpenRenderedContent(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		if not self._formattedContent:
			# Translators: Reported when there is no follow-up content to show in rendered form.
			ui.message(_("There is no content to show."))
			return
		# Translators: The title for the browseable message window showing rendered follow-up content.
		showMarkdownBrowseableMessage(
			self._formattedContent,
			title=_("Formatted Content"),
			closeButton=True,
			copyButton=True,
		)

	def _cancelWorker(self) -> None:
		self._taskCues.stop(self._cueHandle)
		self._cueHandle = None
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
