from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from threading import Event
import sys
import types
import unittest
from unittest.mock import MagicMock, Mock, patch


PLUGIN_DIR = Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware"


class Clock:
	def __init__(self):
		self.now = 0
		self.timers = []
		self.queue = []
		self.isMainThread = True

	def callLater(self, delay, callback):
		timer = types.SimpleNamespace(deadline=self.now + delay, callback=callback, stopped=False)
		timer.Stop = lambda: setattr(timer, "stopped", True)
		self.timers.append(timer)
		return timer

	def callAfter(self, callback, *args):
		self.queue.append((callback, args))

	def flush(self):
		self.isMainThread = True
		while self.queue:
			callback, args = self.queue.pop(0)
			callback(*args)

	def advance(self, milliseconds):
		end = self.now + milliseconds
		while timers := [timer for timer in self.timers if not timer.stopped and timer.deadline <= end]:
			timer = min(timers, key=lambda item: item.deadline)
			self.now = timer.deadline
			timer.stopped = True
			timer.callback()
		self.now = end


def loadMethods(fileName, className, methodNames, namespace):
	# Exercise orchestration methods without importing NVDA's GUI and global plugin registration.
	tree = ast.parse((PLUGIN_DIR / fileName).read_text(encoding="utf-8"))
	cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == className)
	cls.bases = []
	cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in methodNames]
	for method in cls.body:
		method.decorator_list = []
	tree = ast.Module(
		body=[ast.ImportFrom(module="__future__", names=[ast.alias("annotations")]), cls], type_ignores=[]
	)
	exec(compile(ast.fix_missing_locations(tree), fileName, "exec"), namespace)
	return namespace[className]


class TaskCuesTestCase(unittest.TestCase):
	def setUp(self):
		self.clock = Clock()
		self.sounds = []
		self.wx = types.SimpleNamespace(
			IsMainThread=lambda: self.clock.isMainThread,
			CallAfter=self.clock.callAfter,
			CallLater=self.clock.callLater,
			CommandEvent=type("CommandEvent", (), {}),
		)
		self.sound = types.SimpleNamespace(play=lambda name: self.sounds.append((self.clock.now, name)))
		self.log = Mock()
		self.players = []

		def createPlayer(**_kwargs):
			player = Mock()
			self.players.append(player)
			return player

		self.nvwave = types.SimpleNamespace(
			WavePlayer=createPlayer,
			AudioPurpose=types.SimpleNamespace(SOUNDS="sounds"),
			fileWavePlayer=Mock(),
		)
		self.audio = Mock()
		self.audio.getframerate.return_value = 1000
		self.audio.getnframes.return_value = 11492
		self.audio.getnchannels.return_value = 1
		self.audio.getsampwidth.return_value = 2
		self.audio.readframes.return_value = b"\x01\x02" * 11492
		self.wave = types.SimpleNamespace(open=MagicMock())
		self.wave.open.return_value.__enter__.return_value = self.audio
		stubs = {
			"wx": self.wx,
			"nvwave": self.nvwave,
			"wave": self.wave,
			"config": types.SimpleNamespace(conf={"audio": {"outputDevice": "selectedDevice"}}),
			"logHandler": types.SimpleNamespace(log=self.log),
			"cueTests": types.ModuleType("cueTests"),
			"cueTests.cues": types.SimpleNamespace(
				CueType=types.SimpleNamespace(START="start", WAITING="waiting"),
				Sound=self.sound,
				_getSoundPath=lambda name: f"{name}.wav",
			),
		}
		with patch.dict(sys.modules, stubs):
			spec = importlib.util.spec_from_file_location("cueTests._taskCues", PLUGIN_DIR / "_taskCues.py")
			module = importlib.util.module_from_spec(spec)
			spec.loader.exec_module(module)
		self.module = module
		self.thread = module.Thread = Mock()
		self.thread.return_value.start.side_effect = lambda: self.sounds.append((self.clock.now, "waiting"))
		self.cues = module.TaskCueManager()

	def test_short_task_and_long_waiting_stops_immediately(self):
		handle = self.cues.start()
		self.clock.advance(1999)
		self.assertEqual(self.sounds, [(0, "start")])
		self.cues.stop(handle)
		self.clock.advance(10000)
		self.assertEqual(len(self.sounds), 1)
		handle = self.cues.start()
		self.clock.advance(30000)
		self.assertEqual(self.sounds[-2:], [(11999, "start"), (13999, "waiting")])
		self.thread.assert_called_once()
		self.players[-1].stop.assert_not_called()
		self.cues.stop(handle)
		self.players[-1].stop.assert_called_once()
		self.nvwave.fileWavePlayer.stop.assert_not_called()
		self.cues.stop(handle)
		self.clock.advance(10000)
		self.assertEqual(len(self.sounds), 3)

	def test_overlapping_tasks_share_waiting_and_protect_new_start_sound(self):
		first = self.cues.start()
		self.clock.advance(2000)
		second = self.cues.start()
		self.players[0].stop.assert_called_once()
		self.cues.stop(first)
		self.clock.advance(1999)
		self.assertEqual(self.sounds, [(0, "start"), (2000, "waiting"), (2000, "start")])
		self.clock.advance(1201)
		self.assertEqual(self.sounds[-1], (4000, "waiting"))
		self.cues.stop(second)
		self.players[-1].stop.assert_called_once()
		self.clock.advance(10000)
		self.assertEqual(len(self.sounds), 4)

	def test_only_last_waiter_stops_shared_playback(self):
		first = self.cues.start()
		second = self.cues.start()
		self.clock.advance(2000)
		self.cues.stop(first)
		self.players[-1].stop.assert_not_called()
		self.cues.stop(second)
		self.players[-1].stop.assert_called_once()

	def test_waiting_worker_loops_complete_audio(self):
		handle = self.cues.start()
		self.clock.advance(2000)
		player, stopEvent, data, chunkSize, _duration = self.thread.call_args.kwargs["args"]
		chunks, completed = [], []
		player.feed.side_effect = chunks.append

		def idle():
			completed.append(b"".join(chunks))
			chunks.clear()
			if len(completed) == 2:
				self.cues.stop(handle)

		player.idle.side_effect = idle
		self.cues._loopWaiting(player, stopEvent, data, chunkSize, 0)
		self.assertEqual(completed, [data, data])

	def test_stop_racing_with_feed_cannot_restart_the_whole_wave(self):
		handle = self.cues.start()
		self.clock.advance(2000)
		player, stopEvent, data, chunkSize, duration = self.thread.call_args.kwargs["args"]
		player.feed.side_effect = lambda _data: self.cues.stop(handle)
		self.cues._loopWaiting(player, stopEvent, data, chunkSize, duration)
		player.feed.assert_called_once_with(data[:chunkSize])
		self.assertEqual(chunkSize, 200)
		self.assertTrue(stopEvent.is_set())

	def test_audio_device_failure_cannot_spin_waiting_loop(self):
		stopEvent = Mock(wraps=Event())
		stopEvent.wait.side_effect = lambda _timeout: stopEvent.set()
		with patch.object(self.module.time, "monotonic", side_effect=[100, 100.1]):
			self.cues._loopWaiting(Mock(), stopEvent, b"audio", 5, 11.5)
		self.assertAlmostEqual(stopEvent.wait.call_args.args[0], 11.4)

	def test_play_uses_main_thread_preserves_waiting_and_respects_shutdown(self):
		self.cues.play("auto")
		self.assertEqual(self.sounds, [(0, "auto")])
		self.clock.isMainThread = False
		self.cues.play("action")
		self.assertEqual(len(self.sounds), 1)
		self.clock.flush()
		self.assertEqual(self.sounds[-1], (0, "action"))
		self.clock.advance(3000)
		self.assertEqual(self.players, [])
		handle = self.cues.start()
		self.clock.advance(2000)
		self.cues.play("auto")
		self.players[-1].stop.assert_not_called()
		self.cues.stop(handle)
		self.players[-1].stop.assert_called_once()
		self.clock.isMainThread = False
		self.cues.play("success")
		self.cues.terminate()
		self.clock.flush()
		self.assertEqual(len(self.sounds), 5)

	def test_worker_stop_and_shutdown_invalidate_queued_starts(self):
		self.clock.isMainThread = False
		cancelled = self.cues.start()
		self.cues.stop(cancelled)
		self.clock.flush()
		self.assertEqual(self.sounds, [])
		self.cues.start()
		self.clock.isMainThread = False
		self.cues.start()
		self.cues.terminate()
		self.clock.advance(2000)
		self.clock.flush()
		self.cues.start()
		self.clock.advance(10000)
		self.assertEqual(self.sounds, [(0, "start")])

	def test_stopped_worker_does_not_wait_for_main_thread_cleanup(self):
		handle = self.cues.start()
		self.clock.isMainThread = False
		self.cues.stop(handle)
		self.clock.advance(2000)
		self.clock.flush()
		self.assertEqual(self.sounds, [(0, "start")])

	def test_audio_failure_does_not_escape_into_task(self):
		self.sound.play = Mock(side_effect=OSError("audio unavailable"))
		self.wave.open.side_effect = OSError("audio unavailable")
		handle = self.cues.start()
		self.clock.advance(2000)
		self.cues.stop(handle)
		self.assertEqual(self.log.debugWarning.call_count, 2)

	def makePlugin(self):
		streamText = type("StreamText", (), {})
		cls = loadMethods(
			"__init__.py",
			"GlobalPlugin",
			{
				"_makeRecognitionCallback",
				"executeRecognition",
				"_cancelCurrentRecognition",
				"script_recognizeAccordingToSettings",
			},
			{
				"StreamText": streamText,
				"log": self.log,
				"ui": Mock(),
				"_": lambda value: value,
				"config": types.SimpleNamespace(conf={"visAwareGeneral": {"useBrowseableMessage": False}}),
			},
		)
		plugin = cls()
		plugin._taskCues = self.cues
		plugin._recognitionCue = None
		plugin._activeRecognitionSequence = 1
		plugin._activeEngine = None
		plugin._stopAgent = lambda **_kwargs: False
		plugin._autoRecognitionController = None
		plugin._streamingSpeechPresenter = Mock(isActive=False)
		plugin._onRecognitionResult = Mock()
		plugin._onStreamingRecognitionResult = Mock()
		return plugin, streamText

	def test_single_and_double_press_share_start_sound(self):
		plugin, _ = self.makePlugin()
		engine = Mock(name="engine", supportsStreaming=False, _recognitionThread=None)
		plugin._getCurrentEngine = lambda _kind: engine
		plugin._getImageFromSource = lambda *_args: (object(), Mock())
		plugin.startRecognition = lambda gesture, simpleText: plugin.executeRecognition(
			gesture, "clipboardImage", "OCR", simpleText
		)
		plugin.script_recognizeAccordingToSettings(None, 1)
		self.assertFalse(engine.textResult)
		plugin.script_recognizeAccordingToSettings(None, 2)
		self.assertTrue(engine.textResult)
		self.assertEqual(self.sounds, [(0, "start"), (0, "start")])

	def test_manual_stream_stops_on_text_and_old_callback_cannot_stop_new_task(self):
		plugin, streamText = self.makePlugin()
		plugin._recognitionCue = self.cues.start()
		callback = plugin._makeRecognitionCallback(1, True)
		event = streamText()
		event.text = " \n"
		callback(event)
		self.clock.advance(2000)
		event.text = "answer"
		callback(event)
		self.players[-1].stop.assert_called_once()
		self.clock.advance(4000)
		self.assertEqual([name for _, name in self.sounds], ["start", "waiting"])
		plugin._activeRecognitionSequence = 2
		plugin._recognitionCue = self.cues.start()
		callback(RuntimeError("old task"))
		self.clock.advance(2000)
		self.assertEqual(self.sounds[-1], (8000, "waiting"))

	def test_manual_start_error_and_pending_result_cancellation_stop_waiting(self):
		plugin, _ = self.makePlugin()
		engine = Mock(name="engine", supportsStreaming=False, _recognitionThread=None)
		engine.recognize.side_effect = RuntimeError("start failed")
		plugin._getCurrentEngine = lambda _kind: engine
		plugin._getImageFromSource = lambda *_args: (object(), Mock())
		plugin.executeRecognition(None, "clipboardImage", "OCR", False)
		self.clock.advance(3000)
		self.assertEqual(self.sounds, [(0, "start")])
		engine._recognitionThread = Mock()
		engine._recognitionThread.is_alive.return_value = False
		plugin._activeEngine = engine
		plugin._recognitionCue = self.cues.start()
		callback = plugin._makeRecognitionCallback(plugin._activeRecognitionSequence, False)
		self.assertTrue(plugin._cancelCurrentRecognition())
		callback(object())
		self.clock.advance(3000)
		plugin._onRecognitionResult.assert_not_called()
		self.assertEqual(self.sounds, [(0, "start"), (3000, "start")])

	def makeQuestionFrame(self):
		thread = Mock()
		cls = loadMethods(
			"askDialog.py",
			"AskQuestionFrame",
			{
				"_onSend",
				"_onAskTextReceived",
				"_finishRequest",
				"_cancelWorker",
				"_onAskFailed",
				"_onAskCancelled",
				"_onAskSucceeded",
				"_onClose",
			},
			{
				"wx": self.wx,
				"Event": Event,
				"Thread": thread,
				"log": self.log,
				"ui": Mock(),
				"_": lambda value: value,
				"ANSWER_SENDER": "Answer",
			},
		)
		frame = cls()
		frame._taskCues = self.cues
		frame._cueHandle = None
		frame._activeRequestSequence = None
		frame._requestSequence = 0
		frame._streamingAnswerRequestSequence = 1
		frame._streamingAnswerText = ""
		frame._cancellationEvent = None
		for name in (
			"_context",
			"_questionText",
			"_appendMessage",
			"_setSendButtonEnabled",
			"_askWorker",
			"_cancelStreamingAnswer",
			"_messagesText",
			"_streamingSpeechPresenter",
			"_finishStreamingAnswer",
			"Hide",
		):
			setattr(frame, name, Mock())
		frame._questionText.GetValue.return_value = "question"
		return frame, thread

	def test_followup_stops_on_text_and_close_and_ignores_old_result(self):
		frame, _ = self.makeQuestionFrame()
		frame._onSend(object())
		frame._onSend(object())
		frame._onAskTextReceived(1, " ")
		self.clock.advance(2000)
		frame._onAskTextReceived(1, "answer")
		self.players[-1].stop.assert_called_once()
		self.clock.advance(4000)
		self.assertEqual([name for _, name in self.sounds], ["start", "waiting"])
		frame._onClose(object())
		frame._onSend(object())
		frame._onAskFailed(1, "old error")
		self.clock.advance(2000)
		self.assertEqual(self.sounds[-1], (8000, "waiting"))
		frame._onClose(object())
		self.clock.advance(10000)
		self.assertEqual(len(self.sounds), 4)

	def test_followup_final_results_and_start_error_stop_waiting(self):
		for outcome in ("success", "failure", "cancel", "startFailure"):
			with self.subTest(outcome=outcome):
				frame, thread = self.makeQuestionFrame()
				if outcome == "startFailure":
					thread.return_value.start.side_effect = RuntimeError("thread failed")
				frame._onSend(object())
				if outcome == "success":
					frame._onAskSucceeded(1, "question", "answer")
				elif outcome == "failure":
					frame._onAskFailed(1, "error")
				elif outcome == "cancel":
					frame._onAskCancelled(1)
				self.assertIsNone(frame._activeRequestSequence)
				self.clock.advance(3000)
		self.assertEqual([name for _, name in self.sounds], ["start"] * 4)


if __name__ == "__main__":
	unittest.main()
