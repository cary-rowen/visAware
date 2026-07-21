from __future__ import annotations

import importlib.util
import builtins
from dataclasses import dataclass
from pathlib import Path
import sys
from threading import Event, Thread
import types
import unittest


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
			"debugWarning": staticmethod(lambda *args, **kwargs: None),
			"error": staticmethod(lambda *args, **kwargs: None),
			"info": staticmethod(lambda *args, **kwargs: None),
			"warning": staticmethod(lambda *args, **kwargs: None),
		},
	)()
	sys.modules["logHandler"] = logHandlerModule

	uiModule = types.ModuleType("ui")
	uiModule.message = lambda *args, **kwargs: None
	sys.modules["ui"] = uiModule

	wxModule = types.ModuleType("wx")
	wxModule.CallAfter = lambda func, *args, **kwargs: func(*args, **kwargs)
	sys.modules["wx"] = wxModule

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

	class CancellationError(Exception):
		def __init__(self, message: str, event: Event | None = None):
			super().__init__(message)
			self.event = event

	class ApiError(Exception):
		pass

	exceptionsModule.ApiError = ApiError
	exceptionsModule.AuthenticationError = type("AuthenticationError", (ApiError,), {})
	exceptionsModule.CancellationError = CancellationError
	exceptionsModule.NetworkError = type("NetworkError", (Exception,), {})
	sys.modules["addon.globalPlugins.visAware.exceptions"] = exceptionsModule

	actionsModule = types.ModuleType("addon.globalPlugins.visAware.agent.actions")
	actionsModule.ActionExecutionError = type("ActionExecutionError", (Exception,), {})
	actionsModule.AgentAction = type("AgentAction", (), {})
	actionsModule.NORMALIZED_SCALE = 1000
	actionsModule.captureScreen = lambda *args, **kwargs: None
	actionsModule.executeAction = lambda *args, **kwargs: None
	actionsModule.getActiveAgentWindow = lambda: None
	actionsModule.getForegroundWindowInfo = lambda: None
	actionsModule.releaseHeldInputs = lambda: None
	sys.modules["addon.globalPlugins.visAware.agent.actions"] = actionsModule

	clientModule = types.ModuleType("addon.globalPlugins.visAware.agent.client")
	clientModule.createAgentClient = lambda: None
	sys.modules["addon.globalPlugins.visAware.agent.client"] = clientModule

	decisionModule = types.ModuleType("addon.globalPlugins.visAware.agent.decision")

	@dataclass
	class AgentDecision:
		status: str
		message: str = ""
		action: object | None = None
		actions: list | None = None
		finishAfterAction: bool = False

	decisionModule.AgentDecision = AgentDecision
	sys.modules["addon.globalPlugins.visAware.agent.decision"] = decisionModule


def load_session_module():
	_install_module_stubs()
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "agent" / "session.py"
	)
	spec = importlib.util.spec_from_file_location("addon.globalPlugins.visAware.agent.session", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load session.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class AgentSessionRequestThreadTestCase(unittest.TestCase):
	def test_cancelled_request_remains_tracked_until_joined(self) -> None:
		module = load_session_module()
		startedEvent = Event()
		finishEvent = Event()
		result = {}

		class Client:
			def nextAction(self, goal, screenshot, history):
				startedEvent.set()
				finishEvent.wait(5)
				return module.AgentDecision(status="finish")

		session = module.AgentSession("goal")

		def requestAction() -> None:
			try:
				session._requestNextAction(Client(), object(), [])
			except Exception as e:
				result["error"] = e

		thread = Thread(target=requestAction)
		thread.start()
		self.assertTrue(startedEvent.wait(1))
		self.assertIsNotNone(session._requestThread)
		self.assertTrue(session._requestThread.is_alive())

		session.cancel()
		thread.join(1)
		self.assertIsInstance(result.get("error"), module.CancellationError)
		self.assertIsNotNone(session._requestThread)

		finishEvent.set()
		session._joinPendingRequestThread()
		self.assertIsNone(session._requestThread)


if __name__ == "__main__":
	unittest.main()
