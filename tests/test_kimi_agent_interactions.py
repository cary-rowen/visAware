from __future__ import annotations

import builtins
from dataclasses import dataclass, field
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


class _FakeResponse:
	def __init__(self, payload: dict):
		self._payload = payload
		self.content = json.dumps(payload).encode("utf-8")

	def json(self) -> dict:
		return self._payload


@dataclass
class _BoundWindow:
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


def load_kimi_agent_module():
	builtins._ = lambda value: value
	addonHandlerModule = types.ModuleType("addonHandler")
	addonHandlerModule.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandlerModule
	configModule = types.ModuleType("config")
	configModule.conf = {}
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
		"addon.globalPlugins.visAware.agent",
	):
		module = types.ModuleType(moduleName)
		module.__path__ = []  # type: ignore[attr-defined]
		sys.modules[moduleName] = module

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")
	exceptionsModule.ApiError = type("ApiError", (Exception,), {})
	exceptionsModule.AuthenticationError = type("AuthenticationError", (Exception,), {})
	exceptionsModule.StreamIncompleteError = type("StreamIncompleteError", (exceptionsModule.ApiError,), {})
	sys.modules[exceptionsModule.__name__] = exceptionsModule

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")
	networkModule.sendRequest = lambda **kwargs: None
	sys.modules[networkModule.__name__] = networkModule

	actionsModule = types.ModuleType("addon.globalPlugins.visAware.agent.actions")
	actionsModule.JPEG_QUALITY = 90
	actionsModule.Screenshot = _Screenshot
	actionsModule.formatScreenshotPromptContext = lambda screenshot: "Screenshot context."
	sys.modules[actionsModule.__name__] = actionsModule

	decisionModule = types.ModuleType("addon.globalPlugins.visAware.agent.decision")
	decisionModule.AGENT_ACTION_SCHEMA = {
		"type": "object",
		"properties": {},
		"propertyOrdering": [],
	}
	decisionModule.AgentDecision = dict
	decisionModule.parseAgentDecision = lambda data, providerName: data
	sys.modules[decisionModule.__name__] = decisionModule

	_load_module(
		"addon.globalPlugins.visAware.kimiModels",
		"addon/globalPlugins/visAware/kimiModels.py",
	)
	_load_module(
		"addon.globalPlugins.visAware.kimiAPI",
		"addon/globalPlugins/visAware/kimiAPI.py",
	)
	return _load_module(
		"addon.globalPlugins.visAware.agent.kimi",
		"addon/globalPlugins/visAware/agent/kimi.py",
	)


def _toolResponse(callId: str, reasoning: str = "reasoning") -> _FakeResponse:
	return _FakeResponse(
		{
			"choices": [
				{
					"finish_reason": "tool_calls",
					"message": {
						"role": "assistant",
						"content": None,
						"reasoning_content": reasoning,
						"tool_calls": [
							{
								"id": callId,
								"type": "function",
								"function": {
									"name": "agent_decision",
									"arguments": (
										'{"status":"action","message":"click","action":"click_at",'
										'"finished":false,"x":500,"y":500}'
									),
								},
							},
						],
					},
				},
			],
			"usage": {"prompt_tokens": 10, "completion_tokens": 20},
		},
	)


def _streamToolResponse(callId: str) -> list[bytes]:
	return [
		(
			'data: {"id":"stream-1","choices":[{"delta":{"role":"assistant",'
			f'"reasoning_content":"reason","tool_calls":[{{"index":0,"id":"{callId}",'
			'"type":"function","function":{"name":"agent_decision","arguments":"{\\"status\\":\\"action\\",\\"message\\":\\"click\\",\\"action\\":\\"click_at\\",\\"finished\\":false,"}}]},'
			'"finish_reason":null}]}'
		).encode(),
		(
			'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"x\\":500,\\"y\\":500}"}}]},"finish_reason":null}]}'
		).encode(),
		b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
		b"data: [DONE]",
	]


class KimiAgentInteractionsTestCase(unittest.TestCase):
	def test_kimi_url_builders_accept_base_and_full_endpoint_urls(self) -> None:
		load_kimi_agent_module()
		modelsModule = sys.modules["addon.globalPlugins.visAware.kimiModels"]

		for baseUrl, expectedUrl in (
			("https://api.kimi.com/coding/v1", "https://api.kimi.com/coding/v1/chat/completions"),
			("https://api.moonshot.ai/v1", "https://api.moonshot.ai/v1/chat/completions"),
			(
				"https://api.moonshot.ai/v1/chat/completions",
				"https://api.moonshot.ai/v1/chat/completions",
			),
		):
			with self.subTest(baseUrl=baseUrl):
				self.assertEqual(modelsModule.buildKimiChatCompletionsUrl(baseUrl), expectedUrl)
		self.assertEqual(
			modelsModule.buildKimiModelsUrl("https://api.kimi.com/coding/v1/chat/completions"),
			"https://api.kimi.com/coding/v1/models",
		)
		self.assertEqual(
			list(modelsModule.getKimiModelChoices("https://api.kimi.com/coding/v1")),
			["k3-256k", "k3", "kimi-for-coding", "kimi-for-coding-highspeed"],
		)
		self.assertEqual(
			list(modelsModule.getKimiModelChoices("https://api.moonshot.ai/v1")),
			["kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6"],
		)
		self.assertEqual(modelsModule.getDefaultKimiModel("https://api.moonshot.ai/v1"), "kimi-k3")

	def test_official_model_list_only_returns_supported_models(self) -> None:
		module = load_kimi_agent_module()
		module.network.sendRequest = lambda *args, **kwargs: _FakeResponse(
			{"data": [{"id": "text-only"}, {"id": "k3"}]},
		)
		client = module.KimiAgentClient(module.KimiAgentSettings(apiKey="secret"))

		self.assertEqual(client.listModels(), ["k3"])
		client.baseUrl = "https://proxy.example/v1"
		self.assertEqual(client.listModels(), ["k3", "text-only"])

	def test_k3_uses_chat_completions_and_preserves_tool_reasoning(self) -> None:
		module = load_kimi_agent_module()
		requests = []
		responses = [_toolResponse("call-1"), _toolResponse("call-2", "next reasoning")]

		def sendRequest(**kwargs):
			requests.append(kwargs)
			return responses.pop(0)

		module.network.sendRequest = sendRequest
		client = module.KimiAgentClient(
			module.KimiAgentSettings(apiKey="secret", model="k3", reasoningEffort="high"),
		)
		screenshot = module.Screenshot()
		client.nextAction("open settings", screenshot, [])
		client.nextAction("open settings", screenshot, ["Clicked Settings."])

		firstRequest, secondRequest = requests
		self.assertEqual(firstRequest["url"], "https://api.kimi.com/coding/v1/chat/completions")
		self.assertEqual(firstRequest["headers"]["Authorization"], "Bearer secret")
		self.assertNotIn("x-api-key", firstRequest["headers"])
		firstPayload = firstRequest["json"]
		self.assertEqual(firstPayload["reasoning_effort"], "high")
		self.assertEqual(firstPayload["tool_choice"], "required")
		self.assertEqual(firstPayload["tools"][0]["type"], "function")
		self.assertEqual(firstPayload["tools"][0]["function"]["name"], "agent_decision")
		self.assertEqual(firstPayload["messages"][1]["content"][0]["type"], "image_url")

		secondMessages = secondRequest["json"]["messages"]
		self.assertEqual(
			[message["role"] for message in secondMessages], ["system", "user", "assistant", "tool", "user"]
		)
		self.assertEqual(secondMessages[2]["reasoning_content"], "reasoning")
		self.assertEqual(secondMessages[3]["tool_call_id"], "call-1")
		imageCount = sum(
			1
			for message in secondMessages
			for part in message.get("content") or []
			if isinstance(part, dict) and part.get("type") == "image_url"
		)
		self.assertEqual(imageCount, 1)

	def test_k27_omits_unsupported_thinking_and_tool_choice(self) -> None:
		module = load_kimi_agent_module()
		requests = []
		module.network.sendStreamingRequest = lambda **kwargs: (
			requests.append(kwargs) or iter(_streamToolResponse("call-1"))
		)
		client = module.KimiAgentClient(
			module.KimiAgentSettings(apiKey="secret", model="kimi-for-coding"),
		)

		client.nextAction("open settings", module.Screenshot(), [])

		payload = requests[0]["json"]
		self.assertNotIn("reasoning_effort", payload)
		self.assertNotIn("thinking", payload)
		self.assertNotIn("tool_choice", payload)
		self.assertTrue(payload["stream"])

	def test_k3_accepts_tool_call_without_optional_reasoning_content(self) -> None:
		module = load_kimi_agent_module()
		response = _toolResponse("call-1")
		del response._payload["choices"][0]["message"]["reasoning_content"]
		module.network.sendRequest = lambda **kwargs: response
		client = module.KimiAgentClient(module.KimiAgentSettings(apiKey="secret", model="k3"))

		decision = client.nextAction("open settings", module.Screenshot(), [])

		self.assertEqual(decision["status"], "action")

	def test_missing_finish_reason_is_rejected(self) -> None:
		module = load_kimi_agent_module()
		module.network.sendRequest = lambda **kwargs: _FakeResponse(
			{
				"choices": [
					{
						"message": {
							"role": "assistant",
							"tool_calls": [],
						},
					}
				]
			}
		)
		client = module.KimiAgentClient(module.KimiAgentSettings(apiKey="secret", model="k3"))

		with self.assertRaises(module.ApiError):
			client.nextAction("open settings", module.Screenshot(), [])

	def test_length_finish_reason_is_an_error(self) -> None:
		module = load_kimi_agent_module()
		module.network.sendRequest = lambda **kwargs: _FakeResponse(
			{"choices": [{"finish_reason": "length", "message": {"role": "assistant"}}]},
		)
		client = module.KimiAgentClient(module.KimiAgentSettings(apiKey="secret", model="k3"))

		with self.assertRaises(module.ApiError):
			client.nextAction("open settings", module.Screenshot(), [])

	def test_stream_rejects_duplicate_done_marker(self) -> None:
		module = load_kimi_agent_module()
		state = module.KimiStreamState()
		state.consume(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}')
		state.consume(b"data: [DONE]")
		state.consume(b"data: [DONE]")

		with self.assertRaises(module.ApiError):
			state.buildResponse()

	def test_stream_rejects_tool_call_without_complete_arguments(self) -> None:
		module = load_kimi_agent_module()
		state = module.KimiStreamState()
		state.consume(
			b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
			b'"type":"function","function":{"name":"agent_decision"}}]},'
			b'"finish_reason":null}]}'
		)
		state.consume(b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}')
		state.consume(b"data: [DONE]")

		with self.assertRaises(module.ApiError):
			state.buildResponse()

	def test_stream_rejects_conflicting_tool_call_identity(self) -> None:
		module = load_kimi_agent_module()
		state = module.KimiStreamState()
		state.consume(
			b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
			b'"type":"function","function":{"name":"agent_decision","arguments":"{}"}}]},'
			b'"finish_reason":null}]}',
		)
		state.consume(
			b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-2",'
			b'"type":"function","function":{"name":"agent_decision"}}]},"finish_reason":null}]}',
		)
		state.consume(b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}')
		state.consume(b"data: [DONE]")

		with self.assertRaises(module.ApiError):
			state.buildResponse()

	def test_history_trimming_keeps_complete_user_tool_turns(self) -> None:
		module = load_kimi_agent_module()
		requests = []
		responses = [_toolResponse(f"call-{index}") for index in range(1, 8)]

		def sendRequest(**kwargs):
			requests.append(kwargs)
			return responses.pop(0)

		module.network.sendRequest = sendRequest
		client = module.KimiAgentClient(module.KimiAgentSettings(apiKey="secret", model="k3"))
		for index in range(7):
			client.nextAction("open settings", module.Screenshot(), [f"step {index}"])

		roles = [message["role"] for message in requests[-1]["json"]["messages"]]
		self.assertEqual(roles[0], "system")
		self.assertEqual(
			roles[1:],
			["user", "assistant", "tool", "user", "assistant", "tool", "user", "assistant", "tool", "user"],
		)


if __name__ == "__main__":
	unittest.main()
