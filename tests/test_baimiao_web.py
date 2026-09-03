from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
import unittest


class _OCRError(Exception):
	pass


class _ApiError(_OCRError):
	def __init__(self, message: str, errorCode: int | None = None):
		super().__init__(message)
		self.errorCode = errorCode


class _AuthenticationError(_ApiError):
	pass


class _Response:
	def __init__(self, payload: object | None = None):
		self.payload = payload

	def json(self) -> object:
		return self.payload


class _Config(dict):
	def __init__(self):
		super().__init__({"OCR": {"baimiaoOCR": {}}})
		self.saveCount = 0

	def save(self) -> None:
		self.saveCount += 1


def _loadModule(configPath: Path):
	builtins._ = lambda value: value

	addonHandler = types.ModuleType("addonHandler")
	addonHandler.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandler

	configModule = types.ModuleType("config")
	configModule.conf = _Config()
	sys.modules["config"] = configModule

	fileUtilsModule = types.ModuleType("fileUtils")
	fileUtilsModule.FaultTolerantFile = lambda name: open(name, "w+b")
	sys.modules["fileUtils"] = fileUtilsModule

	logHandler = types.ModuleType("logHandler")
	logHandler.log = types.SimpleNamespace(warning=lambda *_args, **_kwargs: None)
	sys.modules["logHandler"] = logHandler

	nvdaStateModule = types.ModuleType("NVDAState")
	nvdaStateModule.shouldWriteToDisk = lambda: True
	nvdaStateModule.WritePaths = types.SimpleNamespace(configDir=str(configPath))
	sys.modules["NVDAState"] = nvdaStateModule

	for moduleName in (
		"addon",
		"addon.globalPlugins",
		"addon.globalPlugins.visAware",
		"addon.globalPlugins.visAware.contentRecognizers",
	):
		module = types.ModuleType(moduleName)
		module.__path__ = []  # type: ignore[attr-defined]
		sys.modules[moduleName] = module

	exceptionsModule = types.ModuleType("addon.globalPlugins.visAware.exceptions")
	exceptionsModule.ApiError = _ApiError
	exceptionsModule.AuthenticationError = _AuthenticationError
	exceptionsModule.OCRError = _OCRError
	sys.modules[exceptionsModule.__name__] = exceptionsModule

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")
	sys.modules[networkModule.__name__] = networkModule
	sys.modules["addon.globalPlugins.visAware"].network = networkModule

	secureStorageModule = types.ModuleType("addon.globalPlugins.visAware.secure_storage")
	secureStorageModule.SecureStorageError = type("SecureStorageError", (Exception,), {})
	secureStorageModule.protectString = lambda value: value
	secureStorageModule.unprotectString = lambda value: value
	sys.modules[secureStorageModule.__name__] = secureStorageModule

	modulePath = (
		Path(__file__).resolve().parents[1]
		/ "addon"
		/ "globalPlugins"
		/ "visAware"
		/ "contentRecognizers"
		/ "_baimiaoWeb.py"
	)
	spec = importlib.util.spec_from_file_location(
		"addon.globalPlugins.visAware.contentRecognizers._baimiaoWeb",
		modulePath,
	)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load _baimiaoWeb.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class BaimiaoWebTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.sessionDirectory = TemporaryDirectory()
		self.addCleanup(self.sessionDirectory.cleanup)
		self.module = _loadModule(Path(self.sessionDirectory.name))

	def test_currentOcrWorkflowUsesOssFileKeyAndPolling(self) -> None:
		calls = []
		waits = []
		responses = [
			_Response({"code": 1, "data": {"token": "login-token", "user": {"id": 1}}}),
			_Response({"code": 1, "data": {"engine": "plus", "token": "permission-token"}}),
			_Response(
				{
					"code": 1,
					"data": {
						"result": {
							"host": "https://oss.example.test",
							"file_key": "file-key",
							"policy": "policy",
							"signature": "signature",
							"x_oss_credential": "credential",
							"x_oss_date": "date",
							"security_token": "security-token",
						},
					},
				},
			),
			_Response(),
			_Response({"code": 1, "data": {"jobStatusId": "job-id"}}),
			_Response({"code": 1, "data": {"isEnded": False}}),
			_Response(
				{
					"code": 1,
					"data": {
						"isEnded": True,
						"ydResp": {
							"words_result": [
								{
									"words": "text",
									"location": {"left": 1, "top": 2, "width": 3, "height": 4},
								},
							],
						},
					},
				},
			),
		]

		def sendRequest(*args, **kwargs):
			calls.append((args, kwargs))
			return responses.pop(0)

		self.module.network.sendRequest = sendRequest
		self.module.network.sleepWithCancellation = lambda seconds, checker: waits.append(seconds)
		client = self.module.BaimiaoWebClient(
			"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
			"login-token",
			cancellationChecker=lambda: None,
		)

		result = client.recognizeImage(b"image")

		self.assertEqual(result["words_result"][0]["words"], "text")
		self.assertEqual(waits, [0.5])
		self.assertEqual(
			[call[0][0] for call in calls], ["POST", "POST", "GET", "POST", "POST", "GET", "GET"]
		)
		self.assertEqual(
			[calls[index][1]["retry"] for index in range(len(calls))],
			[False, False, True, False, False, True, True],
		)
		self.assertEqual(calls[3][0][1], "https://oss.example.test")
		self.assertEqual(calls[3][1]["files"]["file"][1], b"image")
		startPayload = calls[4][1]["json"]
		self.assertEqual(startPayload["fileKey"], "file-key")
		self.assertNotIn("dataUrl", startPayload)
		expectedHash = hashlib.sha1(b"data:image/png;base64,aW1hZ2U=").hexdigest()
		self.assertEqual(startPayload["hash"], expectedHash)

	def testFormulaWorkflowUsesLatexPermissionAndEndpoints(self) -> None:
		calls = []
		responses = [
			_Response({"code": 1, "data": {"token": "login-token", "user": {"id": 1}}}),
			_Response({"code": 1, "data": {"engine": "plus", "token": "permission-token"}}),
			_Response(
				{
					"code": 1,
					"data": {
						"result": {
							"host": "https://oss.example.test",
							"file_key": "file-key",
							"policy": "policy",
							"signature": "signature",
							"x_oss_credential": "credential",
							"x_oss_date": "date",
							"security_token": "security-token",
						},
					},
				},
			),
			_Response(),
			_Response({"code": 1, "data": {"jobStatusId": "job-id"}}),
			_Response(
				{
					"code": 1,
					"data": {
						"isEnded": True,
						"ydResp": {
							"code": 0,
							"data": {"latex": r"x^2+y^2=z^2"},
							"sid": "provider-id",
						},
					},
				},
			),
		]

		def sendRequest(*args, **kwargs):
			calls.append((args, kwargs))
			return responses.pop(0)

		self.module.network.sendRequest = sendRequest
		client = self.module.BaimiaoWebClient(
			"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
			"login-token",
		)

		result = client.recognizeFormula(b"image")

		self.assertEqual(result, {"latex": r"x^2+y^2=z^2"})
		self.assertEqual(
			calls[1][1]["json"],
			{"mode": "single", "type": "latex", "version": "v2"},
		)
		self.assertTrue(calls[4][0][1].endswith("/api/ocr/latex/plus"))
		self.assertTrue(calls[5][0][1].endswith("/api/ocr/latex/plus/status"))

	def testXfsFormulaResponseIsNormalizedAndErrorsArePreserved(self) -> None:
		self.assertEqual(
			self.module._normalizeFormulaResult(
				"xfs",
				{"code": 0, "data": {"latex": r"\frac{a}{b}"}},
			),
			{"latex": r"\frac{a}{b}"},
		)

		with self.assertRaises(_ApiError) as error:
			self.module._normalizeFormulaResult(
				"xfs",
				{"code": 40304, "message": "invalid image"},
			)

		self.assertEqual(error.exception.errorCode, 40304)
		self.assertEqual(str(error.exception), "invalid image")

	def testFormulaResultsBecomeMarkdownWithTextPreserved(self) -> None:
		self.assertEqual(
			self.module.extractFormulaMarkdown({"latex": r"x^2+y^2=z^2"}),
			r"$x^2+y^2=z^2$",
		)
		self.assertEqual(
			self.module.extractFormulaMarkdown(
				{
					"region": [
						{"type": "text", "recog": {"content": "求下式的值"}},
						{
							"type": "text",
							"recog": {
								"content": " ifly-latex-begin \\frac{a}{b} ifly-latex-end ",
							},
						},
						{"type": "image", "recog": {"content": "ignored"}},
					],
				},
			),
			"求下式的值\n\n$ \\frac{a}{b} $",
		)

	def test_loginPersistsNoAccountOrPassword(self) -> None:
		protectedValues = []
		calls = []

		def protectString(value: str) -> str:
			protectedValues.append(json.loads(value))
			return "encrypted-session"

		def sendRequest(*args, **kwargs):
			calls.append((args, kwargs))
			return _Response({"code": 1, "data": {"token": "login-token"}})

		self.module.protectString = protectString
		self.module.network.sendRequest = sendRequest
		self.module.BaimiaoWebClient.login("user@example.test", "secret-password")

		self.assertEqual(len(protectedValues), 2)
		self.assertTrue(all(set(value) == {"uuid", "token"} for value in protectedValues))
		self.assertTrue(all("secret-password" not in json.dumps(value) for value in protectedValues))
		self.assertEqual(protectedValues[-1]["token"], "login-token")
		self.assertEqual(calls[0][1]["json"]["type"], "email")
		self.assertEqual(sys.modules["config"].conf.saveCount, 0)
		self.assertEqual(
			(Path(self.sessionDirectory.name) / self.module.SESSION_FILE_NAME).read_text(encoding="ascii"),
			"encrypted-session",
		)

	def testLoginCancellationBeforePersistenceDoesNotStoreToken(self) -> None:
		cancelChecks = 0

		def checkCancelled() -> None:
			nonlocal cancelChecks
			cancelChecks += 1
			if cancelChecks == 3:
				raise _OCRError("cancelled")

		self.module.network.sendRequest = lambda *args, **kwargs: _Response(
			{"code": 1, "data": {"token": "login-token"}},
		)

		with self.assertRaises(_OCRError):
			self.module.BaimiaoWebClient.login(
				"user@example.test",
				"secret-password",
				cancellationChecker=checkCancelled,
			)

		self.assertEqual(cancelChecks, 3)
		self.assertEqual(self.module._loadSession()["token"], "")

	def testLegacyConfigSessionIsReadUntilIndependentSessionIsSaved(self) -> None:
		deviceUuid = "6c40ae69-d09d-4a9a-8ff1-f86e0139a283"
		legacySession = json.dumps({"uuid": deviceUuid, "token": "legacy-token"})
		sys.modules["config"].conf["OCR"]["baimiaoOCR"]["encryptedSession"] = legacySession

		self.assertEqual(self.module._loadSession(), {"uuid": deviceUuid, "token": "legacy-token"})

		self.module._saveSession(deviceUuid, "new-token")

		self.assertEqual(self.module._loadSession(), {"uuid": deviceUuid, "token": "new-token"})
		self.assertEqual(sys.modules["config"].conf.saveCount, 0)

	def testLoginErrorPreservesServiceCode(self) -> None:
		calls = []

		def sendRequest(*args, **kwargs):
			calls.append((args, kwargs))
			return _Response({"code": 0, "data": {"respCode": 400401}, "msg": "device limit"})

		self.module.network.sendRequest = sendRequest

		with self.assertRaises(_AuthenticationError) as error:
			self.module.BaimiaoWebClient.login("user@example.test", "secret-password")

		self.assertEqual(error.exception.errorCode, 400401)
		self.assertIn("400401", str(error.exception))
		self.assertIn("log out any devices you no longer use", str(error.exception))
		self.assertFalse(calls[0][1]["retry"])

	def testCoordinatesAreGroupedWithinServiceReadingOrder(self) -> None:
		result = {
			"words_result": [
				{"words": "two", "location": {"left": 50, "top": 21, "width": 30, "height": 20}},
				{"words": "one", "location": {"left": 10, "top": 20, "width": 30, "height": 20}},
				{"words": "four", "location": {"left": 50, "top": 61, "width": 30, "height": 20}},
				{"words": "three", "location": {"left": 10, "top": 60, "width": 30, "height": 20}},
			],
		}

		self.assertEqual(self.module.extractText(result), "one two\nthree four")
		self.assertEqual(
			self.module.toLineResult(result),
			[
				[
					{"x": 10, "y": 20, "width": 30, "height": 20, "text": "one"},
					{"x": 50, "y": 21, "width": 30, "height": 20, "text": "two"},
				],
				[
					{"x": 10, "y": 60, "width": 30, "height": 20, "text": "three"},
					{"x": 50, "y": 61, "width": 30, "height": 20, "text": "four"},
				],
			],
		)

	def testDistantColumnsRemainInServiceReadingOrder(self) -> None:
		result = {
			"words_result": [
				{"words": "left one", "location": {"left": 10, "top": 20, "width": 60, "height": 20}},
				{"words": "left two", "location": {"left": 10, "top": 60, "width": 60, "height": 20}},
				{"words": "right one", "location": {"left": 400, "top": 20, "width": 70, "height": 20}},
				{"words": "right two", "location": {"left": 400, "top": 60, "width": 70, "height": 20}},
			],
		}

		self.assertEqual(
			self.module.extractText(result),
			"left one\nleft two\nright one\nright two",
		)
		self.assertEqual(
			[[word["text"] for word in line] for line in self.module.toLineResult(result)],
			[["left one"], ["left two"], ["right one"], ["right two"]],
		)

	def testDistantSameRowFragmentsRemainSeparate(self) -> None:
		result = {
			"words_result": [
				{"words": "left", "location": {"left": 10, "top": 20, "width": 40, "height": 20}},
				{"words": "right", "location": {"left": 400, "top": 20, "width": 50, "height": 20}},
			],
		}

		self.assertEqual(self.module.extractText(result), "left\nright")
		self.assertEqual(len(self.module.toLineResult(result)), 2)

	def testCjkAndPunctuationFragmentsDoNotGainSpaces(self) -> None:
		result = {
			"words_result": [
				{"words": "\u4f60", "location": {"left": 10, "top": 20, "width": 20, "height": 20}},
				{"words": "\u597d", "location": {"left": 31, "top": 20, "width": 20, "height": 20}},
				{"words": "!", "location": {"left": 52, "top": 20, "width": 8, "height": 20}},
				{"words": "hello", "location": {"left": 61, "top": 20, "width": 50, "height": 20}},
				{"words": "world", "location": {"left": 112, "top": 20, "width": 50, "height": 20}},
			],
		}

		self.assertEqual(
			self.module.extractText({"words_result": result["words_result"][:3]}), "\u4f60\u597d!"
		)
		self.assertEqual(self.module.extractText(result), "\u4f60\u597d! hello world")
		self.assertEqual(
			self.module.toLineResult(result),
			[
				[
					{"x": 10, "y": 20, "width": 50, "height": 20, "text": "\u4f60\u597d!"},
					{"x": 61, "y": 20, "width": 50, "height": 20, "text": "hello"},
					{"x": 112, "y": 20, "width": 50, "height": 20, "text": "world"},
				],
			],
		)

	def testVerticalCjkCoordinatesUseTopToBottomRightToLeftOrder(self) -> None:
		result = {
			"words_result": [
				{"words": "\u5de6", "location": {"left": 20, "top": 10, "width": 20, "height": 20}},
				{"words": "\u5217", "location": {"left": 20, "top": 32, "width": 20, "height": 20}},
				{"words": "\u53f3\u5217", "location": {"left": 80, "top": 10, "width": 20, "height": 60}},
			],
		}

		self.assertEqual(self.module.extractText(result), "\u53f3\u5217\n\u5de6\u5217")
		self.assertEqual(
			self.module.toLineResult(result),
			[
				[{"x": 80, "y": 10, "width": 20, "height": 60, "text": "\u53f3\u5217"}],
				[{"x": 20, "y": 10, "width": 20, "height": 42, "text": "\u5de6\u5217"}],
			],
		)

	def testInvalidCoordinateFallsBackToCompleteText(self) -> None:
		result = {
			"words_result": [
				{"words": "one", "location": {"left": 10, "top": 20, "width": 30, "height": 40}},
				{"words": "invalid", "location": {"left": "x"}},
				{
					"words": "two",
					"vertexes_location": [
						{"x": 50, "y": 60},
						{"x": 90, "y": 60},
						{"x": 90, "y": 80},
						{"x": 50, "y": 80},
					],
				},
			],
		}

		self.assertEqual(self.module.extractText(result), "one\ninvalid\ntwo")
		self.assertEqual(self.module.toLineResult(result), [])

	def testUnsafeUploadEndpointIsRejected(self) -> None:
		client = self.module.BaimiaoWebClient(
			"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
			"login-token",
		)
		sign = {
			"host": "http://oss.example.test",
			"file_key": "file-key",
			"policy": "policy",
			"signature": "signature",
			"x_oss_credential": "credential",
			"x_oss_date": "date",
			"security_token": "security-token",
		}

		with self.assertRaises(_ApiError):
			client._uploadImage(sign, b"image")

	def testAnonymousApiFailureClearsStoredSession(self) -> None:
		self.module._saveSession("6c40ae69-d09d-4a9a-8ff1-f86e0139a283", "login-token")
		self.module.network.sendRequest = lambda *args, **kwargs: _Response(
			{"code": 4, "data": {"respCode": 500}, "msg": ""},
		)
		client = self.module.BaimiaoWebClient(
			"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
			"login-token",
		)

		with self.assertRaises(_AuthenticationError) as error:
			client._refreshLogin()

		self.assertEqual(client.loginToken, "")
		self.assertEqual(self.module._loadSession()["token"], "")
		self.assertEqual(error.exception.errorCode, 4)

	def testAnonymousTemporaryApiFailurePreservesStoredSession(self) -> None:
		self.module._saveSession("6c40ae69-d09d-4a9a-8ff1-f86e0139a283", "login-token")
		self.module.network.sendRequest = lambda *args, **kwargs: _Response(
			{"code": 2, "data": {}, "msg": "temporarily unavailable"},
		)
		client = self.module.BaimiaoWebClient(
			"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
			"login-token",
		)

		with self.assertRaises(_ApiError) as error:
			client._refreshLogin()

		self.assertNotIsInstance(error.exception, _AuthenticationError)
		self.assertEqual(client.loginToken, "login-token")
		self.assertEqual(self.module._loadSession()["token"], "login-token")

	def testAnonymousSuccessResponseClearsStoredSession(self) -> None:
		self.module._saveSession("6c40ae69-d09d-4a9a-8ff1-f86e0139a283", "login-token")
		self.module.network.sendRequest = lambda *args, **kwargs: _Response(
			{"code": 1, "data": {"token": "anonymous-token", "user": None}},
		)
		client = self.module.BaimiaoWebClient(
			"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
			"login-token",
		)

		with self.assertRaises(_AuthenticationError):
			client._refreshLogin()

		self.assertEqual(client.loginToken, "")
		self.assertEqual(self.module._loadSession()["token"], "")

	def testStaleRefreshCannotModifyNewSession(self) -> None:
		deviceUuid = "6c40ae69-d09d-4a9a-8ff1-f86e0139a283"
		responses = (
			{"code": 1, "data": {"token": "refreshed-old-token", "user": {"id": 1}}},
			{"code": 4, "data": {}, "msg": "login required"},
		)
		for response in responses:
			with self.subTest(code=response["code"]):
				self.module._saveSession(deviceUuid, "old-token")
				client = self.module.BaimiaoWebClient(deviceUuid, "old-token")

				def sendRequest(*args, **kwargs):
					self.module._saveSession(deviceUuid, "new-token")
					return _Response(response)

				self.module.network.sendRequest = sendRequest
				if response["code"] == 4:
					with self.assertRaises(_AuthenticationError):
						client._refreshLogin()
				else:
					client._refreshLogin()

				self.assertEqual(self.module._loadSession()["token"], "new-token")

	def testHttpAuthenticationFailureClearsStoredSession(self) -> None:
		self.module._saveSession("6c40ae69-d09d-4a9a-8ff1-f86e0139a283", "login-token")
		self.module.network.sendRequest = lambda *args, **kwargs: (_ for _ in ()).throw(
			_AuthenticationError("expired")
		)
		client = self.module.BaimiaoWebClient(
			"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
			"login-token",
		)

		with self.assertRaises(_AuthenticationError):
			client._refreshLogin()

		self.assertEqual(client.loginToken, "")
		self.assertEqual(self.module._loadSession()["token"], "")

	def testCompletedTaskErrorIsRaisedImmediately(self) -> None:
		self.module.network.sendRequest = lambda *args, **kwargs: _Response(
			{
				"code": 1,
				"data": {
					"isEnded": True,
					"ydResp": {"errorCode": 123, "errorMsg": "quota exceeded"},
				},
			}
		)
		client = self.module.BaimiaoWebClient(
			"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
			"login-token",
		)

		with self.assertRaises(_ApiError) as error:
			client._waitForResult("plus", "job-id")

		self.assertIn("quota exceeded", str(error.exception))

	def testBetaTaskErrorIsRaisedImmediately(self) -> None:
		self.module.network.sendRequest = lambda *args, **kwargs: _Response(
			{
				"code": 1,
				"data": {
					"isEnded": True,
					"ydResp": {"error_code": "123", "error_msg": "beta quota exceeded"},
				},
			},
		)
		client = self.module.BaimiaoWebClient(
			"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
			"login-token",
		)

		with self.assertRaises(_ApiError) as error:
			client._waitForResult("beta", "job-id")

		self.assertEqual(error.exception.errorCode, 123)
		self.assertIn("beta quota exceeded", str(error.exception))

	def testProviderResponsesAreNormalized(self) -> None:
		responses = {
			"yield": {
				"errorCode": 0,
				"Result": {
					"regions": [
						{
							"lines": [
								{
									"text": "yield text",
									"boundingBox": "1,2,11,2,11,7,1,7",
								},
							],
						},
					],
				},
			},
			"plus": {
				"sid": "request-id",
				"code": 0,
				"data": {
					"words_result": [
						{
							"words": "plus text",
							"location": {"left": 1, "top": 2, "width": 10, "height": 5},
						},
					],
				},
			},
			"xfs": {
				"code": 0,
				"data": {"lines": [{"text": "xfs text", "position": [1, 2, 11, 2, 11, 7, 1, 7]}]},
			},
		}
		for engine, response in responses.items():
			with self.subTest(engine=engine):
				self.module.network.sendRequest = lambda *args, response=response, **kwargs: _Response(
					{"code": 1, "data": {"isEnded": True, "ydResp": response}},
				)
				client = self.module.BaimiaoWebClient(
					"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
					"login-token",
				)

				result = client._waitForResult(engine, "job-id")

				self.assertEqual(self.module.extractText(result), f"{engine} text")
				self.assertEqual(result["words_result"][0]["location"]["width"], 10)

	def testPollingAllowsServiceProcessingWindow(self) -> None:
		times = iter((100, 219.9))
		originalMonotonic = self.module.time.monotonic
		self.addCleanup(setattr, self.module.time, "monotonic", originalMonotonic)
		self.module.time.monotonic = lambda: next(times)
		self.module.network.sendRequest = lambda *args, **kwargs: _Response(
			{
				"code": 1,
				"data": {"isEnded": True, "ydResp": {"words_result": []}},
			},
		)
		client = self.module.BaimiaoWebClient(
			"6c40ae69-d09d-4a9a-8ff1-f86e0139a283",
			"login-token",
		)

		self.assertEqual(client._waitForResult("plus", "job-id"), {"words_result": []})

	def testLogoutClearsLocallyBeforeStartingRemoteRequest(self) -> None:
		threads = []
		calls = []

		class Thread:
			def __init__(threadSelf, **kwargs):
				threadSelf.kwargs = kwargs

			def start(threadSelf):
				threads.append(threadSelf)

		self.module._saveSession("6c40ae69-d09d-4a9a-8ff1-f86e0139a283", "login-token")
		self.module.Thread = Thread

		def sendRequest(*args, **kwargs):
			calls.append((args, kwargs))
			raise _ApiError("server unavailable")

		self.module.network.sendRequest = sendRequest

		self.module.logoutStoredLoginAsync()

		self.assertEqual(self.module._loadSession()["token"], "")
		self.assertEqual(calls, [])
		self.assertEqual(len(threads), 1)
		thread = threads[0]
		thread.kwargs["target"](*thread.kwargs["args"])
		self.assertEqual(len(calls), 1)


if __name__ == "__main__":
	unittest.main()
