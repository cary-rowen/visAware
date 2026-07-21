from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, override
import unittest


def load_secure_storage_module() -> Any:
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "secure_storage.py"
	)
	spec = importlib.util.spec_from_file_location(
		"addon.globalPlugins.visAware.secure_storage",
		modulePath,
	)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load secure_storage.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class SecureStorageImportTestCase(unittest.TestCase):
	def test_unprotect_string_invalid_base64_raises_secure_storage_error(self) -> None:
		module = load_secure_storage_module()

		with self.assertRaises(module.SecureStorageError):
			module.unprotectString("not valid base64")


@unittest.skipUnless(sys.platform == "win32", "Windows DPAPI is only available on Windows")
class SecureStorageRoundTripTestCase(unittest.TestCase):
	module: Any = None

	@override
	def setUp(self) -> None:
		self.module = load_secure_storage_module()

	def test_protect_string_round_trip(self) -> None:
		secret = "api-token-\u2603-\u4f60\u597d"

		protected = self.module.protectString(secret)

		self.assertIsInstance(protected, str)
		self.assertNotEqual(protected, secret)
		self.assertEqual(self.module.unprotectString(protected), secret)

	def test_protect_data_round_trip(self) -> None:
		secret = b"\x00binary-secret\xff"

		protected = self.module.protectData(secret)

		self.assertIsInstance(protected, bytes)
		self.assertNotEqual(protected, secret)
		self.assertEqual(self.module.unprotectData(protected), secret)

	def test_protect_data_round_trip_with_optional_entropy(self) -> None:
		secret = b"entropy-bound-secret"
		entropy = b"visAware-test-context"

		protected = self.module.protectData(secret, optionalEntropy=entropy)

		self.assertEqual(self.module.unprotectData(protected, optionalEntropy=entropy), secret)

	def test_string_helpers_preserve_empty_secret(self) -> None:
		self.assertEqual(self.module.protectString(""), "")
		self.assertEqual(self.module.unprotectString(""), "")


if __name__ == "__main__":
	_ = unittest.main()
