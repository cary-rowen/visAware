# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Authentication, HTTP, and result helpers for Baimiao Web OCR."""

from __future__ import annotations

import addonHandler
import base64
import config
from fileUtils import FaultTolerantFile  # pyright: ignore[reportUnknownVariableType]
import hashlib
import json
from logHandler import log
import math
from NVDAState import shouldWriteToDisk, WritePaths
import os
import re
from threading import RLock, Thread
import time
from collections.abc import Callable
from typing import Any, cast
import unicodedata
from urllib.parse import urlsplit
import uuid

from .. import network
from ..exceptions import ApiError, AuthenticationError, OCRError
from ..secure_storage import SecureStorageError, protectString, unprotectString

addonHandler.initTranslation()

BASE_URL = "https://web.baimiaoapp.com"
USER_AGENT = (
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
	"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_REQUEST_TIMEOUT = (3, 30)
DEFAULT_UPLOAD_TIMEOUT = (3, 60)
POLL_INTERVAL_SECONDS = 0.5
MAX_POLL_INTERVAL_SECONDS = 2.0
MAX_POLL_SECONDS = 120

ENGINE_CONFIG_SECTION = "OCR"
ENGINE_CONFIG_NAME = "baimiaoOCR"
SESSION_CONFIG_KEY = "encryptedSession"
SESSION_FILE_NAME = "visAwareBaimiaoSession.dat"

_ENGINE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_DEVICE_LIMIT_ERROR_CODE = 400401
_MAX_FRAGMENT_GAP_FACTOR = 2
_sessionLock = RLock()
_UPLOAD_SIGN_FIELDS = (
	"host",
	"file_key",
	"policy",
	"signature",
	"x_oss_credential",
	"x_oss_date",
	"security_token",
)


def _getEngineConfig() -> Any:
	try:
		section: Any = cast(Any, config.conf[ENGINE_CONFIG_SECTION])
	except KeyError:
		return {}
	try:
		isSet = section.isSet(ENGINE_CONFIG_NAME)
	except AttributeError:
		isSet = ENGINE_CONFIG_NAME in section
	if isSet:
		return section[ENGINE_CONFIG_NAME]
	return {}


def _getEncryptedSession() -> str:
	try:
		with open(os.path.join(WritePaths.configDir, SESSION_FILE_NAME), encoding="ascii") as sessionFile:
			return sessionFile.read().strip()
	except FileNotFoundError:
		pass
	except (OSError, UnicodeError):
		log.warning("Stored Baimiao Web session file could not be read.", exc_info=True)
		return ""
	# Read the old config value only to migrate sessions created by earlier builds.
	try:
		return str(_getEngineConfig().get(SESSION_CONFIG_KEY, "") or "")
	except AttributeError:
		return ""


def _loadSession() -> dict[str, str] | None:
	encrypted = _getEncryptedSession()
	if not encrypted:
		return None
	try:
		value: Any = json.loads(unprotectString(encrypted))
	except (SecureStorageError, json.JSONDecodeError):
		log.warning("Stored Baimiao Web session could not be read.", exc_info=True)
		return None
	if not isinstance(value, dict):
		return None
	session = cast(dict[str, Any], value)
	deviceUuid = session.get("uuid")
	loginToken = session.get("token")
	if not _isUuid(deviceUuid) or not isinstance(loginToken, str):
		return None
	return {"uuid": cast(str, deviceUuid), "token": loginToken}


def _saveSession(deviceUuid: str, loginToken: str) -> None:
	if not _isUuid(deviceUuid):
		# Translators: An error when Baimiao returns invalid login information.
		raise AuthenticationError(_("Baimiao returned invalid login information."))
	try:
		encrypted = protectString(
			json.dumps({"uuid": deviceUuid, "token": loginToken}, separators=(",", ":")),
		)
	except SecureStorageError as e:
		# Translators: An error when Baimiao login information cannot be stored securely.
		raise AuthenticationError(_("Could not save Baimiao login information securely.")) from e
	if not encrypted:
		# Translators: An error when Baimiao login information cannot be stored securely.
		raise AuthenticationError(_("Could not save Baimiao login information securely."))
	if not shouldWriteToDisk():
		# Translators: An error when Baimiao login information cannot be stored securely.
		raise AuthenticationError(_("Could not save Baimiao login information securely."))
	encryptedBytes = encrypted.encode("ascii")
	try:
		with (
			_sessionLock,
			FaultTolerantFile(
				os.path.join(WritePaths.configDir, SESSION_FILE_NAME),
			) as sessionFile,
		):
			if sessionFile.write(encryptedBytes) != len(encryptedBytes):
				raise OSError("Incomplete Baimiao session write.")
	except (OSError, UnicodeError) as e:
		# Translators: An error when Baimiao login information cannot be stored securely.
		raise AuthenticationError(_("Could not save Baimiao login information securely.")) from e


def _replaceStoredLoginToken(deviceUuid: str, expectedToken: str, loginToken: str) -> bool:
	with _sessionLock:
		if _loadSession() != {"uuid": deviceUuid, "token": expectedToken}:
			return False
		_saveSession(deviceUuid, loginToken)
		return True


def _getOrCreateDeviceUuid() -> str:
	session = _loadSession()
	if session:
		return session["uuid"]
	deviceUuid = str(uuid.uuid4())
	# Persist before login so a failed attempt does not create a new Baimiao device next time.
	_saveSession(deviceUuid, "")
	return deviceUuid


def hasStoredLogin() -> bool:
	"""Returns whether a usable Baimiao Web login token is stored."""

	session = _loadSession()
	return bool(session and session["token"])


def clearStoredLogin() -> None:
	"""Clears the login token while retaining the registered device UUID."""

	session = _loadSession()
	if session:
		_saveSession(session["uuid"], "")


def logoutStoredLoginAsync() -> None:
	"""Clears local credentials and attempts server logout in the background."""

	session = _loadSession()
	if not session:
		return
	clearStoredLogin()
	if not session["token"]:
		return
	Thread(
		name="BaimiaoLogoutThread",
		target=_logoutSession,
		args=(session,),
		daemon=True,
	).start()


def _logoutSession(session: dict[str, str]) -> None:
	try:
		BaimiaoWebClient(session["uuid"], session["token"])._requestData(
			"POST",
			"/api/user/logout",
			jsonPayload={},
		)
	except OCRError:
		log.warning("Baimiao server logout failed after clearing the local session.", exc_info=True)
	except Exception:
		log.error("Unexpected Baimiao server logout failure.", exc_info=True)


def _isUuid(value: Any) -> bool:
	if not isinstance(value, str):
		return False
	try:
		return str(uuid.UUID(value)) == value.lower()
	except (ValueError, AttributeError):
		return False


class BaimiaoWebClient:
	"""Runs the authenticated multi-step Baimiao Web OCR workflow."""

	def __init__(
		self,
		deviceUuid: str,
		loginToken: str,
		cancellationChecker: Callable[[], None] | None = None,
		requestTimeout: float | tuple[float, float] | None = None,
	) -> None:
		super().__init__()
		self.deviceUuid = deviceUuid
		self.loginToken = loginToken
		self._cancellationChecker = cancellationChecker
		self._requestTimeout = requestTimeout or DEFAULT_REQUEST_TIMEOUT
		self._uploadTimeout = requestTimeout or DEFAULT_UPLOAD_TIMEOUT

	@classmethod
	def fromStoredLogin(
		cls,
		cancellationChecker: Callable[[], None] | None = None,
		requestTimeout: float | tuple[float, float] | None = None,
	) -> BaimiaoWebClient:
		"""Creates a client from the DPAPI-protected local session."""

		session = _loadSession()
		if not session or not session["token"]:
			# Translators: An error when Baimiao Web has not been logged in.
			raise AuthenticationError(_("Please log in to Baimiao in the OCR engine settings."))
		return cls(
			session["uuid"],
			session["token"],
			cancellationChecker=cancellationChecker,
			requestTimeout=requestTimeout,
		)

	@classmethod
	def login(
		cls,
		account: str,
		password: str,
		cancellationChecker: Callable[[], None] | None = None,
		requestTimeout: float | tuple[float, float] | None = None,
	) -> None:
		"""Logs in with an email address or mobile number and stores only the returned session."""

		account = account.strip()
		if not account or not password:
			# Translators: An error when a Baimiao login field is empty.
			raise AuthenticationError(_("Enter your Baimiao account and password."))
		deviceUuid = _getOrCreateDeviceUuid()
		client = cls(
			deviceUuid,
			"",
			cancellationChecker=cancellationChecker,
			requestTimeout=requestTimeout,
		)
		data = client._requestData(
			"POST",
			"/api/user/login",
			jsonPayload={
				"username": account,
				"password": password,
				"type": "mobile" if re.fullmatch(r"[0-9]+", account) else "email",
			},
			authenticationRequest=True,
			retry=False,
		)
		loginToken = data.get("token")
		if not isinstance(loginToken, str) or not loginToken:
			# Translators: An error when Baimiao returns invalid login information.
			raise AuthenticationError(_("Baimiao returned invalid login information."))
		client._checkCancelled()
		_saveSession(deviceUuid, loginToken)

	def recognizeImage(self, imageContent: bytes) -> dict[str, Any]:
		"""Uploads one PNG image and returns Baimiao's OCR response."""

		if not imageContent:
			# Translators: An error when an empty image is passed to Baimiao.
			raise ApiError(_("Invalid image content for Baimiao."))
		self._refreshLogin()
		permission = self._requestData(
			"POST",
			"/api/perm/single",
			jsonPayload={"mode": "single", "version": "v2"},
			retry=False,
		)
		engine = permission.get("engine")
		permissionToken = permission.get("token")
		if (
			not isinstance(engine, str)
			or not _ENGINE_PATTERN.fullmatch(engine)
			or not isinstance(permissionToken, str)
			or not permissionToken
		):
			# Translators: An error when Baimiao does not grant a usable OCR request.
			raise ApiError(_("Baimiao did not provide valid OCR permission."))

		signData = self._requestData(
			"GET",
			"/api/oss/sign",
			params={"mime_type": "image/png"},
		)
		sign = signData.get("result", signData)
		self._validateUploadSign(sign)
		self._uploadImage(cast(dict[str, Any], sign), imageContent)

		imageBase64 = base64.b64encode(imageContent).decode("ascii")
		imageHash = hashlib.sha1(f"data:image/png;base64,{imageBase64}".encode("utf-8")).hexdigest()
		start = self._requestData(
			"POST",
			f"/api/ocr/image/{engine}",
			jsonPayload={
				"batchId": "",
				"total": 1,
				"token": permissionToken,
				"hash": imageHash,
				"fileKey": sign["file_key"],
			},
			retry=False,
		)
		jobStatusId = start.get("jobStatusId")
		if not isinstance(jobStatusId, str) or not jobStatusId:
			# Translators: An error when Baimiao does not start an OCR task.
			raise ApiError(_("Baimiao did not start the OCR task."))
		return self._waitForResult(engine, jobStatusId)

	def _refreshLogin(self) -> None:
		expectedToken = self.loginToken
		try:
			data = self._requestData(
				"POST",
				"/api/user/login/anonymous",
				jsonPayload={},
				retry=False,
			)
		except ApiError as e:
			if not isinstance(e, AuthenticationError) and e.errorCode != 4:
				raise
			self.loginToken = ""
			_replaceStoredLoginToken(self.deviceUuid, expectedToken, "")
			if isinstance(e, AuthenticationError):
				raise
			# Translators: An error when the stored Baimiao login is no longer valid.
			raise AuthenticationError(
				_("Baimiao login has expired. Please log in again."),
				errorCode=e.errorCode,
			) from e
		loginToken = data.get("token")
		user = data.get("user")
		if not isinstance(loginToken, str) or not loginToken or not isinstance(user, dict) or not user:
			self.loginToken = ""
			_replaceStoredLoginToken(self.deviceUuid, expectedToken, "")
			# Translators: An error when the stored Baimiao login is no longer valid.
			raise AuthenticationError(_("Baimiao login has expired. Please log in again."))
		if loginToken != self.loginToken:
			self.loginToken = loginToken
			_replaceStoredLoginToken(self.deviceUuid, expectedToken, loginToken)

	def _waitForResult(self, engine: str, jobStatusId: str) -> dict[str, Any]:
		deadline = time.monotonic() + MAX_POLL_SECONDS
		pollInterval = POLL_INTERVAL_SECONDS
		isFirstPoll = True
		while time.monotonic() < deadline:
			if not isFirstPoll:
				network.sleepWithCancellation(pollInterval, self._cancellationChecker)
				pollInterval = min(pollInterval * 2, MAX_POLL_INTERVAL_SECONDS)
			isFirstPoll = False
			data = self._requestData(
				"GET",
				f"/api/ocr/image/{engine}/status",
				params={"jobStatusId": jobStatusId},
			)
			isEnded = data.get("isEnded")
			if isEnded is False:
				continue
			if isEnded is not True:
				# Translators: An error when Baimiao returns malformed task status data.
				raise ApiError(_("Baimiao returned an invalid OCR task status."))
			result = data.get("ydResp")
			if not isinstance(result, dict):
				# Translators: An error when a completed Baimiao task has no OCR result.
				raise ApiError(_("Baimiao returned an invalid OCR response."))
			result = cast(dict[str, Any], result)
			self._raiseTaskError(result)
			return _normalizeOcrResult(engine, result)
		# Translators: An error when Baimiao does not finish an OCR task in time.
		raise ApiError(_("Baimiao OCR timed out."))

	def _requestData(
		self,
		method: str,
		path: str,
		*,
		params: dict[str, str] | None = None,
		jsonPayload: dict[str, Any] | None = None,
		authenticationRequest: bool = False,
		retry: bool | None = None,
	) -> dict[str, Any]:
		self._checkCancelled()
		kwargs: dict[str, Any] = {
			"headers": self._headers(),
			"allow_redirects": False,
			"timeout": self._requestTimeout,
		}
		if params is not None:
			kwargs["params"] = params
		if jsonPayload is not None:
			kwargs["json"] = jsonPayload
		if self._cancellationChecker:
			kwargs["cancelCheck"] = self._cancellationChecker
		kwargs["retry"] = retry if retry is not None else method.upper() in {"GET", "HEAD"}
		response = network.sendRequest(method, f"{BASE_URL}{path}", **kwargs)
		self._checkCancelled()
		try:
			payloadValue: Any = response.json()
		except ValueError as e:
			# Translators: An error when Baimiao returns data that is not valid JSON.
			raise ApiError(_("Baimiao returned an invalid API response.")) from e
		if not isinstance(payloadValue, dict):
			# Translators: An error when Baimiao returns malformed API data.
			raise ApiError(_("Baimiao returned an invalid API response."))
		payload = cast(dict[str, Any], payloadValue)
		if payload.get("code") != 1:
			message = self._formatApiErrorMessage(payload)
			errorCode = payload.get("code") if isinstance(payload.get("code"), int) else None
			responseData = payload.get("data")
			if (
				errorCode != 4
				and isinstance(responseData, dict)
				and isinstance(responseData.get("respCode"), int)
			):
				errorCode = responseData["respCode"]
			if authenticationRequest:
				# Translators: A Baimiao login error. {message} is returned by the service.
				loginErrorMessage = _("Baimiao login failed: {message}").format(message=message)
				if errorCode == _DEVICE_LIMIT_ERROR_CODE:
					# Translators: Guidance shown when a Baimiao account has reached its device login limit.
					loginErrorMessage = "\n\n".join(
						(
							loginErrorMessage,
							_(
								"In the Baimiao mobile app, log out any devices you no longer use, then try again."
							),
						),
					)
				raise AuthenticationError(loginErrorMessage, errorCode=errorCode)
			# Translators: A Baimiao Web API error. {message} is returned by the service.
			raise ApiError(
				_("Baimiao API error: {message}").format(message=message),
				errorCode=errorCode,
			)
		data = payload.get("data")
		if not isinstance(data, dict):
			# Translators: An error when Baimiao returns malformed API data.
			raise ApiError(_("Baimiao returned an invalid API response."))
		return cast(dict[str, Any], data)

	@staticmethod
	def _formatApiErrorMessage(payload: dict[str, Any]) -> str:
		message = payload.get("msg") or payload.get("message")
		data = payload.get("data")
		respCode = data.get("respCode") if isinstance(data, dict) else None
		if message:
			message = str(message)
			return f"{message} ({respCode})" if respCode is not None else message
		if respCode is not None:
			# Translators: A Baimiao error without a message. {code} is a service error code.
			return _("Baimiao returned service error code {code}.").format(code=respCode)
		code = payload.get("code")
		if code is not None:
			# Translators: A Baimiao error without a message. {code} is an API error code.
			return _("Baimiao returned API error code {code}.").format(code=code)
		# Translators: A Baimiao error response without a message or code.
		return _("Baimiao returned an unknown API error.")

	@staticmethod
	def _raiseTaskError(result: dict[str, Any]) -> None:
		errorCode = result.get("errorCode")
		if errorCode in (None, 0, "0"):
			errorCode = result.get("error_code")
		if errorCode not in (None, 0, "0"):
			message = result.get("errorMsg") or result.get("error_msg") or result.get("message")
			if not message:
				# Translators: An error when a Baimiao OCR task reports a failure.
				message = _("Baimiao OCR task failed.")
			raise ApiError(str(message), errorCode=_toInteger(errorCode))
		if result.get("isSuccess") is False:
			# Translators: An error when a Baimiao OCR task reports a failure.
			raise ApiError(_("Baimiao OCR task failed."))
		if result.get("sid") is not None and result.get("code") not in (None, 0, "0", 1, "1"):
			message = result.get("msg") or result.get("message")
			if not message:
				# Translators: An error when a Baimiao OCR task reports a failure.
				message = _("Baimiao OCR task failed.")
			raise ApiError(str(message))

	def _headers(self) -> dict[str, str]:
		return {
			"Accept": "application/json, text/plain, */*",
			"Origin": BASE_URL,
			"Referer": f"{BASE_URL}/",
			"User-Agent": USER_AGENT,
			"X-Auth-Token": self.loginToken,
			"X-Auth-Uuid": self.deviceUuid,
		}

	def _uploadImage(self, sign: dict[str, Any], imageContent: bytes) -> None:
		host = str(sign["host"])
		parts = urlsplit(host)
		if parts.scheme != "https" or parts.username or parts.password:
			# Translators: An error when Baimiao returns an unsafe upload endpoint.
			raise ApiError(_("Baimiao returned an invalid image upload endpoint."))
		data = {
			"success_action_status": "200",
			"policy": str(sign["policy"]),
			"x-oss-signature": str(sign["signature"]),
			"x-oss-signature-version": "OSS4-HMAC-SHA256",
			"x-oss-credential": str(sign["x_oss_credential"]),
			"x-oss-date": str(sign["x_oss_date"]),
			"key": str(sign["file_key"]),
			"x-oss-security-token": str(sign["security_token"]),
		}
		self._checkCancelled()
		network.sendRequest(
			"POST",
			host,
			headers={"User-Agent": "VisAware"},
			data=data,
			files={"file": ("image.png", imageContent, "image/png")},
			allow_redirects=False,
			timeout=self._uploadTimeout,
			cancelCheck=self._cancellationChecker,
			retry=False,
		)
		self._checkCancelled()

	@staticmethod
	def _validateUploadSign(sign: Any) -> None:
		if not isinstance(sign, dict) or any(
			not isinstance(sign.get(key), str) or not sign[key] for key in _UPLOAD_SIGN_FIELDS
		):
			# Translators: An error when Baimiao returns an incomplete upload signature.
			raise ApiError(_("Baimiao returned an invalid image upload signature."))

	def _checkCancelled(self) -> None:
		if self._cancellationChecker:
			self._cancellationChecker()


def _normalizeOcrResult(engine: str, result: dict[str, Any]) -> dict[str, Any]:
	if isinstance(result.get("words_result"), list):
		return result
	if engine == "plus" and result.get("sid"):
		result = result.get("data")
	elif engine == "yield":
		return _normalizeYieldResult(result)
	elif engine == "xfs":
		return _normalizeXfsResult(result)
	if isinstance(result, dict) and isinstance(result.get("words_result"), list):
		return cast(dict[str, Any], result)
	raise _invalidOcrResponse()


def _normalizeYieldResult(result: dict[str, Any]) -> dict[str, Any]:
	providerResult = result.get("Result")
	if not isinstance(providerResult, dict) or not isinstance(providerResult.get("regions"), list):
		raise _invalidOcrResponse()
	words: list[dict[str, Any]] = []
	for region in providerResult["regions"]:
		if not isinstance(region, dict) or not isinstance(region.get("lines"), list):
			raise _invalidOcrResponse()
		for line in region["lines"]:
			if not isinstance(line, dict):
				raise _invalidOcrResponse()
			boundingBox = line.get("boundingBox")
			coordinates = boundingBox.split(",") if isinstance(boundingBox, str) else None
			words.append(_wordFromCoordinates(line.get("text"), coordinates))
	return {"words_result": words}


def _normalizeXfsResult(result: dict[str, Any]) -> dict[str, Any]:
	code = result.get("code")
	if code not in (None, 0, "0"):
		message = result.get("msg") or result.get("message") or _("Baimiao OCR task failed.")
		raise ApiError(str(message), errorCode=code if isinstance(code, int) else None)
	providerResult = result.get("data")
	if not isinstance(providerResult, dict) or not isinstance(providerResult.get("lines"), list):
		raise _invalidOcrResponse()
	lines = cast(list[Any], providerResult["lines"])
	if not all(isinstance(line, dict) for line in lines):
		raise _invalidOcrResponse()
	return {
		"words_result": [_wordFromCoordinates(line.get("text"), line.get("position")) for line in lines],
	}


def _invalidOcrResponse() -> ApiError:
	# Translators: An error when a completed Baimiao task has no usable OCR result.
	return ApiError(_("Baimiao returned an invalid OCR response."))


def _wordFromCoordinates(text: Any, coordinateValues: Any) -> dict[str, Any]:
	word: dict[str, Any] = {"words": str(text or "")}
	if not isinstance(coordinateValues, list) or len(coordinateValues) != 8:
		return word
	try:
		coordinates = [float(value) for value in coordinateValues]
	except (OverflowError, TypeError, ValueError):
		return word
	if not all(math.isfinite(value) for value in coordinates):
		return word
	width = math.hypot(coordinates[2] - coordinates[0], coordinates[3] - coordinates[1])
	height = math.hypot(coordinates[6] - coordinates[0], coordinates[7] - coordinates[1])
	if not math.isfinite(width) or not math.isfinite(height):
		return word
	vertices = [
		{"x": coordinates[index], "y": coordinates[index + 1]} for index in range(0, len(coordinates), 2)
	]
	word.update(
		location={
			"left": coordinates[0],
			"top": coordinates[1],
			"width": width,
			"height": height,
		},
		vertexes_location=vertices,
	)
	return word


def extractText(apiResult: dict[str, Any]) -> str:
	"""Extracts text from one Baimiao Web OCR response."""

	lines, textInServiceOrder, hasUnpositionedText = _parseLineResult(apiResult)
	if hasUnpositionedText:
		return "\n".join(textInServiceOrder)
	textLines = [" ".join(word["text"] for word in line) for line in lines]
	return "\n".join(textLines)


def toLineResult(apiResult: dict[str, Any]) -> list[list[dict[str, Any]]]:
	"""Converts Baimiao rectangles into NVDA line/word data."""

	lines, _textInServiceOrder, hasUnpositionedText = _parseLineResult(apiResult)
	return [] if hasUnpositionedText else lines


def _parseLineResult(
	apiResult: dict[str, Any],
) -> tuple[list[list[dict[str, Any]]], list[str], bool]:
	wordsValue = apiResult.get("words_result")
	if not isinstance(wordsValue, list):
		return [], [], False
	wordsResult = cast(list[Any], wordsValue)
	words: list[dict[str, Any]] = []
	textInServiceOrder: list[str] = []
	hasUnpositionedText = False
	for itemValue in wordsResult:
		if not isinstance(itemValue, dict):
			continue
		item = cast(dict[str, Any], itemValue)
		text = str(item.get("words") or "").strip()
		if not text:
			continue
		textInServiceOrder.append(text)
		location = item.get("location")
		if not isinstance(location, dict):
			location = _locationFromVertices(item.get("vertexes_location"))
		if not isinstance(location, dict):
			hasUnpositionedText = True
			continue
		location = cast(dict[str, Any], location)
		left = _toInteger(location.get("left"))
		top = _toInteger(location.get("top"))
		width = _toInteger(location.get("width"))
		height = _toInteger(location.get("height"))
		if left is None or top is None or width is None or height is None:
			hasUnpositionedText = True
			continue
		if width <= 0 or height <= 0:
			hasUnpositionedText = True
			continue
		words.append(
			{
				"x": max(0, left),
				"y": max(0, top),
				"width": width,
				"height": height,
				"text": text,
			},
		)
	return _groupWordsIntoLines(words), textInServiceOrder, hasUnpositionedText


def _groupWordsIntoLines(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
	if _looksLikeVerticalCjkLayout(words):
		return _groupWordsIntoColumns(words)
	lines: list[list[dict[str, Any]]] = []
	for word in words:
		# Non-adjacent items may belong to another column, so preserve Baimiao's reading boundaries.
		if lines and _belongsToVisualLine(word, lines[-1]):
			lines[-1].append(word)
		else:
			lines.append([word])
	return [_mergeUnspacedWords(sorted(line, key=lambda word: word["x"])) for line in lines]


def _looksLikeVerticalCjkLayout(words: list[dict[str, Any]]) -> bool:
	# ponytail: Whole-result heuristic; prefer provider direction metadata if Baimiao exposes it.
	if len(words) < 2 or not all(word["height"] >= word["width"] for word in words):
		return False
	if not any(any(_isUnspacedCharacter(character) for character in word["text"]) for word in words):
		return False
	wordPairs = list(zip(words, words[1:]))
	verticalConnections = sum(_belongsToVisualColumn(right, [left]) for left, right in wordPairs)
	horizontalConnections = sum(_belongsToVisualLine(right, [left]) for left, right in wordPairs)
	return (verticalConnections > 0 and verticalConnections >= horizontalConnections) or all(
		word["height"] > word["width"] * 2 for word in words
	)


def _groupWordsIntoColumns(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
	columns: list[list[dict[str, Any]]] = []
	for word in words:
		if columns and _belongsToVisualColumn(word, columns[-1]):
			columns[-1].append(word)
		else:
			columns.append([word])
	columns.sort(
		key=lambda column: sum(word["x"] + word["width"] / 2 for word in column) / len(column),
		reverse=True,
	)
	return [_mergeUnspacedWords(sorted(column, key=lambda word: word["y"])) for column in columns]


def _belongsToVisualLine(word: dict[str, Any], line: list[dict[str, Any]]) -> bool:
	lineTop = sum(item["y"] for item in line) / len(line)
	lineBottom = sum(item["y"] + item["height"] for item in line) / len(line)
	lineLeft = min(item["x"] for item in line)
	lineRight = max(item["x"] + item["width"] for item in line)
	wordBottom = word["y"] + word["height"]
	overlap = min(wordBottom, lineBottom) - max(word["y"], lineTop)
	horizontalGap = max(lineLeft - word["x"] - word["width"], word["x"] - lineRight, 0)
	return overlap * 2 >= min(word["height"], lineBottom - lineTop) and horizontalGap <= (
		max(word["height"], lineBottom - lineTop) * _MAX_FRAGMENT_GAP_FACTOR
	)


def _belongsToVisualColumn(word: dict[str, Any], column: list[dict[str, Any]]) -> bool:
	columnLeft = sum(item["x"] for item in column) / len(column)
	columnRight = sum(item["x"] + item["width"] for item in column) / len(column)
	columnTop = min(item["y"] for item in column)
	columnBottom = max(item["y"] + item["height"] for item in column)
	wordRight = word["x"] + word["width"]
	overlap = min(wordRight, columnRight) - max(word["x"], columnLeft)
	verticalGap = max(columnTop - word["y"] - word["height"], word["y"] - columnBottom, 0)
	return overlap * 2 >= min(word["width"], columnRight - columnLeft) and verticalGap <= (
		max(word["width"], columnRight - columnLeft) * _MAX_FRAGMENT_GAP_FACTOR
	)


def _mergeUnspacedWords(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
	mergedWords: list[dict[str, Any]] = []
	for word in words:
		if not mergedWords or _requiresSpace(mergedWords[-1]["text"], word["text"]):
			mergedWords.append(word)
			continue
		previous = mergedWords[-1]
		left = min(previous["x"], word["x"])
		top = min(previous["y"], word["y"])
		right = max(previous["x"] + previous["width"], word["x"] + word["width"])
		bottom = max(previous["y"] + previous["height"], word["y"] + word["height"])
		previous.update(
			text=previous["text"] + word["text"],
			x=left,
			y=top,
			width=right - left,
			height=bottom - top,
		)
	return mergedWords


def _requiresSpace(leftText: str, rightText: str) -> bool:
	leftCharacter = leftText[-1]
	rightCharacter = rightText[0]
	return not (
		_isUnspacedCharacter(leftCharacter)
		or _isUnspacedCharacter(rightCharacter)
		or unicodedata.category(rightCharacter).startswith("P")
		or unicodedata.category(leftCharacter) in {"Ps", "Pi"}
		or leftCharacter in "'/-"
	)


def _isUnspacedCharacter(character: str) -> bool:
	return unicodedata.name(character, "").startswith(("BOPOMOFO", "CJK", "HIRAGANA", "KATAKANA")) or (
		unicodedata.category(character).startswith("P")
		and unicodedata.east_asian_width(character) in {"F", "W"}
	)


def _locationFromVertices(vertices: Any) -> dict[str, int] | None:
	if not isinstance(vertices, list) or len(vertices) < 3:
		return None
	points: list[tuple[int, int]] = []
	for pointValue in cast(list[Any], vertices):
		if not isinstance(pointValue, dict):
			return None
		point = cast(dict[str, Any], pointValue)
		x = _toInteger(point.get("x"))
		y = _toInteger(point.get("y"))
		if x is None or y is None:
			return None
		points.append((x, y))
	xValues, yValues = zip(*points)
	return {
		"left": min(xValues),
		"top": min(yValues),
		"width": max(xValues) - min(xValues),
		"height": max(yValues) - min(yValues),
	}


def _toInteger(value: Any) -> int | None:
	try:
		return int(float(value))
	except (OverflowError, TypeError, ValueError):
		return None
