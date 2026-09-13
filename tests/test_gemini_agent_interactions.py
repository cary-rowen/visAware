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


def _install_module_stubs() -> None:
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
	networkModule.sendRequest = lambda **kwargs: None
	sys.modules["addon.globalPlugins.visAware.network"] = networkModule

	actionsModule = types.ModuleType("addon.globalPlugins.visAware.agent.actions")
	actionsModule.JPEG_QUALITY = 90
	actionsModule.Screenshot = _Screenshot
	actionsModule.formatScreenshotPromptContext = lambda screenshot: "Screenshot context."
	sys.modules["addon.globalPlugins.visAware.agent.actions"] = actionsModule

	decisionModule = types.ModuleType("addon.globalPlugins.visAware.agent.decision")
	decisionModule.AGENT_ACTION_SCHEMA = {
		"type": "object",
		"properties": {},
		"propertyOrdering": [],
	}
	decisionModule.AgentDecision = dict
	decisionModule.parseAgentDecision = lambda data, providerName: data
	sys.modules["addon.globalPlugins.visAware.agent.decision"] = decisionModule


def load_gemini_module():
	load_gemini_models_module()
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "agent" / "gemini.py"
	)
	spec = importlib.util.spec_from_file_location("addon.globalPlugins.visAware.agent.gemini", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load gemini.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


def load_gemini_models_module():
	_install_module_stubs()
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "geminiModels.py"
	)
	spec = importlib.util.spec_from_file_location("addon.globalPlugins.visAware.geminiModels", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load geminiModels.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class GeminiAgentInteractionsTestCase(unittest.TestCase):
	def test_current_model_presets(self) -> None:
		module = load_gemini_module()
		models = sys.modules["addon.globalPlugins.visAware.geminiModels"]
		expected = {
			"gemini-3.8-flash": ("low", "low", "high"),
			"gemini-3.7-flash": ("low", "low", "high"),
			"gemini-3.6-flash": ("medium", "medium", "high"),
			"gemini-3.5-flash-lite": ("minimal", "minimal", "high"),
			"gemini-3.5-flash": ("minimal", "minimal", "high"),
			"gemini-flash-latest": ("medium", "medium", "high"),
			"gemini-3.1-pro-preview": ("low", "low", "high"),
			"gemini-pro-latest": ("low", "low", "high"),
			"gemini-3-flash-preview": ("minimal", "minimal", "high"),
			"gemini-3.1-flash-lite": ("minimal", "minimal", "high"),
			"gemini-flash-lite-latest": ("minimal", "minimal", "high"),
			"gemini-2.5-flash-lite": (0, None, None),
		}

		self.assertEqual(models.DEFAULT_GEMINI_MODEL, "gemini-3.6-flash")
		self.assertEqual(list(models.getGeminiModelChoices()), list(expected))
		agentModels = models.getGeminiAgentModelChoices()
		self.assertEqual(
			list(agentModels.items()),
			[
				(model, label)
				for model, label in models.getGeminiModelChoices().items()
				if model not in {"gemini-3.1-pro-preview", "gemini-pro-latest"}
			],
		)
		self.assertIn(models.DEFAULT_GEMINI_MODEL, agentModels)
		expected["gemini-3-unknown"] = (None, None, None)
		for model, (thinking, interactionsLevel, resolution) in expected.items():
			with self.subTest(model=model):
				if isinstance(thinking, str):
					thinkingConfig = {"thinkingLevel": thinking}
				elif thinking == 0:
					thinkingConfig = {"thinkingBudget": 0}
				else:
					thinkingConfig = None
				self.assertEqual(models.getGeminiLowLatencyThinkingConfig(model), thinkingConfig)
				generationConfig = module._buildGenerationConfig(model)
				if interactionsLevel:
					self.assertEqual(generationConfig["thinking_level"], interactionsLevel)
				else:
					self.assertNotIn("thinking_level", generationConfig)
				client = module.GeminiAgentClient(
					module.GeminiAgentSettings(
						apiKey="key",
						model=model,
						mediaResolution="MEDIA_RESOLUTION_HIGH",
					),
				)
				image = client._buildInputSteps("read text", module.Screenshot(), [])[0]["content"][1]
				if resolution:
					self.assertEqual(image["resolution"], resolution)
				else:
					self.assertNotIn("resolution", image)

	def test_next_action_continues_interaction_with_function_result(self) -> None:
		module = load_gemini_module()
		payloads = []
		responses = [
			_FakeResponse(
				{
					"id": "interaction-1",
					"steps": [
						{
							"type": "function_call",
							"id": "call-1",
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
				},
			),
			_FakeResponse(
				{
					"id": "interaction-2",
					"steps": [
						{
							"type": "function_call",
							"id": "call-2",
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
			),
		]

		def sendRequest(**kwargs):
			self.assertEqual(kwargs["url"], "https://generativelanguage.googleapis.com/v1beta/interactions")
			self.assertEqual(kwargs["headers"]["Api-Revision"], "2026-05-20")
			payloads.append(kwargs["json"])
			return responses.pop(0)

		module.network.sendRequest = sendRequest
		client = module.GeminiAgentClient(module.GeminiAgentSettings(apiKey="key", model="gemini-3.8-flash"))
		screenshot = module.Screenshot()

		client.nextAction("open settings", screenshot, [])
		client.nextAction("open settings", screenshot, ["Clicked Settings."])

		firstPayload, secondPayload = payloads
		self.assertEqual(firstPayload["generation_config"]["thinking_level"], "low")
		self.assertEqual(secondPayload["generation_config"]["thinking_level"], "low")
		self.assertNotIn("max_output_tokens", firstPayload["generation_config"])
		self.assertNotIn("max_output_tokens", secondPayload["generation_config"])
		self.assertNotIn("temperature", firstPayload["generation_config"])
		self.assertNotIn("temperature", secondPayload["generation_config"])
		self.assertNotIn("previous_interaction_id", firstPayload)
		self.assertEqual(secondPayload["previous_interaction_id"], "interaction-1")
		self.assertEqual(secondPayload["input"][0]["type"], "function_result")
		self.assertEqual(secondPayload["input"][0]["call_id"], "call-1")
		self.assertEqual(secondPayload["input"][0]["name"], "agent_decision")
		self.assertEqual(secondPayload["input"][1]["type"], "user_input")


if __name__ == "__main__":
	unittest.main()
