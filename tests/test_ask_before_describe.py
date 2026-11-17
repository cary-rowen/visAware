from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import sys
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_task_cues import loadMethods, PLUGIN_DIR


def loadModule(name, file, **dependencies):
	spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / file)
	module = importlib.util.module_from_spec(spec)
	with patch.dict(
		sys.modules,
		{
			name: module,
			"logHandler": SimpleNamespace(log=Mock()),
			"addonHandler": SimpleNamespace(initTranslation=lambda: None),
			**dependencies,
		},
	):
		spec.loader.exec_module(module)
	module._ = lambda value: value
	return module


@dataclass
class Result:
	text: str


class InitialQuestionTests(unittest.TestCase):
	def setUp(self):
		self.history = loadModule("initialQuestionHistory", "recogHistory.py")
		exceptions = loadModule("initialQuestion.exceptions", "exceptions.py")
		conversation = loadModule(
			"initialQuestion.conversation",
			"conversation.py",
			**{"initialQuestion.exceptions": exceptions},
		)
		self.ui = Mock()
		self.config = SimpleNamespace(
			conf={
				"visAwareGeneral": {
					"useBrowseableMessage": False,
					"copyToClipboard": True,
					"engineType": "ImageDescriber",
				},
			},
		)
		self.env = {
			"log": Mock(),
			"ui": self.ui,
			"api": Mock(),
			"config": self.config,
			"_": lambda value: value,
			"recogHistory": self.history,
			"RecognitionResult": Result,
			"SimpleTextResult": Result,
			"CancellationError": exceptions.CancellationError,
			"AuthenticationError": exceptions.AuthenticationError,
			"NetworkError": exceptions.NetworkError,
			"ApiError": exceptions.ApiError,
			"makeConversationContext": conversation.makeConversationContext,
			"isScreenCurtainCaptureSupported": lambda: True,
			"Event": Event,
			"Thread": Mock(),
			"wx": SimpleNamespace(CommandEvent=type("CommandEvent", (), {})),
			"ANSWER_SENDER": "Answer",
			"ROLE_ASSISTANT": "assistant",
			"gui": SimpleNamespace(mainFrame=Mock()),
			"getConfigChoiceValue": lambda conf, key, choices: conf[key],
			"ENGINE_TYPES": [],
			"getValidSourceType": lambda conf: "clipboardImage",
		}
		self.runtime = self.loadClass("recognitionRuntime.py", "RecognitionRuntime")(
			Mock(),
			Mock(isActive=False),
			None,
			None,
			Mock(return_value=False),
		)
		self.engine = SimpleNamespace(
			name="test",
			description="Test",
			prompt="default",
			supportsQuestions=True,
			_recognitionThread=None,
			cancel=Mock(),
			recognize=Mock(),
			isSupported=lambda name: name == "prompt",
			extractText=lambda response: response["answer"],
		)
		self.runtime.getCurrentEngine = lambda kind: self.engine
		self.imageData = (object(), Mock())
		self.runtime.getImageFromSource = Mock(return_value=self.imageData)
		self.runtime.showHtmlRecognitionResult = Mock(return_value=False)
		self.runtime.showRecognitionResultDocument = Mock()
		frameClass = self.loadClass("askDialog.py", "AskQuestionFrame")
		self.frame = frameClass.__new__(frameClass)
		self.frame._context = None
		self.frame._onPendingDescribeSend = None
		self.frame._cancelPendingDescribe = None
		self.frame._taskCues = Mock()
		self.frame._cueHandle = None
		self.frame._cancellationEvent = None
		self.frame._activeRequestSequence = None
		self.frame._requestSequence = 0
		self.frame._streamingAnswerRequestSequence = None
		self.frame._formattedContent = ""
		self.frame._questionText = Mock()
		self.frame._messagesText = Mock()
		self.frame._sendButton = Mock()
		self.frame._streamingSpeechPresenter = Mock(isActive=False)
		self.frame._appendMessage = Mock()
		self.frame.Layout = Mock()
		self.frame.Hide = Mock()
		self.frame.Show = Mock()
		self.frame.Raise = Mock()
		self.frame.IsShown = Mock(return_value=False)
		self.plugin = self.loadClass(
			"__init__.py",
			"GlobalPlugin",
			{
				"script_askQuestionBeforeDescribe",
				"_promptBeforeDescribe",
				"script_askQuestion",
			},
		)()
		self.plugin._askQuestionContext = None
		self.plugin._askQuestionHistoryEntry = None
		self.plugin._askQuestionFrame = self.frame
		self.plugin._recognitionRuntime = self.runtime
		self.plugin.descHandler = SimpleNamespace(getCurrentEngine=lambda: self.engine)
		self.plugin._isScreenCurtainRunning = lambda: False

	def loadClass(self, file, name, methods=None):
		return loadMethods(file, name, methods, dict(self.env))

	def send(self, question="Read the price"):
		self.frame._questionText.GetValue.return_value = question
		self.frame._onSend(object())

	def finish(self, text="Ten dollars"):
		result = Result(text)
		self.history.attachEntry(result, self.history.createEntry(self.engine, Mock(), {"answer": text}))
		self.engine.recognize.call_args.args[2](result)
		return result

	def test_capture_before_show_and_question_survives_cancelling_same_engine(self):
		order = []
		self.runtime.getImageFromSource.side_effect = lambda *args: order.append("capture") or self.imageData
		self.frame.Show.side_effect = lambda: order.append("show")
		self.runtime._activeEngine = self.engine
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.send()
		self.assertEqual(order, ["capture", "show"])
		self.engine.cancel.assert_called_once()
		self.assertEqual(self.engine.recognize.call_args.kwargs, {"prompt": "Read the price"})
		self.assertEqual(self.engine.prompt, "default")
		self.runtime.getImageFromSource.assert_called_once()
		self.send("duplicate")
		self.engine.recognize.assert_called_once()

	def test_repeated_command_reuses_open_window_without_capturing_it(self):
		self.plugin.script_askQuestionBeforeDescribe(None)
		onSend = self.frame._onPendingDescribeSend
		self.frame.IsShown.return_value = True
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.assertIs(self.frame._onPendingDescribeSend, onSend)
		self.send()
		self.finish()
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.runtime.getImageFromSource.assert_called_once()
		self.frame.Hide.assert_not_called()
		self.assertEqual(self.frame.Raise.call_count, 3)

	def test_close_then_ordinary_recognition_ignores_late_result(self):
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.send()
		oldCallback = self.engine.recognize.call_args.args[2]
		self.frame._onClose(object())
		self.assertIsNone(self.runtime._onFinished)
		self.assertIsNone(self.frame._onPendingDescribeSend)
		self.runtime.execute(None, "clipboardImage", "ImageDescriber", False)
		oldCallback(Result("late"))
		self.assertIsNone(self.history.getPreviousResult())
		self.assertIs(self.runtime._activeEngine, self.engine)
		self.finish("ordinary result")
		self.ui.message.assert_called_with("ordinary result")
		self.assertEqual(self.engine.recognize.call_args.kwargs, {})

	def test_old_window_cancellation_cannot_cancel_replacement_task(self):
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.send()
		oldCancel = self.frame._cancelPendingDescribe
		self.runtime.execute(None, "clipboardImage", "ImageDescriber", False)
		oldCancel()
		self.assertIs(self.runtime._activeEngine, self.engine)
		self.assertIsNone(self.frame._activeRequestSequence)

	def test_success_reuses_history_and_reopening_preserves_conversation(self):
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.send()
		self.finish()
		context = self.frame._context
		self.assertIs(context, self.plugin._askQuestionContext)
		self.assertIs(self.plugin._askQuestionHistoryEntry, self.history.getPreviousResult())
		self.assertIsNot(context.engine, self.engine)
		self.assertEqual(context.engine.prompt, "Read the price")
		self.assertEqual(context.initialText, "Ten dollars")
		self.assertEqual(context.turns, [])  # The initial answer is not also appended as a follow-up.
		self.send("Currency?")
		self.env["Thread"].return_value.start.assert_called_once()
		self.assertIs(self.env["Thread"].call_args.kwargs["args"][1], context)
		self.frame._onAskSucceeded(self.frame._activeRequestSequence, "Currency?", "USD")
		self.frame._onClose(object())
		self.plugin.script_askQuestion(None)
		self.assertIs(self.frame._context, context)
		self.assertEqual(len(context.turns), 2)
		self.runtime.showHtmlRecognitionResult.assert_not_called()
		self.runtime.showRecognitionResultDocument.assert_not_called()
		self.env["api"].copyToClip.assert_called_once_with("Ten dollars", notify=True)

	def test_abandoned_new_question_preserves_previous_conversation(self):
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.send("Read the price")
		self.finish()
		context = self.frame._context
		context.addExchange("Currency?", "USD")
		self.frame._onClose(object())
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.frame._onClose(object())
		self.frame._appendMessage.reset_mock()
		self.plugin.script_askQuestion(None)
		self.assertIs(self.frame._context, context)
		self.assertEqual(len(context.turns), 2)
		self.frame._appendMessage.assert_any_call("You", "Read the price", report=False)
		self.assertEqual(self.frame._context.engine.prompt, "Read the price")
		self.assertEqual(self.frame._context.initialText, "Ten dollars")
		self.assertEqual(self.engine.prompt, "default")

	def test_history_rebuild_restores_initial_prompt_without_changing_engine(self):
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.send("Read the price")
		self.finish()
		context = self.env["makeConversationContext"](self.history.getPreviousResult())
		self.assertEqual(context.engine.prompt, "Read the price")
		self.assertEqual(self.engine.prompt, "default")

	def test_errors_cancellation_and_start_failure_allow_retry(self):
		for outcome in ("network", "unknown", "cancel", "start"):
			with self.subTest(outcome=outcome):
				self.plugin.script_askQuestionBeforeDescribe(None)
				if outcome == "start":
					self.engine.recognize.side_effect = RuntimeError("start failed")
				self.send()
				if outcome == "cancel":
					self.runtime.cancel()
				elif outcome in ("network", "unknown"):
					result = self.env["NetworkError"]("offline") if outcome == "network" else object()
					self.engine.recognize.call_args.args[2](result)
				self.assertIsNone(self.frame._activeRequestSequence)
				self.assertIsNone(self.runtime._onFinished)
				self.frame._sendButton.Enable.assert_called_with(True)
				self.engine.recognize.side_effect = None
				self.send("retry")
				self.finish()

	def test_result_without_history_does_not_reuse_an_older_image(self):
		self.history.addEntry(self.history.createEntry(self.engine, Mock(), {"answer": "old"}))
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.send()
		self.engine.recognize.call_args.args[2](Result("new, without history"))
		self.assertIsNone(self.frame._context)
		self.assertIsNone(self.plugin._askQuestionContext)

	def test_early_runtime_exits_release_the_completion_callback(self):
		for outcome in ("terminated", "noEngine", "curtain", "noImage"):
			with self.subTest(outcome=outcome):
				callback = Mock()
				self.runtime._terminated = outcome == "terminated"
				self.runtime.getCurrentEngine = lambda kind: None if outcome == "noEngine" else self.engine
				self.runtime.isScreenCurtainRunning = lambda: outcome == "curtain"
				self.runtime.execute.__globals__["isScreenCurtainCaptureSupported"] = lambda: False
				self.runtime.getImageFromSource.return_value = None
				sequence = self.runtime.execute(
					None,
					"wholeDesktop",
					"ImageDescriber",
					False,
					onFinished=callback,
				)
				callback.assert_called_once_with(None)
				self.assertIsNone(sequence)
				self.assertIsNone(self.runtime._onFinished)

	def test_prompt_only_engine_can_show_answer_but_cannot_follow_up(self):
		self.engine.supportsQuestions = False
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.send()
		self.finish()
		self.frame._sendButton.Enable.assert_called_with(False)
		self.send("another question")
		self.env["Thread"].assert_not_called()

	def test_capability_and_engine_change_guards_do_not_start_requests(self):
		self.config.conf["visAwareGeneral"]["engineType"] = "OCR"
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.config.conf["visAwareGeneral"]["engineType"] = "ImageDescriber"
		self.engine.isSupported = lambda name: False
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.runtime.getImageFromSource.assert_not_called()
		self.engine.isSupported = lambda name: True
		self.plugin.script_askQuestionBeforeDescribe(None)
		self.plugin.descHandler.getCurrentEngine = lambda: object()
		self.send()
		self.engine.recognize.assert_not_called()
		self.assertIsNone(self.frame._activeRequestSequence)


if __name__ == "__main__":
	unittest.main()
