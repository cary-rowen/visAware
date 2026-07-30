from __future__ import annotations

import builtins
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


class _BaseRecognizer:
	@staticmethod
	def _convertToJson(data: bytes) -> dict[str, object]:
		return json.loads(data.decode("utf-8"))


class _TextInputEngineSetting:
	def __init__(self, **kwargs: object) -> None:
		self.name = kwargs["name"]


def loadAppleOCRServerModule() -> types.ModuleType:
	builtins._ = lambda value: value

	addonHandlerModule = types.ModuleType("addonHandler")
	addonHandlerModule.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandlerModule

	logHandlerModule = types.ModuleType("logHandler")
	logHandlerModule.log = type(
		"Log",
		(),
		{
			"error": staticmethod(lambda *args, **kwargs: None),
			"warning": staticmethod(lambda *args, **kwargs: None),
		},
	)()
	sys.modules["logHandler"] = logHandlerModule

	for moduleName in (
		"addon",
		"addon.globalPlugins",
		"addon.globalPlugins.visAware",
		"addon.globalPlugins.visAware.contentRecognizers",
	):
		module = types.ModuleType(moduleName)
		module.__path__ = []  # type: ignore[attr-defined]
		sys.modules[moduleName] = module

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")
	exceptionsModule.ApiError = type("ApiError", (Exception,), {})
	sys.modules[exceptionsModule.__name__] = exceptionsModule

	engineGUIHelperModule = types.ModuleType("addon.globalPlugins.visAware.engineGUIHelper")
	engineGUIHelperModule.TextInputEngineSetting = _TextInputEngineSetting
	sys.modules[engineGUIHelperModule.__name__] = engineGUIHelperModule

	recogHandlerModule = types.ModuleType("addon.globalPlugins.visAware.recogHandler")
	recogHandlerModule.BaseRecognizer = _BaseRecognizer
	recogHandlerModule.RecognitionRequest = type("RecognitionRequest", (), {})
	sys.modules[recogHandlerModule.__name__] = recogHandlerModule

	modulePath = (
		Path(__file__).resolve().parents[1]
		/ "addon"
		/ "globalPlugins"
		/ "visAware"
		/ "contentRecognizers"
		/ "appleOCRServer.py"
	)
	spec = importlib.util.spec_from_file_location(
		"addon.globalPlugins.visAware.contentRecognizers.appleOCRServer",
		modulePath,
	)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load appleOCRServer.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class AppleOCRServerTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.module = loadAppleOCRServerModule()
		self.engine = self.module.CustomContentRecognizer()

	def test_requires_explicit_server_address(self) -> None:
		self.assertEqual(self.engine.serverAddress, "")

		with self.assertRaises(self.module.ApiError):
			self.engine._buildRequestParams(b"image", self.module.RecognitionRequest())

	def test_builds_upstream_upload_request(self) -> None:
		self.engine.serverAddress = " 192.168.1.20:8000/ "

		requestParams = self.engine._buildRequestParams(b"image", self.module.RecognitionRequest())

		self.assertEqual(requestParams["url"], "http://192.168.1.20:8000/upload")
		self.assertEqual(requestParams["files"]["file"], ("image.png", b"image", "image/png"))
		self.assertEqual(requestParams["headers"], {"Accept": "application/json"})
		self.assertEqual(requestParams["timeout"], 60)

	def test_uses_fallback_for_missing_server_error_message(self) -> None:
		result = self.engine.processApiResult(b'{"success": false}')

		self.assertEqual(result, "OCR Server error: Unknown server error")


if __name__ == "__main__":
	unittest.main()
