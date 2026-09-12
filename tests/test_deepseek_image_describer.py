from __future__ import annotations

import builtins
from dataclasses import dataclass, field
import importlib.util
from pathlib import Path
import sys
import types
import unittest


@dataclass
class _BoundWindow:
	hwnd: int = 1
	processID: int = 1
	appName: str = "test"
	title: str = "Test"
	left: int = 0
	top: int = 0
	width: int = 800
	height: int = 600


@dataclass
class _Screenshot:
	imageBase64: str = "image"
	mimeType: str = "image/jpeg"
	width: int = 800
	height: int = 600
	screenLeft: int = 0
	screenTop: int = 0
	screenWidth: int = 800
	screenHeight: int = 600
	digest: str = "digest"
	window: _BoundWindow = field(default_factory=_BoundWindow)


def _load_module(moduleName: str, relativePath: str):
	modulePath = Path(__file__).resolve().parents[1] / relativePath
	spec = importlib.util.spec_from_file_location(moduleName, modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError(f"Failed to load {relativePath}")
	module = importlib.util.module_from_spec(spec)
	sys.modules[moduleName] = module
	spec.loader.exec_module(module)
	return module


def _install_stubs() -> list[dict]:
	builtins._ = lambda value: value
	requests_seen: list[dict] = []

	addonHandlerModule = types.ModuleType("addonHandler")
	addonHandlerModule.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandlerModule

	configModule = types.ModuleType("config")
	configModule.conf = {"visAwareGeneral": {"verboseDebugLogging": False}}
	sys.modules["config"] = configModule

	logHandlerModule = types.ModuleType("logHandler")
	logHandlerModule.log = type(
		"Log",
		(),
		{
			"debug": staticmethod(lambda *args, **kwargs: None),
			"info": staticmethod(lambda *args, **kwargs: None),
		},
	)()
	sys.modules["logHandler"] = logHandlerModule

	for moduleName in (
		"addon",
		"addon.globalPlugins",
		"addon.globalPlugins.visAware",
		"addon.globalPlugins.visAware.imageDescribers",
	):
		module = types.ModuleType(moduleName)
		module.__path__ = []  # type: ignore[attr-defined]
		sys.modules[moduleName] = module

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")
	exceptionsModule.ApiError = type("ApiError", (Exception,), {})
	exceptionsModule.AuthenticationError = type("AuthenticationError", (Exception,), {})
	sys.modules[exceptionsModule.__name__] = exceptionsModule
	ApiError = exceptionsModule.ApiError

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")

	class _Response:
		def __init__(self, payload: dict | bytes):
			self._payload = payload
			self.content = payload if isinstance(payload, bytes) else b""

		def json(self) -> dict:
			if isinstance(self._payload, dict):
				return self._payload
			raise TypeError("No JSON payload was provided.")

	def sendRequest(*args, **kwargs):
		record = dict(kwargs)
		if args:
			record["method"] = args[0]
		if len(args) > 1:
			record["url"] = args[1]
		requests_seen.append(record)
		return _Response(kwargs.pop("_response"))

	networkModule.sendRequest = sendRequest
	sys.modules[networkModule.__name__] = networkModule

	recogHandlerModule = types.ModuleType("addon.globalPlugins.visAware.recogHandler")

	@dataclass
	class RecognitionRequest:
		textResult: bool = True
		streamResult: bool = False
		prompt: str | None = None

	class BaseDescriber:
		def generateStringSettings(self, values):
			return values

		def isSupported(self, settingName):
			return any(setting.name == settingName for setting in self.supportedSettings)

		def _convertToJson(self, data: bytes):
			import json

			return json.loads(data.decode("utf-8"))

		def prepareImageContentFromImage(self, image):
			return b"encoded-image"

		def _getConversationImage(self, context):
			return context.image

		def _checkQuestionCancelled(self, cancellationChecker):
			if cancellationChecker is not None:
				cancellationChecker()

		def _validateQuestionAnswer(self, answer):
			answer = answer.strip()
			if not answer:
				raise ApiError("Answer is blank.")
			return answer

		@staticmethod
		def imageQualitySetting():
			return types.SimpleNamespace(name="imageQuality")

		@staticmethod
		def autoRecognitionPromptSetting():
			return types.SimpleNamespace(name="autoRecognitionPrompt")

		@staticmethod
		def autoRecognitionModelSetting():
			return types.SimpleNamespace(name="autoRecognitionModel")

	recogHandlerModule.BaseDescriber = BaseDescriber
	recogHandlerModule.RecognitionRequest = RecognitionRequest
	recogHandlerModule._redactRequestParamsForLog = lambda value, keyName="": value
	sys.modules[recogHandlerModule.__name__] = recogHandlerModule

	engineGuiModule = types.ModuleType("addon.globalPlugins.visAware.engineGUIHelper")
	engineGuiModule.ChoiceEngineSetting = lambda *args, **kwargs: types.SimpleNamespace(
		name=kwargs.get("name", args[0] if args else ""),
	)
	engineGuiModule.TextInputEngineSetting = lambda *args, **kwargs: types.SimpleNamespace(
		name=kwargs.get("name", args[0] if args else ""),
	)
	sys.modules[engineGuiModule.__name__] = engineGuiModule

	_load_module(
		"addon.globalPlugins.visAware.imageDescribers._prompts",
		"addon/globalPlugins/visAware/imageDescribers/_prompts.py",
	)
	_load_module(
		"addon.globalPlugins.visAware.deepseekModels",
		"addon/globalPlugins/visAware/deepseekModels.py",
	)
	module = _load_module(
		"addon.globalPlugins.visAware.imageDescribers.deepseek",
		"addon/globalPlugins/visAware/imageDescribers/deepseek.py",
	)
	return requests_seen


class DeepSeekImageDescriberTestCase(unittest.TestCase):
	def test_build_request_uses_chat_payload(self) -> None:
		requests_seen = _install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.imageDescribers.deepseek"]
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		requests_seen.clear()

		request = engine._buildRequestParams(
			b"encoded-image",
			module.RecognitionRequest(prompt="Describe it."),
		)

		self.assertEqual(request["url"], "https://api.deepseek.com/chat/completions")
		self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
		self.assertEqual(request["timeout"], 60)
		self.assertEqual(request["json"]["model"], "deepseek-flash")
		self.assertEqual(request["json"]["thinking"], {"type": "disabled"})
		self.assertEqual(request["json"]["max_tokens"], 2048)
		self.assertEqual(request["json"]["messages"][0]["role"], "user")
		self.assertEqual(request["json"]["messages"][0]["content"][0]["type"], "text")
		self.assertEqual(request["json"]["messages"][0]["content"][1]["type"], "image_url")
		self.assertEqual(
			request["json"]["messages"][0]["content"][1]["image_url"]["url"],
			"data:image/jpeg;base64,encoded-image",
		)
		self.assertEqual(request["json"]["messages"][0]["content"][0]["text"], "Describe it.")

	def test_extract_text_reads_chat_message(self) -> None:
		_install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.imageDescribers.deepseek"]
		engine = module.CustomContentRecognizer()
		response = {
			"id": "chatcmpl-1",
			"object": "chat.completion",
			"choices": [
				{
					"index": 0,
					"finish_reason": "stop",
					"message": {"role": "assistant", "content": "A cat on a chair."},
				},
			],
		}

		self.assertEqual(
			engine.processApiResult(
				b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":""}}]}',
			),
			"Server returned a successful but empty response.",
		)
		self.assertEqual(engine.extractText(response), "A cat on a chair.")

	def test_incomplete_response_returns_error(self) -> None:
		_install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.imageDescribers.deepseek"]
		engine = module.CustomContentRecognizer()

		result = engine.processApiResult(
			b'{"choices":[{"finish_reason":"length","message":{"role":"assistant","content":"partial"}}]}',
		)

		self.assertEqual(result, "DeepSeek response was truncated at the token limit.")

	def test_process_api_result_rejects_malformed_body(self) -> None:
		_install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.imageDescribers.deepseek"]
		engine = module.CustomContentRecognizer()

		self.assertEqual(
			engine.processApiResult(b"<html>bad gateway</html>"),
			"Invalid response from server.",
		)

	def test_follow_up_rejects_blank_answer(self) -> None:
		_install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.imageDescribers.deepseek"]
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		engine.processApiResult = lambda result: False
		engine._convertToJson = lambda result: {
			"choices": [
				{
					"finish_reason": "stop",
					"message": {"role": "assistant", "content": "   "},
				},
			],
		}
		context = types.SimpleNamespace(image=object(), initialText="A cat sits on a chair.", turns=[])
		sys.modules["addon.globalPlugins.visAware.network"].sendRequest = (
			lambda **kwargs: types.SimpleNamespace(
				content=b"{}",
			)
		)

		with self.assertRaisesRegex(module.ApiError, "Answer is blank."):
			engine.askQuestion(context, "What does the chair look like?")

	def test_ask_question_rejects_malformed_body(self) -> None:
		requests_seen = _install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.imageDescribers.deepseek"]
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		responseModule = sys.modules["addon.globalPlugins.visAware.network"]
		responseModule.sendRequest = lambda **kwargs: (
			requests_seen.append(dict(kwargs)) or types.SimpleNamespace(content=b"<html>bad gateway</html>")
		)
		context = types.SimpleNamespace(image=object(), initialText="A cat sits on a chair.", turns=[])

		with self.assertRaisesRegex(module.ApiError, "Invalid response from server."):
			engine.askQuestion(context, "What does the chair look like?")
		self.assertEqual(len(requests_seen), 1)

	def test_follow_up_reuses_image_and_history(self) -> None:
		requests_seen = _install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.imageDescribers.deepseek"]
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		context = types.SimpleNamespace(
			image=object(),
			initialText="A cat sits on a chair.",
			turns=[
				types.SimpleNamespace(role="user", text="What color is the chair?"),
				types.SimpleNamespace(role="assistant", text="The chair is blue."),
			],
		)

		responseModule = sys.modules["addon.globalPlugins.visAware.network"]

		class _FollowUpResponse:
			content = b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"It is a blue chair."}}]}'

			def json(self):
				return {
					"choices": [
						{
							"finish_reason": "stop",
							"message": {"role": "assistant", "content": "It is a blue chair."},
						},
					],
				}

		responseModule.sendRequest = lambda **kwargs: (
			requests_seen.append(dict(kwargs)) or _FollowUpResponse()
		)

		answer = engine.askQuestion(context, "What does the chair look like?")

		self.assertEqual(answer, "It is a blue chair.")
		self.assertEqual(requests_seen[0]["url"], "https://api.deepseek.com/chat/completions")
		self.assertEqual(requests_seen[0]["json"]["thinking"], {"type": "disabled"})
		self.assertEqual(requests_seen[0]["json"]["messages"][0]["content"][1]["type"], "image_url")
		self.assertEqual(requests_seen[0]["json"]["messages"][1]["role"], "assistant")
		self.assertEqual(requests_seen[0]["json"]["messages"][1]["content"], "A cat sits on a chair.")
		self.assertEqual(
			requests_seen[0]["json"]["messages"][-1]["content"],
			"What does the chair look like?",
		)

	def test_redacts_image_url_before_logging(self) -> None:
		_install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.imageDescribers.deepseek"]
		redacted = module._redactForLog(
			{
				"json": {
					"messages": [
						{
							"role": "user",
							"content": [
								{
									"type": "image_url",
									"image_url": {
										"url": "data:image/jpeg;base64,AAAA",
										"detail": "high",
									},
								},
							],
						},
					],
				},
			},
		)

		self.assertTrue(
			redacted["json"]["messages"][0]["content"][0]["image_url"]["url"].startswith(
				"<redacted data URL:",
			),
		)

	def test_available_models_are_fixed(self) -> None:
		_install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.imageDescribers.deepseek"]
		engine = module.CustomContentRecognizer()

		self.assertEqual(list(engine.availableModels), ["deepseek-flash"])
		engine.model = "deepseek-v4-pro"
		self.assertEqual(engine.model, "deepseek-flash")


if __name__ == "__main__":
	unittest.main()
