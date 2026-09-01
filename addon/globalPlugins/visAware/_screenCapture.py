# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.

"""Compatibility helpers for capturing screen pixels through NVDA."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from contentRecog import RecogImageInfo
	from PIL import Image


class ScreenCaptureError(RuntimeError):
	"""Raised when the screen cannot be captured while Screen Curtain is active."""


def _isScreenCurtainActive() -> bool:
	from screenCurtain import screenCurtain

	return screenCurtain is not None and screenCurtain.enabled


def isScreenCurtainCaptureSupported() -> bool:
	"""Return whether the current NVDA can capture a curtained screen."""
	if not _isScreenCurtainActive():
		return True
	from contentRecog import recogUi

	isSupported = getattr(recogUi, "_isWgcCaptureSupported", None)
	return bool(
		callable(getattr(recogUi, "_captureImage", None)) and callable(isSupported) and isSupported(),
	)


def hasNvdaCapture() -> bool:
	"""Return whether NVDA exposes its centralized screen capture function."""
	from contentRecog import recogUi

	return callable(getattr(recogUi, "_captureImage", None))


def captureNvdaPixels(imageInfo: "RecogImageInfo") -> bytes | None:
	"""Capture a region with NVDA's content recognition backend when available."""
	if not isScreenCurtainCaptureSupported():
		raise ScreenCaptureError("Screen capture is unavailable while Screen Curtain is active.")
	if not hasNvdaCapture():
		if _isScreenCurtainActive():
			raise ScreenCaptureError("Screen Curtain capture is unavailable in this NVDA version.")
		return None
	from contentRecog import recogUi

	capture = getattr(recogUi, "_captureImage", None)
	if not callable(capture):
		return None
	try:
		return bytes(capture(imageInfo))
	except RuntimeError as e:
		if _isScreenCurtainActive():
			raise ScreenCaptureError("Screen Curtain capture failed.") from e
		raise


def imageFromNvdaPixels(pixels: bytes, imageInfo: "RecogImageInfo") -> Image.Image:
	"""Convert NVDA's top-down BGRX pixels into a PIL RGB image."""
	from PIL import Image

	return Image.frombytes(
		"RGBX",
		(imageInfo.recogWidth, imageInfo.recogHeight),
		pixels,
		"raw",
		"BGRX",
	).convert("RGB")
