from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from threading import Event, Thread
import types
import unittest


def _installModuleStubs() -> None:
	for moduleName in ("addon", "addon.globalPlugins", "addon.globalPlugins.visAware"):
		module = types.ModuleType(moduleName)
		module.__path__ = []  # type: ignore[attr-defined]
		sys.modules[moduleName] = module

	apiModule = types.ModuleType("api")
	apiModule.getDesktopObject = lambda: types.SimpleNamespace(location=(0, 0, 2000, 2000))
	sys.modules["api"] = apiModule

	configModule = types.ModuleType("config")
	configModule.conf = {
		"visAwareGeneral": {
			"preferScreenshotForWebImages": False,
			"verboseDebugLogging": False,
		},
	}
	sys.modules["config"] = configModule

	controlTypesModule = types.ModuleType("controlTypes")
	controlTypesModule.Role = types.SimpleNamespace(BUTTON="button", GRAPHIC="graphic", LISTITEM="listItem")
	controlTypesModule.State = types.SimpleNamespace(INVISIBLE="invisible", OFFSCREEN="offscreen")
	sys.modules["controlTypes"] = controlTypesModule

	textInfosModule = types.ModuleType("textInfos")
	textInfosModule.POSITION_CARET = "caret"
	textInfosModule.UNIT_CHARACTER = "character"
	textInfosModule.TextInfo = object
	textInfosModule.FieldCommand = object
	sys.modules["textInfos"] = textInfosModule

	uiModule = types.ModuleType("ui")
	uiModule.message = lambda _message: None
	sys.modules["ui"] = uiModule

	pilModule = types.ModuleType("PIL")
	pilModule.__path__ = []  # type: ignore[attr-defined]
	imageModule = types.ModuleType("PIL.Image")
	imageModule.Image = object
	imageGrabModule = types.ModuleType("PIL.ImageGrab")
	imageGrabModule.grab = lambda **_kwargs: None
	pilModule.Image = imageModule
	pilModule.ImageGrab = imageGrabModule
	sys.modules["PIL"] = pilModule
	sys.modules["PIL.Image"] = imageModule
	sys.modules["PIL.ImageGrab"] = imageGrabModule

	wxModule = types.ModuleType("wx")
	wxModule.CallAfter = lambda callback, *args: callback(*args)
	wxModule.CallLater = object
	sys.modules["wx"] = wxModule

	contentRecogModule = types.ModuleType("contentRecog")
	contentRecogModule.RecogImageInfo = object
	contentRecogModule.RecognitionResult = type("RecognitionResult", (), {})
	sys.modules["contentRecog"] = contentRecogModule

	logHandlerModule = types.ModuleType("logHandler")
	logHandlerModule.log = types.SimpleNamespace(
		debugWarning=lambda *args, **kwargs: None,
		io=lambda *args, **kwargs: None,
	)
	sys.modules["logHandler"] = logHandlerModule

	recogHistoryModule = types.ModuleType("addon.globalPlugins.visAware.recogHistory")
	recogHistoryModule.addEntry = lambda *args, **kwargs: None
	recogHistoryModule.getAttachedEntry = lambda _result: None
	sys.modules[recogHistoryModule.__name__] = recogHistoryModule

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")
	exceptionsModule.CancellationError = type("CancellationError", (Exception,), {})
	sys.modules[exceptionsModule.__name__] = exceptionsModule

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")
	networkModule.sendRequest = lambda *args, **kwargs: None
	sys.modules[networkModule.__name__] = networkModule

	recogHandlerModule = types.ModuleType("addon.globalPlugins.visAware.recogHandler")
	recogHandlerModule.AUTO_RECOGNITION_CURRENT_ENGINE_NAME = "current"
	recogHandlerModule.AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX = "imageDescriber:"
	recogHandlerModule.AUTO_RECOGNITION_OCR_PREFIX = "ocr:"
	recogHandlerModule.AUTO_RECOGNITION_OFF = "off"
	recogHandlerModule.CustomOCRHandler = object
	recogHandlerModule.ImageDescriberHandler = object
	recogHandlerModule.StreamFinished = type("StreamFinished", (), {})
	recogHandlerModule.StreamText = type("StreamText", (), {})
	recogHandlerModule.getEffectiveAutoRecognitionEngine = lambda: "off"
	sys.modules[recogHandlerModule.__name__] = recogHandlerModule

	class StreamingSpeechPresenter:
		isActive = False

		def cancel(self) -> None:
			pass

	streamingSpeechModule = types.ModuleType("addon.globalPlugins.visAware.streamingSpeech")
	streamingSpeechModule.StreamingSpeechPresenter = StreamingSpeechPresenter
	sys.modules[streamingSpeechModule.__name__] = streamingSpeechModule


def loadAutoRecognitionModule():
	_installModuleStubs()
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "_autoRecognition.py"
	)
	spec = importlib.util.spec_from_file_location("addon.globalPlugins.visAware._autoRecognition", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load _autoRecognition.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class AutoRecognitionP0TestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.module = loadAutoRecognitionModule()

	def test_worker_keeps_only_latest_queued_task(self) -> None:
		controller = self.module.AutoRecognitionController()
		firstStarted = Event()
		releaseFirst = Event()
		latestFinished = Event()
		calls = []

		def first() -> None:
			calls.append("first")
			firstStarted.set()
			releaseFirst.wait(timeout=2)

		def second() -> None:
			calls.append("second")

		def latest() -> None:
			calls.append("latest")
			latestFinished.set()

		controller._startWorker(first, ())
		self.assertTrue(firstStarted.wait(timeout=1))
		controller._startWorker(second, ())
		controller._startWorker(latest, ())
		releaseFirst.set()
		self.assertTrue(latestFinished.wait(timeout=1))
		controller.terminate()
		self.assertEqual(calls, ["first", "latest"])

	def test_worker_slot_reserves_an_assigned_thread_before_it_starts(self) -> None:
		controller = self.module.AutoRecognitionController()
		assignedWorker = Thread(target=lambda: None)
		controller._workerThread = assignedWorker

		def queuedTarget() -> None:
			pass

		controller._startWorker(queuedTarget, ())

		self.assertIs(controller._workerThread, assignedWorker)
		self.assertIsNotNone(controller._queuedWorker)
		self.assertIs(controller._queuedWorker[0], queuedTarget)
		controller.terminate()

	def test_cancel_reports_active_task_only_once_while_worker_drains(self) -> None:
		controller = self.module.AutoRecognitionController()
		controller._activeKey = "key"
		controller._workerThread = Thread(target=lambda: None)

		self.assertTrue(controller.cancel())
		self.assertFalse(controller.cancel())
		controller.terminate()

	def test_url_start_tone_waits_for_queued_worker(self) -> None:
		controller = self.module.AutoRecognitionController()
		queuedWork = []
		tones = []
		self.module._playStartTone = lambda: tones.append("tone")
		controller._currentKeyMatchesGetter = lambda *_args: True
		controller._startWorker = lambda target, args: queuedWork.append((target, args))

		controller._beginUrlDescription("key", "https://example.test/image", "test", lambda: "key", 0.0)

		self.assertEqual(tones, [])
		self.assertEqual(len(queuedWork), 1)
		controller._createRecognitionEngine = lambda *_args: None
		target, args = queuedWork[0]
		target(*args)
		self.assertEqual(tones, ["tone"])
		controller.cancel()

	def test_recognition_keeps_armed_event_when_cancelled_during_start(self) -> None:
		controller = self.module.AutoRecognitionController()
		controller._token = 1
		controller._activeKey = "key"
		cancellationEvent = Event()
		recognitionEvents = []

		def prepareCancellation() -> Event:
			return cancellationEvent

		engine = types.SimpleNamespace(
			textResult=False,
			streamResult=False,
			supportsStreaming=False,
			_prepareCancellation=prepareCancellation,
			cancel=lambda **_kwargs: cancellationEvent.set(),
			recognizeImage=lambda *_args, **kwargs: recognitionEvents.append(kwargs["cancellationEvent"]),
		)
		handler = types.SimpleNamespace(getEngineInstance=lambda _name: engine)
		controller._resolveRecognitionScopedEngine = lambda _key: (handler, "engine", "key")

		engineAndCancellation = controller._createRecognitionEngine(1, "key")
		self.assertIsNotNone(engineAndCancellation)
		createdEngine, armedEvent = engineAndCancellation
		self.assertIs(createdEngine, engine)
		self.assertIs(armedEvent, cancellationEvent)

		def cancelDuringStart(_token: int, _key: str) -> bool:
			controller.cancel()
			return True

		controller._isCurrent = cancelDuringStart
		controller._recognizeImage(
			engine,
			types.SimpleNamespace(width=8, height=8),
			armedEvent,
			1,
			"key",
			0.0,
		)

		self.assertTrue(cancellationEvent.is_set())
		self.assertEqual(recognitionEvents, [cancellationEvent])


if __name__ == "__main__":
	unittest.main()
