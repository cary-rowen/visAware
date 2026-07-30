# Copyright (C) 2025 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Provides abstract base classes and handlers for various recognition engines."""

from abc import ABC, abstractmethod
import addonHandler
import base64
import config
from collections.abc import Iterator
from collections.abc import Mapping, Sequence
from contentRecog import ContentRecognizer, LinesWordsResult, RecogImageInfo
from dataclasses import dataclass
from gui import guiHelper
from gui.nvdaControls import CustomCheckListBox
from gui.guiHelper import BoxSizerHelper
from gui.settingsDialogs import SettingsPanel
from . import imageDescribers
import json
from logHandler import log
from threading import Thread, Event, current_thread
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import wx
from io import BytesIO
from PIL import Image
from PIL.Image import LANCZOS
from typing import Optional, Any, List, Tuple, Callable

from .abstractEngine import AbstractEngine, AbstractEngineHandler, AbstractEngineSettingsPanel, EngineSetting
from . import contentRecognizers
from . import network
from . import recogHistory
from .conversation import QuestionStreamEvent, QuestionStreamFinished, QuestionStreamText
from .engineGUIHelper import NumericEngineSetting, TextInputEngineSetting
from .exceptions import ApiError, CancellationError, StreamIncompleteError, StreamReplacementError

addonHandler.initTranslation()

ENGINE_TYPES: List[Tuple[str, str]] = [
	("OCR", _("OCR")),
	("ImageDescriber", _("Image description")),
	("Agent", _("AI Agent")),
]

SOURCE_TYPES: List[Tuple[str, str]] = [
	# Translators: A recognition source type
	("navigatorObject", _("Navigator object")),
	# Translators: A recognition source type
	("wholeDesktop", _("Whole screen")),
	# Translators: A recognition source type
	("foreGroundWindow", _("Foreground window")),
	# Translators: A recognition source type
	("clipboardImage", _("Image or image file on the clipboard")),
]

AUTO_RECOGNITION_OFF = "off"
AUTO_RECOGNITION_IMAGE_DESCRIBER_MODE = "imageDescriber"
AUTO_RECOGNITION_OCR_MODE = "ocr"
AUTO_RECOGNITION_CURRENT_ENGINE_NAME = "current"
AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX = "imageDescriber:"
AUTO_RECOGNITION_OCR_PREFIX = "ocr:"
AUTO_RECOGNITION_CURRENT_IMAGE_DESCRIBER = (
	f"{AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX}{AUTO_RECOGNITION_CURRENT_ENGINE_NAME}"
)
AUTO_RECOGNITION_CURRENT_OCR = f"{AUTO_RECOGNITION_OCR_PREFIX}{AUTO_RECOGNITION_CURRENT_ENGINE_NAME}"

SENSITIVE_LOG_KEYS = {
	"authorization",
	"app_id",
	"app_key",
	"appid",
	"apikey",
	"api_key",
	"api_secret",
	"apisecret",
	"client_id",
	"client_secret",
	"password",
	"src",
	"signature",
	"token",
	"x_ai_gateway_app_id",
	"x_ai_gateway_signature",
	"x_goog_api_key",
}

SENSITIVE_QUERY_KEYS = {
	"access_token",
	"api_key",
	"apikey",
	"app_key",
	"client_secret",
	"key",
	"token",
}

LARGE_PAYLOAD_KEYS = {
	"audio",
	"content",
	"data",
	"file",
	"image",
	"imagebytes",
	"imagecontent",
	"inline_data",
	"payload",
	"src",
}


def _redactRequestParamsForLog(value: Any, keyName: str = "") -> Any:
	"""Returns request parameters safe enough for verbose debug logging."""
	normalizedKeyName = keyName.lower().replace("-", "_")
	if normalizedKeyName in SENSITIVE_LOG_KEYS:
		return "<redacted>"
	if normalizedKeyName in LARGE_PAYLOAD_KEYS and isinstance(value, str):
		return f"<redacted payload: {len(value)} chars>"
	if isinstance(value, Mapping):
		return {key: _redactRequestParamsForLog(childValue, str(key)) for key, childValue in value.items()}
	if isinstance(value, memoryview):
		return f"<memoryview: {value.nbytes} bytes>"
	if isinstance(value, tuple):
		return tuple(_redactRequestParamsForLog(item, keyName) for item in value)
	if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
		return [_redactRequestParamsForLog(item, keyName) for item in value]
	if isinstance(value, (bytes, bytearray)):
		return f"<{type(value).__name__}: {len(value)} bytes>"
	if isinstance(value, str) and keyName.lower() == "url":
		return _redactUrlForLog(value)
	if isinstance(value, str) and len(value) > 500:
		return f"{value[:200]}...[{len(value)} chars]"
	return value


def _redactUrlForLog(url: str) -> str:
	"""Redacts sensitive URL query parameters in verbose request logs."""
	try:
		parts = urlsplit(url)
	except ValueError:
		return url
	if not parts.query:
		return url
	query = urlencode(
		[
			(key, "<redacted>" if key.lower() in SENSITIVE_QUERY_KEYS else value)
			for key, value in parse_qsl(parts.query, keep_blank_values=True)
		],
	)
	return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


@dataclass(frozen=True)
class StreamText:
	"""A text fragment produced by a streaming recognition engine."""

	text: str
	replace: bool = False


@dataclass(frozen=True)
class StreamFinished:
	"""The final text produced by a streaming recognition engine."""

	text: str
	incompleteReason: str | None = None
	historyEntry: recogHistory.HistoryEntryPayload | None = None


@dataclass(frozen=True)
class RecognitionRequest:
	"""State captured for one recognition request."""

	textResult: bool
	streamResult: bool


def getConfigChoiceValue(configSection: Any, configName: str, configList: List[Tuple[str, str]]) -> str:
	"""Returns a valid configuration choice, falling back to the first option."""
	availableValues = [name for name, _ in configList]
	try:
		currentValue = configSection.__getitem__(configName, checkValidity=False)
	except KeyError:
		currentValue = None
	try:
		return availableValues[availableValues.index(currentValue)]
	except ValueError:
		fallbackValue = availableValues[0]
		log.debugWarning(
			f"Invalid Vis Aware setting '{configName}' value {currentValue!r}; using {fallbackValue!r}.",
		)
		configSection[configName] = fallbackValue
		return fallbackValue


def getExcludedSourceTypes(configSection: Any) -> List[str]:
	"""Returns valid source types excluded from the cycle recognition source command."""
	try:
		excludedSourceTypes = configSection["excludedSourceTypes"]
	except (KeyError, AttributeError):
		return []
	if isinstance(excludedSourceTypes, str):
		excludedSourceTypes = [excludedSourceTypes]
	availableSourceTypes = [name for name, _description in SOURCE_TYPES]
	result: List[str] = []
	for sourceType in excludedSourceTypes:
		sourceType = str(sourceType)
		if sourceType in availableSourceTypes and sourceType not in result:
			result.append(sourceType)
	if len(result) >= len(availableSourceTypes):
		log.debugWarning("All recognition source types were excluded; using all source types.")
		return []
	return result


def setExcludedSourceTypes(configSection: Any, excludedSourceTypes: List[str]) -> None:
	"""Stores excluded source types in SOURCE_TYPES order."""
	excludedSet = set(excludedSourceTypes)
	configSection["excludedSourceTypes"] = [
		name for name, _description in SOURCE_TYPES if name in excludedSet
	]


def getCycleSourceTypes(configSection: Any) -> List[Tuple[str, str]]:
	"""Returns source types available in the cycle recognition source command."""
	excludedSourceTypes = set(getExcludedSourceTypes(configSection))
	return [(name, description) for name, description in SOURCE_TYPES if name not in excludedSourceTypes]


def getValidSourceType(configSection: Any) -> str:
	"""Returns a valid current source type from the configured cycle source set."""
	sourceType = getConfigChoiceValue(configSection, "sourceType", SOURCE_TYPES)
	cycleSourceTypes = getCycleSourceTypes(configSection)
	cycleSourceNames = [name for name, _description in cycleSourceTypes]
	if sourceType in cycleSourceNames:
		return sourceType
	fallbackSourceType = cycleSourceNames[0]
	log.debugWarning(
		f"Configured sourceType {sourceType!r} is excluded; using {fallbackSourceType!r}.",
	)
	configSection["sourceType"] = fallbackSourceType
	return fallbackSourceType


class BaseRecognizer(ContentRecognizer, AbstractEngine, ABC):
	"""Abstract base class for all online recognition engines."""

	description: str = ""
	configSectionName: str = "OCR"
	supportsStreaming: bool = False
	isStreaming: bool = False

	_recognitionThread: Optional[Thread] = None
	_cancellationEvent: Optional[Event] = None
	originalImage: Optional[Image.Image] = None

	# Default engine parameters
	_compression: int = 9
	_quality: int = 75
	_apiKey: str = ""
	_apiSecretKey: str = ""
	_appID: str = ""
	textResult: bool = False
	minHeight: int = 50
	maxHeight: int = 4096
	minWidth: int = 50
	maxWidth: int = 4096
	maxPixels: int = 10000000
	maxSize: int = 4 * 1024 * 1024
	uploadBase64EncodeImage: bool = True
	uploadImageFormat: str = "PNG"
	_autoRecognitionPrompt: str = ""
	_autoRecognitionModel: str = ""
	streamResult: bool = False

	@property
	def compression(self) -> int:
		return self._compression

	@compression.setter
	def compression(self, value: int) -> None:
		self._compression = value

	@property
	def quality(self) -> int:
		return self._quality

	@quality.setter
	def quality(self, value: int) -> None:
		self._quality = value

	@property
	def appid(self) -> str:
		return self._appID

	@appid.setter
	def appid(self, value: str) -> None:
		self._appID = value

	@property
	def apikey(self) -> str:
		return self._apiKey

	@apikey.setter
	def apikey(self, value: str) -> None:
		self._apiKey = value

	@property
	def apisecret(self) -> str:
		return self._apiSecretKey

	@apisecret.setter
	def apisecret(self, value: str) -> None:
		self._apiSecretKey = value

	@property
	def autoRecognitionPrompt(self) -> str:
		return self._autoRecognitionPrompt

	@autoRecognitionPrompt.setter
	def autoRecognitionPrompt(self, value: str) -> None:
		self._autoRecognitionPrompt = value.strip() if value else ""

	@property
	def autoRecognitionModel(self) -> str:
		return self._autoRecognitionModel

	@autoRecognitionModel.setter
	def autoRecognitionModel(self, value: str) -> None:
		self._autoRecognitionModel = value.strip() if value else ""

	def applyAutoRecognitionOverrides(self) -> None:
		"""Applies automatic recognition setting overrides supported by this engine."""
		autoModel = self.autoRecognitionModel
		if autoModel and self.isSupported("model"):
			setattr(self, "model", autoModel)
		autoPrompt = self.autoRecognitionPrompt
		if autoPrompt and self.isSupported("prompt"):
			setattr(self, "prompt", autoPrompt)

	@abstractmethod
	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		"""
		Builds the parameters for the network request.

		:param imageContent: The byte content of the image to be sent.
		:param request: The request-local recognition options.
		:returns: A dictionary of parameters for `requests`.
		"""
		pass

	@abstractmethod
	def processApiResult(self, result: bytes) -> str | bool:
		"""
		Processes the API result to check for errors.

		:param result: The raw byte response from the server.
		:returns: An error message string on failure, or False on success.
		"""
		pass

	@abstractmethod
	def extractText(self, apiResult: dict) -> str:
		"""
		Extracts the plain text result from the parsed API response.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: The recognized text as a single string.
		"""
		pass

	@abstractmethod
	def _convertToLineResultFormat(self, apiResult: dict) -> list:
		"""
		Converts the API response into NVDA's rich format with coordinates.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: A list of lines, where each line is a list of word dictionaries.
		"""
		pass

	def processStreamChunk(self, chunk: bytes, request: RecognitionRequest) -> Optional[str]:
		"""Processes a chunk of a streaming response."""
		if request.streamResult:
			raise NotImplementedError("Streaming engines must implement processStreamChunk")
		return None

	def recognize(self, pixels: bytes, imageInfo: RecogImageInfo, onResult: Callable) -> None:
		"""
		Starts the recognition process in a background thread.

		:param pixels: The raw pixel data of the image.
		:param imageInfo: Information about the image's location and size.
		:param onResult: The callback function to be called with the result.
		"""
		request = RecognitionRequest(
			textResult=self.textResult,
			streamResult=self.streamResult,
		)
		self._cancellationEvent = Event()
		self._recognitionThread = Thread(
			name=f"RecognitionThread-{self.name}",
			target=self._recognitionWorker,
			args=(pixels, imageInfo, onResult, self._cancellationEvent, request),
		)
		self._recognitionThread.start()

	def recognizeImage(self, image: Image.Image, onResult: Callable) -> None:
		"""
		Starts recognition for an already available PIL image.

		This avoids converting a PIL image to raw pixels only for the worker to
		reconstruct the same image again.

		:param image: The image to recognize.
		:param onResult: The callback function to be called with the result.
		"""
		request = RecognitionRequest(
			textResult=self.textResult,
			streamResult=self.streamResult,
		)
		self._cancellationEvent = Event()
		self._recognitionThread = Thread(
			name=f"RecognitionThread-{self.name}",
			target=self._recognitionImageWorker,
			args=(image, onResult, self._cancellationEvent, request),
		)
		self._recognitionThread.start()

	def cancel(self, isUserInitiated: bool = True) -> None:
		if self._cancellationEvent:
			self._cancellationEvent.is_user_initiated = isUserInitiated
			self._cancellationEvent.set()

	def terminate(self) -> None:
		self.cancel()

	def _checkCancelled(self, cancellationEvent: Event) -> None:
		"""
		Checks if the cancellation event has been set and raises an exception if so.

		:param cancellationEvent: The `threading.Event` to check.
		:raises CancellationError: If the event is set.
		"""
		if cancellationEvent.is_set():
			raise CancellationError("Task was cancelled.", cancellationEvent)

	def _runRecognition(
		self,
		imageObject: Image.Image,
		imageInfo: RecogImageInfo,
		onResult: Callable,
		cancellationEvent: Event,
		request: RecognitionRequest,
	) -> None:
		self._checkCancelled(cancellationEvent)
		imageContent = self._prepareImageContent(imageObject, imageInfo)
		if not imageContent:
			# Translators: An error message when failing to prepare the image for upload.
			raise ApiError(_("Failed to prepare image content for upload."))
		self._checkCancelled(cancellationEvent)
		requestParams = self._buildRequestParams(imageContent, request)
		if config.conf["visAwareGeneral"]["verboseDebugLogging"]:
			log.debug(f"Request Params for {self.name}: {_redactRequestParamsForLog(requestParams)}")
		if request.streamResult and self.supportsStreaming:
			self._handleStreamingResponse(requestParams, onResult, cancellationEvent, request)
		else:
			resultPayload = self._handleStandardResponse(
				requestParams,
				imageInfo,
				cancellationEvent,
				request,
			)
			wx.CallAfter(onResult, resultPayload)

	def _recognitionWorker(
		self,
		pixels: bytes,
		imageInfo: RecogImageInfo,
		onResult: Callable,
		cancellationEvent: Event,
		request: RecognitionRequest,
	) -> None:
		"""The worker function that runs in a separate thread to perform recognition."""
		try:
			self._checkCancelled(cancellationEvent)
			imageObject = self._prepareImageObject(pixels, imageInfo)
			if not imageObject:
				# Translators: An error message when failing to prepare the image object.
				raise ApiError(_("Failed to prepare image object."))
			self._runRecognition(imageObject, imageInfo, onResult, cancellationEvent, request)
		except Exception as e:
			if not isinstance(e, CancellationError):
				log.warning(f"Recognition failed for {self.name}", exc_info=True)
			wx.CallAfter(onResult, e)
		finally:
			self._cleanup()

	def _recognitionImageWorker(
		self,
		image: Image.Image,
		onResult: Callable,
		cancellationEvent: Event,
		request: RecognitionRequest,
	) -> None:
		"""The worker function that runs recognition for an existing image object."""
		try:
			self._checkCancelled(cancellationEvent)
			imageInfo = RecogImageInfo(0, 0, image.width, image.height, 1)
			imageObject = self._prepareImageObjectFromImage(image, imageInfo)
			if not imageObject:
				# Translators: An error message when failing to prepare the image object.
				raise ApiError(_("Failed to prepare image object."))
			self._runRecognition(imageObject, imageInfo, onResult, cancellationEvent, request)
		except Exception as e:
			if not isinstance(e, CancellationError):
				log.warning(f"Recognition failed for {self.name}", exc_info=True)
			wx.CallAfter(onResult, e)
		finally:
			self._cleanup()

	def _handleStandardResponse(
		self,
		requestParams: dict,
		imageInfo: RecogImageInfo,
		cancellationEvent: Event,
		request: RecognitionRequest,
	) -> Any:
		"""Handles a standard, non-streaming API response."""
		response = network.sendRequest(**requestParams)
		self._checkCancelled(cancellationEvent)
		apiErrorMessage = self.processApiResult(response.content)
		if apiErrorMessage:
			raise ApiError(str(apiErrorMessage))
		jsonResponse = self._convertToJson(response.content)
		self._checkCancelled(cancellationEvent)
		historyEntry = (
			recogHistory.createEntry(self, self.originalImage, jsonResponse) if self.originalImage else None
		)
		if request.textResult:
			from contentRecog import SimpleTextResult

			ocrResult = self.extractText(jsonResponse)
			if not ocrResult or ocrResult.isspace():
				# Translators: An error message for a blank recognition result.
				raise ApiError(_("Recognition result is blank."))
			resultText = f"{ocrResult}"
			return recogHistory.attachEntry(SimpleTextResult(resultText), historyEntry)
		else:
			lineResult = self._convertToLineResultFormat(jsonResponse)
			if not lineResult:
				# Translators: An error message for a blank recognition result.
				raise ApiError(_("Recognition result is blank."))
			return recogHistory.attachEntry(LinesWordsResult(lineResult, imageInfo), historyEntry)

	def _handleStreamingResponse(
		self,
		requestParams: dict,
		onResult: Callable,
		cancellationEvent: Event,
		request: RecognitionRequest,
	) -> None:
		"""Handles a streaming API response."""
		fullResponseText = ""
		rawChunkCount = 0
		textChunkCount = 0
		emptyTextChunkCount = 0
		incompleteReason: str | None = None
		for chunk in network.sendStreamingRequest(**requestParams):
			self._checkCancelled(cancellationEvent)
			rawChunkCount += 1
			try:
				processedText = self.processStreamChunk(chunk, request)
			except StreamReplacementError as e:
				fullResponseText = e.replacementText.strip()
				if not fullResponseText:
					# Translators: An error message for a blank recognition result.
					raise ApiError(_("Recognition result is blank.")) from e
				textChunkCount += 1
				wx.CallAfter(onResult, StreamText(fullResponseText, replace=True))
				log.warning(
					"Streaming response replaced previous partial text. "
					f"engine={self.name}, textChunkCount={textChunkCount}",
				)
				break
			except StreamIncompleteError as e:
				processedText = e.partialText or None
				if processedText:
					fullResponseText += processedText
					textChunkCount += 1
					wx.CallAfter(onResult, StreamText(processedText))
				if not fullResponseText or fullResponseText.isspace():
					raise
				incompleteReason = str(e)
				log.warning(
					"Streaming response ended after a partial result. "
					f"engine={self.name}, reason={incompleteReason}, textChunkCount={textChunkCount}",
				)
				break
			if processedText:
				fullResponseText += processedText
				textChunkCount += 1
				wx.CallAfter(onResult, StreamText(processedText))
			else:
				emptyTextChunkCount += 1
		if not fullResponseText or fullResponseText.isspace():
			log.warning(
				"Streaming HTTP response completed with blank result. "
				f"engine={self.name}, rawChunkCount={rawChunkCount}, textChunkCount={textChunkCount}, "
				f"emptyTextChunkCount={emptyTextChunkCount}",
			)
			# Translators: An error message for a blank recognition result.
			raise ApiError(_("Recognition result is blank."))
		historyEntry = None
		if fullResponseText and self.originalImage:
			pseudoResponse = {"streamed_text": fullResponseText}
			historyEntry = recogHistory.createEntry(self, self.originalImage, pseudoResponse)
		wx.CallAfter(
			onResult,
			StreamFinished(fullResponseText, incompleteReason=incompleteReason, historyEntry=historyEntry),
		)

	def _prepareImageObject(self, pixels: bytes, imageInfo: RecogImageInfo) -> Optional[Image.Image]:
		"""Prepares the image object from raw pixels."""
		imageObject = self._getConvertedImage(pixels, imageInfo)
		resizedImage = self._checkAndResizeImage(imageObject, imageInfo)
		if not resizedImage:
			log.warning("Image resizing failed or was cancelled.")
			return None
		return resizedImage

	def _prepareImageObjectFromImage(
		self,
		image: Image.Image,
		imageInfo: RecogImageInfo,
	) -> Optional[Image.Image]:
		"""Prepares an already available PIL image object."""
		self.originalImage = image.convert("RGB")
		resizedImage = self._checkAndResizeImage(self.originalImage, imageInfo)
		if not resizedImage:
			log.warning("Image resizing failed or was cancelled.")
			return None
		return resizedImage

	def _prepareImageContent(self, image: Image.Image, imageInfo: RecogImageInfo) -> bytes:
		"""Prepares the image for upload by resizing and serializing it."""
		# First serialization to check initial size.
		imageContent = self._serializeImage(image)
		initialSize = len(imageContent)
		if initialSize < self.maxSize:
			return base64.b64encode(imageContent) if self.uploadBase64EncodeImage else imageContent

		# Estimate the resize factor needed to meet the size constraint.
		sizeRatio = (self.maxSize * 0.95) / initialSize
		imageInfo.resizeFactor = sizeRatio**0.5

		# Ensure the new size is not smaller than the minimum dimensions.
		if (
			image.width * imageInfo.resizeFactor < self.minWidth
			or image.height * imageInfo.resizeFactor < self.minHeight
		):
			widthFactor = self.minWidth / image.width if image.width < self.minWidth else 1.0
			heightFactor = self.minHeight / image.height if image.height < self.minHeight else 1.0
			imageInfo.resizeFactor = max(widthFactor, heightFactor)
			log.warning("Image resize estimation was too aggressive; falling back to dimension-based factor.")

		resizedImage = self._getResizedImage(imageInfo)
		imageContent = self._serializeImage(resizedImage)
		currentSize = len(imageContent)

		# Iteratively reduce size if the estimation was not sufficient.
		while (
			currentSize >= self.maxSize
			and resizedImage.width > self.minWidth
			and resizedImage.height > self.minHeight
		):
			log.debug("Image size still too large after estimation, entering safety loop.")
			imageInfo.resizeFactor *= 0.9
			resizedImage = self._getResizedImage(imageInfo)
			imageContent = self._serializeImage(resizedImage)
			currentSize = len(imageContent)

		if currentSize >= self.maxSize:
			# Translators: An error message when the image is too large to upload.
			raise ApiError(_("Image is too large to upload."))
		return base64.b64encode(imageContent) if self.uploadBase64EncodeImage else imageContent

	def prepareImageContentFromImage(self, image: Image.Image) -> bytes:
		"""
		Prepares a PIL image for a request outside the contentRecog capture pipeline.

		This is used by follow-up questions, where the original image is already
		available from recognition history.

		:param image: The image to serialize for this engine.
		:returns: Raw or base64-encoded image content according to engine settings.
		"""
		previousOriginalImage = self.originalImage
		try:
			self.originalImage = image.convert("RGB")
			imageInfo = RecogImageInfo(0, 0, self.originalImage.width, self.originalImage.height, 1)
			resizedImage = self._checkAndResizeImage(self.originalImage, imageInfo)
			return self._prepareImageContent(resizedImage, imageInfo)
		finally:
			self.originalImage = previousOriginalImage

	def _getConvertedImage(self, pixels: bytes, imageInfo: RecogImageInfo) -> Image.Image:
		"""Converts raw pixel data to a PIL Image object."""
		img = Image.frombytes("RGBX", (imageInfo.recogWidth, imageInfo.recogHeight), pixels, "raw", "BGRX")
		self.originalImage = img.convert("RGB")
		return self.originalImage

	def _getResizedImage(self, imageInfo: RecogImageInfo) -> Image.Image:
		"""Resizes the original image based on the given image info."""
		if not self.originalImage:
			raise RuntimeError("Cannot resize without an original image.")
		newWidth = int(imageInfo.recogWidth * imageInfo.resizeFactor)
		newHeight = int(imageInfo.recogHeight * imageInfo.resizeFactor)
		return self.originalImage.resize((newWidth, newHeight), resample=LANCZOS)

	def _serializeImage(self, pilImage: Image.Image) -> bytes:
		"""Serializes a PIL Image object to bytes."""
		imageBuffer = BytesIO()
		imageFormat = self.uploadImageFormat.upper()
		if imageFormat == "JPG":
			imageFormat = "JPEG"
		if imageFormat == "JPEG":
			pilImage = pilImage.convert("RGB")
		saveOptions: dict[str, Any] = {"optimize": True}
		if imageFormat == "PNG":
			saveOptions["compression_level"] = self.compression
		elif imageFormat == "JPEG":
			saveOptions["quality"] = self.quality
		pilImage.save(imageBuffer, imageFormat, **saveOptions)
		return imageBuffer.getvalue()

	def _checkAndResizeImage(self, image: Image.Image, imageInfo: RecogImageInfo) -> Image.Image:
		"""Checks image dimensions and resizes if necessary."""
		width, height = image.width, image.height
		minAllowedFactor = max(self.minWidth / width, self.minHeight / height)
		maxAllowedFactor = min(self.maxWidth / width, self.maxHeight / height)
		imageInfo.resizeFactor = min(max(1.0, minAllowedFactor), maxAllowedFactor)
		resizedImage = self._getResizedImage(imageInfo)
		pixelCount = resizedImage.width * resizedImage.height
		while pixelCount > self.maxPixels:
			imageInfo.resizeFactor *= 0.8
			resizedImage = self._getResizedImage(imageInfo)
			pixelCount = resizedImage.width * resizedImage.height
			if resizedImage.width < self.minWidth or resizedImage.height < self.minHeight:
				break
		if pixelCount > self.maxPixels:
			# Translators: An error message when the image has too many pixels after resizing.
			raise ApiError(_("Image has too many pixels after resizing."))
		return resizedImage

	def _cleanup(self) -> None:
		"""Cleans up resources after a recognition task."""
		if self._recognitionThread is not current_thread():
			return
		self.originalImage = None
		self._recognitionThread = None

	@staticmethod
	def _convertToJson(data: bytes) -> dict[str, Any]:
		"""Decodes and parses JSON data from bytes."""
		return json.loads(data.decode("utf-8", errors="ignore"))

	@classmethod
	def imageQualitySetting(cls) -> EngineSetting:
		# Translators: The label for an engine setting to control image quality.
		return NumericEngineSetting("quality", _("Upload image quality"))

	@classmethod
	def autoRecognitionPromptSetting(cls) -> EngineSetting:
		# Translators: The label for an engine setting to override the prompt used for automatic recognition.
		return TextInputEngineSetting(
			"autoRecognitionPrompt",
			_("Automatic recognition &prompt"),
			multiline=True,
		)

	@classmethod
	def autoRecognitionModelSetting(cls) -> EngineSetting:
		# Translators: The label for an engine setting to override the model used for automatic recognition.
		return TextInputEngineSetting("autoRecognitionModel", _("Automatic recognition &model"))


class CustomOCRHandler(AbstractEngineHandler):
	"""Handler for custom OCR engines."""

	engineClass = BaseRecognizer
	enginePackageName = ".contentRecognizers"
	enginePackage = contentRecognizers
	configSectionName = "OCR"
	defaultEnginePriorityList = ["baiduOCR"]
	mandatoryClassName = "CustomContentRecognizer"


class OCRPanel(AbstractEngineSettingsPanel):
	"""Settings panel for OCR engines."""

	# Translators: The title of the OCR engine settings panel.
	title = _("OCR")
	handler = CustomOCRHandler


class BaseDescriber(BaseRecognizer):
	"""Abstract base class for all image description engines."""

	configSectionName = "ImageDescriber"
	supportsQuestions: bool = False
	supportsQuestionStreaming: bool = False

	def _convertToLineResultFormat(self, apiResult: dict) -> list:
		"""Converts plain image-description text into a single-line virtual document result."""
		text = self.extractText(apiResult)
		if not text:
			return []
		return [[{"x": 0, "y": 0, "width": 1, "height": 1, "text": text}]]

	def askQuestion(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> str:
		"""
		Asks a follow-up question about a previous image description.

		:param context: Conversation context built from recognition history.
		:param question: The user's follow-up question.
		:param cancellationChecker: Optional callback that raises when cancelled.
		:returns: The engine's answer.
		"""
		raise NotImplementedError("This image describer does not support follow-up questions.")

	def shouldStreamQuestion(self, context: Any) -> bool:
		"""
		Returns whether follow-up answers should stream for this engine.

		This follows the engine's existing streaming option while keeping UI code
		independent of provider-specific setting names.

		:param context: Conversation context built from recognition history.
		:returns: True when this question should use streaming output.
		"""
		return self.supportsQuestionStreaming and bool(getattr(self, "isStreaming", False))

	def askQuestionEvents(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> Iterator[QuestionStreamEvent]:
		"""
		Asks a follow-up question and yields answer events for the dialog.

		This method is the UI-facing conversation interface. It follows the
		engine's streaming configuration without exposing provider-specific
		settings to the dialog.

		:param context: Conversation context built from recognition history.
		:param question: The user's follow-up question.
		:param cancellationChecker: Optional callback that raises when cancelled.
		:returns: An iterator of follow-up answer events.
		"""
		if self.shouldStreamQuestion(context):
			yield from self.askQuestionStream(context, question, cancellationChecker)
			return
		answer = self.askQuestion(context, question, cancellationChecker)
		yield QuestionStreamFinished(answer)

	def askQuestionStream(
		self,
		context: Any,
		question: str,
		cancellationChecker: Callable[[], None] | None = None,
	) -> Iterator[QuestionStreamEvent]:
		"""
		Asks a follow-up question and yields answer events.

		Engines without streaming support use this default implementation, which
		preserves one uniform interface for the dialog.

		:param context: Conversation context built from recognition history.
		:param question: The user's follow-up question.
		:param cancellationChecker: Optional callback that raises when cancelled.
		:returns: An iterator of follow-up answer events.
		"""
		answer = self.askQuestion(context, question, cancellationChecker)
		yield QuestionStreamFinished(answer)

	def _iterQuestionStreamingResponse(
		self,
		requestParams: dict,
		request: RecognitionRequest,
		cancellationChecker: Callable[[], None] | None = None,
	) -> Iterator[QuestionStreamEvent]:
		"""
		Iterates a streaming HTTP response as follow-up answer events.

		:param requestParams: Parameters for network.sendStreamingRequest.
		:param request: Request-local streaming options.
		:param cancellationChecker: Optional callback that raises when cancelled.
		:returns: An iterator of answer text fragments and a final answer event.
		"""
		fullResponseText = ""
		incompleteReason: str | None = None
		self._checkQuestionCancelled(cancellationChecker)
		for chunk in network.sendStreamingRequest(**requestParams):
			self._checkQuestionCancelled(cancellationChecker)
			try:
				processedText = self.processStreamChunk(chunk, request)
			except StreamReplacementError as e:
				fullResponseText = self._validateQuestionAnswer(e.replacementText)
				yield QuestionStreamText(fullResponseText, replace=True)
				log.warning(
					f"Streaming follow-up answer replaced previous partial text. engine={self.name}",
				)
				break
			except StreamIncompleteError as e:
				processedText = e.partialText or None
				if processedText:
					fullResponseText += processedText
					yield QuestionStreamText(processedText)
				if not fullResponseText or fullResponseText.isspace():
					raise
				incompleteReason = str(e)
				log.warning(
					"Streaming follow-up answer ended after a partial result. "
					f"engine={self.name}, reason={incompleteReason}",
				)
				break
			if processedText:
				fullResponseText += processedText
				yield QuestionStreamText(processedText)
		answer = self._validateQuestionAnswer(fullResponseText)
		yield QuestionStreamFinished(answer, incompleteReason=incompleteReason)

	def _checkQuestionCancelled(self, cancellationChecker: Callable[[], None] | None) -> None:
		if cancellationChecker:
			cancellationChecker()

	def _getConversationImage(self, context: Any) -> Image.Image:
		image = getattr(context, "image", None)
		if image is None:
			# Translators: Reported when the previous image is unavailable for a follow-up question.
			raise ApiError(_("The previous image is no longer available for follow-up questions."))
		return image

	def _validateQuestionAnswer(self, answer: str) -> str:
		answer = answer.strip()
		if not answer:
			# Translators: An error message for a blank follow-up answer.
			raise ApiError(_("Answer is blank."))
		return answer


class ImageDescriberHandler(AbstractEngineHandler):
	"""Handler for image describer engines."""

	engineClass = BaseDescriber
	mandatoryClassName = "CustomContentRecognizer"
	enginePackageName = ".imageDescribers"
	enginePackage = imageDescribers
	configSectionName = "ImageDescriber"
	defaultEnginePriorityList = ["vivoImageDescriber"]


class ImageDescriberPanel(AbstractEngineSettingsPanel):
	"""Settings panel for image describer engines."""

	# Translators: The title of the Image Describer engine settings panel.
	title = _("Image description")
	handler = ImageDescriberHandler


def getEffectiveAutoRecognitionEngine() -> str:
	"""Returns the configured automatic recognition engine."""
	try:
		conf = config.conf["visAwareGeneral"]
		configuredEngine = conf.__getitem__("autoRecognitionEngine", checkValidity=False)
	except Exception:
		configuredEngine = None
	if isinstance(configuredEngine, str):
		configuredEngine = configuredEngine.strip()
		if configuredEngine and configuredEngine != AUTO_RECOGNITION_OFF:
			return configuredEngine
	return AUTO_RECOGNITION_OFF


def isAutomaticRecognitionEnabled() -> bool:
	"""Returns whether automatic recognition should react to focus, browse, and navigator moves."""
	return getEffectiveAutoRecognitionEngine() != AUTO_RECOGNITION_OFF


def getAutoRecognitionTypeChoices() -> List[Tuple[str, str]]:
	"""Returns choices for the automatic recognition type setting."""
	return [
		# Translators: A choice meaning automatic recognition is disabled.
		(AUTO_RECOGNITION_OFF, _("Off")),
		# Translators: A choice meaning automatic recognition should use image description engines.
		(AUTO_RECOGNITION_IMAGE_DESCRIBER_MODE, _("Image description")),
		# Translators: A choice meaning automatic recognition should use OCR engines.
		(AUTO_RECOGNITION_OCR_MODE, _("OCR")),
	]


def getAutoRecognitionTypeAndEngine(value: str) -> Tuple[str, str]:
	"""Splits an automatic recognition config value into type and engine selection."""
	if value.startswith(AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX):
		return (
			AUTO_RECOGNITION_IMAGE_DESCRIBER_MODE,
			value.removeprefix(AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX),
		)
	if value.startswith(AUTO_RECOGNITION_OCR_PREFIX):
		return AUTO_RECOGNITION_OCR_MODE, value.removeprefix(AUTO_RECOGNITION_OCR_PREFIX)
	return AUTO_RECOGNITION_OFF, AUTO_RECOGNITION_CURRENT_ENGINE_NAME


def getAutoRecognitionEngineChoices(autoRecognitionType: str) -> List[Tuple[str, str]]:
	"""Returns engine choices for the selected automatic recognition type."""
	if autoRecognitionType == AUTO_RECOGNITION_IMAGE_DESCRIBER_MODE:
		choices = [
			# Translators: A choice for automatic recognition to use the current image description engine.
			(AUTO_RECOGNITION_CURRENT_ENGINE_NAME, _("Current image description engine")),
		]
		choices.extend(
			(engineName, engineDescription)
			for engineName, engineDescription in ImageDescriberHandler.getEngineList()
			if engineName != "empty"
		)
		return choices
	if autoRecognitionType == AUTO_RECOGNITION_OCR_MODE:
		choices = [
			# Translators: A choice for automatic recognition to use the current OCR engine.
			(AUTO_RECOGNITION_CURRENT_ENGINE_NAME, _("Current OCR engine")),
		]
		choices.extend(
			(engineName, engineDescription)
			for engineName, engineDescription in CustomOCRHandler.getEngineList()
			if engineName != "empty"
		)
		return choices
	return []


def getAutoRecognitionHandler(autoRecognitionType: str) -> type[AbstractEngineHandler] | None:
	"""Returns the engine handler for the selected automatic recognition type."""
	if autoRecognitionType == AUTO_RECOGNITION_IMAGE_DESCRIBER_MODE:
		return ImageDescriberHandler
	if autoRecognitionType == AUTO_RECOGNITION_OCR_MODE:
		return CustomOCRHandler
	return None


def getAutoRecognitionTypeAndEngineChoices() -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]], int, int]:
	"""Returns automatic recognition choices and initial selections for the settings panel."""
	autoRecognitionTypes = getAutoRecognitionTypeChoices()
	autoRecognitionTypeValues = [name for name, _description in autoRecognitionTypes]
	autoRecognitionType, engineName = getAutoRecognitionTypeAndEngine(getEffectiveAutoRecognitionEngine())
	try:
		typeSelection = autoRecognitionTypeValues.index(autoRecognitionType)
	except ValueError:
		autoRecognitionType = AUTO_RECOGNITION_OFF
		typeSelection = autoRecognitionTypeValues.index(AUTO_RECOGNITION_OFF)
	engineChoices = getAutoRecognitionEngineChoices(autoRecognitionType)
	engineValues = [name for name, _description in engineChoices]
	try:
		engineSelection = engineValues.index(engineName)
	except ValueError:
		engineSelection = 0 if engineChoices else wx.NOT_FOUND
	return autoRecognitionTypes, engineChoices, typeSelection, engineSelection


class AutomaticRecognitionPanel(SettingsPanel):
	"""Automatic recognition settings panel for the Vis Aware add-on."""

	# Translators: The title of the automatic recognition settings panel for the add-on.
	title = _("Automatic recognition")

	def makeSettings(self, sizer: wx.BoxSizer) -> None:
		"""
		Creates the settings controls for the automatic recognition panel.

		:param sizer: The sizer to which controls will be added.
		"""
		settingsSizerHelper = BoxSizerHelper(self, sizer=sizer)
		conf = config.conf["visAwareGeneral"]
		(
			self.autoRecognitionTypeChoices,
			self.autoRecognitionEngineChoices,
			typeSelection,
			engineSelection,
		) = getAutoRecognitionTypeAndEngineChoices()
		# Translators: The label for a choice control to select the kind of automatic recognition.
		self.autoRecognitionTypeList = settingsSizerHelper.addLabeledControl(
			_("Automatic recognition &type:"),
			wx.Choice,
			choices=[description for _name, description in self.autoRecognitionTypeChoices],
		)
		self.autoRecognitionTypeList.SetSelection(typeSelection)
		self.autoRecognitionTypeList.Bind(wx.EVT_CHOICE, self._onAutoRecognitionTypeChanged)
		# Translators: The label for a choice control to select the engine used for automatic recognition.
		self.autoRecognitionEngineList = settingsSizerHelper.addLabeledControl(
			_("Automatic recognition &engine:"),
			wx.Choice,
			choices=[],
		)
		self.autoRecognitionEngineList.Bind(wx.EVT_CHOICE, self._onAutoRecognitionEngineChanged)
		self._setAutoRecognitionEngineChoices(self.autoRecognitionEngineChoices, engineSelection)
		# Translators: The label for a text control to override the prompt used for automatic recognition.
		self.autoRecognitionPromptLabel = wx.StaticText(
			self,
			label=_("Automatic recognition &prompt:"),
		)
		self.autoRecognitionPromptCtrl = wx.TextCtrl(
			self,
			size=(-1, self.scaleSize(75)),
			style=wx.TE_MULTILINE,
		)
		autoRecognitionPromptSizer = wx.BoxSizer(wx.VERTICAL)
		autoRecognitionPromptSizer.Add(self.autoRecognitionPromptLabel)
		autoRecognitionPromptSizer.AddSpacer(guiHelper.SPACE_BETWEEN_ASSOCIATED_CONTROL_VERTICAL)
		autoRecognitionPromptSizer.Add(self.autoRecognitionPromptCtrl, proportion=1, flag=wx.EXPAND)
		settingsSizerHelper.addItem(autoRecognitionPromptSizer, flag=wx.EXPAND)
		# Translators: The label for a choice control to override the model used for automatic recognition.
		modelControlHelper = guiHelper.LabeledControlHelper(
			self,
			_("Automatic recognition &model:"),
			wx.Choice,
			choices=[],
		)
		self.autoRecognitionModelList = modelControlHelper.control
		modelSizer = wx.BoxSizer(wx.HORIZONTAL)
		modelSizer.Add(modelControlHelper.sizer, flag=wx.ALIGN_CENTER_VERTICAL)
		# Translators: The label for a button that fetches model names.
		self.autoRecognitionFetchModelsButton = wx.Button(self, label=_("&Fetch models"))
		modelSizer.Add(
			self.autoRecognitionFetchModelsButton,
			flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT,
			border=guiHelper.SPACE_BETWEEN_BUTTONS_HORIZONTAL,
		)
		settingsSizerHelper.addItem(modelSizer)
		self.autoRecognitionFetchModelsButton.Bind(wx.EVT_BUTTON, self._onFetchAutoRecognitionModels)
		# Translators: The label for a checkbox to prefer screenshots before downloading web image URLs.
		self.preferScreenshotForWebImagesCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("Prefer screenshots for &web images")),
		)
		self.preferScreenshotForWebImagesCheckBox.SetValue(conf["preferScreenshotForWebImages"])
		self._updateAutoRecognitionOverrideControls()
		self._updateAutoRecognitionControlState()

	def _getSelectedAutoRecognitionType(self) -> str:
		selection = self.autoRecognitionTypeList.GetSelection()
		if selection == wx.NOT_FOUND:
			return AUTO_RECOGNITION_OFF
		return self.autoRecognitionTypeChoices[selection][0]

	def _setAutoRecognitionEngineChoices(
		self,
		choices: List[Tuple[str, str]],
		selection: int = 0,
	) -> None:
		self.autoRecognitionEngineChoices = choices
		self.autoRecognitionEngineList.Clear()
		self.autoRecognitionEngineList.AppendItems([description for _name, description in choices])
		if choices:
			if selection == wx.NOT_FOUND or selection >= len(choices):
				selection = 0
			self.autoRecognitionEngineList.SetSelection(selection)
		self._updateAutoRecognitionControlState()

	def _updateAutoRecognitionControlState(self) -> None:
		isEnabled = self._getSelectedAutoRecognitionType() != AUTO_RECOGNITION_OFF
		self.autoRecognitionEngineList.Enable(isEnabled and bool(self.autoRecognitionEngineChoices))
		engine = getattr(self, "_autoRecognitionEngine", None)
		if hasattr(self, "autoRecognitionPromptCtrl"):
			promptEnabled = isEnabled and bool(engine and engine.isSupported("autoRecognitionPrompt"))
			self.autoRecognitionPromptLabel.Enable(promptEnabled)
			self.autoRecognitionPromptCtrl.Enable(promptEnabled)
		if hasattr(self, "autoRecognitionModelList"):
			self.autoRecognitionModelList.Enable(
				isEnabled and bool(engine and engine.isSupported("autoRecognitionModel")),
			)
		if hasattr(self, "autoRecognitionFetchModelsButton"):
			self.autoRecognitionFetchModelsButton.Enable(
				isEnabled and bool(engine and callable(getattr(engine, "fetchModelsChanger", None))),
			)
		if hasattr(self, "preferScreenshotForWebImagesCheckBox"):
			self.preferScreenshotForWebImagesCheckBox.Enable(isEnabled)

	def _onAutoRecognitionTypeChanged(self, evt: wx.CommandEvent) -> None:
		autoRecognitionType = self._getSelectedAutoRecognitionType()
		self._setAutoRecognitionEngineChoices(getAutoRecognitionEngineChoices(autoRecognitionType))
		self._updateAutoRecognitionOverrideControls()

	def _onAutoRecognitionEngineChanged(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		self._updateAutoRecognitionOverrideControls()

	def _getSelectedAutoRecognitionEngineName(self) -> str:
		engineSelection = self.autoRecognitionEngineList.GetSelection()
		if engineSelection == wx.NOT_FOUND or not self.autoRecognitionEngineChoices:
			return AUTO_RECOGNITION_CURRENT_ENGINE_NAME
		return self.autoRecognitionEngineChoices[engineSelection][0]

	def _getSelectedAutoRecognitionEngine(self) -> AbstractEngine | None:
		handler = getAutoRecognitionHandler(self._getSelectedAutoRecognitionType())
		if not handler:
			return None
		engineName = self._getSelectedAutoRecognitionEngineName()
		if engineName == AUTO_RECOGNITION_CURRENT_ENGINE_NAME:
			currentEngine = handler.getCurrentEngine()
			engineName = currentEngine.name if currentEngine else ""
		if not engineName or engineName == "empty":
			return None
		try:
			return handler.getEngineInstance(engineName)
		except Exception:
			log.debugWarning(f"Could not load automatic recognition engine {engineName!r}.", exc_info=True)
			return None

	def _getAutoRecognitionModelChoices(self, engine: AbstractEngine | None) -> List[Tuple[str, str]]:
		# Translators: A choice meaning automatic recognition should use the engine's regular model setting.
		choices = [("", _("Use the model selected in engine settings"))]
		if not engine or not engine.isSupported("model"):
			return choices
		seenModelNames = {""}
		try:
			availableModels = getattr(engine, "availableModels").values()
		except AttributeError:
			availableModels = []
		for modelInfo in availableModels:
			modelName = str(modelInfo.id)
			if not modelName or modelName in seenModelNames:
				continue
			seenModelNames.add(modelName)
			choices.append((modelName, modelInfo.displayName))
		autoModel = str(getattr(engine, "autoRecognitionModel", "")).strip()
		if autoModel and autoModel not in seenModelNames:
			choices.append((autoModel, autoModel))
		return choices

	def _setAutoRecognitionModelChoices(
		self,
		choices: List[Tuple[str, str]],
		selectedModel: str,
	) -> None:
		self.autoRecognitionModelChoices = choices
		self.autoRecognitionModelList.Clear()
		self.autoRecognitionModelList.AppendItems([description for _name, description in choices])
		modelNames = [name for name, _description in choices]
		try:
			selection = modelNames.index(selectedModel)
		except ValueError:
			selection = 0
		if choices:
			self.autoRecognitionModelList.SetSelection(selection)

	def _updateAutoRecognitionOverrideControls(self) -> None:
		self._autoRecognitionEngine = self._getSelectedAutoRecognitionEngine()
		engine = self._autoRecognitionEngine
		if engine and engine.isSupported("autoRecognitionPrompt"):
			self.autoRecognitionPromptCtrl.SetValue(engine.autoRecognitionPrompt)
		else:
			self.autoRecognitionPromptCtrl.SetValue("")
		selectedModel = str(getattr(engine, "autoRecognitionModel", "")).strip() if engine else ""
		self._setAutoRecognitionModelChoices(self._getAutoRecognitionModelChoices(engine), selectedModel)
		self._updateAutoRecognitionControlState()

	def updateDriverSettings(self) -> None:
		"""Refreshes dynamic automatic recognition controls after model discovery."""
		engine = getattr(self, "_autoRecognitionEngine", None)
		if not engine:
			self._updateAutoRecognitionOverrideControls()
			return
		selectedModel = str(getattr(engine, "autoRecognitionModel", "")).strip()
		self._setAutoRecognitionModelChoices(self._getAutoRecognitionModelChoices(engine), selectedModel)
		self._updateAutoRecognitionControlState()

	def _onFetchAutoRecognitionModels(self, evt: wx.CommandEvent) -> None:
		engine = getattr(self, "_autoRecognitionEngine", None)
		fetchModels = getattr(engine, "fetchModelsChanger", None)
		if callable(fetchModels):
			fetchModels(evt)
			return
		evt.Skip()

	def onSave(self) -> None:
		"""Saves the settings from the automatic recognition panel."""
		conf = config.conf["visAwareGeneral"]
		autoRecognitionType = self._getSelectedAutoRecognitionType()
		engineName = self._getSelectedAutoRecognitionEngineName()
		if autoRecognitionType == AUTO_RECOGNITION_IMAGE_DESCRIBER_MODE:
			autoRecognitionEngine = f"{AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX}{engineName}"
		elif autoRecognitionType == AUTO_RECOGNITION_OCR_MODE:
			autoRecognitionEngine = f"{AUTO_RECOGNITION_OCR_PREFIX}{engineName}"
		else:
			autoRecognitionEngine = AUTO_RECOGNITION_OFF
		conf["autoRecognitionEngine"] = autoRecognitionEngine
		conf["preferScreenshotForWebImages"] = self.preferScreenshotForWebImagesCheckBox.GetValue()
		engine = getattr(self, "_autoRecognitionEngine", None) or self._getSelectedAutoRecognitionEngine()
		if not engine:
			return
		if engine.isSupported("autoRecognitionPrompt"):
			engine.autoRecognitionPrompt = self.autoRecognitionPromptCtrl.GetValue()
		if engine.isSupported("autoRecognitionModel"):
			modelSelection = self.autoRecognitionModelList.GetSelection()
			if modelSelection == wx.NOT_FOUND or not self.autoRecognitionModelChoices:
				engine.autoRecognitionModel = ""
			else:
				engine.autoRecognitionModel = self.autoRecognitionModelChoices[modelSelection][0]
		engine.saveSettings()


class CustomOCRPanel(SettingsPanel):
	"""General settings panel for the Vis Aware add-on."""

	# Translators: The title of the general settings panel for the add-on.
	title = _("General")

	def makeSettings(self, sizer: wx.BoxSizer) -> None:
		"""
		Creates the settings controls for the general panel.

		:param sizer: The sizer to which controls will be added.
		"""
		settingsSizerHelper = BoxSizerHelper(self, sizer=sizer)
		conf = config.conf["visAwareGeneral"]
		# Translators: The label for a checkbox to copy recognition results to the clipboard.
		self.copyToClipboardCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("Copy recognition result to the &clipboard")),
		)
		self.copyToClipboardCheckBox.SetValue(conf["copyToClipboard"])
		# Translators: The label for a checkbox to use a browseable message for text results.
		self.useBrowseableMessageCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("Show text results in a &browsable message")),
		)
		self.useBrowseableMessageCheckBox.SetValue(conf["useBrowseableMessage"])
		# Translators: The label for a checkbox to automatically read recognition results shown as documents.
		self.autoSayAllOnResultCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("Automatically &read recognition result documents")),
		)
		self.autoSayAllOnResultCheckBox.SetValue(conf["autoSayAllOnResult"])
		# Translators: The label for a checkbox to enable verbose debug logging.
		self.verboseDebugLoggingCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("&Enable more verbose logging for debug purposes")),
		)
		self.verboseDebugLoggingCheckBox.SetValue(conf["verboseDebugLogging"])

		sourceTypeChoices = [description for _name, description in SOURCE_TYPES]
		# Translators: The label for a checklist of recognition sources available in the cycle command.
		self.sourceTypesList = settingsSizerHelper.addLabeledControl(
			_("Recognition &sources included when cycling:"),
			CustomCheckListBox,
			choices=sourceTypeChoices,
		)
		excludedSourceTypes = set(getExcludedSourceTypes(conf))
		self.sourceTypesList.SetCheckedItems(
			[
				index
				for index, (name, _description) in enumerate(SOURCE_TYPES)
				if name not in excludedSourceTypes
			],
		)
		self.sourceTypesList.Select(0)

		engineTypeChoices = [desc for name, desc in ENGINE_TYPES]
		# Translators: The label for a choice control to select the Vis Aware operating mode.
		self.engineTypeList = settingsSizerHelper.addLabeledControl(
			_("Vis Aware &mode:"),
			wx.Choice,
			choices=engineTypeChoices,
		)
		engineType = getConfigChoiceValue(conf, "engineType", ENGINE_TYPES)
		self.engineTypeList.SetSelection([name for name, desc in ENGINE_TYPES].index(engineType))

		settingsSizerHelper.addItem(wx.StaticLine(self, style=wx.LI_HORIZONTAL))

		# Translators: The label for a group box for NVDACN account settings.
		nvdacnGroupLabel = _("NVDACN account (for supported engines like Vivo OCR)")
		nvdacnGroupSizer = wx.StaticBoxSizer(wx.VERTICAL, self, nvdacnGroupLabel)
		nvdacnGroupBox = nvdacnGroupSizer.GetStaticBox()
		nvdacnGroup = BoxSizerHelper(nvdacnGroupBox, sizer=nvdacnGroupSizer)
		settingsSizerHelper.addItem(nvdacnGroup)

		# Translators: The label for the username text control in the NVDACN settings.
		nvdacnUserLabel = _("Username:")
		self.nvdacnUserCtrl = nvdacnGroup.addLabeledControl(
			nvdacnUserLabel,
			wx.TextCtrl,
			size=(self.scaleSize(250), -1),
		)
		self.nvdacnUserCtrl.SetValue(conf["nvdacnUser"])

		# Translators: The label for the password text control in the NVDACN settings.
		nvdacnPassLabel = _("Password:")
		self.nvdacnPassCtrl = nvdacnGroup.addLabeledControl(
			nvdacnPassLabel,
			wx.TextCtrl,
			style=wx.TE_PASSWORD,
			size=(self.scaleSize(250), -1),
		)

		from .secure_storage import SecureStorageError, unprotectString

		try:
			self.originalDecryptedPassword = unprotectString(conf["nvdacnPass"])
		except SecureStorageError:
			log.warning("Stored NVDACN password could not be unprotected.", exc_info=True)
			self.originalDecryptedPassword = ""
		self.nvdacnPassCtrl.SetValue(self.originalDecryptedPassword)

	def onSave(self) -> None:
		"""Saves the settings from the general panel."""
		conf = config.conf["visAwareGeneral"]
		conf["copyToClipboard"] = self.copyToClipboardCheckBox.GetValue()
		conf["verboseDebugLogging"] = self.verboseDebugLoggingCheckBox.GetValue()
		conf["useBrowseableMessage"] = self.useBrowseableMessageCheckBox.GetValue()
		conf["autoSayAllOnResult"] = self.autoSayAllOnResultCheckBox.GetValue()
		conf["engineType"] = ENGINE_TYPES[self.engineTypeList.GetSelection()][0]
		enabledSourceTypes = [SOURCE_TYPES[index][0] for index in self.sourceTypesList.GetCheckedItems()]
		setExcludedSourceTypes(
			conf,
			[name for name, _description in SOURCE_TYPES if name not in enabledSourceTypes],
		)
		currentSourceType = getConfigChoiceValue(conf, "sourceType", SOURCE_TYPES)
		if enabledSourceTypes and currentSourceType not in enabledSourceTypes:
			conf["sourceType"] = enabledSourceTypes[0]

		from .secure_storage import SecureStorageError, protectString

		conf["nvdacnUser"] = self.nvdacnUserCtrl.GetValue()

		currentPasswordInput = self.nvdacnPassCtrl.GetValue()
		if currentPasswordInput != self.originalDecryptedPassword:
			try:
				conf["nvdacnPass"] = protectString(currentPasswordInput)
			except SecureStorageError:
				log.error("NVDACN password could not be protected.", exc_info=True)
				conf["nvdacnPass"] = ""

	def isValid(self) -> bool:
		"""Validates the general settings panel."""
		if not self.sourceTypesList.GetCheckedItems():
			self._validationErrorMessageBox(
				# Translators: Shown when no recognition source is available for the cycle source command.
				message=_("At least one recognition source must be selected."),
				# Translators: The setting name used in the validation error for the recognition source checklist.
				option=_("Recognition sources included when cycling"),
			)
			return False
		return super().isValid()
