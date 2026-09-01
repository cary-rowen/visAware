from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path
import sys
import types
import unittest


def loadScreenCaptureModule(recogUi, screenCurtain):
	contentRecogModule = types.ModuleType("contentRecog")
	contentRecogModule.recogUi = recogUi
	sys.modules["contentRecog"] = contentRecogModule
	screenCurtainModule = types.ModuleType("screenCurtain")
	screenCurtainModule.screenCurtain = screenCurtain
	sys.modules["screenCurtain"] = screenCurtainModule
	builtins._ = lambda value: value
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "_screenCapture.py"
	)
	spec = importlib.util.spec_from_file_location("screenCaptureUnderTest", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load _screenCapture.py")
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class ScreenCaptureTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.recogUi = types.SimpleNamespace()
		self.screenCurtain = types.SimpleNamespace(enabled=False)
		self.module = loadScreenCaptureModule(self.recogUi, self.screenCurtain)
		self.imageInfo = object()

	def test_usesNvdaCaptureWhenAvailable(self) -> None:
		self.recogUi._captureImage = lambda imageInfo: bytearray(b"pixels")

		pixels = self.module.captureNvdaPixels(self.imageInfo)

		self.assertEqual(pixels, b"pixels")

	def test_returnsNoneForOlderNvda(self) -> None:
		self.assertIsNone(self.module.captureNvdaPixels(self.imageInfo))

	def test_screenCurtainRequiresWgc(self) -> None:
		self.screenCurtain.enabled = True
		self.recogUi._isWgcCaptureSupported = lambda: False

		with self.assertRaises(self.module.ScreenCaptureError):
			self.module.captureNvdaPixels(self.imageInfo)

	def test_screenCurtainRequiresNvdaCaptureEntryPoint(self) -> None:
		self.screenCurtain.enabled = True
		self.recogUi._isWgcCaptureSupported = lambda: True

		with self.assertRaises(self.module.ScreenCaptureError):
			self.module.captureNvdaPixels(self.imageInfo)

	def test_wgcFailureDoesNotFallbackWithScreenCurtain(self) -> None:
		self.screenCurtain.enabled = True
		self.recogUi._isWgcCaptureSupported = lambda: True
		self.recogUi._captureImage = lambda _imageInfo: (_ for _ in ()).throw(RuntimeError("failed"))

		with self.assertRaises(self.module.ScreenCaptureError):
			self.module.captureNvdaPixels(self.imageInfo)


if __name__ == "__main__":
	unittest.main()
