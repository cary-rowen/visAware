# Copyright (C) 2025 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Network utility functions for the Vis Aware add-on."""

import functools
import time
from typing import TypeVar, ParamSpec, Any, NoReturn
from collections.abc import Iterator, Callable

import addonHandler
import requests
from logHandler import log

from .exceptions import ApiError, AuthenticationError, NetworkError

addonHandler.initTranslation()

P = ParamSpec("P")
R = TypeVar("R")


def sleepWithCancellation(seconds: float, cancelCheck: Any) -> None:
	"""Sleeps in short slices when a request supplies a cancellation callback."""
	if not callable(cancelCheck):
		time.sleep(seconds)
		return
	deadline = time.monotonic() + seconds
	while True:
		cancelCheck()
		remaining = deadline - time.monotonic()
		if remaining <= 0:
			return
		time.sleep(min(0.1, remaining))


def retryOnNetworkError(
	attempts: int = 3,
	delay: float = 0.5,
	backoff: float = 1.5,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
	"""
	A decorator to provide intelligent retry logic for non-streaming requests calls.

	:param attempts: The maximum number of attempts.
	:param delay: The initial delay between retries in seconds.
	:param backoff: The factor by which the delay is multiplied after each retry.
	:returns: A decorated function with retry logic.
	"""

	def decorator(func: Callable[P, R]) -> Callable[P, R]:
		@functools.wraps(func)
		def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
			cancelCheck = kwargs.get("cancelCheck")

			def checkCancelled() -> None:
				if callable(cancelCheck):
					cancelCheck()

			currentDelay = delay
			lastException: Exception | None = None
			for attempt in range(attempts):
				checkCancelled()
				try:
					return func(*args, **kwargs)
				except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
					lastException = e
					logMessagePrefix = (
						f"Network error on attempt {attempt + 1}/{attempts} for {func.__name__}"
					)
				except requests.exceptions.HTTPError as e:
					statusCode = e.response.status_code if e.response is not None else None
					# A set of HTTP status codes that are considered retryable.
					retryableStatusCodes = {429}  # 429: Too Many Requests
					# Server errors (5xx) are generally temporary and worth retrying.
					if statusCode is not None and (statusCode >= 500 or statusCode in retryableStatusCodes):
						lastException = e
						logMessagePrefix = f"Retryable HTTP {statusCode} on attempt {attempt + 1}/{attempts} for {func.__name__}"
					else:
						# For other HTTP errors (like 4xx client errors), don't retry.
						raise e

				if attempt + 1 >= attempts:
					log.debugWarning(
						f"{func.__name__} failed after {attempts} attempts.",
						exc_info=lastException,
					)
					break

				checkCancelled()
				log.warning(f"{logMessagePrefix}: {lastException}. Retrying in {currentDelay:.1f}s...")
				sleepWithCancellation(currentDelay, cancelCheck)
				currentDelay *= backoff

			assert lastException is not None
			if isinstance(lastException, requests.exceptions.HTTPError):
				_handleHttpError(lastException)
			# Translators: An error message for persistent network connection failures.
			raise NetworkError(
				_(
					"Network connection failed after multiple attempts. Please check your connection and try again.",
				),
			) from lastException

		return wrapper

	return decorator


def _handleHttpError(e: requests.exceptions.HTTPError) -> NoReturn:
	"""
	Centralized handler for HTTPError exceptions.

	It translates specific HTTP status codes into custom, more meaningful exceptions.
	This function never returns; it always raises an exception.

	:param e: The `requests.exceptions.HTTPError` instance.
	:raises AuthenticationError: For 401 or 403 errors.
	:raises ApiError: For other client or server errors.
	"""
	statusCode = e.response.status_code
	# Handle authentication/authorization errors.
	if statusCode in (401, 403):
		# Translators: An error message for authentication failures.
		raise AuthenticationError(
			_("Authentication failed. Please check your API key or credentials."),
		) from e

	# Handle other client/server errors by providing a concise summary.
	errorDetails = _getHttpErrorDetails(e.response)
	if not errorDetails and (statusCode == 429 or statusCode >= 500):
		# Translators: An error message when a service is temporarily unavailable or rate limited.
		raise ApiError(
			_(
				"Service is temporarily unavailable or rate limited: {code} {reason}. Please try again later.",
			).format(
				code=statusCode,
				reason=e.response.reason,
			),
		) from e
	if not errorDetails:
		# Translators: Used when an HTTP error response does not include a response body.
		errorDetails = _("No additional details were provided.")
	# Translators: A generic error message from the service. {code}, {reason}, and {details} are placeholders.
	raise ApiError(
		_("Service returned an error: {code} {reason}. Details: {details}").format(
			code=statusCode,
			reason=e.response.reason,
			details=errorDetails,
		),
	) from e


def _getHttpErrorDetails(response: requests.Response) -> str:
	"""
	Extracts a short user-visible error detail from an HTTP response.

	:param response: The error response.
	:returns: A concise error detail, or an empty string if no detail is available.
	"""
	try:
		responseJson = response.json()
	except ValueError:
		responseJson = None
	if isinstance(responseJson, dict):
		error = responseJson.get("error")
		if isinstance(error, dict):
			message = error.get("message")
			if message:
				return str(message)
		elif error:
			return str(error)
		message = responseJson.get("message")
		if message:
			return str(message)
	responseText = response.text.strip()
	if responseText:
		return responseText[:200]
	return ""


def _isRetryableHttpStatus(statusCode: int) -> bool:
	"""
	Checks whether an HTTP status code is worth retrying before any stream text arrives.

	:param statusCode: The HTTP status code.
	:returns: True for transient service and rate-limit errors.
	"""
	return statusCode == 429 or statusCode >= 500


@retryOnNetworkError()
def sendRequest(method: str, url: str, **kwargs: Any) -> requests.Response:
	"""
	Sends a standard (non-streaming) HTTP request with unified error handling.

	:param method: The HTTP method (e.g., 'GET', 'POST').
	:param url: The URL for the request.
	:param kwargs: Other arguments passed to requests.request (e.g., headers, data, files, json).
	:raises NetworkError: For connection or timeout errors after multiple retries.
	:raises AuthenticationError: For 401 or 403 HTTP errors.
	:raises ApiError: For other non-retryable client or server HTTP errors.
	:returns: A `requests.Response` object on success.
	"""
	kwargs.pop("cancelCheck", None)
	if "timeout" not in kwargs:
		kwargs["timeout"] = 100

	try:
		response = requests.request(method=method, url=url, **kwargs)
		# This will raise an HTTPError for 4xx/5xx responses.
		response.raise_for_status()
		return response
	except requests.exceptions.HTTPError as e:
		statusCode = e.response.status_code if e.response is not None else None
		if statusCode is not None and _isRetryableHttpStatus(statusCode):
			raise
		# Delegate non-retryable error handling to the centralized function.
		_handleHttpError(e)


def sendStreamingRequest(method: str, url: str, **kwargs: Any) -> Iterator[bytes]:
	"""
	Sends a streaming HTTP request with unified error handling.

	:param method: The HTTP method.
	:param url: The URL for the request.
	:param kwargs: Other arguments passed to `requests.request`.
	:raises NetworkError: For connection or timeout errors.
	:raises AuthenticationError: For 401 or 403 HTTP errors.
	:raises ApiError: For other non-retryable client or server HTTP errors.
	:yields: Raw byte chunks of the response content.
	"""
	cancelCheck = kwargs.pop("cancelCheck", None)

	def checkCancelled() -> None:
		if callable(cancelCheck):
			cancelCheck()

	if "timeout" not in kwargs:
		kwargs["timeout"] = 100

	kwargs["stream"] = True

	attempts = 2
	retryDelay = 0.6
	attempt = 0
	hasYieldedChunk = False

	while attempt < attempts:
		try:
			checkCancelled()
			with requests.request(method=method, url=url, **kwargs) as response:
				# This will raise an HTTPError for 4xx/5xx responses.
				response.raise_for_status()
				for chunk in response.iter_lines():
					checkCancelled()
					if chunk:
						hasYieldedChunk = True
						yield chunk
				return
		except requests.exceptions.HTTPError as e:
			statusCode = e.response.status_code
			if not hasYieldedChunk and attempt + 1 < attempts and _isRetryableHttpStatus(statusCode):
				attempt += 1
				log.warning(
					f"Retryable streaming HTTP {statusCode} before first chunk. "
					f"Retrying in {retryDelay:.1f}s...",
				)
				checkCancelled()
				sleepWithCancellation(retryDelay, cancelCheck)
				continue
			# Delegate error handling to the centralized function.
			_handleHttpError(e)
		except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
			if not hasYieldedChunk and attempt + 1 < attempts:
				attempt += 1
				log.warning(
					f"Streaming network error before first chunk: {e}. Retrying in {retryDelay:.1f}s...",
				)
				checkCancelled()
				sleepWithCancellation(retryDelay, cancelCheck)
				continue
			# Handle non-HTTP network errors directly for streaming requests.
			# Translators: An error message for network connection failures.
			raise NetworkError(
				_("Network connection failed. Please check your connection and try again."),
			) from e
