from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path
import sys
import types
import unittest


class _FakeResponse:
	def __init__(self, content: bytes = b'{"errorCode": 0}') -> None:
		self.content = content
		self.text = content.decode()


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

	class ApiError(Exception):
		pass

	class AuthenticationError(ApiError):
		pass

	exceptionsModule.ApiError = ApiError
	exceptionsModule.AuthenticationError = AuthenticationError
	sys.modules["addon.globalPlugins.visAware.exceptions"] = exceptionsModule

	networkModule = types.ModuleType("addon.globalPlugins.visAware.network")
	networkModule.sendRequest = lambda *args, **kwargs: None
	networkModule.sleepWithCancellation = lambda _seconds, _cancelCheck: None
	sys.modules["addon.globalPlugins.visAware.network"] = networkModule


def load_paddleocr_client_module():
	_install_module_stubs()
	modulePath = (
		Path(__file__).resolve().parents[1]
		/ "addon"
		/ "globalPlugins"
		/ "visAware"
		/ "contentRecognizers"
		/ "_paddleOCRClient.py"
	)
	spec = importlib.util.spec_from_file_location(
		"addon.globalPlugins.visAware.contentRecognizers._paddleOCRClient",
		modulePath,
	)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load _paddleOCRClient.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class _EngineSetting:
	configSpec = "string(default=None)"

	def __init__(self, name: str, displayNameWithAccelerator: str = "", optionsPropertyName: str = ""):
		self.name = name
		self.displayNameWithAccelerator = displayNameWithAccelerator
		self.optionsPropertyName = optionsPropertyName


class _BooleanEngineSetting(_EngineSetting):
	configSpec = "boolean(default=False)"


class _BaseRecognizer:
	configSectionName = "ocr"
	_autoRecognitionModel = ""

	@property
	def autoRecognitionModel(self) -> str:
		return self._autoRecognitionModel

	@staticmethod
	def generateStringSettings(settingsDict: dict) -> dict:
		return {key: types.SimpleNamespace(id=key, displayName=value) for key, value in settingsDict.items()}

	def getConfigSpec(self) -> dict:
		return {setting.name: setting.configSpec for setting in self.supportedSettings}


def load_paddleocr_module():
	_install_module_stubs()
	for moduleName in ("config", "contentRecog", "PIL", "PIL.Image"):
		if moduleName not in sys.modules:
			sys.modules[moduleName] = types.ModuleType(moduleName)
	sys.modules["config"].conf = {"ocr": {"paddleOCR": {}}}

	contentRecogModule = sys.modules["contentRecog"]
	contentRecogModule.LinesWordsResult = type("LinesWordsResult", (), {})
	contentRecogModule.RecogImageInfo = type("RecogImageInfo", (), {})
	contentRecogModule.SimpleTextResult = type("SimpleTextResult", (), {})

	pilImageModule = sys.modules["PIL.Image"]
	pilImageModule.Image = type("Image", (), {})

	engineGUIHelperModule = types.ModuleType("addon.globalPlugins.visAware.engineGUIHelper")
	engineGUIHelperModule.BooleanEngineSetting = _BooleanEngineSetting
	engineGUIHelperModule.ChoiceEngineSetting = _EngineSetting
	engineGUIHelperModule.TextInputEngineSetting = _EngineSetting
	sys.modules["addon.globalPlugins.visAware.engineGUIHelper"] = engineGUIHelperModule

	recogHistoryModule = types.ModuleType("addon.globalPlugins.visAware.recogHistory")
	recogHistoryModule.createEntry = lambda *args, **kwargs: None
	recogHistoryModule.attachEntry = lambda result, entry: result
	sys.modules["addon.globalPlugins.visAware.recogHistory"] = recogHistoryModule

	recogHandlerModule = types.ModuleType("addon.globalPlugins.visAware.recogHandler")
	recogHandlerModule.BaseRecognizer = _BaseRecognizer
	recogHandlerModule.RecognitionRequest = type("RecognitionRequest", (), {})
	recogHandlerModule.AUTO_RECOGNITION_REQUEST_TIMEOUT = (3, 30)
	sys.modules["addon.globalPlugins.visAware.recogHandler"] = recogHandlerModule

	load_paddleocr_client_module()
	modulePath = (
		Path(__file__).resolve().parents[1]
		/ "addon"
		/ "globalPlugins"
		/ "visAware"
		/ "contentRecognizers"
		/ "paddleOCR.py"
	)
	spec = importlib.util.spec_from_file_location(
		"addon.globalPlugins.visAware.contentRecognizers.paddleOCR",
		modulePath,
	)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load paddleOCR.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class PaddleOCRClientTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.module = load_paddleocr_client_module()

	def _makeClient(self, model: str):
		options = self.module.PaddleOCRClientOptions(
			serviceType=self.module.SERVICE_TYPE_SELF_HOSTED,
			apiUrl="https://paddleocr.example.test",
			token="",
			model=model,
			useDocOrientationClassify=True,
			useDocUnwarping=True,
			useTextlineOrientation=True,
			useChartRecognition=True,
		)
		return self.module.PaddleOCRClient(options)

	def test_self_hosted_vl_uses_layout_endpoint_and_chart_option(self) -> None:
		client = self._makeClient(self.module.MODEL_PADDLEOCR_VL_1_6)

		self.assertEqual(client._getSyncUrl(), "https://paddleocr.example.test/layout-parsing")
		payload = client._buildOptionalPayload()
		self.assertTrue(payload["useLayoutDetection"])
		self.assertTrue(payload["useChartRecognition"])
		self.assertNotIn("useTextlineOrientation", payload)

	def test_self_hosted_ocr_uses_ocr_endpoint_and_textline_option(self) -> None:
		client = self._makeClient(self.module.MODEL_PP_OCR_V6)

		self.assertEqual(client._getSyncUrl(), "https://paddleocr.example.test/ocr")
		payload = client._buildOptionalPayload()
		self.assertTrue(payload["useTextlineOrientation"])
		self.assertNotIn("useChartRecognition", payload)
		self.assertNotIn("useLayoutDetection", payload)

	def test_self_hosted_uses_bearer_authorization_by_default(self) -> None:
		calls = []

		def sendRequest(*args, **kwargs):
			calls.append((args, kwargs))
			return _FakeResponse()

		self.module.network.sendRequest = sendRequest
		options = self.module.PaddleOCRClientOptions(
			serviceType=self.module.SERVICE_TYPE_SELF_HOSTED,
			apiUrl="https://paddleocr.example.test",
			token="secret-token",
			model=self.module.MODEL_PP_OCR_V6,
			useDocOrientationClassify=False,
			useDocUnwarping=False,
			useTextlineOrientation=False,
			useChartRecognition=False,
		)

		self.module.PaddleOCRClient(options).recognizeImage(b"")

		self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer secret-token")

	def test_automatic_request_policy_applies_to_all_async_http_requests(self) -> None:
		calls = []
		waitCalls = []
		responses = [
			b'{"data":{"jobId":"job-1"}}',
			b'{"data":{"status":"running"}}',
			b'{"data":{"status":"success","resultJsonUrl":"https://result.example.test/result"}}',
			b'{"errorCode":0}',
		]

		def sendRequest(*args, **kwargs):
			calls.append((args, kwargs))
			return _FakeResponse(responses.pop(0))

		def sleepWithCancellation(seconds, cancelCheck) -> None:
			waitCalls.append(seconds)
			cancelCheck()

		self.module.network.sendRequest = sendRequest
		self.module.network.sleepWithCancellation = sleepWithCancellation
		options = self.module.PaddleOCRClientOptions(
			serviceType=self.module.SERVICE_TYPE_AISTUDIO_ASYNC,
			apiUrl="https://paddleocr.example.test/jobs",
			token="secret-token",
			model=self.module.MODEL_PADDLEOCR_VL_1_5,
			useDocOrientationClassify=False,
			useDocUnwarping=False,
			useTextlineOrientation=False,
			useChartRecognition=False,
		)
		client = self.module.PaddleOCRClient(
			options,
			cancellationChecker=lambda: None,
			requestTimeout=(3, 30),
		)

		client.recognizeImage(b"eA==")

		self.assertEqual(waitCalls, [self.module.DEFAULT_AISTUDIO_POLL_INTERVAL_SECONDS])
		self.assertEqual(len(calls), 4)
		self.assertTrue(all(call[1]["timeout"] == (3, 30) for call in calls))
		self.assertTrue(all(callable(call[1]["cancelCheck"]) for call in calls))


class PaddleOCRSettingsTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.module = load_paddleocr_module()

	def test_service_profiles_keep_separate_url_token_and_model(self) -> None:
		conf = {
			"serviceType": self.module.SERVICE_TYPE_SELF_HOSTED,
			"aistudioAsyncApiUrl": "https://official.example.test/jobs",
			"aistudioAsyncToken": "official-token",
			"aistudioAsyncModel": self.module.MODEL_PADDLEOCR_VL_1_5,
			"selfHostedApiUrl": "https://self.example.test",
			"selfHostedToken": "self-token",
			"selfHostedModel": self.module.MODEL_PP_OCR_V6,
		}
		sys.modules["config"].conf = {"ocr": {"paddleOCR": conf}}
		engine = self.module.CustomContentRecognizer()

		engine.loadSettings()
		self.assertEqual(engine.apiUrl, "https://self.example.test")
		self.assertEqual(engine.token, "self-token")
		self.assertEqual(engine.model, self.module.MODEL_PP_OCR_V6)

		engine.serviceType = self.module.SERVICE_TYPE_AISTUDIO_ASYNC
		engine.apiUrl = "https://official2.example.test/jobs"
		engine.saveSettings()

		saved = sys.modules["config"].conf["ocr"]["paddleOCR"]
		self.assertEqual(saved["aistudioAsyncApiUrl"], "https://official2.example.test/jobs")
		self.assertEqual(saved["aistudioAsyncToken"], "official-token")
		self.assertEqual(saved["selfHostedApiUrl"], "https://self.example.test")
		self.assertEqual(saved["selfHostedToken"], "self-token")

	def test_only_automatic_recognition_uses_the_short_client_timeout(self) -> None:
		capturedTimeouts = []

		class FakeClient:
			def __init__(self, _options, cancellationChecker=None, requestTimeout=None):
				capturedTimeouts.append((cancellationChecker, requestTimeout))

			def recognizeImage(self, _imageContent):
				return {}

		self.module.PaddleOCRClient = FakeClient
		self.module.SimpleTextResult = lambda text: types.SimpleNamespace(text=text)
		engine = object.__new__(self.module.CustomContentRecognizer)
		engine._buildClientOptions = lambda: object()
		engine._checkCancelled = lambda _event: None
		engine.originalImage = None
		engine.extractText = lambda _result: "recognized"

		for isAutomaticRecognition in (True, False):
			request = types.SimpleNamespace(
				isAutomaticRecognition=isAutomaticRecognition,
				textResult=True,
			)
			engine._handleStandardResponse({"imageContent": b""}, object(), object(), request)

		self.assertEqual(
			[capturedTimeout for _checker, capturedTimeout in capturedTimeouts],
			[(3, 30), None],
		)
		self.assertTrue(all(callable(checker) for checker, _timeout in capturedTimeouts))


if __name__ == "__main__":
	unittest.main()
