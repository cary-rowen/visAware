# Copyright (C) 2025 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

import functools
import os
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

import addonHandler
import api
import config
import NVDAObjects
import ui
from contentRecog import (
	RecogImageInfo,
	RecognitionResult,
	SimpleTextResult,
	recogUi,
)
from logHandler import log
from PIL import Image, ImageGrab

from ._screenCapture import captureNvdaPixels, imageFromNvdaPixels, isScreenCurtainCaptureSupported
from .exceptions import ApiError, AuthenticationError, CancellationError, NetworkError
from . import recogHistory
from .markdownRenderer import showMarkdownBrowseableMessage
from .recogHandler import StreamFinished, StreamText

addonHandler.initTranslation()

if TYPE_CHECKING:
	from ._taskCues import TaskCueManager
	from .streamingSpeech import StreamingSpeechPresenter


class _AutoSayAllRecogResultNVDAObject(recogUi.RecogResultNVDAObject):
	"""Recognition result document that starts say-all after first focus."""

	def __init__(self, *args: Any, **kwargs: Any) -> None:
		super().__init__(*args, **kwargs)
		self._shouldSayAllOnFirstFocus = True

	def event_gainFocus(self) -> None:
		super().event_gainFocus()
		if not self._shouldSayAllOnFirstFocus:
			return
		self._shouldSayAllOnFirstFocus = False
		from speech import sayAll

		sayAll.SayAllHandler.readText(sayAll.CURSOR.CARET)


class RecognitionRuntime:
	"""Owns the state and worker callbacks for a manual recognition request."""

	def __init__(
		self,
		taskCues: "TaskCueManager",
		streamingSpeechPresenter: "StreamingSpeechPresenter",
		ocrHandler: Any,
		descHandler: Any,
		cancelOtherTasks: Callable[[bool], bool],
	) -> None:
		self._taskCues = taskCues
		self._streamingSpeechPresenter = streamingSpeechPresenter
		self._ocrHandler = ocrHandler
		self._descHandler = descHandler
		self._cancelOtherTasks = cancelOtherTasks
		self._activeEngine: Any | None = None
		self._activeRecognitionSequence = 0
		self._recognitionCue: Any | None = None
		self._terminated = False
		self.resultHook: Callable[[Any], bool] | None = None

	def setHandlers(self, ocrHandler: Any, descHandler: Any) -> None:
		self._ocrHandler = ocrHandler
		self._descHandler = descHandler

	def _prepareImageFromObject(
		self,
		getObjectFunc: Callable[[], NVDAObjects],
		recognizer: Any,
	) -> tuple[RecogImageInfo, Image.Image] | None:
		"""Validate an object, capture its screen image, and prepare recognition info."""
		notVisibleMsg = _("Content is not visible")
		targetObject = getObjectFunc()
		if not recognizer.validateObject(targetObject):
			return None
		try:
			left, top, width, height = targetObject.location
			if not recognizer.validateCaptureBounds(targetObject.location):
				return None
		except TypeError:
			log.debugWarning("Object returned invalid location %r" % getattr(targetObject, "location", "N/A"))
			ui.message(notVisibleMsg)
			return None
		try:
			imageInfo = RecogImageInfo.createFromRecognizer(left, top, width, height, recognizer)
		except ValueError:
			ui.message(notVisibleMsg)
			return None
		try:
			pixels = captureNvdaPixels(imageInfo)
			if pixels is None:
				bbox = (
					imageInfo.screenLeft,
					imageInfo.screenTop,
					imageInfo.screenLeft + imageInfo.screenWidth,
					imageInfo.screenTop + imageInfo.screenHeight,
				)
				recognizeImage = ImageGrab.grab(bbox=bbox)
			else:
				recognizeImage = imageFromNvdaPixels(pixels, imageInfo)
		except Exception:
			log.error(f"Failed to grab image from object: {targetObject.name}", exc_info=True)
			ui.message(_("Failed to capture screen image."))
			return None
		return imageInfo, recognizeImage

	def cancel(self, isUserInitiated: bool = True) -> bool:
		"""Cancel the active manual recognition task, if any."""
		if self._activeEngine:
			self._activeRecognitionSequence += 1
			self._taskCues.stop(self._recognitionCue)
			activeEngine = self._activeEngine
			log.info(f"Cancelling active task from engine '{activeEngine.name}'.")
			activeEngine.cancel(isUserInitiated=isUserInitiated)
			if activeEngine._recognitionThread and activeEngine._recognitionThread.is_alive():
				activeEngine._recognitionThread.join(timeout=0.2)
			self._streamingSpeechPresenter.cancel()
			self._activeEngine = None
			if isUserInitiated:
				ui.message(_("Recognition cancelled"))
			return True
		if self._streamingSpeechPresenter.isActive:
			self._activeRecognitionSequence += 1
			self._taskCues.stop(self._recognitionCue)
			log.info("Cancelling active streaming speech presenter without active recognition thread.")
			self._streamingSpeechPresenter.cancel()
			return True
		return False

	def terminate(self) -> None:
		"""Invalidate worker callbacks and stop any active recognition."""
		self._terminated = True
		self._activeRecognitionSequence += 1
		self.cancel(isUserInitiated=False)
		self._taskCues.stop(self._recognitionCue)
		self._recognitionCue = None

	def getImageFromClipboard(self) -> Image.Image | None:
		try:
			clipboardContent = ImageGrab.grabclipboard()
			if isinstance(clipboardContent, Image.Image):
				log.debug("Found raw image data in clipboard.")
				return clipboardContent.convert("RGB")
			if isinstance(clipboardContent, list) and clipboardContent:
				log.debug(f"Found file paths in clipboard from ImageGrab: {clipboardContent}")
				try:
					return Image.open(clipboardContent[0]).convert("RGB")
				except (OSError, FileNotFoundError):
					log.warning(f"File from clipboard is not a valid image: {clipboardContent[0]}")
					return None
		except Exception:
			log.debug(
				"ImageGrab.grabclipboard failed, will try reading clipboard as text path.",
				exc_info=True,
			)
		try:
			path = api.getClipData()
			if path and os.path.isfile(path):
				log.debug(f"Found a text file path in clipboard: {path}")
				try:
					return Image.open(path).convert("RGB")
				except OSError:
					log.warning(f"Text path points to a non-image file: {path}")
		except OSError:
			log.debug("api.getClipData() failed, likely due to non-text clipboard format.")
		return None

	def _getImageFromClipboardSource(self) -> tuple[RecogImageInfo, Image.Image] | None:
		recognizeImage = self.getImageFromClipboard()
		if not recognizeImage:
			ui.message(_("No image in clipboard"))
			return None
		imageInfo = RecogImageInfo(0, 0, recognizeImage.width, recognizeImage.height, 1)
		return imageInfo, recognizeImage

	def getImageFromSource(
		self,
		currentSource: str,
		recognizer: Any,
	) -> tuple[RecogImageInfo, Image.Image] | None:
		sourceHandlers = {
			"clipboardImage": self._getImageFromClipboardSource,
			"navigatorObject": functools.partial(
				self._prepareImageFromObject,
				api.getNavigatorObject,
				recognizer,
			),
			"foreGroundWindow": functools.partial(
				self._prepareImageFromObject,
				api.getForegroundObject,
				recognizer,
			),
			"wholeDesktop": functools.partial(self._prepareImageFromObject, api.getDesktopObject, recognizer),
		}
		handler = sourceHandlers.get(currentSource)
		if handler:
			return handler()
		log.warning(f"Unknown image source type: {currentSource}")
		return None

	def getCurrentEngine(self, currentEngineType: str) -> Any | None:
		if currentEngineType == "OCR":
			return self._ocrHandler.getCurrentEngine() if self._ocrHandler else None
		if currentEngineType == "ImageDescriber":
			return self._descHandler.getCurrentEngine() if self._descHandler else None
		return None

	def showRecognitionResultDocument(self, result: RecognitionResult, autoSayAll: bool = False) -> None:
		resultObjectClass = _AutoSayAllRecogResultNVDAObject if autoSayAll else recogUi.RecogResultNVDAObject
		displayObject = resultObjectClass(result=result)
		displayObject.setFocus()

	def showHtmlRecognitionResult(self, result: RecognitionResult) -> bool:
		htmlContent = getattr(result, "htmlContent", None)
		if not isinstance(htmlContent, str) or not htmlContent.strip():
			return False
		showMarkdownBrowseableMessage(htmlContent, title=_("Recognition result"))
		return True

	def isScreenCurtainRunning(self) -> bool:
		from screenCurtain import screenCurtain as screenCurtainController

		return screenCurtainController is not None and screenCurtainController.enabled

	def _onRecognitionResult(self, result: Any) -> None:
		try:
			if self.resultHook is not None:
				try:
					if self.resultHook(result):
						return
				except Exception:
					log.error("Error in recognition result hook.", exc_info=True)
			if isinstance(result, Exception):
				if isinstance(result, CancellationError):
					if getattr(result.event, "is_user_initiated", True):
						ui.message(_("Recognition cancelled"))
					return
				if isinstance(result, AuthenticationError):
					message = _("Authentication failed: {}").format(result)
				elif isinstance(result, NetworkError):
					message = _("Network error: {}").format(result)
				elif isinstance(result, ApiError):
					message = _("Recognition failed: {}").format(result)
				else:
					log.error("An unexpected error occurred during recognition.", exc_info=result)
					message = _("Recognition failed with an unexpected error.")
				ui.message(message)
				return
			if not isinstance(result, RecognitionResult):
				log.error(f"Received an unknown result type from recognition: {type(result)}")
				ui.message(_("Recognition returned an unknown result type."))
				return
			historyEntry = recogHistory.getAttachedEntry(result)
			if historyEntry:
				recogHistory.addEntry(historyEntry, result=result)
			conf = config.conf["visAwareGeneral"]
			if conf["copyToClipboard"]:
				api.copyToClip(result.text, notify=True)
			if self.showHtmlRecognitionResult(result):
				return
			if isinstance(result, SimpleTextResult):
				forceVirtualDocument = getattr(result, "forceVirtualDocument", False)
				if getattr(result, "forceBrowseableMessage", False) or (
					conf["useBrowseableMessage"] and not forceVirtualDocument
				):
					showMarkdownBrowseableMessage(result.text, title=_("Recognition result"))
				elif forceVirtualDocument:
					self.showRecognitionResultDocument(result, autoSayAll=conf["autoSayAllOnResult"])
				else:
					ui.message(result.text)
			else:
				self.showRecognitionResultDocument(result, autoSayAll=conf["autoSayAllOnResult"])
		finally:
			log.debug("Recognition task completed. Clearing active engine reference.")
			self._activeEngine = None

	def _onStreamingRecognitionResult(self, result: Any) -> None:
		if isinstance(result, StreamText):
			if result.replace:
				self._streamingSpeechPresenter.cancel()
				self._streamingSpeechPresenter.start()
			self._streamingSpeechPresenter.addText(result.text)
			return
		if isinstance(result, StreamFinished):
			try:
				if result.historyEntry:
					recogHistory.addEntry(result.historyEntry, text=result.text)
				finalMessage = None
				if result.incompleteReason:
					finalMessage = _("Recognition was interrupted. The result may be incomplete.")
				self._streamingSpeechPresenter.finish(finalMessage=finalMessage)
				if config.conf["visAwareGeneral"]["copyToClipboard"]:
					api.copyToClip(result.text)
			finally:
				log.debug("Streaming recognition task completed. Clearing active engine reference.")
				self._activeEngine = None
			return
		if isinstance(result, Exception):
			log.warning("Streaming recognition result received exception.", exc_info=result)
			self._streamingSpeechPresenter.cancel()
			self._onRecognitionResult(result)
			return
		log.error(f"Received an unknown streaming result type: {type(result)}")
		ui.message(_("Recognition returned an unknown result type."))
		self._activeEngine = None

	def makeRecognitionCallback(self, recognitionSequence: int, streamResult: bool) -> Callable[[Any], None]:
		cueHandle = self._recognitionCue

		def onResult(result: Any) -> None:
			if getattr(self, "_terminated", False) or recognitionSequence != self._activeRecognitionSequence:
				self._taskCues.stop(cueHandle)
				log.debug(
					"Ignoring result from a superseded recognition task. "
					f"callbackSequence={recognitionSequence}, activeSequence={self._activeRecognitionSequence}, "
					f"streamResult={streamResult}, resultType={type(result)}",
				)
				return
			if not streamResult or not isinstance(result, StreamText) or result.text.strip():
				self._taskCues.stop(cueHandle)
			if streamResult:
				self._onStreamingRecognitionResult(result)
			else:
				self._onRecognitionResult(result)

		return onResult

	def execute(
		self,
		gesture: Any,
		currentSource: str,
		currentEngineType: str,
		simpleText: bool,
		imageData: tuple | None = None,
	) -> None:
		if getattr(self, "_terminated", False):
			return
		try:
			self._cancelOtherTasks(False)
			self.cancel(isUserInitiated=False)
			self._taskCues.stop(self._recognitionCue)
			self._recognitionCue = None
			engine = self.getCurrentEngine(currentEngineType)
			self._activeRecognitionSequence += 1
			recognitionSequence = self._activeRecognitionSequence
			self._activeEngine = engine
			if not engine or engine.name == "empty":
				ui.message(_("No recognition engine is configured."))
				self._activeEngine = None
				return
			if (
				currentSource != "clipboardImage"
				and self.isScreenCurtainRunning()
				and not isScreenCurtainCaptureSupported()
			):
				ui.message(_("Please disable screen curtain before recognition."))
				self._activeEngine = None
				return
			conf = config.conf["visAwareGeneral"]
			shouldStream = (
				simpleText
				and not conf["useBrowseableMessage"]
				and bool(getattr(engine, "supportsStreaming", False))
				and bool(getattr(engine, "isStreaming", False))
			)
			engine.textResult = simpleText
			engine.streamResult = shouldStream
			if imageData is None:
				imageData = self.getImageFromSource(currentSource, engine)
			if not imageData:
				engine.streamResult = False
				if hasattr(engine, "_initialPromptForDescribe"):
					engine._initialPromptForDescribe = None
				self._activeEngine = None
				return
			imageInfo, recognizeImage = imageData
			pixels = recognizeImage.tobytes("raw", "BGRX")
			ui.message(_("Recognizing"))
			self._recognitionCue = self._taskCues.start()
			if shouldStream:
				self._streamingSpeechPresenter.start()
			onResult = self.makeRecognitionCallback(recognitionSequence, shouldStream)
			engine.recognize(pixels, imageInfo, onResult)
		except Exception as e:
			self._taskCues.stop(self._recognitionCue)
			log.error(f"Error preparing for recognition: {e!r}", exc_info=True)
			if "shouldStream" in locals() and shouldStream:
				self._streamingSpeechPresenter.cancel()
			if "engine" in locals() and engine:
				engine.streamResult = False
				if hasattr(engine, "_initialPromptForDescribe"):
					engine._initialPromptForDescribe = None
			ui.message(
				_("An error occurred before starting recognition. Please check your settings and try again."),
			)
			self._activeEngine = None
