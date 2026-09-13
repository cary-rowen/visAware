from __future__ import annotations

import importlib.util
import builtins
from dataclasses import dataclass
from pathlib import Path
import sys
from threading import Event, Thread
import types
import unittest
from unittest.mock import Mock, patch


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
	sys.modules["addon.globalPlugins.visAware.cues"] = types.SimpleNamespace(
		CueType=types.SimpleNamespace(
			ACTION="action",
			QUESTION="question",
			SUCCESS="success",
			ERROR="error",
			CANCEL="cancel",
		),
	)

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
	def setUp(self) -> None:
		self.cues = Mock()
		self.handles = []

		def start():
			handle = Event()
			self.handles.append(handle)
			return handle

		self.cues.start.side_effect = start
		self.cues.stop.side_effect = lambda handle: handle.set() if handle is not None else None

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

		session = module.AgentSession("goal", self.cues)

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
		self.assertTrue(self.handles[0].is_set())
		thread.join(1)
		self.assertIsInstance(result.get("error"), module.CancellationError)
		self.assertIsNotNone(session._requestThread)

		finishEvent.set()
		session._joinPendingRequestThread()
		self.assertIsNone(session._requestThread)

	def test_cues_end_on_request_error_or_thread_start_failure(self) -> None:
		module = load_session_module()
		session = module.AgentSession("goal", self.cues)
		client = Mock()
		client.nextAction.side_effect = RuntimeError("request failed")
		with self.assertRaises(RuntimeError):
			session._requestNextAction(client, object(), [])
		self.assertTrue(self.handles[-1].is_set())
		with patch.object(module, "Thread") as thread:
			thread.return_value.start.side_effect = RuntimeError("thread failed")
			with self.assertRaises(RuntimeError):
				session._requestNextAction(client, object(), [])
		self.assertTrue(self.handles[-1].is_set())
		self.assertIsNone(session._requestThread)

	def test_each_analysis_has_cues_but_actions_and_user_waits_do_not(self) -> None:
		module = load_session_module()
		window = types.SimpleNamespace(hwnd=1, appName="app", title="title")
		module.getForegroundWindowInfo = lambda: window
		screenshots = [types.SimpleNamespace(digest=str(index), window=window) for index in range(3)]
		module.captureScreen = lambda *_args, **_kwargs: screenshots.pop(0)
		decisions = [
			module.AgentDecision(status="ask_user", message="question"),
			module.AgentDecision(
				status="action",
				actions=[types.SimpleNamespace(name="click", message="click", arguments={})],
			),
			module.AgentDecision(status="finish"),
		]

		def nextAction(*_args):
			self.assertFalse(self.handles[-1].is_set())
			return decisions.pop(0)

		module.createAgentClient = lambda: types.SimpleNamespace(imageQuality=80, nextAction=nextAction)

		def askUser(_question, _cancelEvent):
			self.assertTrue(self.handles[-1].is_set())
			return "answer"

		session = module.AgentSession("goal", self.cues, askUser=askUser)
		session._message = Mock()
		session._getReboundWindow = lambda bound: bound
		session._executeAction = lambda *_args: self.assertTrue(self.handles[-1].is_set())
		session._sleepAfterAction = lambda: self.assertTrue(self.handles[-1].is_set())
		session._run()
		self.assertEqual(self.cues.start.call_count, 3)
		self.assertTrue(all(handle.is_set() for handle in self.handles))
		self.assertEqual(
			[call.args[0] for call in self.cues.play.call_args_list],
			["question", "action", "success"],
		)

	def test_agent_termination_uses_cancel_or_error_sound(self) -> None:
		module = load_session_module()
		for error, cue in (
			(module.CancellationError("cancelled"), "cancel"),
			(module.NetworkError("offline"), "error"),
			(RuntimeError("unexpected"), "error"),
		):
			with self.subTest(cue=cue, error=type(error)):
				self.cues.reset_mock()
				module.createAgentClient = Mock(side_effect=error)
				session = module.AgentSession("goal", self.cues)
				session._run()
				self.cues.play.assert_called_once_with(cue)


if __name__ == "__main__":
	unittest.main()
