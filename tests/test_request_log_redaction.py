from __future__ import annotations

import importlib.util
import builtins
from pathlib import Path
import sys
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

	engineGUIHelperModule = types.ModuleType("addon.globalPlugins.visAware.engineGUIHelper")
	engineGUIHelperModule.NumericEngineSetting = type("NumericEngineSetting", (), {})
	engineGUIHelperModule.TextInputEngineSetting = type("TextInputEngineSetting", (), {})
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
				"app_key": "app-key",
			},
			"imageContent": memoryview(b"image-bytes"),
			"url": "https://example.test/ocr?access_token=secret-token&safe=1",
			"json": {
				"src": "data:image/png;base64,abc",
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
		self.assertEqual(redacted["headers"]["app_key"], "<redacted>")
		self.assertEqual(redacted["imageContent"], "<memoryview: 11 bytes>")
		self.assertIn("access_token=%3Credacted%3E", redacted["url"])
		self.assertEqual(redacted["json"]["src"], "<redacted>")
		self.assertEqual(redacted["json"]["imageBytes"], "<redacted payload: 13 chars>")
		inlineData = redacted["json"]["contents"][0]["parts"][0]["inline_data"]
		self.assertEqual(inlineData["data"], "<redacted payload: 12 chars>")
		self.assertEqual(redacted["files"]["file"][1], "<bytes: 3 bytes>")


if __name__ == "__main__":
	unittest.main()
