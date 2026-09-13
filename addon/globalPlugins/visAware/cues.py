# Copyright (C) 2025-2026 cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License version 3 or later.
# See the file COPYING.txt for more details.

"""Provide sound, beep, and speech feedback for Polyglot workflows."""

import os
import threading
from collections.abc import Callable

import addonHandler
import config
import globalVars
import nvwave
import queueHandler
import tones
import ui


addonHandler.initTranslation()


class CueType:
	"""Define names for the sound cues used by translation workflows."""

	START = "start"
	AUTO = "auto"
	ACTION = "action"
	QUESTION = "question"
	CANCEL = "cancel"
	SUCCESS = "success"
	ERROR = "error"
	WAITING = "waiting"
	INFO = "info"


_soundsDir = os.path.join(globalVars.appArgs.configPath, "addons", "visAware", "sounds")


def _getSoundPath(name: str) -> str:
	"""Return the full path for a bundled sound file."""
	return os.path.join(_soundsDir, f"{name}.wav")


# --- Internal Periodic Cue Machinery (Shared by all cue types) ---
_stopEvent = threading.Event()


def _startPeriodicCueInternal(cueFunction: Callable[[], None], intervalMs: int, delayMs: int) -> None:
	"""Start a periodic cue in a background thread."""
	stopPeriodicCue()  # Stop any existing cue first
	_stopEvent.clear()

	def loopTarget():
		intervalSec = intervalMs / 1000.0
		delaySec = delayMs / 1000.0
		if delaySec > 0:
			if _stopEvent.wait(delaySec):
				return
		while not _stopEvent.is_set():
			cueFunction()
			if _stopEvent.wait(intervalSec):
				break

	threading.Thread(target=loopTarget, daemon=True).start()


def stopPeriodicCue() -> None:
	"""
	Signal the currently running periodic cue, if any, to stop.

	This function is safe to call even if no cue is running.
	"""
	_stopEvent.set()


class Sound:
	"""A namespace for all sound-file-based cues."""

	@staticmethod
	def play(soundName: str) -> None:
		"""Play a bundled sound cue when its file exists."""
		soundPath = _getSoundPath(soundName)
		if os.path.exists(soundPath):
			nvwave.playWaveFile(soundPath)

	@staticmethod
	def startPeriodic(eventName: str, intervalMs: int, delayMs: int) -> None:
		"""
		Start a periodic sound cue using asynchronous playback.

		The interval must exceed the sound duration to prevent overlapping playback.
		"""

		def cueFunction():
			Sound.play(eventName)

		_startPeriodicCueInternal(cueFunction, intervalMs, delayMs)


class Beep:
	"""A namespace for all beep-based cues."""

	_BEEPS = {
		CueType.START: (100, 10),
		CueType.SUCCESS: (440, 50),
		CueType.ERROR: (220, 120),
		CueType.WAITING: (450, 30),
		CueType.INFO: (330, 30),
	}

	@staticmethod
	def play(eventName: str) -> None:
		"""Play a predefined beep pattern."""
		freq, dur = Beep._BEEPS.get(eventName, (200, 50))
		tones.beep(freq, dur)

	@staticmethod
	def startPeriodic(eventName: str, intervalMs: int, delayMs: int) -> None:
		"""Start a periodic beep cue."""

		def cueFunction():
			Beep.play(eventName)

		_startPeriodicCueInternal(cueFunction, intervalMs, delayMs)

	_lastBeepPct: int = -100
	_lastSpeechPct: int = -100

	@classmethod
	def reportProgress(cls, current: int, total: int) -> None:
		"""Report progress according to NVDA's progress-bar output setting.

		Beep feedback runs on any thread.
		Speech feedback is scheduled to the main thread via queueHandler.
		"""
		if total <= 1:
			return
		current = max(0, min(current, total))
		pct = int(current / total * 100)
		pbConf = config.conf["presentation"]["progressBarUpdates"]
		mode = pbConf["progressBarOutputMode"]
		if mode == "off":
			return
		if mode in ("beep", "both"):
			if abs(pct - cls._lastBeepPct) >= pbConf["beepPercentageInterval"]:
				freq = int(pbConf["beepMinHZ"] * 2 ** (pct / 25.0))
				tones.beep(freq, 40)
				cls._lastBeepPct = pct
		if mode in ("speak", "both"):
			if abs(pct - cls._lastSpeechPct) >= pbConf["speechPercentageInterval"]:
				cls._lastSpeechPct = pct
				queueHandler.queueFunction(
					queueHandler.eventQueue,
					Speech.message,
					_("%d percent") % pct,
				)

	@classmethod
	def resetProgress(cls) -> None:
		"""Reset progress tracking before a new tracked task."""
		cls._lastBeepPct = -100
		cls._lastSpeechPct = -100

	@staticmethod
	def playProgress(current: int, total: int) -> None:
		"""
		Plays a dynamic pitch beep based on progress.
		The pitch rises exponentially as the task nears completion,
		matching NVDA's native progress bar beep range (110Hz to 1760Hz).
		"""
		if total <= 1:
			return
		# Clamp current value to range [1, total]
		current = max(1, min(current, total))
		progress = current / total
		# NVDA's native progress bar range: 4 octaves starting from 110Hz
		# Formula: 110 * (2 ^ (progress * 4))
		freq = int(110 * (2 ** (progress * 4)))
		tones.beep(freq, 40)


class Speech:
	"""A namespace for all speech-based cues."""

	@staticmethod
	def message(text: str, shouldSuppressCapture: bool = True) -> None:
		"""Speaks text. MUST be called from the main NVDA thread.

		Args:
			text: The text to speak.
			shouldSuppressCapture: If True, notifies the speech filter to skip
				capturing this message as `lastSpokenText`.
		"""
		if shouldSuppressCapture and _onBeforeSpeech is not None:
			_onBeforeSpeech()
		ui.message(text)


# --- Speech Hook Registration ---
_onBeforeSpeech: Callable[[], None] | None = None


def registerSpeechHook(callback: Callable[[], None]) -> None:
	"""Register a callback to invoke before this module speaks."""
	global _onBeforeSpeech
	_onBeforeSpeech = callback


def unregisterSpeechHook() -> None:
	"""Unregisters the speech hook."""
	global _onBeforeSpeech
	_onBeforeSpeech = None
