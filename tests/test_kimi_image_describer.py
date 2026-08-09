from __future__ import annotations

import builtins
from dataclasses import dataclass
import importlib.util
from pathlib import Path
from types import SimpleNamespace
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


def load_kimi_image_module():
	builtins._ = lambda value: value
	addonHandlerModule = types.ModuleType("addonHandler")
	addonHandlerModule.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandlerModule
	for moduleName in (
		"addon",
		"addon.globalPlugins",
		"addon.globalPlugins.visAware",
		"addon.globalPlugins.visAware.imageDescribers",
	):
		module = types.ModuleType(moduleName)
		module.__path__ = []  # type: ignore[attr-defined]
		sys.modules[moduleName] = module

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")
	sys.modules[networkModule.__name__] = networkModule

	conversationModule = types.ModuleType("addon.globalPlugins.visAware.conversation")
	conversationModule.QuestionStreamFinished = type("QuestionStreamFinished", (), {})
	conversationModule.QuestionStreamText = type("QuestionStreamText", (), {})
	sys.modules[conversationModule.__name__] = conversationModule

	class Setting:
		def __init__(self, name=None, *args, **kwargs):
			self.name = name or kwargs.get("name", "")
			for key, value in kwargs.items():
				setattr(self, key, value)

	settingsModule = types.ModuleType("addon.globalPlugins.visAware.engineGUIHelper")
	settingsModule.BooleanEngineSetting = Setting
	settingsModule.ChoiceEngineSetting = Setting
	settingsModule.TextInputEngineSetting = Setting
	sys.modules[settingsModule.__name__] = settingsModule

	class ApiError(Exception):
		pass

	class StreamIncompleteError(ApiError):
		def __init__(self, message: str, partialText: str = ""):
			super().__init__(message)
			self.partialText = partialText

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")
	exceptionsModule.ApiError = ApiError
	exceptionsModule.AuthenticationError = type("AuthenticationError", (ApiError,), {})
	exceptionsModule.StreamIncompleteError = StreamIncompleteError
	sys.modules[exceptionsModule.__name__] = exceptionsModule

	@dataclass
	class RecognitionRequest:
		textResult: bool = True
		streamResult: bool = True

	class BaseDescriber:
		def generateStringSettings(self, values):
			return values

		def isSupported(self, settingName):
			return any(setting.name == settingName for setting in self.supportedSettings)

		def _getConversationImage(self, context):
			return context.image

		def prepareImageContentFromImage(self, image):
			return b"encoded-image"

		def autoRecognitionModelSetting(self):
			return Setting("autoRecognitionModel")

		def autoRecognitionPromptSetting(self):
			return Setting("autoRecognitionPrompt")

		def imageQualitySetting(self):
			return Setting("imageQuality")

	recogHandlerModule = types.ModuleType("addon.globalPlugins.visAware.recogHandler")
	recogHandlerModule.BaseDescriber = BaseDescriber
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
		"addon.globalPlugins.visAware.imageDescribers.kimi",
		"addon/globalPlugins/visAware/imageDescribers/kimi.py",
	)


def _event(payload: str) -> bytes:
	return f"data: {payload}".encode()


class KimiImageDescriberTestCase(unittest.TestCase):
	def test_stream_reconstructs_thinking_and_text_response(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()
		request = module.RecognitionRequest()
		engine._resetStreamingState()

		chunks = [
			_event(
				'{"id":"completion-1","choices":[{"delta":{"role":"assistant",'
				'"reasoning_content":"reason","content":""},"finish_reason":null}]}',
			),
			_event('{"choices":[{"delta":{"content":"answer"},"finish_reason":null}]}'),
			_event('{"choices":[{"delta":{},"finish_reason":"stop"}]}'),
			_event("[DONE]"),
		]

		visibleText = "".join(filter(None, (engine.processStreamChunk(chunk, request) for chunk in chunks)))
		response = engine._getStreamingResponse(visibleText)

		self.assertEqual(visibleText, "answer")
		message = response["choices"][0]["message"]
		self.assertEqual(message["reasoning_content"], "reason")
		self.assertEqual(message["content"], "answer")
		self.assertEqual(response["choices"][0]["finish_reason"], "stop")

	def test_stream_requires_explicit_completion(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()
		engine._resetStreamingState()
		engine.processStreamChunk(
			_event(
				'{"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}',
			),
			module.RecognitionRequest(),
		)

		with self.assertRaises(module.StreamIncompleteError):
			engine._getStreamingResponse("partial")

	def test_stream_empty_response_raises_stream_error(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()
		engine._resetStreamingState()
		engine.processStreamChunk(
			_event('{"choices":[{"delta":{},"finish_reason":"stop"}]}'),
			module.RecognitionRequest(),
		)
		engine.processStreamChunk(_event("[DONE]"), module.RecognitionRequest())

		with self.assertRaises(module.StreamIncompleteError):
			engine._getStreamingResponse("")

	def test_non_stream_empty_response_returns_error(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()

		result = engine.processApiResult(
			b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":""}}]}',
		)

		self.assertEqual(result, "Server returned a successful but empty response.")

	def test_follow_up_reuses_complete_assistant_content(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		engine.model = "k3"
		engine.reasoningEffort = "high"
		initialContent = {
			"choices": [
				{
					"message": {
						"role": "assistant",
						"reasoning_content": "initial reasoning",
						"content": "initial answer",
					},
					"finish_reason": "stop",
				}
			]
		}
		turnContent = {
			"choices": [
				{
					"message": {
						"role": "assistant",
						"reasoning_content": "turn reasoning",
						"content": "turn answer",
					},
					"finish_reason": "stop",
				}
			]
		}
		context = SimpleNamespace(
			image=object(),
			initialText="initial answer",
			response=initialContent,
			turns=[
				SimpleNamespace(role="user", text="first question", response=None),
				SimpleNamespace(role="assistant", text="turn answer", response=turnContent),
			],
		)

		request = engine._buildQuestionRequestParams(context, "next question", stream=False)
		payload = request["json"]

		self.assertEqual(payload["messages"][1], initialContent["choices"][0]["message"])
		self.assertEqual(payload["messages"][3], turnContent["choices"][0]["message"])
		self.assertEqual(payload["reasoning_effort"], "high")
		self.assertNotIn("thinking", payload)
		self.assertEqual(request["timeout"], 180)

	def test_k27_rejects_text_only_follow_up_history(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()

		engine.model = "kimi-for-coding"
		with self.assertRaises(module.ApiError):
			engine._assistantMessage({"streamed_text": "answer"}, "answer")

	def test_k3_accepts_response_without_optional_reasoning_content(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()
		engine.model = "k3"

		message = engine._assistantMessage(
			{"choices": [{"message": {"role": "assistant", "content": "answer"}}]},
			"answer",
		)

		self.assertEqual(message, {"role": "assistant", "content": "answer"})

	def test_available_models_follow_official_base_url(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()

		baseUrlSetting = next(setting for setting in engine.supportedSettings if setting.name == "baseUrl")
		self.assertTrue(baseUrlSetting.refreshSettingsOnChange)
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

	def test_k26_uses_thinking_keep_all_for_follow_up(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		engine.baseUrl = "https://api.moonshot.ai/v1"
		engine.model = "kimi-k2.6"
		engine.reasoningEffort = "high"
		request = engine._buildRequestParams(b"encoded-image", module.RecognitionRequest(streamResult=False))

		self.assertEqual(request["json"]["thinking"], {"type": "enabled", "keep": "all"})
		self.assertNotIn("reasoning_effort", request["json"])

	def test_empty_custom_prompt_restores_default(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()

		engine.prompt = "custom prompt"
		engine.prompt = "  "

		self.assertEqual(engine.prompt, module.DEFAULT_KIMI_PROMPT)

	def test_context_windows_follow_official_model_limits(self) -> None:
		load_kimi_image_module()
		modelsModule = sys.modules["addon.globalPlugins.visAware.kimiModels"]

		for model in ("k3", "kimi-k3"):
			self.assertEqual(modelsModule.getKimiContextWindow(model), 1_048_576)
		for model in ("k3-256k", "kimi-for-coding", "kimi-k2.6", "custom-model"):
			self.assertEqual(modelsModule.getKimiContextWindow(model), 262_144)

	def test_follow_up_token_budget_keeps_newest_history_suffix(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		module.KIMI_MAX_COMPLETION_TOKENS = 100
		module.KIMI_CONTEXT_SAFETY_TOKENS = 0
		module.getKimiContextWindow = lambda model: 1_300

		def response(text: str, completionTokens: int, promptTokens: int | None = None) -> dict:
			usage = {"completion_tokens": completionTokens}
			if promptTokens is not None:
				usage["prompt_tokens"] = promptTokens
			return {
				"choices": [
					{
						"message": {
							"role": "assistant",
							"reasoning_content": "r",
							"content": text,
						},
						"finish_reason": "stop",
					}
				],
				"usage": usage,
			}

		initialResponse = response("initial", 100, promptTokens=100)
		turns = []
		for index in range(1, 4):
			turns.extend(
				[
					SimpleNamespace(role="user", text=f"q{index}", response=None),
					SimpleNamespace(
						role="assistant",
						text=f"a{index}",
						response=response(f"a{index}", 300),
					),
				],
			)
		context = SimpleNamespace(
			image=object(),
			initialText="initial",
			response=initialResponse,
			turns=turns,
		)

		messages = engine._buildQuestionRequestParams(context, "next", stream=False)["json"]["messages"]

		userTexts = [
			message["content"]
			for message in messages
			if message["role"] == "user" and isinstance(message["content"], str)
		]
		self.assertEqual(userTexts, ["q2", "q3", "next"])

	def test_follow_up_rejects_mandatory_context_over_model_limit(self) -> None:
		module = load_kimi_image_module()
		engine = module.CustomContentRecognizer()
		engine.apiKey = "secret"
		module.KIMI_MAX_COMPLETION_TOKENS = 100
		module.KIMI_CONTEXT_SAFETY_TOKENS = 0
		module.getKimiContextWindow = lambda model: 300
		context = SimpleNamespace(
			image=object(),
			initialText="initial",
			response={
				"choices": [
					{
						"message": {"role": "assistant", "content": "initial"},
						"finish_reason": "stop",
					}
				],
				"usage": {"prompt_tokens": 200, "completion_tokens": 100},
			},
			turns=[],
		)

		with self.assertRaises(module.ApiError):
			engine._buildQuestionRequestParams(context, "next", stream=False)


if __name__ == "__main__":
	unittest.main()
