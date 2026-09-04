from __future__ import annotations

import builtins
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest


class _ApiError(Exception):
	pass


@dataclass
class _AgentAction:
	name: str
	arguments: dict
	message: str = ""


def _install_stubs() -> None:
	builtins._ = lambda value: value

	addonHandlerModule = types.ModuleType("addonHandler")
	addonHandlerModule.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandlerModule

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
	exceptionsModule.ApiError = _ApiError
	sys.modules[exceptionsModule.__name__] = exceptionsModule

	actionsModule = types.ModuleType("addon.globalPlugins.visAware.agent.actions")
	actionsModule.AgentAction = _AgentAction
	sys.modules[actionsModule.__name__] = actionsModule


def load_decision_module():
	_install_stubs()
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "agent" / "decision.py"
	)
	spec = importlib.util.spec_from_file_location(
		"addon.globalPlugins.visAware.agent.decision",
		modulePath,
	)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load decision.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class AgentDecisionParsingTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.module = load_decision_module()

	def _parse(self, **updates):
		data = {
			"status": "action",
			"message": "test",
			"action": "click_at",
			"finished": False,
			"x": 500,
			"y": 500,
		}
		data.update(updates)
		return self.module.parseAgentDecision(data, "Test provider")

	def test_string_false_does_not_mark_action_finished(self) -> None:
		decision = self._parse(finished="false")

		self.assertFalse(decision.finishAfterAction)

	def test_string_true_marks_action_finished(self) -> None:
		decision = self._parse(finished=" TRUE ")

		self.assertTrue(decision.finishAfterAction)

	def test_string_boolean_action_arguments_are_normalized(self) -> None:
		decision = self._parse(
			action="type_text_at",
			text="captcha",
			press_enter="false",
			clear_before_typing="false",
		)

		self.assertIs(decision.action.arguments["press_enter"], False)
		self.assertIs(decision.action.arguments["clear_before_typing"], False)

	def test_trailing_newline_still_requests_enter(self) -> None:
		decision = self._parse(
			action="type_text_at",
			text="captcha\n",
			press_enter="false",
		)

		self.assertEqual(decision.action.arguments["text"], "captcha")
		self.assertIs(decision.action.arguments["press_enter"], True)

	def test_unknown_boolean_value_is_rejected(self) -> None:
		with self.assertRaises(_ApiError):
			self._parse(finished="sometimes")


if __name__ == "__main__":
	unittest.main()
