from __future__ import annotations

import importlib.util
import builtins
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import requests

from test_network import load_network_module


class _FakeResponse:
	def __init__(self, payload: dict):
		self.content = json.dumps(payload).encode("utf-8")


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
			"debugWarning": staticmethod(lambda *args, **kwargs: None),
		},
	)()
	sys.modules["logHandler"] = logHandlerModule

	for moduleName in (
		"addon",
		"addon.globalPlugins",
		"addon.globalPlugins.visAware",
		"addon.globalPlugins.visAware.contentRecognizers",
	):
		module = sys.modules.get(moduleName)
		if module is None:
			module = types.ModuleType(moduleName)
			module.__path__ = []  # type: ignore[attr-defined]
			sys.modules[moduleName] = module

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")

	class ApiError(Exception):
		pass

	class AuthenticationError(ApiError):
		pass

	exceptionsModule.ApiError = ApiError
	exceptionsModule.AuthenticationError = AuthenticationError
	sys.modules["addon.globalPlugins.visAware.exceptions"] = exceptionsModule

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")
	networkModule.sendRequest = lambda *args, **kwargs: None
	networkModule.sleepWithCancellation = lambda *args, **kwargs: None
	sys.modules["addon.globalPlugins.visAware.network"] = networkModule
	sys.modules["addon.globalPlugins.visAware"].network = networkModule


def load_captcha_text_client_module():
	_install_module_stubs()
	modulePath = (
		Path(__file__).resolve().parents[1]
		/ "addon"
		/ "globalPlugins"
		/ "visAware"
		/ "contentRecognizers"
		/ "_captchaTextClient.py"
	)
	spec = importlib.util.spec_from_file_location(
		"addon.globalPlugins.visAware.contentRecognizers._captchaTextClient",
		modulePath,
	)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load _captchaTextClient.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class CaptchaTextClientTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.module = load_captcha_text_client_module()

	def _makeClient(self, responses: list[dict]):
		calls = []

		def sendRequest(method: str, url: str, **kwargs):
			calls.append((method, url, kwargs))
			return _FakeResponse(responses.pop(0))

		self.module.network.sendRequest = sendRequest
		options = self.module.CaptchaTextClientOptions(
			serviceType=self.module.SERVICE_TYPE_RUCAPTCHA,
			apiKey=" test-key \r\n",
			caseSensitive=True,
			hasMultipleWords=True,
			answerType=self.module.ANSWER_TYPE_NUMBERS_AND_LETTERS,
			isMathCaptcha=True,
			minLength=4,
			maxLength=6,
			language=self.module.LANGUAGE_LATIN,
			instructionText="read the text",
		)
		return self.module.CaptchaTextClient(options), calls

	def test_recognize_image_submits_and_polls_until_ready(self) -> None:
		client, calls = self._makeClient(
			[
				{"status": 1, "request": "12345"},
				{"status": 0, "request": self.module.CAPTCHA_NOT_READY},
				{"status": 1, "request": "A7k2"},
			],
		)

		result = client.recognizeImage(b"YWJjZA==")

		self.assertEqual(result["text"], "A7k2")
		self.assertEqual(result["captchaId"], "12345")
		self.assertEqual(calls[0][0], "POST")
		self.assertTrue(calls[0][1].endswith("/in.php"))
		self.assertIn("key=test-key", calls[0][2]["data"])
		self.assertIn("method=base64", calls[0][2]["data"])
		self.assertIn("body=YWJjZA%3D%3D", calls[0][2]["data"])
		self.assertIn("phrase=1", calls[0][2]["data"])
		self.assertIn("regsense=1", calls[0][2]["data"])
		self.assertIn("numeric=4", calls[0][2]["data"])
		self.assertIn("calc=1", calls[0][2]["data"])
		self.assertIn("min_len=4", calls[0][2]["data"])
		self.assertIn("max_len=6", calls[0][2]["data"])
		self.assertIn("language=2", calls[0][2]["data"])
		self.assertIn("textinstructions=read+the+text", calls[0][2]["data"])
		self.assertEqual(calls[1][0], "GET")
		self.assertTrue(calls[1][1].endswith("/res.php"))
		self.assertEqual(calls[1][2]["params"]["id"], "12345")

	def test_get_balance_returns_service_balance(self) -> None:
		client, calls = self._makeClient([{"status": 1, "request": "12.34"}])

		self.assertEqual(client.getBalance(), "12.34")
		self.assertEqual(calls[0][2]["params"]["action"], "getbalance")
		self.assertEqual(calls[0][2]["params"]["key"], "test-key")
		self.assertEqual(calls[0][2]["timeout"], 30)

	def test_engine_passes_timeout_to_submission_and_polling(self) -> None:
		package = "addon.globalPlugins.visAware"
		stubs = {
			"contentRecog": types.SimpleNamespace(
				RecogImageInfo=object,
				SimpleTextResult=lambda text: types.SimpleNamespace(text=text),
			),
			"ui": types.SimpleNamespace(),
			"wx": types.SimpleNamespace(),
			f"{package}.engineGUIHelper": types.SimpleNamespace(
				**dict.fromkeys(
					(
						"BooleanEngineSetting",
						"ButtonEngineSetting",
						"ChoiceEngineSetting",
						"NumericEngineSetting",
						"ReadOnlyEngineSetting",
						"TextInputEngineSetting",
					),
					object,
				),
			),
			f"{package}.recogHandler": types.SimpleNamespace(
				BaseRecognizer=object,
				RecognitionRequest=object,
			),
			f"{package}.recogHistory": types.SimpleNamespace(attachEntry=lambda result, entry: result),
		}
		parent = types.ModuleType(package)
		parent.__path__ = []
		stubs[package] = parent
		modulePath = Path(self.module.__file__).with_name("captchaText.py")
		spec = importlib.util.spec_from_file_location(f"{package}.contentRecognizers.captchaText", modulePath)
		module = importlib.util.module_from_spec(spec)
		with patch.dict(sys.modules, stubs):
			spec.loader.exec_module(module)

		engine = module.CustomContentRecognizer()
		engine.apikey = "test-key"
		engine.originalImage = None
		engine._checkCancelled = lambda event: None
		for timeout in (None, (3, 30)):
			with self.subTest(timeout=timeout):
				_client, calls = self._makeClient(
					[
						{"status": 1, "request": "12345"},
						{"status": 0, "request": self.module.CAPTCHA_NOT_READY},
						{"status": 1, "request": "A7k2"},
					],
				)
				params = {"imageContent": b"YWJjZA=="}
				if timeout is not None:
					params["timeout"] = timeout
				result = engine._handleStandardResponse(
					params,
					None,
					None,
					types.SimpleNamespace(textResult=True),
				)
				self.assertEqual(result.text, "A7k2")
				self.assertEqual(
					[call[2]["timeout"] for call in calls],
					[timeout] * 3 if timeout else [120, 30, 30],
				)
				self.assertFalse(calls[0][2]["retry"])
				self.assertTrue(all(callable(call[2]["cancelCheck"]) for call in calls))

	def test_submission_timeout_does_not_create_another_task(self) -> None:
		client, _calls = self._makeClient([])
		network = load_network_module()
		with (
			patch.object(self.module, "network", network),
			patch.object(requests, "request", side_effect=requests.exceptions.ReadTimeout) as request,
		):
			with self.assertRaises(network.NetworkError):
				client.recognizeImage(b"YWJjZA==")
			self.assertEqual(request.call_count, 1)
			self.assertTrue(request.call_args.kwargs["url"].endswith("/in.php"))

	def test_authentication_error_code_raises_authentication_error(self) -> None:
		client, _calls = self._makeClient([{"status": 0, "request": "ERROR_KEY_DOES_NOT_EXIST"}])

		with self.assertRaises(self.module.AuthenticationError):
			client.getBalance()

	def test_invalid_answer_length_raises_api_error(self) -> None:
		options = self.module.CaptchaTextClientOptions(
			serviceType=self.module.SERVICE_TYPE_RUCAPTCHA,
			apiKey="test-key",
			caseSensitive=False,
			hasMultipleWords=False,
			answerType=self.module.ANSWER_TYPE_UNSPECIFIED,
			isMathCaptcha=False,
			minLength=21,
			maxLength=0,
			language=self.module.LANGUAGE_UNSPECIFIED,
			instructionText="",
		)
		client = self.module.CaptchaTextClient(options)

		with self.assertRaises(self.module.ApiError):
			client.recognizeImage(b"YWJjZA==")


if __name__ == "__main__":
	unittest.main()
