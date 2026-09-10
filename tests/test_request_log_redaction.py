from __future__ import annotations

import importlib.util
import builtins
from pathlib import Path
import sys
from threading import Event, current_thread
import types
import unittest


def _install_module_stubs() -> None:
	builtins._ = lambda value: value
	for moduleName in (
		"addonHandler",
		"api",
		"config",
		"contentRecog",
		"gui",
		"gui.nvdaControls",
		"gui.guiHelper",
		"gui.settingsDialogs",
		"logHandler",
		"PIL",
		"PIL.Image",
		"PIL.ImageGrab",
		"wx",
	):
		if moduleName not in sys.modules:
			sys.modules[moduleName] = types.ModuleType(moduleName)

	sys.modules["addonHandler"].initTranslation = lambda: None
	sys.modules["config"].conf = {}
	sys.modules["logHandler"].log = type(
		"Log",
		(),
		{
			"debugWarning": staticmethod(lambda *args, **kwargs: None),
			"warning": staticmethod(lambda *args, **kwargs: None),
		},
	)()

	contentRecogModule = sys.modules["contentRecog"]
	contentRecogModule.ContentRecognizer = type("ContentRecognizer", (), {})
	contentRecogModule.LinesWordsResult = type("LinesWordsResult", (), {})
	contentRecogModule.RecogImageInfo = type("RecogImageInfo", (), {})

	guiNvdaControlsModule = sys.modules["gui.nvdaControls"]
	guiNvdaControlsModule.CustomCheckListBox = type("CustomCheckListBox", (), {})
	guiNvdaControlsModule.EnhancedInputSlider = type("EnhancedInputSlider", (), {})

	guiGuiHelperModule = sys.modules["gui.guiHelper"]
	guiGuiHelperModule.BoxSizerHelper = type("BoxSizerHelper", (), {})
	sys.modules["gui"].guiHelper = guiGuiHelperModule

	guiSettingsDialogsModule = sys.modules["gui.settingsDialogs"]
	guiSettingsDialogsModule.SettingsPanel = type("SettingsPanel", (), {})

	pilImageModule = sys.modules["PIL.Image"]
	pilImageModule.LANCZOS = 1
	pilImageModule.Image = type("Image", (), {})

	wxModule = sys.modules["wx"]
	wxModule.BoxSizer = type("BoxSizer", (), {})
	wxModule.CommandEvent = type("CommandEvent", (), {})
	wxModule.NOT_FOUND = -1

	for moduleName in (
		"addon",
		"addon.globalPlugins",
		"addon.globalPlugins.visAware",
	):
		module = sys.modules.get(moduleName)
		if module is None:
			module = types.ModuleType(moduleName)
			module.__path__ = []  # type: ignore[attr-defined]
			sys.modules[moduleName] = module

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")
	for name in (
		"ApiError",
		"CancellationError",
		"StreamIncompleteError",
		"StreamReplacementError",
	):
		setattr(exceptionsModule, name, type(name, (Exception,), {}))
	sys.modules["addon.globalPlugins.visAware.exceptions"] = exceptionsModule

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")
	sys.modules["addon.globalPlugins.visAware.network"] = networkModule
	recogHistoryModule = types.ModuleType("addon.globalPlugins.visAware.recogHistory")
	recogHistoryModule.HistoryEntryPayload = type("HistoryEntryPayload", (), {})
	sys.modules["addon.globalPlugins.visAware.recogHistory"] = recogHistoryModule
	sys.modules["addon.globalPlugins.visAware"].recogHistory = recogHistoryModule
	conversationModule = types.ModuleType("addon.globalPlugins.visAware.conversation")
	for name in ("QuestionStreamEvent", "QuestionStreamFinished", "QuestionStreamText"):
		setattr(conversationModule, name, type(name, (), {}))
	sys.modules["addon.globalPlugins.visAware.conversation"] = conversationModule
	contentRecognizersModule = types.ModuleType("addon.globalPlugins.visAware.contentRecognizers")
	contentRecognizersModule.__path__ = []  # type: ignore[attr-defined]
	sys.modules["addon.globalPlugins.visAware.contentRecognizers"] = contentRecognizersModule
	imageDescribersModule = types.ModuleType("addon.globalPlugins.visAware.imageDescribers")
	imageDescribersModule.__path__ = []  # type: ignore[attr-defined]
	sys.modules["addon.globalPlugins.visAware.imageDescribers"] = imageDescribersModule
	promptsModule = types.ModuleType("addon.globalPlugins.visAware.imageDescribers._prompts")
	promptsModule.DEFAULT_AUTO_RECOGNITION_PROMPT = "auto prompt"
	promptsModule.buildImageDescriptionPrompt = lambda prompt, useMarkdown: prompt
	sys.modules["addon.globalPlugins.visAware.imageDescribers._prompts"] = promptsModule

	engineGUIHelperModule = types.ModuleType("addon.globalPlugins.visAware.engineGUIHelper")
	engineGUIHelperModule.NumericEngineSetting = type("NumericEngineSetting", (), {})

	class TextInputEngineSetting:
		def __init__(self, name: str, displayNameWithAccelerator: str, **kwargs):
			self.name = name
			self.displayNameWithAccelerator = displayNameWithAccelerator
			for key, value in kwargs.items():
				setattr(self, key, value)

	engineGUIHelperModule.TextInputEngineSetting = TextInputEngineSetting
	sys.modules["addon.globalPlugins.visAware.engineGUIHelper"] = engineGUIHelperModule

	abstractEngineModule = types.ModuleType("addon.globalPlugins.visAware.abstractEngine")
	abstractEngineModule.AbstractEngine = type("AbstractEngine", (), {})
	abstractEngineModule.AbstractEngineHandler = type("AbstractEngineHandler", (), {})
	abstractEngineModule.AbstractEngineSettingsPanel = type("AbstractEngineSettingsPanel", (), {})
	abstractEngineModule.EngineSetting = type("EngineSetting", (), {})
	sys.modules["addon.globalPlugins.visAware.abstractEngine"] = abstractEngineModule


def load_recog_handler_module():
	_install_module_stubs()
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "recogHandler.py"
	)
	spec = importlib.util.spec_from_file_location("addon.globalPlugins.visAware.recogHandler", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load recogHandler.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class RequestLogRedactionTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.module = load_recog_handler_module()

	def test_redacts_nested_request_payloads_and_query_secrets(self) -> None:
		requestParams = {
			"headers": {
				"x-goog-api-key": "gemini-key",
				"x-api-key": "kimi-key",
				"app_key": "app-key",
			},
			"imageContent": memoryview(b"image-bytes"),
			"url": "https://example.test/ocr?access_token=secret-token&safe=1",
			"json": {
				"src": "data:image/png;base64,abc",
				"image_url": {"url": "data:image/jpeg;base64,encoded-image"},
				"imageBytes": "encoded-image",
				"contents": [
					{
						"parts": [
							{
								"inline_data": {
									"mime_type": "image/png",
									"data": "base64-image",
								},
							},
						],
					},
				],
			},
			"files": {
				"file": ("image.png", b"123", "image/png"),
			},
		}
		redacted = self.module._redactRequestParamsForLog(requestParams)

		self.assertEqual(redacted["headers"]["x-goog-api-key"], "<redacted>")
		self.assertEqual(redacted["headers"]["x-api-key"], "<redacted>")
		self.assertEqual(redacted["headers"]["app_key"], "<redacted>")
		self.assertEqual(redacted["imageContent"], "<memoryview: 11 bytes>")
		self.assertIn("access_token=%3Credacted%3E", redacted["url"])
		self.assertEqual(redacted["json"]["src"], "<redacted>")
		self.assertEqual(
			redacted["json"]["image_url"]["url"],
			"<redacted data URL: 36 chars>",
		)
		self.assertEqual(redacted["json"]["imageBytes"], "<redacted payload: 13 chars>")
		inlineData = redacted["json"]["contents"][0]["parts"][0]["inline_data"]
		self.assertEqual(inlineData["data"], "<redacted payload: 12 chars>")
		self.assertEqual(redacted["files"]["file"][1], "<bytes: 3 bytes>")

	def test_image_description_automatic_prompt_uses_new_configuration_key(self) -> None:
		setting = self.module.BaseDescriber.autoRecognitionPromptSetting()

		self.assertEqual(setting.name, "autoRecognitionPrompt")
		self.assertEqual(setting.configKey, "autoRecognitionPromptV2")
		self.assertEqual(self.module.BaseDescriber._autoRecognitionPrompt, "auto prompt")

	def test_markdown_instruction_only_applies_to_simple_image_results(self) -> None:
		self.module.config.conf = {"visAwareGeneral": {"useBrowseableMessage": True}}
		self.module.buildImageDescriptionPrompt = (
			lambda prompt, useMarkdown: f"{prompt} [markdown={useMarkdown}]"
		)
		testDescriber = type(
			"TestDescriber",
			(self.module.BaseDescriber,),
			{
				"supportedSettings": property(lambda _self: []),
				"_buildRequestParams": lambda _self, _imageContent, _request: {},
				"processApiResult": lambda _self, _result: False,
				"extractText": lambda _self, _apiResult: "",
			},
		)
		engine = object.__new__(testDescriber)
		engine.prompt = "Describe the image."
		engine.streamResult = False

		engine.textResult = True
		simpleRequest = engine._buildRecognitionRequest()
		self.assertEqual(simpleRequest.prompt, "Describe the image. [markdown=True]")
		self.module.config.conf["visAwareGeneral"]["useBrowseableMessage"] = False
		plainRequest = engine._buildRecognitionRequest()
		self.assertEqual(plainRequest.prompt, "Describe the image. [markdown=False]")

		engine.textResult = False
		richRequest = engine._buildRecognitionRequest()
		self.assertEqual(richRequest.prompt, "Describe the image.")

		automaticRequest = engine._buildRecognitionRequest(isAutomaticRecognition=True)
		self.assertTrue(automaticRequest.isAutomaticRecognition)

	def test_automatic_requests_use_short_timeout_and_cancellation_check(self) -> None:
		capturedParams = {}
		testRecognizer = type(
			"TestRecognizer",
			(self.module.BaseRecognizer,),
			{
				"supportedSettings": property(lambda _self: []),
				"supportsStreaming": False,
				"_prepareImageContent": lambda _self, _image, _imageInfo: b"image",
				"_buildRequestParams": lambda _self, _imageContent, _request: {
					"method": "POST",
					"url": "https://example.test",
				},
				"_handleStandardResponse": lambda _self, requestParams, *_args: capturedParams.update(
					requestParams,
				),
				"processApiResult": lambda _self, _result: False,
				"extractText": lambda _self, _apiResult: "",
				"_convertToLineResultFormat": lambda _self, _apiResult: [],
			},
		)
		engine = object.__new__(testRecognizer)
		engine.name = "test"
		request = self.module.RecognitionRequest(
			textResult=False,
			streamResult=False,
			isAutomaticRecognition=True,
		)
		cancellationEvent = Event()
		self.module.config.conf = {"visAwareGeneral": {"verboseDebugLogging": False}}
		self.module.wx.CallAfter = lambda *_args: None

		engine._runRecognition(object(), object(), lambda _result: None, cancellationEvent, request)

		self.assertEqual(capturedParams["timeout"], self.module.AUTO_RECOGNITION_REQUEST_TIMEOUT)
		self.assertTrue(callable(capturedParams["cancelCheck"]))
		cancellationEvent.set()
		with self.assertRaises(self.module.CancellationError):
			capturedParams["cancelCheck"]()

	def test_automatic_recognition_can_reuse_the_controller_worker(self) -> None:
		workerThreads = []
		engine = types.SimpleNamespace(
			_buildRecognitionRequest=lambda _automatic: self.module.RecognitionRequest(False, False),
		)
		engine._recognitionImageWorker = (
			lambda _image, _onResult, _cancellationEvent, _request: workerThreads.append(
				engine._recognitionThread,
			)
		)

		self.module.BaseRecognizer.recognizeImage(
			engine,
			object(),
			lambda _result: None,
			isAutomaticRecognition=True,
			runInBackground=False,
		)

		self.assertEqual(workerThreads, [current_thread()])


class AutoRecognitionSettingsTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.module = load_recog_handler_module()

	def test_unavailable_configured_auto_recognition_engine_stays_selected(self) -> None:
		class _ConfigSection(dict):
			def __getitem__(self, key: str, checkValidity: bool = True):
				return super().__getitem__(key)

		self.module.config.conf = {
			"visAwareGeneral": _ConfigSection(
				autoRecognitionEngine="imageDescriber:missingImageEngine",
			),
		}
		self.module.ImageDescriberHandler.getEngineList = classmethod(
			lambda _cls: [("vivoImageDescriber", "Vivo image describer")],
		)

		_typeChoices, engineChoices, _typeSelection, engineSelection = (
			self.module.getAutoRecognitionTypeAndEngineChoices()
		)

		self.assertEqual(engineChoices[engineSelection][0], "missingImageEngine")


if __name__ == "__main__":
	unittest.main()
