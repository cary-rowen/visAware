from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from threading import Event, Thread
import types
import unittest
from unittest.mock import Mock, patch

from test_request_log_redaction import load_recog_handler_module


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

	class _FieldCommand:
		def __init__(self, command: str, field: dict | None = None) -> None:
			self.command = command
			self.field = field or {}

	textInfosModule.TextInfo = object
	textInfosModule.FieldCommand = _FieldCommand
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
	wxModule.IsMainThread = lambda: True
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
	sys.modules["addon.globalPlugins.visAware.cues"] = types.SimpleNamespace(
		CueType=types.SimpleNamespace(AUTO="auto"),
	)

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")
	exceptionsModule.CancellationError = type("CancellationError", (Exception,), {})
	sys.modules[exceptionsModule.__name__] = exceptionsModule

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")
	networkModule.sendRequest = lambda *args, **kwargs: None
	sys.modules[networkModule.__name__] = networkModule

	recogHandlerModule = types.ModuleType("addon.globalPlugins.visAware.recogHandler")
	recogHandlerModule.AUTO_RECOGNITION_CURRENT_ENGINE_NAME = "current"
	recogHandlerModule.AUTO_RECOGNITION_CURRENT_IMAGE_DESCRIBER = "imageDescriber:current"
	recogHandlerModule.AUTO_RECOGNITION_CURRENT_OCR = "ocr:current"
	recogHandlerModule.AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX = "imageDescriber:"
	recogHandlerModule.AUTO_RECOGNITION_OCR_PREFIX = "ocr:"
	recogHandlerModule.AUTO_RECOGNITION_OFF = "off"
	recogHandlerModule.CustomOCRHandler = object
	recogHandlerModule.ImageDescriberHandler = object
	recogHandlerModule.StreamFinished = type("StreamFinished", (), {})
	recogHandlerModule.StreamText = type("StreamText", (), {})
	recogHandlerModule.getEffectiveAutoRecognitionEngine = lambda: "off"
	recogHandlerModule.getAutoRecognitionEngineList = lambda handler: handler.getEngineList()
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
		self.cues = Mock()

	def tearDown(self) -> None:
		self.cues.start.assert_not_called()
		self.cues.stop.assert_not_called()

	def test_automatic_recognition_setting_cycle(self) -> None:
		getNextSetting = self.module.getNextAutoRecognitionSetting
		self.assertEqual(getNextSetting("off"), "imageDescriber:current")
		self.assertEqual(getNextSetting("imageDescriber:gemini"), "ocr:current")
		self.assertEqual(getNextSetting("ocr:paddleOCR"), "off")
		self.assertEqual(getNextSetting("invalid"), "imageDescriber:current")

	def test_capability_check_applies_to_saved_current_and_queued_engines(self) -> None:
		recogHandler = load_recog_handler_module()
		self.module.getAutoRecognitionEngineList = recogHandler.getAutoRecognitionEngineList
		current = types.SimpleNamespace(name="captchaText")
		classes = {
			"captchaText": types.SimpleNamespace(supportsAutomaticRecognition=False),
			"supported": types.SimpleNamespace(supportsAutomaticRecognition=True),
		}
		handler = types.SimpleNamespace(
			getEngineList=lambda: [(name, name) for name in classes],
			getEngine=classes.__getitem__,
			getCurrentEngine=lambda: current,
			getEngineInstance=Mock(),
		)
		self.module.CustomOCRHandler = self.module.ImageDescriberHandler = handler
		controller = self.module.AutoRecognitionController(self.cues)
		for kind in ("ocr", "imageDescriber"):
			for name in ("captchaText", "current", "missing"):
				self.assertIsNone(controller._resolveAutoRecognitionEngine(f"{kind}:{name}"))
			key = self.module.makeRecognitionScopedKey(f"{kind}:captchaText", "target")
			self.assertIsNone(controller._createRecognitionEngine(0, key))
		handler.getEngineInstance.assert_not_called()
		self.cues.play.assert_not_called()
		current.name = "supported"
		self.assertEqual(controller._resolveAutoRecognitionEngine("ocr:current")[1], "supported")
		handler.getEngineList = lambda: [("captchaText", "CAPTCHA")]
		self.assertIsNone(controller._resolveAutoRecognitionEngine("ocr:current"))

	def test_browse_mode_caret_keeps_object_without_src(self) -> None:
		module = self.module

		class _Obj:
			__module__ = "NVDAObjects.UIA.web.test"
			role = module.controlTypes.Role.BUTTON

		class _Info:
			def __init__(self) -> None:
				self.NVDAObjectAtStart = _Obj()

			def copy(self):
				return self

			def expand(self, _unit):
				pass

			def getTextWithFields(self):
				return [
					module.textInfos.FieldCommand(
						"controlStart",
						{"role": module.controlTypes.Role.GRAPHIC},
					),
				]

		cursorManager = types.SimpleNamespace(
			makeTextInfo=lambda _position: _Info(),
		)

		obj, src = module.getImageObjectAndSrcFromBrowseModeCaret(cursorManager)

		self.assertIsInstance(obj, _Obj)
		self.assertIsNone(src)

	def test_browse_mode_caret_returns_object_with_src_even_if_not_graphic(self) -> None:
		module = self.module

		class _Obj:
			__module__ = "NVDAObjects.UIA.web.test"
			role = module.controlTypes.Role.BUTTON

		class _Info:
			def __init__(self) -> None:
				self.NVDAObjectAtStart = _Obj()

			def copy(self):
				return self

			def expand(self, _unit):
				pass

			def getTextWithFields(self):
				return [
					module.textInfos.FieldCommand(
						"controlStart",
						{"role": module.controlTypes.Role.GRAPHIC, "src": "https://example.test/a.jpg"},
					),
				]

		cursorManager = types.SimpleNamespace(
			makeTextInfo=lambda _position: _Info(),
		)

		obj, src = module.getImageObjectAndSrcFromBrowseModeCaret(cursorManager)

		self.assertIsInstance(obj, _Obj)
		self.assertEqual(src, "https://example.test/a.jpg")

	def test_browse_mode_screenshot_target_key_accepts_web_object(self) -> None:
		module = self.module

		class _Obj:
			__module__ = "NVDAObjects.UIA.web.test"
			role = module.controlTypes.Role.BUTTON
			windowHandle = 1
			windowClassName = "test"
			name = "Image"
			appModule = types.SimpleNamespace(appName="app")
			location = (10, 20, 30, 40)

		class _Info:
			def __init__(self) -> None:
				self.NVDAObjectAtStart = _Obj()

			def copy(self):
				return self

			def expand(self, _unit):
				pass

			def getTextWithFields(self):
				return []

		cursorManager = types.SimpleNamespace(
			makeTextInfo=lambda _position: _Info(),
		)

		key = module.getBrowseModeScreenshotTargetKey(cursorManager)

		self.assertIsNotNone(key)
		self.assertIn("screenshotTarget:", key)

	def test_oldNvdaUsesImageGrabForNegativeCoordinates(self) -> None:
		module = self.module
		captureCalls = []

		class _Image:
			mode = "RGB"
			width = 30
			height = 40

			def tobytes(self):
				return b"pixels"

		image = _Image()
		module.ImageGrab.grab = lambda **kwargs: (captureCalls.append(kwargs), image)[1]
		screenCaptureModule = types.ModuleType("addon.globalPlugins.visAware._screenCapture")
		screenCaptureModule.ScreenCaptureError = RuntimeError
		screenCaptureModule.hasNvdaCapture = lambda: False
		screenCaptureModule.isScreenCurtainCaptureSupported = lambda: True
		screenCaptureModule.captureNvdaPixels = lambda _imageInfo: self.fail(
			"Old NVDA should not construct RecogImageInfo for the fallback path.",
		)
		screenCaptureModule.imageFromNvdaPixels = lambda *_args: self.fail(
			"Old NVDA should use ImageGrab for the fallback path.",
		)
		sys.modules[screenCaptureModule.__name__] = screenCaptureModule

		controller = module.AutoRecognitionController(self.cues)
		controller._token = 1
		controller._activeKey = "key"
		controller._captureAndDescribe(1, "key", (-10, 20, 30, 40), 0, None, None)

		self.assertEqual(captureCalls, [{"bbox": (-10, 20, 20, 60)}])
		controller.terminate()

	def test_worker_keeps_only_latest_queued_task(self) -> None:
		controller = self.module.AutoRecognitionController(self.cues)
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
		controller = self.module.AutoRecognitionController(self.cues)
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
		controller = self.module.AutoRecognitionController(self.cues)
		controller._activeKey = "key"
		controller._workerThread = Thread(target=lambda: None)

		self.assertTrue(controller.cancel())
		self.assertFalse(controller.cancel())
		controller.terminate()

	def test_url_start_cue_waits_for_queued_worker(self) -> None:
		controller = self.module.AutoRecognitionController(self.cues)
		queuedWork = []
		controller._currentKeyMatchesGetter = lambda *_args: True
		controller._startWorker = lambda target, args: queuedWork.append((target, args))
		engine = types.SimpleNamespace(cancel=Mock())
		handler = types.SimpleNamespace(getEngineInstance=lambda _name: engine)
		controller._resolveRecognitionScopedEngine = lambda _key: (handler, "engine", "key")
		controller._downloadAndDescribeWithEngine = Mock()

		controller._beginUrlDescription("key", "https://example.test/image", "test", lambda: "key", 0.0)

		self.cues.play.assert_not_called()
		self.assertEqual(len(queuedWork), 1)
		target, args = queuedWork[0]
		target(*args)
		self.cues.play.assert_called_once_with("auto")
		controller.cancel()

	def test_recognition_keeps_armed_event_when_cancelled_during_start(self) -> None:
		controller = self.module.AutoRecognitionController(self.cues)
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

	def test_start_cue_is_once_per_task_and_ignores_stale_results(self) -> None:
		controller = self.module.AutoRecognitionController(self.cues)
		controller._activeKey = "key"
		controller._activeCurrentKeyGetter = lambda: "key"
		controller._startTaskCue(0, "key")
		controller._startTaskCue(0, "key")
		self.cues.play.assert_called_once_with("auto")
		controller.cancel()
		controller._activeKey = "key"
		controller._activeCurrentKeyGetter = lambda: "key"
		controller._startTaskCue(controller._token, "key")
		controller._onResult(0, "key", RuntimeError("old task"), 0)
		controller._startTaskCue(0, "key")
		self.assertEqual(controller._activeKey, "key")
		controller._onResult(controller._token, "key", RuntimeError("current task"), 0)
		controller._startTaskCue(controller._token, "key")
		self.assertEqual(self.cues.play.call_count, 2)
		self.assertIsNone(controller._activeKey)

	def test_cancel_discards_queued_cue_start(self) -> None:
		controller = self.module.AutoRecognitionController(self.cues)
		controller._activeKey = "key"
		controller._activeCurrentKeyGetter = lambda: "key"
		engine = types.SimpleNamespace(cancel=Mock())
		handler = types.SimpleNamespace(getEngineInstance=lambda _name: engine)
		controller._resolveRecognitionScopedEngine = lambda _key: (handler, "engine", "key")
		queued = []
		self.module.wx.CallAfter = lambda callback, *args: queued.append((callback, args))
		controller._createRecognitionEngine(0, "key")
		controller.cancel()
		for callback, args in queued:
			callback(*args)
		self.cues.play.assert_not_called()

	def test_screenshot_cache_and_url_fallback_cue_lifecycle(self) -> None:
		module = self.module
		screenCapture = types.SimpleNamespace(
			ScreenCaptureError=RuntimeError,
			hasNvdaCapture=lambda: False,
			captureNvdaPixels=Mock(),
			imageFromNvdaPixels=Mock(),
			isScreenCurtainCaptureSupported=lambda: True,
		)
		module.ImageGrab.grab = lambda **_kwargs: types.SimpleNamespace(width=10, height=10)
		module.makeScreenshotContentKey = lambda image: ("content", image)
		with patch.dict(sys.modules, {"addon.globalPlugins.visAware._screenCapture": screenCapture}):
			for outcome in ("cache", "fallback", "fallbackFailure"):
				with self.subTest(outcome=outcome):
					self.cues.reset_mock()
					controller = module.AutoRecognitionController(self.cues)
					controller._activeKey = "key"
					controller._activeCurrentKeyGetter = lambda: "key"
					controller._makeTargetScopedContentKey = lambda *_args: "content"
					controller._getCachedResult = lambda _key: "cached" if outcome == "cache" else None
					engine = types.SimpleNamespace(
						cancel=Mock(),
						prefetchAuthHeaders=Mock(
							side_effect=[
								None,
								RuntimeError("auth failed") if outcome == "fallbackFailure" else None,
							]
						),
					)
					handler = types.SimpleNamespace(getEngineInstance=lambda _name: engine)
					controller._resolveRecognitionScopedEngine = lambda _key: (handler, "engine", "key")
					controller._recognizeImage = Mock(side_effect=RuntimeError("screenshot failed"))
					controller._downloadAndDescribeWithEngine = Mock()
					controller._captureAndDescribe(
						0, "key", (0, 0, 10, 10), 0, "https://example.test/image", lambda: "key"
					)
					if outcome == "cache":
						self.cues.play.assert_not_called()
						self.assertIsNone(controller._activeKey)
					else:
						self.cues.play.assert_called_once_with("auto")
						if outcome == "fallbackFailure":
							self.assertIsNone(controller._activeKey)
						else:
							controller._downloadAndDescribeWithEngine.assert_called_once()
							controller.cancel()


if __name__ == "__main__":
	unittest.main()
