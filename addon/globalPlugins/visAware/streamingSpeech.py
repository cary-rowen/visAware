# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Buffered speech presentation for streaming text."""

import functools

from speech import cancelSpeech, speak
from speech.commands import CallbackCommand


class StreamingSpeechPresenter:
	"""Speaks streaming text in buffered chunks."""

	MAX_BUFFER_LENGTH = 80
	SOFT_SPLIT_MIN_LENGTH = 16
	STRONG_TERMINATORS = frozenset("\n.!?;\u3002\uff01\uff1f\uff1b")
	SOFT_TERMINATORS = frozenset(",\uff0c\u3001:\uff1a")

	def __init__(self) -> None:
		self._buffer = ""
		self._pendingChunks: list[str] = []
		self._isSpeaking = False
		self._isActive = False
		self._speechSession = 0

	@property
	def isActive(self) -> bool:
		return self._isActive or self._isSpeaking or bool(self._pendingChunks)

	def start(self) -> None:
		"""Starts a new streaming speech session."""
		self._speechSession += 1
		self._buffer = ""
		self._pendingChunks.clear()
		self._isSpeaking = False
		self._isActive = True

	def cancel(self) -> None:
		"""Stops streaming speech and clears any buffered text."""
		self._speechSession += 1
		self._buffer = ""
		self._pendingChunks.clear()
		self._isSpeaking = False
		self._isActive = False
		cancelSpeech()

	def addText(self, text: str) -> None:
		"""Adds new text from a streaming engine and speaks complete chunks."""
		if not text:
			return
		self._buffer += text
		self._queueReadyChunks()
		self._speakNextChunk()

	def finish(self, finalMessage: str | None = None) -> None:
		"""Flushes remaining text when the stream completes."""
		if self._buffer.strip():
			self._pendingChunks.append(self._buffer.strip())
		if finalMessage:
			self._pendingChunks.append(finalMessage)
		self._buffer = ""
		self._isActive = False
		self._speakNextChunk()

	def _queueReadyChunks(self) -> None:
		while self._buffer:
			splitIndex = self._findReadySplit()
			if splitIndex < 0 and len(self._buffer) >= self.MAX_BUFFER_LENGTH:
				splitIndex = self._findLengthSplit()
			if splitIndex < 0:
				return
			chunk = self._buffer[: splitIndex + 1].strip()
			self._buffer = self._buffer[splitIndex + 1 :]
			if chunk:
				self._pendingChunks.append(chunk)

	def _findReadySplit(self) -> int:
		for index, char in enumerate(self._buffer):
			if char in self.STRONG_TERMINATORS and self._canSplitAt(index):
				return index
			if (
				index + 1 >= self.SOFT_SPLIT_MIN_LENGTH
				and char in self.SOFT_TERMINATORS
				and self._canSplitAt(index)
			):
				return index
		return -1

	def _canSplitAt(self, index: int) -> bool:
		char = self._buffer[index]
		prevChar = self._buffer[index - 1] if index > 0 else ""
		nextChar = self._buffer[index + 1] if index + 1 < len(self._buffer) else ""
		if char == "." and prevChar.isdigit():
			return bool(nextChar) and not nextChar.isdigit()
		if char in {".", ","}:
			return not (prevChar.isdigit() and nextChar.isdigit())
		return True

	def _findLengthSplit(self) -> int:
		splitIndex = self._buffer.rfind(" ", 0, self.MAX_BUFFER_LENGTH)
		if splitIndex < self.MAX_BUFFER_LENGTH // 2:
			return min(self.MAX_BUFFER_LENGTH - 1, len(self._buffer) - 1)
		return splitIndex

	def _speakNextChunk(self) -> None:
		if self._isSpeaking or not self._pendingChunks:
			return
		chunk = self._pendingChunks.pop(0)
		self._isSpeaking = True
		speechSession = self._speechSession
		speak(
			[
				chunk,
				CallbackCommand(
					functools.partial(self._onSpeechDone, speechSession),
					name="visAware-stream-chunk-done",
				),
			],
		)

	def _onSpeechDone(self, speechSession: int) -> None:
		if speechSession != self._speechSession:
			return
		self._isSpeaking = False
		self._speakNextChunk()
