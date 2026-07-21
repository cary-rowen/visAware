from __future__ import annotations

import importlib.util
import builtins
from pathlib import Path
import sys
import types
import unittest


class _FakeResponse:
	def __init__(self, payload: dict):
		self._payload = payload
		self.text = str(payload)

	def json(self) -> dict:
		return self._payload


def _install_module_stubs() -> None:
	builtins._ = lambda value: value

	logHandlerModule = types.ModuleType("logHandler")
	logHandlerModule.log = type(
		"Log",
		(),
		{
			"debug": staticmethod(lambda *args, **kwargs: None),
			"info": staticmethod(lambda *args, **kwargs: None),
			"error": staticmethod(lambda *args, **kwargs: None),
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

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")
	networkModule.sendRequest = lambda **kwargs: None
	sys.modules["addon.globalPlugins.visAware.network"] = networkModule


def load_vivo_auth_module():
	_install_module_stubs()
	modulePath = (
		Path(__file__).resolve().parents[1]
		/ "addon"
		/ "globalPlugins"
		/ "visAware"
		/ "contentRecognizers"
		/ "_vivo_auth.py"
	)
	spec = importlib.util.spec_from_file_location(
		"addon.globalPlugins.visAware.contentRecognizers._vivo_auth",
		modulePath,
	)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load _vivo_auth.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class VivoAuthTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.module = load_vivo_auth_module()

	def test_service_authentication_error_is_not_rewritten_as_connection_error(self) -> None:
		self.module.network.sendRequest = lambda **kwargs: _FakeResponse(
			{"code": 401, "data": "bad credentials"},
		)

		with self.assertRaises(self.module.AuthenticationError) as error:
			self.module._fetchSignatureFromService("user", "pass", b"signing-string")

		self.assertIn("NVDACN API Error: bad credentials", str(error.exception))


if __name__ == "__main__":
	unittest.main()
