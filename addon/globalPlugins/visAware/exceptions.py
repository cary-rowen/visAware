# Copyright (C) 2025 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Custom exceptions for the Vis Aware add-on."""

import addonHandler
import threading

addonHandler.initTranslation()


class OCRError(Exception):
	"""Base class for all online OCR and image description related errors."""

	pass


class NetworkError(OCRError):
	"""Exception representing a network-level error.

	This includes retryable errors like connection timeouts, DNS failures, etc.
	"""

	pass


class ApiError(OCRError):
	"""
	Exception representing a business logic error returned by an API.

	E.g., invalid API key, insufficient quota, bad request parameters.
	"""

	def __init__(self, message: str, errorCode: int | None = None):
		"""
		Initializes the API error.

		:param message: The error message.
		:param errorCode: An optional error code from the API.
		"""
		super().__init__(message)
		self.errorCode = errorCode


class StreamIncompleteError(ApiError):
	"""
	Exception representing a streaming response that ended after a partial result.

	This is distinct from a normal API error so callers can keep already delivered
	streaming text while still treating empty responses as failures.
	"""

	def __init__(self, message: str, partialText: str = ""):
		"""
		Initializes the streaming incomplete error.

		:param message: The error message.
		:param partialText: Text included in the same final streaming event, if any.
		"""
		super().__init__(message)
		self.partialText = partialText


class StreamReplacementError(ApiError):
	"""
	Exception representing a streaming response that replaces previous partial text.

	Some services may emit partial chunks and then send an intervened replacement
	response. Callers should replace already collected stream text with this value
	instead of appending it.
	"""

	def __init__(self, message: str, replacementText: str):
		"""
		Initializes the streaming replacement error.

		:param message: The error or status message.
		:param replacementText: Text that replaces previous streaming chunks.
		"""
		super().__init__(message)
		self.replacementText = replacementText


class AuthenticationError(ApiError):
	"""A specific ApiError for authentication failures."""

	pass


class CancellationError(OCRError):
	"""Raised when a recognition task is actively cancelled by the user."""

	def __init__(self, message: str, event: threading.Event | None = None):
		"""
		Initializes the cancellation error.

		:param message: The cancellation message.
		:param event: The cancellation event object, if any.
		"""
		super().__init__(message)
		self.event = event
