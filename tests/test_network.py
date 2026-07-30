from __future__ import annotations

import importlib.util
import builtins
from pathlib import Path
import sys
import types
import unittest

import requests


class _FakeResponse:
	def __init__(self, statusCode: int, content: bytes = b"", reason: str = ""):
		self.status_code = statusCode
		self.content = content
		self.reason = reason or "reason"
		self.text = content.decode("utf-8", errors="ignore")

	def raise_for_status(self) -> None:
		if self.status_code >= 400:
			raise requests.exceptions.HTTPError(response=self)

	def json(self) -> dict:
		raise ValueError("not json")

	def __bool__(self) -> bool:
		return self.status_code < 400


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
			"warning": staticmethod(lambda *args, **kwargs: None),
		},
	)()
	sys.modules["logHandler"] = logHandlerModule

	for moduleName in (
		"addon",
		"addon.globalPlugins",
		"addon.globalPlugins.visAware",
	):
		module = sys.modules.get(moduleName)
		if module is None:
			module = types.ModuleType(moduleName)
			module.__path__ = []  # type: ignore[attr-defined]
			sys.modules[moduleName] = module

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")

	class NetworkError(Exception):
		pass

	class ApiError(Exception):
		pass

	class AuthenticationError(ApiError):
		pass

	exceptionsModule.NetworkError = NetworkError
	exceptionsModule.ApiError = ApiError
	exceptionsModule.AuthenticationError = AuthenticationError
	sys.modules["addon.globalPlugins.visAware.exceptions"] = exceptionsModule


def load_network_module():
	_install_module_stubs()
	modulePath = Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "network.py"
	spec = importlib.util.spec_from_file_location("addon.globalPlugins.visAware.network", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load network.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	module.time.sleep = lambda seconds: None
	return module


class NetworkRequestRetryTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.module = load_network_module()
		self._originalRequest = requests.request

	def tearDown(self) -> None:
		requests.request = self._originalRequest

	def test_send_request_retries_retryable_http_errors(self) -> None:
		responses = [
			_FakeResponse(500),
			_FakeResponse(429),
			_FakeResponse(200, b"ok"),
		]
		calls = []

		def request(**kwargs):
			calls.append(kwargs)
			return responses.pop(0)

		self.module.requests.request = request

		response = self.module.sendRequest("GET", "https://example.test")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.content, b"ok")
		self.assertEqual(len(calls), 3)

	def test_send_request_handles_retryable_http_error_after_exhausting_attempts(self) -> None:
		responses = [
			_FakeResponse(503),
			_FakeResponse(503),
			_FakeResponse(503),
		]
		calls = []

		def request(**kwargs):
			calls.append(kwargs)
			return responses.pop(0)

		self.module.requests.request = request

		with self.assertRaises(self.module.ApiError) as error:
			self.module.sendRequest("GET", "https://example.test")

		self.assertIn("Service is temporarily unavailable or rate limited", str(error.exception))
		self.assertEqual(len(calls), 3)

	def test_send_request_uses_top_level_error_message(self) -> None:
		response = _FakeResponse(400, reason="Bad Request")
		response.json = lambda: {"success": False, "message": "Missing or empty 'file' part"}
		self.module.requests.request = lambda **kwargs: response

		with self.assertRaises(self.module.ApiError) as error:
			self.module.sendRequest("POST", "https://example.test/upload")

		self.assertIn("Missing or empty 'file' part", str(error.exception))


if __name__ == "__main__":
	unittest.main()
