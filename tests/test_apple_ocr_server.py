from __future__ import annotations

import builtins
import importlib.util
import json
from pathlib import Path
import sys
import types
from typing import Any
import unittest


class _BaseRecognizer:
	@staticmethod
	def _convertToJson(data: bytes) -> Any:
		return json.loads(data.decode("utf-8"))


class _TextInputEngineSetting:
	def __init__(self, **kwargs: object) -> None:
		self.name = kwargs["name"]


class _Log:
	def __init__(self) -> None:
		self.debugWarnings: list[str] = []

	def debugWarning(self, message: str, *args: object, **kwargs: object) -> None:
		self.debugWarnings.append(message)


def loadAppleOCRServerModule() -> types.ModuleType:
	builtins._ = lambda value: value

	addonHandlerModule = types.ModuleType("addonHandler")
	addonHandlerModule.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandlerModule

	logHandlerModule = types.ModuleType("logHandler")
	logHandlerModule.log = _Log()
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

	def test_normalizes_full_upload_url_and_scheme(self) -> None:
		self.engine.serverAddress = " HTTP://192.168.1.20:8000/upload/ "

		requestParams = self.engine._buildRequestParams(b"image", self.module.RecognitionRequest())

		self.assertEqual(requestParams["url"], "http://192.168.1.20:8000/upload")

	def test_rejects_invalid_server_addresses(self) -> None:
		for address in (
			"http://",
			"ftp://192.168.1.20:8000",
			"http://192.168.1.20:8000/other",
			"http://192.168.1.20:70000",
		):
			with self.subTest(address=address):
				self.engine.serverAddress = address
				with self.assertRaises(self.module.ApiError):
					self.engine._buildRequestParams(b"image", self.module.RecognitionRequest())

	def test_processes_upstream_response(self) -> None:
		response = {
			"success": True,
			"message": "File uploaded successfully",
			"ocr_result": "Hello\nWorld",
			"image_width": 1247,
			"image_height": 648,
			"ocr_boxes": [
				{"text": "Hello", "x": 429.65, "y": 268.0, "w": 201.83, "h": 72.0},
				{"text": "World", "x": 421.66, "y": 417.99, "w": 251.79, "h": 80.0},
			],
		}

		self.assertFalse(self.engine.processApiResult(json.dumps(response).encode("utf-8")))
		self.assertEqual(self.engine.extractText(response), "Hello\nWorld")
		self.assertEqual(
			self.engine._convertToLineResultFormat(response),
			[
				[{"text": "Hello", "x": 429, "y": 268, "width": 201, "height": 72}],
				[{"text": "World", "x": 421, "y": 417, "width": 251, "height": 80}],
			],
		)

	def test_rejects_invalid_response_shapes(self) -> None:
		responses = (
			[],
			None,
			{"success": True, "ocr_result": [], "ocr_boxes": []},
			{"success": True, "ocr_result": "", "ocr_boxes": {}},
		)
		for response in responses:
			with self.subTest(response=response):
				result = self.engine.processApiResult(json.dumps(response).encode("utf-8"))
				self.assertEqual(result, "OCR Server returned an invalid response.")

	def test_filters_malformed_boxes_without_logging_content(self) -> None:
		response = {
			"ocr_result": "Private text\nValid",
			"ocr_boxes": [
				None,
				{"text": "Private text", "x": "invalid", "y": 1, "w": 2, "h": 3},
				{"text": "Valid", "x": 10, "y": 20, "w": 30, "h": 40},
			],
		}

		self.assertEqual(
			self.engine._convertToLineResultFormat(response),
			[[{"text": "Valid", "x": 10, "y": 20, "width": 30, "height": 40}]],
		)
		self.assertEqual(len(self.module.log.debugWarnings), 1)
		self.assertNotIn("Private text", self.module.log.debugWarnings[0])

	def test_does_not_invent_coordinates_without_boxes(self) -> None:
		response = {"ocr_result": "Text without coordinates", "ocr_boxes": []}

		self.assertEqual(self.engine._convertToLineResultFormat(response), [])


if __name__ == "__main__":
	unittest.main()
