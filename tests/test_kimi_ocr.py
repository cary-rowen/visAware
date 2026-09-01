from __future__ import annotations

import builtins
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest


def _load_module(moduleName: str, relativePath: str):
	modulePath = Path(__file__).resolve().parents[1] / relativePath
	spec = importlib.util.spec_from_file_location(moduleName, modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError(f"Failed to load {relativePath}")
	module = importlib.util.module_from_spec(spec)
	sys.modules[moduleName] = module
	spec.loader.exec_module(module)
	return module


def load_kimi_ocr_module():
	builtins._ = lambda value: value
	addonHandlerModule = types.ModuleType("addonHandler")
	addonHandlerModule.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandlerModule
	logHandlerModule = types.ModuleType("logHandler")
	logHandlerModule.log = type(
		"Log",
		(),
		{
			"warning": staticmethod(lambda *args, **kwargs: None),
			"debugWarning": staticmethod(lambda *args, **kwargs: None),
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

	pilModule = types.ModuleType("PIL")
	pilImageModule = types.ModuleType("PIL.Image")
	pilImageModule.Image = type("Image", (), {})
	pilModule.Image = pilImageModule
	sys.modules["PIL"] = pilModule
	sys.modules["PIL.Image"] = pilImageModule

	class Setting:
		def __init__(self, name=None, *args, **kwargs):
			self.name = name or kwargs.get("name", "")

	settingsModule = types.ModuleType("addon.globalPlugins.visAware.engineGUIHelper")
	settingsModule.ChoiceEngineSetting = Setting
	settingsModule.TextInputEngineSetting = Setting
	sys.modules[settingsModule.__name__] = settingsModule

	class ApiError(Exception):
		pass

	class StreamIncompleteError(ApiError):
		pass

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")
	exceptionsModule.ApiError = ApiError
	exceptionsModule.AuthenticationError = type("AuthenticationError", (ApiError,), {})
	exceptionsModule.StreamIncompleteError = StreamIncompleteError
	sys.modules[exceptionsModule.__name__] = exceptionsModule

	@dataclass
	class RecognitionRequest:
		textResult: bool = False
		streamResult: bool = False

	class BaseRecognizer:
		def generateStringSettings(self, values):
			return values

		def isSupported(self, settingName):
			return any(setting.name == settingName for setting in self.supportedSettings)

		def autoRecognitionModelSetting(self):
			return Setting("autoRecognitionModel")

		def _serializeImage(self, image):
			return b"image"

		def _cleanup(self):
			pass

	recogHandlerModule = types.ModuleType("addon.globalPlugins.visAware.recogHandler")
	recogHandlerModule.BaseRecognizer = BaseRecognizer
	recogHandlerModule.RecognitionRequest = RecognitionRequest
	sys.modules[recogHandlerModule.__name__] = recogHandlerModule

	_load_module(
		"addon.globalPlugins.visAware.kimiModels",
		"addon/globalPlugins/visAware/kimiModels.py",
	)
	_load_module(
		"addon.globalPlugins.visAware.kimiAPI",
		"addon/globalPlugins/visAware/kimiAPI.py",
	)
	return _load_module(
		"addon.globalPlugins.visAware.contentRecognizers.kimiOCR",
		"addon/globalPlugins/visAware/contentRecognizers/kimiOCR.py",
	)


class KimiOCRTestCase(unittest.TestCase):
	def test_rejects_non_finite_coordinates(self) -> None:
		module = load_kimi_ocr_module()
		engine = module.CustomContentRecognizer()
		result = {
			"lines": [
				{"words": [{"text": "bad", "box2d": [0, float("nan"), 10, 20]}]},
			],
		}

		with self.assertRaises(module.ApiError):
			engine._validateStructuredResult(result)

	def test_normalizes_reversed_and_out_of_range_box(self) -> None:
		module = load_kimi_ocr_module()

		self.assertEqual(
			module.CustomContentRecognizer._normalizeBox([-10, 900, 1200, 100]),
			(0, 100, 1000, 900),
		)

	def test_converts_valid_box_to_uploaded_image_coordinates(self) -> None:
		module = load_kimi_ocr_module()
		engine = module.CustomContentRecognizer()
		engine._uploadedImageSize = (200, 100)

		word = engine._convertStructuredWord(
			{"text": "hello", "box2d": [100.4, 250.4, 500.4, 750.4]},
		)

		self.assertEqual(word, {"text": "hello", "x": 50, "y": 10, "width": 100, "height": 40})

	def test_rejects_truncated_json_response(self) -> None:
		module = load_kimi_ocr_module()
		engine = module.CustomContentRecognizer()

		with self.assertRaises(module.ApiError):
			engine._extractStructuredResult(
				{
					"choices": [
						{
							"finish_reason": "length",
							"message": {"role": "assistant", "content": '{"lines": []}'},
						},
					],
				},
			)

	def test_empty_response_raises_api_error(self) -> None:
		module = load_kimi_ocr_module()
		engine = module.CustomContentRecognizer()

		with self.assertRaises(module.ApiError):
			engine._extractStructuredResult(
				{
					"choices": [
						{
							"finish_reason": "stop",
							"message": {"role": "assistant", "content": ""},
						},
					],
				},
			)

	def test_accepts_json_markdown_fence(self) -> None:
		module = load_kimi_ocr_module()
		engine = module.CustomContentRecognizer()

		result = engine._extractStructuredResult(
			{
				"choices": [
					{
						"finish_reason": "stop",
						"message": {
							"role": "assistant",
							"content": '```json\n{"lines": []}\n```',
						},
					},
				],
			},
		)

		self.assertEqual(result, {"lines": []})

	def test_k3_builds_strict_chat_structured_request(self) -> None:
		module = load_kimi_ocr_module()
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		request = engine._buildRequestParams(b"image", module.RecognitionRequest())

		self.assertEqual(engine.reasoningEffort, "high")
		self.assertEqual(engine.baseUrl, "https://api.kimi.com/coding/v1")
		self.assertEqual(request["url"], "https://api.kimi.com/coding/v1/chat/completions")
		self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
		self.assertNotIn("x-api-key", request["headers"])
		self.assertEqual(request["json"]["response_format"]["type"], "json_schema")
		self.assertTrue(request["json"]["response_format"]["json_schema"]["strict"])
		self.assertNotIn(
			"minItems",
			request["json"]["response_format"]["json_schema"]["schema"]["properties"]["lines"]["items"][
				"properties"
			]["words"]["items"]["properties"]["box2d"],
		)
		self.assertEqual(request["json"]["messages"][1]["content"][0]["type"], "image_url")

	def test_k26_uses_json_mode_and_local_validation(self) -> None:
		module = load_kimi_ocr_module()
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		engine.baseUrl = "https://api.moonshot.ai/v1"
		engine.model = "kimi-k2.6"
		request = engine._buildRequestParams(b"image", module.RecognitionRequest())

		self.assertEqual(request["json"]["response_format"], {"type": "json_object"})
		self.assertEqual(request["json"]["thinking"], {"type": "enabled", "keep": "all"})
		self.assertIn(
			"Each word object must contain exactly a string text and a four-number box2d array.",
			request["json"]["messages"][1]["content"][1]["text"],
		)

	def test_available_models_follow_official_base_url(self) -> None:
		module = load_kimi_ocr_module()
		engine = module.CustomContentRecognizer()

		self.assertEqual(
			list(engine.availableModels),
			["k3-256k", "k3", "kimi-for-coding", "kimi-for-coding-highspeed"],
		)
		engine.baseUrl = "https://api.moonshot.ai/v1"
		self.assertEqual(engine.model, "kimi-k3")
		self.assertEqual(
			list(engine.availableModels),
			["kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6"],
		)
		engine.baseUrl = "https://api.moonshot.cn/v1"
		self.assertEqual(engine.model, "kimi-k3")

	def test_k3_preserves_max_reasoning_effort(self) -> None:
		module = load_kimi_ocr_module()
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		engine.model = "k3"
		engine.reasoningEffort = "max"

		request = engine._buildRequestParams(b"image", module.RecognitionRequest())

		self.assertEqual(request["json"]["reasoning_effort"], "max")

	def test_invalid_k26_reasoning_effort_uses_safe_default(self) -> None:
		module = load_kimi_ocr_module()
		engine = module.CustomContentRecognizer()
		engine.baseUrl = "https://api.moonshot.ai/v1"
		engine.model = "kimi-k2.6"
		engine.reasoningEffort = "invalid"

		self.assertEqual(engine.reasoningEffort, "high")


if __name__ == "__main__":
	unittest.main()
