# Copyright (C) 2025 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Provides abstract base classes and handlers for various recognition engines."""

from abc import ABC, abstractmethod
import addonHandler
import base64
import config
from collections.abc import Iterator
from contentRecog import ContentRecognizer, LinesWordsResult, RecogImageInfo
from dataclasses import dataclass
from . import imageDescribers
import json
from logHandler import log
from threading import Event, Thread, current_thread
import wx
from io import BytesIO
from PIL import Image
from PIL.Image import LANCZOS
from typing import Optional, Any, List, Tuple, Callable

from .abstractEngine import AbstractEngine, AbstractEngineHandler
from .engineSettings import EngineSetting
from . import contentRecognizers
from . import network
from . import recogHistory
from .imageDescribers._prompts import DEFAULT_AUTO_RECOGNITION_PROMPT, buildImageDescriptionPrompt
from .conversation import QuestionStreamEvent, QuestionStreamFinished, QuestionStreamText
from .engineSettings import NumericEngineSetting, TextInputEngineSetting
from .requestLogging import redactForLog
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
AUTO_RECOGNITION_REQUEST_TIMEOUT = (3, 30)


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
	prompt: str | None = None
	isAutomaticRecognition: bool = False
	cancellationChecker: Callable[[], None] | None = None


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
	supportsAutomaticRecognition: bool = False
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

	def processApiResult(self, result: bytes) -> str | bool:
		"""
		Processes the API result to check for errors.

		:param result: The raw byte response from the server.
		:returns: An error message string on failure, or False on success.
		"""
		return False

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

	def _resetStreamingState(self) -> None:
		"""Resets provider-specific state before a streaming request."""
		pass

	def _getStreamingResponse(self, fullResponseText: str) -> dict[str, Any] | None:
		"""Returns the raw response to store for a streamed recognition."""
		return {"streamed_text": fullResponseText}

	def _consumeQuestionResponse(self) -> dict[str, Any] | None:
		"""Returns a raw non-streaming follow-up response, when a provider has one."""
		return None

	def _buildRecognitionRequest(
		self,
		isAutomaticRecognition: bool = False,
		cancellationChecker: Callable[[], None] | None = None,
	) -> RecognitionRequest:
		"""Captures request options, including the effective image-description prompt."""
		initialPrompt = getattr(self, "_initialPromptForDescribe", None)
		if initialPrompt:
			self._initialPromptForDescribe = None
			prompt = initialPrompt
		else:
			prompt = getattr(self, "prompt", None)
		if (
			self.configSectionName == "ImageDescriber"
			and self.textResult
			and isinstance(prompt, str)
			and prompt.strip()
			and not isAutomaticRecognition
		):
			prompt = buildImageDescriptionPrompt(
				prompt,
				useMarkdown=bool(config.conf["visAwareGeneral"]["useBrowseableMessage"]),
			)
		return RecognitionRequest(
			textResult=self.textResult,
			streamResult=self.streamResult,
			prompt=prompt if isinstance(prompt, str) and prompt.strip() else None,
			isAutomaticRecognition=isAutomaticRecognition,
			cancellationChecker=cancellationChecker,
		)

	def recognize(
		self,
		pixels: bytes,
		imageInfo: RecogImageInfo,
		onResult: Callable,
		*,
		isAutomaticRecognition: bool = False,
		runInBackground: bool = True,
		cancellationEvent: Event | None = None,
	) -> None:
		"""
		Starts the recognition process in a background thread.

		:param pixels: The raw pixel data of the image.
		:param imageInfo: Information about the image's location and size.
		:param onResult: The callback function to be called with the result.
		:param runInBackground: False to run on the caller's existing worker thread.
		:param cancellationEvent: An already armed event for serialized recognition.
		"""
		if cancellationEvent is None:
			cancellationEvent = Event()
		request = self._buildRecognitionRequest(
			isAutomaticRecognition,
			lambda: self._checkCancelled(cancellationEvent),
		)
		self._cancellationEvent = cancellationEvent
		if not runInBackground:
			self._recognitionThread = current_thread()
			self._recognitionWorker(pixels, imageInfo, onResult, self._cancellationEvent, request)
			return
		self._recognitionThread = Thread(
			name=f"RecognitionThread-{self.name}",
			target=self._recognitionWorker,
			args=(pixels, imageInfo, onResult, self._cancellationEvent, request),
		)
		self._recognitionThread.start()

	def recognizeImage(
		self,
		image: Image.Image,
		onResult: Callable,
		*,
		isAutomaticRecognition: bool = False,
		runInBackground: bool = True,
		cancellationEvent: Event | None = None,
	) -> None:
		"""
		Starts recognition for an already available PIL image.

		This avoids converting a PIL image to raw pixels only for the worker to
		reconstruct the same image again.

		:param image: The image to recognize.
		:param onResult: The callback function to be called with the result.
		:param runInBackground: False to run on the caller's existing worker thread.
		:param cancellationEvent: An already armed event for serialized recognition.
		"""
		if cancellationEvent is None:
			cancellationEvent = Event()
		request = self._buildRecognitionRequest(
			isAutomaticRecognition,
			lambda: self._checkCancelled(cancellationEvent),
		)
		self._cancellationEvent = cancellationEvent
		if not runInBackground:
			self._recognitionThread = current_thread()
			self._recognitionImageWorker(image, onResult, self._cancellationEvent, request)
			return
		self._recognitionThread = Thread(
			name=f"RecognitionThread-{self.name}",
			target=self._recognitionImageWorker,
			args=(image, onResult, self._cancellationEvent, request),
		)
		self._recognitionThread.start()

	def cancel(self, isUserInitiated: bool = True) -> None:
		if hasattr(self, "_initialPromptForDescribe"):
			self._initialPromptForDescribe = None
		if self._cancellationEvent:
			self._cancellationEvent.is_user_initiated = isUserInitiated
			self._cancellationEvent.set()

	def _prepareCancellation(self) -> Event:
		"""Arms cancellation before a controller exposes this engine to callers."""
		self._cancellationEvent = Event()
		return self._cancellationEvent

	def terminate(self) -> None:
		if hasattr(self, "_initialPromptForDescribe"):
			self._initialPromptForDescribe = None
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
		if request.isAutomaticRecognition and not self.supportsAutomaticRecognition:
			# Translators: An error when an engine is called automatically but only supports manual recognition.
			raise ApiError(_("This engine does not support automatic recognition."))
		imageContent = self._prepareImageContent(imageObject, imageInfo)
		if not imageContent:
			# Translators: An error message when failing to prepare the image for upload.
			raise ApiError(_("Failed to prepare image content for upload."))
		self._checkCancelled(cancellationEvent)
		requestParams = self._buildRequestParams(imageContent, request)
		self._checkCancelled(cancellationEvent)
		if config.conf["visAwareGeneral"]["verboseDebugLogging"]:
			log.debug(f"Request Params for {self.name}: {redactForLog(requestParams)}")
		if request.isAutomaticRecognition:
			requestParams = {
				**requestParams,
				"cancelCheck": lambda: self._checkCancelled(cancellationEvent),
				"timeout": AUTO_RECOGNITION_REQUEST_TIMEOUT,
			}
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

	def _getApiResult(self, requestParams: dict[str, Any], cancellationEvent: Event) -> dict[str, Any]:
		"""Returns a validated response; multi-step clients can override the request flow."""
		response = network.sendRequest(**requestParams)
		self._checkCancelled(cancellationEvent)
		apiErrorMessage = self.processApiResult(response.content)
		if apiErrorMessage:
			raise ApiError(str(apiErrorMessage))
		return self._convertToJson(response.content)

	def _handleStandardResponse(
		self,
		requestParams: dict,
		imageInfo: RecogImageInfo,
		cancellationEvent: Event,
		request: RecognitionRequest,
	) -> Any:
		"""Handles a standard, non-streaming API response."""
		jsonResponse = self._getApiResult(requestParams, cancellationEvent)
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
		self._resetStreamingState()
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
		streamingResponse: dict[str, Any] | None = None
		try:
			streamingResponse = self._getStreamingResponse(fullResponseText)
		except StreamIncompleteError as e:
			if not fullResponseText or fullResponseText.isspace():
				raise
			incompleteReason = incompleteReason or str(e)
			log.warning(
				"Streaming response did not include a complete provider response. "
				f"engine={self.name}, reason={incompleteReason}",
			)
		historyEntry = None
		if fullResponseText and self.originalImage:
			historyEntry = recogHistory.createEntry(
				self,
				self.originalImage,
				streamingResponse or {"streamed_text": fullResponseText},
			)
		wx.CallAfter(
			onResult,
			StreamFinished(
				fullResponseText,
				incompleteReason=incompleteReason,
				historyEntry=historyEntry,
			),
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
	def _convertToJson(data: bytes) -> Any:
		"""Decodes and parses JSON data from bytes."""
		return json.loads(data.decode("utf-8", errors="ignore"))

	@classmethod
	def imageQualitySetting(cls) -> EngineSetting:
		# Translators: The label for an engine setting to control image quality.
		return NumericEngineSetting("quality", _("Upload image quality"))

	@classmethod
	def autoRecognitionPromptSetting(cls) -> EngineSetting:
		# Translators: The label for an engine setting to override the prompt used for automatic recognition.
		setting = TextInputEngineSetting(
			"autoRecognitionPrompt",
			_("Automatic recognition &prompt"),
			multiline=True,
		)
		if cls.configSectionName == "ImageDescriber":
			setting.configKey = "autoRecognitionPromptV2"
		return setting

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


class BaseDescriber(BaseRecognizer):
	"""Abstract base class for all image description engines."""

	configSectionName = "ImageDescriber"
	supportsQuestions: bool = False
	supportsQuestionStreaming: bool = False
	_autoRecognitionPrompt: str = DEFAULT_AUTO_RECOGNITION_PROMPT
	_initialPromptForDescribe: str | None = None

	def applyAutoRecognitionOverrides(self) -> None:
		"""Applies automatic settings and the concise default image prompt."""
		super().applyAutoRecognitionOverrides()
		if not self.autoRecognitionPrompt and self.isSupported("prompt"):
			self.prompt = DEFAULT_AUTO_RECOGNITION_PROMPT

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
		yield QuestionStreamFinished(answer, response=self._consumeQuestionResponse())

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
		yield QuestionStreamFinished(answer, response=self._consumeQuestionResponse())

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
		self._resetStreamingState()
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
		streamingResponse: dict[str, Any] | None = None
		try:
			streamingResponse = self._getStreamingResponse(fullResponseText)
		except StreamIncompleteError as e:
			incompleteReason = incompleteReason or str(e)
			log.warning(
				"Streaming follow-up did not include a complete provider response. "
				f"engine={self.name}, reason={incompleteReason}",
			)
		yield QuestionStreamFinished(
			answer,
			incompleteReason=incompleteReason,
			response=streamingResponse,
		)

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


def getAutoRecognitionEngineList(handler: type[AbstractEngineHandler]) -> List[Tuple[str, str]]:
	"""Returns enabled engines that declare support for automatic recognition, without instantiating them."""
	return [
		(name, description)
		for name, description in handler.getEngineList()
		if name != "empty" and getattr(handler.getEngine(name), "supportsAutomaticRecognition", False)
	]


def getAutoRecognitionEngineChoices(autoRecognitionType: str) -> List[Tuple[str, str]]:
	"""Returns engine choices for the selected automatic recognition type."""
	if autoRecognitionType == AUTO_RECOGNITION_IMAGE_DESCRIBER_MODE:
		choices = [
			# Translators: A choice for automatic recognition to use the current image description engine.
			(AUTO_RECOGNITION_CURRENT_ENGINE_NAME, _("Current image description engine")),
		]
		choices.extend(getAutoRecognitionEngineList(ImageDescriberHandler))
		return choices
	if autoRecognitionType == AUTO_RECOGNITION_OCR_MODE:
		choices = [
			# Translators: A choice for automatic recognition to use the current OCR engine.
			(AUTO_RECOGNITION_CURRENT_ENGINE_NAME, _("Current OCR engine")),
		]
		choices.extend(getAutoRecognitionEngineList(CustomOCRHandler))
		return choices
	return []


def getAutoRecognitionHandler(autoRecognitionType: str) -> type[AbstractEngineHandler] | None:
	"""Returns the engine handler for the selected automatic recognition type."""
	if autoRecognitionType == AUTO_RECOGNITION_IMAGE_DESCRIBER_MODE:
		return ImageDescriberHandler
	if autoRecognitionType == AUTO_RECOGNITION_OCR_MODE:
		return CustomOCRHandler
	return None
