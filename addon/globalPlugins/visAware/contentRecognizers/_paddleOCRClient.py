# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Client helpers for PaddleOCR and PaddleOCR-VL services."""

from __future__ import annotations

import addonHandler
import base64
from collections.abc import Callable
from dataclasses import dataclass
import json
from logHandler import log
import time
from typing import Any

from .. import network
from ..exceptions import ApiError, AuthenticationError

addonHandler.initTranslation()

SERVICE_TYPE_AISTUDIO_ASYNC = "aistudioAsync"
SERVICE_TYPE_AISTUDIO_SYNC = "aistudioSync"
SERVICE_TYPE_SELF_HOSTED = "selfHosted"

MODEL_PADDLEOCR_VL_1_5 = "PaddleOCR-VL-1.5"
MODEL_PADDLEOCR_VL_1_6 = "PaddleOCR-VL-1.6"
MODEL_PADDLEOCR_VL = "PaddleOCR-VL"
MODEL_PP_OCR_V5 = "PP-OCRv5"
MODEL_PP_OCR_V6 = "PP-OCRv6"
MODEL_PP_STRUCTURE_V3 = "PP-StructureV3"

DEFAULT_AISTUDIO_ASYNC_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_AISTUDIO_POLL_INTERVAL_SECONDS = 3
DEFAULT_AISTUDIO_MAX_WAIT_SECONDS = 240


@dataclass(frozen=True)
class PaddleOCRClientOptions:
	"""Options needed to call a PaddleOCR service."""

	serviceType: str
	apiUrl: str
	token: str
	model: str
	useDocOrientationClassify: bool
	useDocUnwarping: bool
	useTextlineOrientation: bool
	useChartRecognition: bool


class PaddleOCRClient:
	"""A small HTTP client for PaddleOCR hosted and self-hosted deployments."""

	def __init__(
		self,
		options: PaddleOCRClientOptions,
		cancellationChecker: Callable[[], None] | None = None,
	) -> None:
		self.options = options
		self._cancellationChecker = cancellationChecker

	def recognizeImage(self, imageContent: bytes) -> dict[str, Any]:
		"""
		Recognizes one already-serialized image.

		:param imageContent: Base64 encoded image bytes from the recognizer pipeline.
		:returns: A decoded PaddleOCR result dictionary.
		"""
		if self.options.serviceType == SERVICE_TYPE_AISTUDIO_ASYNC:
			return self._recognizeWithAistudioAsync(imageContent)
		if self.options.serviceType in (SERVICE_TYPE_AISTUDIO_SYNC, SERVICE_TYPE_SELF_HOSTED):
			return self._recognizeWithSyncService(imageContent)
		# Translators: An error message when a PaddleOCR service type setting is invalid.
		raise ApiError(_("Unsupported PaddleOCR service type."))

	def _recognizeWithSyncService(self, imageContent: bytes) -> dict[str, Any]:
		url = self._getSyncUrl()
		if not url:
			# Translators: An error message if the PaddleOCR API URL is missing.
			raise AuthenticationError(_("API URL is missing. Please configure it in PaddleOCR settings."))
		if self.options.serviceType == SERVICE_TYPE_AISTUDIO_SYNC:
			self._requireToken()

		headers = {
			"Accept": "application/json",
			"Content-Type": "application/json",
		}
		defaultAuthScheme = "Bearer" if self.options.serviceType == SERVICE_TYPE_SELF_HOSTED else "token"
		authorization = self._buildAuthorizationHeader(defaultAuthScheme)
		if authorization:
			headers["Authorization"] = authorization

		payload = self._buildJsonPayload(imageContent)
		self._checkCancelled()
		response = network.sendRequest(
			"POST",
			url,
			headers=headers,
			json=payload,
			timeout=120,
		)
		result = self._loadJsonResponse(response.content)
		self._raiseForApiError(result)
		return result

	def _recognizeWithAistudioAsync(self, imageContent: bytes) -> dict[str, Any]:
		self._requireToken()
		url = self.options.apiUrl.strip() or DEFAULT_AISTUDIO_ASYNC_URL
		rawImage = self._decodeBase64Image(imageContent)
		headers = {
			"Accept": "application/json",
			"Authorization": self._buildAuthorizationHeader("Bearer"),
		}
		data = {
			"model": self.options.model,
			"optionalPayload": json.dumps(self._buildOptionalPayload(), ensure_ascii=False),
		}
		files = {
			"file": ("image.png", rawImage, "image/png"),
		}

		self._checkCancelled()
		submitResult = self._requestJson(
			"POST",
			url,
			headers=headers,
			data=data,
			files=files,
			timeout=120,
		)
		jobId = self._extractJobId(submitResult)
		if not jobId:
			# Translators: An error message when PaddleOCR does not return a job id.
			raise ApiError(_("PaddleOCR did not return a job id."))

		jobResult = self._pollAistudioJob(url, jobId, headers)
		resultUrl = self._extractResultJsonUrl(jobResult)
		if not resultUrl:
			return jobResult
		return self._downloadAsyncResult(resultUrl)

	def _requestJson(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
		response = network.sendRequest(method, url, **kwargs)
		result = self._loadJsonResponse(response.content)
		self._raiseForApiError(result)
		return result

	def _pollAistudioJob(
		self,
		submitUrl: str,
		jobId: str,
		headers: dict[str, str],
	) -> dict[str, Any]:
		statusUrl = f"{submitUrl.rstrip('/')}/{jobId}"
		deadline = time.monotonic() + DEFAULT_AISTUDIO_MAX_WAIT_SECONDS
		lastResult: dict[str, Any] = {}
		while time.monotonic() < deadline:
			self._checkCancelled()
			lastResult = self._requestJson("GET", statusUrl, headers=headers, timeout=30)
			status = self._extractJobStatus(lastResult).lower()
			if status in ("success", "succeeded", "completed", "complete", "done", "finished"):
				return lastResult
			if status in ("fail", "failed", "error", "cancelled", "canceled"):
				message = self._extractErrorMessage(lastResult) or status
				# Translators: An error message when a PaddleOCR async job fails.
				raise ApiError(_("PaddleOCR job failed: {}").format(message))
			time.sleep(DEFAULT_AISTUDIO_POLL_INTERVAL_SECONDS)
		log.debugWarning(f"PaddleOCR async job timed out. Last result: {lastResult!r}")
		# Translators: An error message when PaddleOCR does not finish within the timeout.
		raise ApiError(_("PaddleOCR job did not finish before the timeout."))

	def _downloadAsyncResult(self, resultUrl: str) -> dict[str, Any]:
		self._checkCancelled()
		response = network.sendRequest("GET", resultUrl, timeout=120)
		text = response.text.strip()
		if not text:
			# Translators: An error message when PaddleOCR returns an empty result file.
			raise ApiError(_("PaddleOCR returned an empty result file."))
		try:
			result = json.loads(text)
		except json.JSONDecodeError:
			result = self._loadJsonlResult(text)
		if isinstance(result, list):
			records = [record for record in result if isinstance(record, dict)]
			if records:
				result = self._combineJsonlRecords(records)
		if not isinstance(result, dict):
			# Translators: An error message when PaddleOCR returns a malformed result file.
			raise ApiError(_("PaddleOCR returned an invalid result file."))
		self._raiseForApiError(result)
		return result

	def _loadJsonlResult(self, text: str) -> dict[str, Any]:
		records: list[dict[str, Any]] = []
		for line in text.splitlines():
			line = line.strip()
			if not line:
				continue
			try:
				record = json.loads(line)
			except json.JSONDecodeError as e:
				# Translators: An error message when PaddleOCR returns malformed JSONL.
				raise ApiError(_("PaddleOCR returned an invalid JSONL result file.")) from e
			if isinstance(record, dict):
				records.append(record)
		if not records:
			# Translators: An error message when PaddleOCR returns no usable JSONL records.
			raise ApiError(_("PaddleOCR returned no usable result records."))
		return self._combineJsonlRecords(records)

	def _combineJsonlRecords(self, records: list[dict[str, Any]]) -> dict[str, Any]:
		combinedResult: dict[str, Any] = {}
		for record in records:
			result = record.get("result", record)
			if not isinstance(result, dict):
				continue
			for key, value in result.items():
				if isinstance(value, list):
					combinedResult.setdefault(key, [])
					if isinstance(combinedResult[key], list):
						combinedResult[key].extend(value)
				elif key not in combinedResult:
					combinedResult[key] = value
		return {
			"result": combinedResult,
			"records": records,
		}

	def _getSyncUrl(self) -> str:
		url = self.options.apiUrl.strip()
		if self.options.serviceType != SERVICE_TYPE_SELF_HOSTED or not url:
			return url
		url = url.rstrip("/")
		if url.endswith(("/ocr", "/layout-parsing")):
			return url
		endpoint = "ocr" if self._isOCRModel() else "layout-parsing"
		return f"{url}/{endpoint}"

	def _isOCRModel(self) -> bool:
		return self.options.model in (MODEL_PP_OCR_V5, MODEL_PP_OCR_V6)

	def _buildJsonPayload(self, imageContent: bytes) -> dict[str, Any]:
		imageBase64 = imageContent.decode("ascii", errors="ignore")
		return {
			"file": imageBase64,
			"fileType": 1,
			**self._buildOptionalPayload(),
		}

	def _buildOptionalPayload(self) -> dict[str, Any]:
		payload: dict[str, Any] = {
			"useDocOrientationClassify": self.options.useDocOrientationClassify,
			"useDocUnwarping": self.options.useDocUnwarping,
			"visualize": False,
		}
		if self._isOCRModel():
			payload["useTextlineOrientation"] = self.options.useTextlineOrientation
		else:
			payload["useLayoutDetection"] = True
			payload["useChartRecognition"] = self.options.useChartRecognition
		return payload

	def _requireToken(self) -> None:
		if self.options.token.strip():
			return
		# Translators: An error message if the PaddleOCR token is missing.
		raise AuthenticationError(_("API token is missing. Please configure it in PaddleOCR settings."))

	def _buildAuthorizationHeader(self, defaultScheme: str) -> str:
		token = self.options.token.strip()
		if not token:
			return ""
		lowerToken = token.lower()
		if lowerToken.startswith(("bearer ", "token ")):
			return token
		return f"{defaultScheme} {token}"

	def _checkCancelled(self) -> None:
		if self._cancellationChecker:
			self._cancellationChecker()

	@staticmethod
	def _decodeBase64Image(imageContent: bytes) -> bytes:
		try:
			return base64.b64decode(imageContent, validate=True)
		except ValueError as e:
			# Translators: An error message when an image cannot be prepared for PaddleOCR upload.
			raise ApiError(_("Could not prepare the image for PaddleOCR upload.")) from e

	@classmethod
	def _loadJsonResponse(cls, content: bytes) -> dict[str, Any]:
		try:
			result = json.loads(content.decode("utf-8", errors="ignore"))
		except json.JSONDecodeError as e:
			# Translators: An error message when PaddleOCR returns malformed JSON.
			raise ApiError(_("PaddleOCR returned an invalid JSON response.")) from e
		if not isinstance(result, dict):
			# Translators: An error message when PaddleOCR returns a JSON value that is not an object.
			raise ApiError(_("PaddleOCR returned an invalid response."))
		return result

	@classmethod
	def _raiseForApiError(cls, result: dict[str, Any]) -> None:
		message = cls._extractErrorMessage(result)
		if message:
			# Translators: An error message returned from the PaddleOCR API.
			raise ApiError(_("PaddleOCR API error: {}").format(message))

	@classmethod
	def _extractErrorMessage(cls, result: dict[str, Any]) -> str:
		error = result.get("error")
		if isinstance(error, dict):
			message = error.get("message") or error.get("msg")
			if message:
				return str(message)
		elif error:
			return str(error)
		code = cls._firstExisting(result, ("errorCode", "error_code", "code", "ret_code"))
		if cls._isSuccessCode(code):
			return ""
		if code is None:
			return ""
		message = cls._firstExisting(
			result,
			("errorMsg", "error_msg", "message", "msg", "ret_msg"),
		)
		if message:
			return str(message)
		return str(code)

	@staticmethod
	def _isSuccessCode(code: Any) -> bool:
		if code is None:
			return True
		if code in (0, 200):
			return True
		if isinstance(code, str):
			return code.lower() in ("0", "000000", "200", "success", "succeeded", "ok")
		return False

	@staticmethod
	def _firstExisting(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
		for key in keys:
			if key in data:
				return data[key]
		return None

	@classmethod
	def _extractJobId(cls, result: dict[str, Any]) -> str:
		for container in cls._iterContainers(result):
			value = cls._firstExisting(container, ("jobId", "job_id", "id", "taskId", "task_id"))
			if value:
				return str(value)
		return ""

	@classmethod
	def _extractJobStatus(cls, result: dict[str, Any]) -> str:
		for container in cls._iterContainers(result):
			value = cls._firstExisting(container, ("status", "state", "jobStatus", "job_status"))
			if value:
				return str(value)
		if cls._extractResultJsonUrl(result):
			return "success"
		return "running"

	@classmethod
	def _extractResultJsonUrl(cls, result: dict[str, Any]) -> str:
		for container in cls._iterContainers(result):
			value = cls._firstExisting(container, ("jsonUrl", "json_url", "resultJsonUrl", "result_json_url"))
			if value:
				return str(value)
			value = cls._firstExisting(
				container,
				("jsonlUrl", "jsonl_url", "resultJsonlUrl", "result_jsonl_url"),
			)
			if value:
				return str(value)
			resultUrl = container.get("resultUrl") or container.get("result_url")
			if isinstance(resultUrl, dict):
				value = cls._firstExisting(resultUrl, ("jsonUrl", "json_url", "jsonlUrl", "jsonl_url", "url"))
				if value:
					return str(value)
			elif resultUrl:
				return str(resultUrl)
			urls = container.get("urls")
			if isinstance(urls, dict):
				value = cls._firstExisting(
					urls,
					("json", "jsonl", "jsonUrl", "json_url", "jsonlUrl", "jsonl_url", "result"),
				)
				if value:
					return str(value)
		return ""

	@staticmethod
	def _iterContainers(result: dict[str, Any]) -> list[dict[str, Any]]:
		containers = [result]
		index = 0
		while index < len(containers):
			container = containers[index]
			index += 1
			for key in ("data", "result", "output"):
				value = container.get(key)
				if isinstance(value, dict) and value not in containers:
					containers.append(value)
		return containers
