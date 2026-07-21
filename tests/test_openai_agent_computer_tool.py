from __future__ import annotations

import importlib.util
import builtins
from dataclasses import dataclass, field
from pathlib import Path
import sys
import types
import unittest


class _FakeResponse:
	def __init__(self, payload: dict):
		self._payload = payload

	def json(self) -> dict:
		return self._payload


@dataclass
class _BoundWindow:
	appName: str = "test"
	title: str = "Test"


@dataclass
class _Screenshot:
	imageBase64: str = "image"
	mimeType: str = "image/jpeg"
	width: int = 800
	height: int = 600
	screenWidth: int = 800
	screenHeight: int = 600
	window: _BoundWindow = field(default_factory=_BoundWindow)


@dataclass
class _AgentAction:
	name: str
	arguments: dict
	message: str = ""


@dataclass
class _AgentDecision:
	status: str
	message: str
	action: _AgentAction | None = None
	actions: list[_AgentAction] | None = None
	finishAfterAction: bool = False


def _install_module_stubs() -> None:
	builtins._ = lambda value: value

	addonHandlerModule = types.ModuleType("addonHandler")
	addonHandlerModule.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandlerModule

	logHandlerModule = types.ModuleType("logHandler")
	logHandlerModule.log = type(
		"Log",
		(),
		{
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
		module = sys.modules.get(moduleName)
		if module is None:
			module = types.ModuleType(moduleName)
			module.__path__ = []  # type: ignore[attr-defined]
			sys.modules[moduleName] = module

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")
	exceptionsModule.ApiError = type("ApiError", (Exception,), {})
	exceptionsModule.AuthenticationError = type("AuthenticationError", (Exception,), {})
	sys.modules["addon.globalPlugins.visAware.exceptions"] = exceptionsModule

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")
	networkModule.sendRequest = lambda *args, **kwargs: None
	sys.modules["addon.globalPlugins.visAware.network"] = networkModule

	actionsModule = types.ModuleType("addon.globalPlugins.visAware.agent.actions")
	actionsModule.AgentAction = _AgentAction
	actionsModule.JPEG_QUALITY = 90
	actionsModule.NORMALIZED_SCALE = 1000
	actionsModule.Screenshot = _Screenshot
	actionsModule.formatScreenshotPromptContext = lambda screenshot: "Screenshot context."
	sys.modules["addon.globalPlugins.visAware.agent.actions"] = actionsModule

	decisionModule = types.ModuleType("addon.globalPlugins.visAware.agent.decision")
	decisionModule.AgentDecision = _AgentDecision
	sys.modules["addon.globalPlugins.visAware.agent.decision"] = decisionModule


def load_openai_module():
	_install_module_stubs()
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "agent" / "openai.py"
	)
	spec = importlib.util.spec_from_file_location("addon.globalPlugins.visAware.agent.openai", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load openai.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class OpenAIAgentComputerToolTestCase(unittest.TestCase):
	def test_screenshot_first_turn_continues_with_computer_call_output(self) -> None:
		module = load_openai_module()
		payloads = []
		responses = [
			_FakeResponse(
				{
					"id": "response-1",
					"output": [
						{
							"type": "computer_call",
							"call_id": "call-1",
							"actions": [{"type": "screenshot"}],
						},
					],
				},
			),
			_FakeResponse(
				{
					"id": "response-2",
					"output": [
						{
							"type": "computer_call",
							"call_id": "call-2",
							"actions": [{"type": "click", "x": 400, "y": 300, "button": "left"}],
						},
					],
				},
			),
			_FakeResponse(
				{
					"id": "response-3",
					"output_text": "done",
					"output": [],
				},
			),
		]

		def sendRequest(_method, _url, **kwargs):
			payloads.append(kwargs["json"])
			return responses.pop(0)

		module.network.sendRequest = sendRequest
		client = module.OpenAIAgentClient(module.OpenAIAgentSettings(apiKey="key", model="gpt-test"))
		decision = client.nextAction("click the button", module.Screenshot(), [])

		firstPayload, secondPayload = payloads
		self.assertEqual(firstPayload["tools"][0], {"type": "computer"})
		self.assertNotIn("previous_response_id", firstPayload)
		self.assertEqual(secondPayload["previous_response_id"], "response-1")
		self.assertEqual(secondPayload["input"][0]["type"], "computer_call_output")
		self.assertEqual(secondPayload["input"][0]["call_id"], "call-1")
		self.assertEqual(secondPayload["input"][0]["output"]["type"], "computer_screenshot")
		self.assertEqual(secondPayload["input"][0]["output"]["detail"], "original")
		self.assertEqual(decision.status, "action")
		self.assertEqual(decision.action.name, "click_at")
		self.assertEqual(decision.action.arguments, {"x": 500, "y": 500})
		self.assertEqual(client._pendingComputerCallId, "call-2")

		finishDecision = client.nextAction("click the button", module.Screenshot(), ["Clicked."])
		thirdPayload = payloads[2]
		self.assertEqual(thirdPayload["previous_response_id"], "response-2")
		self.assertEqual(thirdPayload["input"][0]["type"], "computer_call_output")
		self.assertEqual(thirdPayload["input"][0]["call_id"], "call-2")
		self.assertEqual(finishDecision.status, "finish")
		self.assertEqual(finishDecision.message, "done")


if __name__ == "__main__":
	unittest.main()
