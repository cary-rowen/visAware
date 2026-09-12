from __future__ import annotations

import builtins
from dataclasses import dataclass, field
import importlib.util
from pathlib import Path
from types import SimpleNamespace
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
			"debugWarning": staticmethod(lambda *args, **kwargs: None),
			"info": staticmethod(lambda *args, **kwargs: None),
			"warning": staticmethod(lambda *args, **kwargs: None),
		},
	)()
	sys.modules["logHandler"] = logHandlerModule

	uiModule = types.ModuleType("ui")
	uiModule.message = lambda *args, **kwargs: None
	sys.modules["ui"] = uiModule

	wxModule = types.ModuleType("wx")
	wxModule.Window = object
	wxModule.Button = object
	wxModule.CommandEvent = object
	wxModule.CallAfter = lambda func, *args, **kwargs: func(*args, **kwargs)
	sys.modules["wx"] = wxModule

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
	sys.modules[exceptionsModule.__name__] = exceptionsModule

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")

	class _Response:
		def __init__(self, payload: dict):
			self._payload = payload

		def json(self) -> dict:
			return self._payload

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
	recogHandlerModule._redactRequestParamsForLog = lambda value, keyName="": value
	sys.modules[recogHandlerModule.__name__] = recogHandlerModule

	engineGuiModule = types.ModuleType("addon.globalPlugins.visAware.engineGUIHelper")

	class _Setting:
		def __init__(self, name=None, *args, **kwargs):
			self.name = name or kwargs.get("name", "")
			for key, value in kwargs.items():
				setattr(self, key, value)

	engineGuiModule.ChoiceEngineSetting = _Setting
	engineGuiModule.TextInputEngineSetting = _Setting
	sys.modules[engineGuiModule.__name__] = engineGuiModule

	actionsModule = types.ModuleType("addon.globalPlugins.visAware.agent.actions")
	actionsModule.JPEG_QUALITY = 90

	@dataclass
	class AgentAction:
		name: str
		arguments: dict
		message: str = ""

	actionsModule.AgentAction = AgentAction
	actionsModule.Screenshot = _Screenshot
	actionsModule.formatScreenshotPromptContext = lambda screenshot: "Screenshot context."
	sys.modules[actionsModule.__name__] = actionsModule

	settingsModule = types.ModuleType("addon.globalPlugins.visAware.agent.settings")

	class BaseAgentEngine:
		configSectionName = "visAwareAgent"
		_imageQuality = 90

		def generateStringSettings(self, values):
			return values

		@staticmethod
		def _imageQualitySetting():
			return _Setting("imageQuality")

		@property
		def imageQuality(self):
			return self._imageQuality

	settingsModule.BaseAgentEngine = BaseAgentEngine
	sys.modules[settingsModule.__name__] = settingsModule

	_load_module(
		"addon.globalPlugins.visAware.agent.decision",
		"addon/globalPlugins/visAware/agent/decision.py",
	)

	_load_module(
		"addon.globalPlugins.visAware.deepseekModels",
		"addon/globalPlugins/visAware/deepseekModels.py",
	)
	module = _load_module(
		"addon.globalPlugins.visAware.agent.deepseek",
		"addon/globalPlugins/visAware/agent/deepseek.py",
	)
	return requests_seen


class DeepSeekAgentTestCase(unittest.TestCase):
	def test_next_action_builds_responses_tool_request(self) -> None:
		requests_seen = _install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.agent.deepseek"]
		client = module.DeepSeekAgentClient(
			SimpleNamespace(
				apiKey="secret",
				baseUrl="https://api.deepseek.com",
				model="deepseek-v4-flash-vision-exp",
				imageQuality=90,
				source="test",
			),
		)
		requests_seen.clear()
		responsePayload = {
			"id": "resp-1",
			"object": "response",
			"status": "completed",
			"output": [
				{
					"type": "function_call",
					"call_id": "call-1",
					"name": "agent_decision",
					"arguments": {
						"status": "action",
						"message": "click",
						"action": "click_at",
						"finished": False,
						"x": 500,
						"y": 500,
					},
				},
			],
		}

		def sendRequest(*args, **kwargs):
			record = dict(kwargs)
			if args:
				record["method"] = args[0]
			if len(args) > 1:
				record["url"] = args[1]
			requests_seen.append(record)
			return SimpleNamespace(json=lambda: responsePayload)

		module.network.sendRequest = sendRequest
		decision = client.nextAction("open settings", _Screenshot(), ["Clicked Settings."])

		self.assertEqual(decision.status, "action")
		self.assertEqual(decision.action.name, "click_at")
		self.assertEqual(decision.action.arguments["x"], 500)
		self.assertEqual(requests_seen[0]["url"], "https://api.deepseek.com/responses")
		self.assertEqual(requests_seen[0]["headers"]["Authorization"], "Bearer secret")
		self.assertEqual(requests_seen[0]["json"]["model"], "deepseek-v4-flash-vision-exp")
		self.assertEqual(
			requests_seen[0]["json"]["tool_choice"],
			{"type": "function", "name": "agent_decision"},
		)
		self.assertEqual(requests_seen[0]["json"]["reasoning"], {"effort": "none"})
		tool = requests_seen[0]["json"]["tools"][0]
		self.assertEqual(tool["type"], "function")
		self.assertEqual(tool["name"], "agent_decision")
		self.assertEqual(
			tool["description"],
			"Return the next Windows desktop action for the local agent host.",
		)
		self.assertEqual(tool["parameters"]["type"], "object")
		self.assertEqual(tool["parameters"]["required"], ["status", "message", "action", "finished"])
		self.assertIn("properties", tool["parameters"])
		self.assertNotIn("function", tool)
		self.assertEqual(requests_seen[0]["json"]["input"][0]["content"][0]["type"], "input_image")
		self.assertEqual(
			requests_seen[0]["json"]["input"][0]["content"][0]["image_url"],
			"data:image/jpeg;base64,image",
		)

	def test_next_action_requires_function_call(self) -> None:
		requests_seen = _install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.agent.deepseek"]
		client = module.DeepSeekAgentClient(
			SimpleNamespace(
				apiKey="secret",
				baseUrl="https://api.deepseek.com",
				model="deepseek-v4-flash-vision-exp",
				imageQuality=90,
				source="test",
			),
		)
		requests_seen.clear()
		responsePayload = {
			"id": "resp-1",
			"object": "response",
			"status": "completed",
			"output": [
				{
					"type": "message",
					"role": "assistant",
					"content": [{"type": "output_text", "text": "Done."}],
				},
			],
		}
		module.network.sendRequest = lambda **kwargs: SimpleNamespace(json=lambda: responsePayload)

		with self.assertRaises(module.ApiError):
			client.nextAction("open settings", _Screenshot(), [])

	def test_second_request_sends_previous_tool_result(self) -> None:
		requests_seen = _install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.agent.deepseek"]
		client = module.DeepSeekAgentClient(
			SimpleNamespace(
				apiKey="secret",
				baseUrl="https://api.deepseek.com",
				model="deepseek-v4-flash-vision-exp",
				imageQuality=90,
				source="test",
			),
		)
		responsePayloads = [
			{
				"id": "resp-1",
				"object": "response",
				"status": "completed",
				"output": [
					{
						"type": "function_call",
						"call_id": "call-1",
						"name": "agent_decision",
						"arguments": {
							"status": "action",
							"message": "wait",
							"action": "wait",
							"finished": False,
							"seconds": 1,
						},
					},
				],
			},
			{
				"id": "resp-2",
				"object": "response",
				"status": "completed",
				"output": [
					{
						"type": "function_call",
						"call_id": "call-2",
						"name": "agent_decision",
						"arguments": {
							"status": "finish",
							"message": "done",
							"action": "none",
							"finished": True,
						},
					},
				],
			},
		]

		def sendRequest(**kwargs):
			requests_seen.append(dict(kwargs))
			return SimpleNamespace(json=lambda: responsePayloads.pop(0))

		module.network.sendRequest = sendRequest
		client.nextAction("open settings", _Screenshot(), [])
		client.nextAction("open settings", _Screenshot(), ["Waited."])

		secondInput = requests_seen[1]["json"]["input"]
		self.assertEqual(secondInput[1]["type"], "function_call")
		self.assertEqual(secondInput[1]["call_id"], "call-1")
		self.assertEqual(secondInput[2]["type"], "function_call_output")
		self.assertEqual(secondInput[2]["call_id"], "call-1")
		self.assertEqual(secondInput[3]["role"], "user")

	def test_incomplete_response_raises_api_error(self) -> None:
		_install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.agent.deepseek"]
		client = module.DeepSeekAgentClient(
			SimpleNamespace(
				apiKey="secret",
				baseUrl="https://api.deepseek.com",
				model="deepseek-v4-flash-vision-exp",
				imageQuality=90,
				source="test",
			),
		)
		module.network.sendRequest = lambda **kwargs: SimpleNamespace(
			json=lambda: {
				"id": "resp-1",
				"object": "response",
				"status": "incomplete",
				"incomplete_details": {"reason": "max_output_tokens"},
				"output": [],
			},
		)

		with self.assertRaises(module.ApiError):
			client.nextAction("open settings", _Screenshot(), [])

	def test_redacts_image_url_before_logging(self) -> None:
		_install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.agent.deepseek"]
		redacted = module._redactForLog(
			{
				"json": {
					"input": [
						{
							"role": "user",
							"content": [
								{
									"type": "input_image",
									"image_url": "data:image/jpeg;base64,AAAA",
								},
							],
						},
					],
				},
			},
		)

		self.assertTrue(
			redacted["json"]["input"][0]["content"][0]["image_url"].startswith("<redacted data URL:"),
		)

	def test_agent_engine_uses_fixed_vision_model_choice(self) -> None:
		_install_stubs()
		module = sys.modules["addon.globalPlugins.visAware.agent.deepseek"]
		engine = module.AgentEngine()

		self.assertNotIn("fetchModels", [setting.name for setting in engine.supportedSettings])
		self.assertEqual(list(engine.availableModels), ["deepseek-flash"])
		engine.model = "deepseek-v4-pro"
		self.assertEqual(engine.model, "deepseek-flash")


if __name__ == "__main__":
	unittest.main()
