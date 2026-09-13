# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

from threading import Event, Thread
import time
import wave

import config
import nvwave
import wx
from logHandler import log

from .cues import CueType, Sound, _getSoundPath

WAITING_DELAY_MS = 2000


class TaskCueManager:
	"""Share one cancellable waiting sound across tasks."""

	def __init__(self) -> None:
		self._pending: set[Event] = set()
		self._timer: wx.CallLater | None = None
		self._waitingPlayer: nvwave.WavePlayer | None = None
		self._waitingStop: Event | None = None
		self._terminated = Event()

	def start(self) -> Event:
		handle = Event()
		if wx.IsMainThread():
			self._start(handle)
		else:
			wx.CallAfter(self._start, handle)
		return handle

	def play(self, soundName: str) -> None:
		if wx.IsMainThread():
			self._play(soundName)
		else:
			wx.CallAfter(self._play, soundName)

	def stop(self, handle: Event | None) -> None:
		if handle is None or handle.is_set():
			return
		# Cancel even when the queued start has not reached the main thread yet.
		handle.set()
		if wx.IsMainThread():
			self._removeStopped()
		else:
			wx.CallAfter(self._removeStopped)

	def terminate(self) -> None:
		self._terminated.set()
		if wx.IsMainThread():
			self._removeStopped()
		else:
			wx.CallAfter(self._removeStopped)

	def _start(self, handle: Event) -> None:
		if self._terminated.is_set() or handle.is_set():
			return
		self._pending.add(handle)
		self._stopTimer()
		self._stopWaiting()
		self._play(CueType.START)
		self._timer = wx.CallLater(WAITING_DELAY_MS, self._onWaiting)

	def _removeStopped(self) -> None:
		self._pending = {handle for handle in self._pending if not handle.is_set()}
		if self._terminated.is_set():
			self._pending.clear()
		if not self._pending:
			self._stopTimer()
			self._stopWaiting()

	def _stopTimer(self) -> None:
		if self._timer is not None:
			self._timer.Stop()
			self._timer = None

	def _onWaiting(self) -> None:
		self._timer = None
		self._removeStopped()
		if not self._pending:
			return
		try:
			with wave.open(_getSoundPath(CueType.WAITING), "rb") as audio:
				data = audio.readframes(audio.getnframes())
				if not data:
					return
				duration = audio.getnframes() / audio.getframerate()
				chunkSize = max(1, audio.getframerate() // 10) * audio.getnchannels() * audio.getsampwidth()
				player = nvwave.WavePlayer(
					channels=audio.getnchannels(),
					samplesPerSec=audio.getframerate(),
					bitsPerSample=audio.getsampwidth() * 8,
					outputDevice=config.conf["audio"]["outputDevice"],
					wantDucking=False,
					purpose=nvwave.AudioPurpose.SOUNDS,
				)
			self._waitingPlayer = player
			self._waitingStop = stopEvent = Event()
			Thread(
				target=self._loopWaiting,
				args=(player, stopEvent, data, chunkSize, duration),
				name="VisAwareWaitingSound",
				daemon=True,
			).start()
		except Exception:
			log.debugWarning("Could not start Vis Aware waiting sound.", exc_info=True)
			self._stopWaiting()

	@staticmethod
	def _loopWaiting(
		player: nvwave.WavePlayer,
		stopEvent: Event,
		data: bytes,
		chunkSize: int,
		duration: float,
	) -> None:
		try:
			while not stopEvent.is_set():
				startedAt = time.monotonic()
				for offset in range(0, len(data), chunkSize):
					if stopEvent.is_set():
						return
					# Short chunks keep a stop racing with feed from restarting the whole WAV.
					player.feed(data[offset : offset + chunkSize])
				player.idle()
				# WavePlayer may return early after an audio device error; avoid spinning.
				stopEvent.wait(max(0, duration - (time.monotonic() - startedAt)))
		except Exception:
			log.debugWarning("Could not play Vis Aware waiting sound.", exc_info=True)
		finally:
			try:
				player.stop()
			except Exception:
				log.debugWarning("Could not stop Vis Aware waiting sound.", exc_info=True)

	def _stopWaiting(self) -> None:
		if self._waitingStop is not None:
			self._waitingStop.set()
			self._waitingStop = None
		player, self._waitingPlayer = self._waitingPlayer, None
		if player is not None:
			try:
				player.stop()
			except Exception:
				log.debugWarning("Could not stop Vis Aware waiting sound.", exc_info=True)

	def _play(self, name: str) -> None:
		if self._terminated.is_set():
			return
		try:
			Sound.play(name)
		except Exception:
			log.debugWarning(f"Could not play Vis Aware cue {name!r}.", exc_info=True)
