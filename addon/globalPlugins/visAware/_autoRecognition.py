# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Automatic recognition for web images and selected screen objects."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable
from io import BytesIO
from threading import Lock, Thread
import time
from typing import Any
from urllib.parse import urldefrag, urlsplit

import api
import config
import controlTypes
import textInfos
import ui
import wx
from contentRecog import RecogImageInfo, RecognitionResult
from logHandler import log
from PIL import Image, ImageGrab

from . import recogHistory
from .exceptions import CancellationError
from .network import sendRequest
from .recogHandler import (
	AUTO_RECOGNITION_CURRENT_ENGINE_NAME,
	AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX,
	AUTO_RECOGNITION_OCR_PREFIX,
	AUTO_RECOGNITION_OFF,
	CustomOCRHandler,
	ImageDescriberHandler,
	StreamFinished,
	StreamText,
	getEffectiveAutoRecognitionEngine,
)
from .streamingSpeech import StreamingSpeechPresenter

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 10_000_000
MAX_CACHE_ENTRIES = 20
DOWNLOAD_TIMEOUT = (2, 5)
DOWNLOAD_CHUNK_SIZE = 16 * 1024
DESCRIPTION_DEBOUNCE_MS = 200
START_TONE_HZ = 230
START_TONE_LENGTH_MS = 30
SCREENSHOT_OBJECT_NAMES = frozenset(("", "图片", "图形", "图像", "Image"))
SCREENSHOT_OBJECT_ROLES = frozenset((controlTypes.Role.BUTTON, controlTypes.Role.LISTITEM))
WEB_OBJECT_MODULE_PREFIXES = (
	"NVDAObjects.IAccessible.mozilla",
	"NVDAObjects.IAccessible.MSHTML",
	"NVDAObjects.IAccessible.webKit",
	"NVDAObjects.IAccessible.chromium",
	"NVDAObjects.UIA.web",
	"NVDAObjects.UIA.chromium",
	"NVDAObjects.UIA.spartanEdge",
)
WEB_OBJECT_ATTRIBUTE_NAMES = ("HTMLAttributes", "ariaProperties")
WEB_IA2_ATTRIBUTE_KEYS = ("tag", "src", "xml-roles")
IMAGE_REQUEST_HEADERS = {
	"User-Agent": "Mozilla/5.0",
	"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}
OBJECT_SRC_KEYS = ("src", "IAccessible2::attribute_src", "HTMLAttrib::src", "IAccessible::value", "value")
CONTROL_FIELD_SRC_KEYS = OBJECT_SRC_KEYS
SCREENSHOT_CONTENT_KEY_PREFIX = "screenshotImage:"
RECOGNITION_KEY_SEPARATOR = "|"


def _verboseDebugLogging() -> bool:
	try:
		return bool(config.conf["visAwareGeneral"]["verboseDebugLogging"])
	except Exception:
		return False


def _preferScreenshotForWebImages() -> bool:
	try:
		return bool(config.conf["visAwareGeneral"]["preferScreenshotForWebImages"])
	except Exception:
		return False


def _debug(message: str) -> None:
	if _verboseDebugLogging():
		log.io(f"Vis Aware automatic recognition: {message}")


def _playStartTone() -> None:
	try:
		from tones import beep
	except Exception:
		log.debugWarning("Could not play automatic recognition start tone.", exc_info=True)
		return
	wx.CallAfter(beep, START_TONE_HZ, START_TONE_LENGTH_MS)


def _urlForLog(url: str) -> str:
	try:
		parts = urlsplit(url)
	except ValueError:
		return "<invalid url>"
	path = parts.path
	if len(path) > 80:
		path = f"{path[:77]}..."
	return f"{parts.scheme}://{parts.netloc}{path}"


def _stripFragment(url: str) -> str:
	url, _fragment = urldefrag(url)
	return url


def _isSupportedImageUrl(url: str) -> bool:
	try:
		parts = urlsplit(url)
	except ValueError:
		return False
	return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def _normalizeImageSrc(value: str) -> str | None:
	src = _stripFragment(value.strip())
	return src if _isSupportedImageUrl(src) else None


def makeUrlKey(src: str) -> str:
	return f"url:{src}"


def makeScreenshotContentKey(image: Image.Image) -> tuple[str, Image.Image]:
	rgbImage = image if image.mode == "RGB" else image.convert("RGB")
	digest = hashlib.sha256()
	digest.update(f"{rgbImage.width}x{rgbImage.height}\0".encode("ascii"))
	digest.update(rgbImage.tobytes())
	return f"{SCREENSHOT_CONTENT_KEY_PREFIX}{digest.hexdigest()}", rgbImage


def makeRecognitionScopedKey(recognitionKey: str, targetKey: str) -> str:
	return f"{recognitionKey}{RECOGNITION_KEY_SEPARATOR}{targetKey}"


def getRecognitionKeyFromScopedKey(scopedKey: str) -> str | None:
	recognitionKey, separator, _targetKey = scopedKey.partition(RECOGNITION_KEY_SEPARATOR)
	return recognitionKey if separator else None


def _parseSemicolonAttributes(value: str) -> dict[str, str]:
	attrs: dict[str, str] = {}
	key = ""
	buf = ""
	inEscape = False
	for char in value:
		if inEscape:
			buf += char
			inEscape = False
		elif char == "\\":
			inEscape = True
		elif char == "=" and not key:
			key = buf
			buf = ""
		elif char == ";":
			if key:
				attrs[key] = buf
			key = ""
			buf = ""
		else:
			buf += char
	if key:
		attrs[key] = buf
	return attrs


def _getMappingValue(container: Any, key: str) -> str | None:
	try:
		value = container.get(key)
	except AttributeError:
		return None
	except Exception:
		log.debugWarning(f"Could not read image {key!r} attribute.", exc_info=True)
		return None
	if isinstance(value, str) and value.strip():
		return value.strip()
	return None


def _getImageSrcFromMapping(container: Any, keys: tuple[str, ...]) -> str | None:
	for key in keys:
		value = _getMappingValue(container, key)
		if not value:
			continue
		src = _normalizeImageSrc(value)
		if src:
			return src
		if _verboseDebugLogging():
			_debug(f"Ignoring unsupported image URL from {key!r}: {value[:80]!r}")
	return None


def getImageSrcFromObject(obj: Any) -> str | None:
	"""Returns an image URL exposed by an accessibility object, if available."""
	for attrName in ("IA2Attributes", "HTMLAttributes", "ariaProperties"):
		try:
			attrs = getattr(obj, attrName, None)
		except Exception:
			log.debugWarning(f"Could not read {attrName} from focused image object.", exc_info=True)
			continue
		src = _getImageSrcFromMapping(attrs, OBJECT_SRC_KEYS)
		if src:
			return src
	try:
		rawAriaProperties = getattr(getattr(obj, "UIAElement", None), "currentAriaProperties", "")
	except Exception:
		rawAriaProperties = ""
	if isinstance(rawAriaProperties, str) and rawAriaProperties:
		src = _parseSemicolonAttributes(rawAriaProperties).get("src")
		if src:
			return _normalizeImageSrc(src)
	return None


def getImageSrcFromControlField(field: Any) -> str | None:
	"""Returns an image URL exposed by a browse mode control field, if available."""
	return _getImageSrcFromMapping(field, CONTROL_FIELD_SRC_KEYS)


def getBrowseModeCaretInfo(cursorManager: Any) -> textInfos.TextInfo | None:
	try:
		return cursorManager.makeTextInfo(textInfos.POSITION_CARET)
	except Exception:
		_debug("Could not resolve browse mode caret info.")
		return None


def getImageObjectFromBrowseModeCaretInfo(info: textInfos.TextInfo | None) -> Any | None:
	if not info:
		return None
	try:
		return info.NVDAObjectAtStart
	except Exception:
		_debug("Could not resolve NVDAObject at browse mode caret.")
		return None


def getImageControlFieldFromBrowseModeCaretInfo(info: textInfos.TextInfo | None) -> Any | None:
	if not info:
		return None
	try:
		fieldInfo = info.copy()
		fieldInfo.expand(textInfos.UNIT_CHARACTER)
		fields = fieldInfo.getTextWithFields()
	except Exception:
		_debug("Could not resolve control field at browse mode caret.")
		return None
	if isinstance(fields, str):
		return None
	for item in reversed(fields):
		if not isinstance(item, textInfos.FieldCommand) or item.command != "controlStart":
			continue
		field = item.field
		if field and field.get("role") == controlTypes.Role.GRAPHIC:
			return field
	return None


def getImageSrcFromBrowseModeCaret(cursorManager: Any) -> str | None:
	_obj, src = getImageObjectAndSrcFromBrowseModeCaret(cursorManager)
	return src


def getImageObjectAndSrcFromBrowseModeCaret(cursorManager: Any) -> tuple[Any | None, str | None]:
	info = getBrowseModeCaretInfo(cursorManager)
	field = getImageControlFieldFromBrowseModeCaretInfo(info)
	if field:
		src = getImageSrcFromControlField(field)
		if not src:
			obj = getImageObjectFromBrowseModeCaretInfo(info)
			if getattr(obj, "role", None) == controlTypes.Role.GRAPHIC:
				return obj, None
			return None, None
		obj = None
		if _preferScreenshotForWebImages():
			obj = getImageObjectFromBrowseModeCaretInfo(info)
		if getattr(obj, "role", None) == controlTypes.Role.GRAPHIC:
			return obj, src
		return None, src
	obj = getImageObjectFromBrowseModeCaretInfo(info)
	if getattr(obj, "role", None) == controlTypes.Role.GRAPHIC:
		src = getImageSrcFromObject(obj)
		if src:
			return obj, src
		return obj, None
	return None, None


def getFocusedImageSrc() -> str | None:
	try:
		focus = api.getFocusObject()
	except Exception:
		return None
	if getattr(focus, "role", None) != controlTypes.Role.GRAPHIC:
		return None
	return getImageSrcFromObject(focus)


def getNavigatorImageSrc() -> str | None:
	try:
		nav = api.getNavigatorObject()
	except Exception:
		return None
	if getattr(nav, "role", None) != controlTypes.Role.GRAPHIC:
		return None
	return getImageSrcFromObject(nav)


def getGraphicScreenshotTargetKey(obj: Any) -> str | None:
	if getattr(obj, "role", None) != controlTypes.Role.GRAPHIC:
		return None
	location = getObjectLocation(obj)
	if not location:
		return None
	return makeScreenshotTargetKey(obj, location)


def getFocusedGraphicScreenshotTargetKey() -> str | None:
	try:
		return getGraphicScreenshotTargetKey(api.getFocusObject())
	except Exception:
		return None


def getNavigatorGraphicScreenshotTargetKey() -> str | None:
	try:
		return getGraphicScreenshotTargetKey(api.getNavigatorObject())
	except Exception:
		return None


def getBrowseModeGraphicScreenshotTargetKey(cursorManager: Any) -> str | None:
	info = getBrowseModeCaretInfo(cursorManager)
	obj = getImageObjectFromBrowseModeCaretInfo(info)
	return getGraphicScreenshotTargetKey(obj)


def _hasAnyMappingValue(container: Any, keys: tuple[str, ...]) -> bool:
	for key in keys:
		if _getMappingValue(container, key):
			return True
	return False


def isWebContentObject(obj: Any) -> bool:
	"""Returns whether an object appears to come from web content."""
	moduleName = type(obj).__module__
	if moduleName.startswith(WEB_OBJECT_MODULE_PREFIXES):
		return True
	for attrName in WEB_OBJECT_ATTRIBUTE_NAMES:
		try:
			attrs = getattr(obj, attrName, None)
		except Exception:
			continue
		if attrs:
			return True
	try:
		ia2Attrs = getattr(obj, "IA2Attributes", None)
	except Exception:
		return False
	return _hasAnyMappingValue(ia2Attrs, WEB_IA2_ATTRIBUTE_KEYS)


def isScreenshotCandidateObject(obj: Any) -> bool:
	if getattr(obj, "role", None) not in SCREENSHOT_OBJECT_ROLES:
		return False
	name = getattr(obj, "name", "") or ""
	return str(name).strip() in SCREENSHOT_OBJECT_NAMES


def _hasIrrelevantLocationState(obj: Any) -> bool:
	try:
		states = getattr(obj, "states", set())
	except Exception:
		return False
	return controlTypes.State.INVISIBLE in states or controlTypes.State.OFFSCREEN in states


def _isLocationWithinDesktop(location: tuple[int, int, int, int]) -> bool:
	try:
		deskLeft, deskTop, deskWidth, deskHeight = api.getDesktopObject().location
	except Exception:
		return True
	if not deskWidth or not deskHeight:
		return True
	left, top, width, height = location
	return (
		left >= deskLeft
		and top >= deskTop
		and left + width <= deskLeft + deskWidth
		and top + height <= deskTop + deskHeight
	)


def getObjectLocation(obj: Any) -> tuple[int, int, int, int] | None:
	if _hasIrrelevantLocationState(obj):
		_debug("Screenshot candidate ignored: object is invisible or off screen.")
		return None
	try:
		left, top, width, height = obj.location
		left, top, width, height = int(left), int(top), int(width), int(height)
	except Exception:
		_debug("Could not read screenshot candidate object location.")
		return None
	if width <= 0 or height <= 0:
		if _verboseDebugLogging():
			_debug(f"Screenshot candidate ignored: invalid location {(left, top, width, height)!r}.")
		return None
	if width * height > MAX_IMAGE_PIXELS:
		if _verboseDebugLogging():
			_debug(f"Screenshot candidate ignored: too many pixels {(left, top, width, height)!r}.")
		return None
	location = (left, top, width, height)
	if not _isLocationWithinDesktop(location):
		if _verboseDebugLogging():
			_debug(f"Screenshot candidate ignored: location outside desktop bounds {location!r}.")
		return None
	return location


def getScreenshotTargetKey(obj: Any) -> str | None:
	if not isScreenshotCandidateObject(obj):
		return None
	location = getObjectLocation(obj)
	if not location:
		return None
	return makeScreenshotTargetKey(obj, location)


def makeScreenshotTargetKey(obj: Any, location: tuple[int, int, int, int]) -> str:
	name = str(getattr(obj, "name", "") or "").strip()
	role = getattr(obj, "role", None)
	appModule = getattr(obj, "appModule", None)
	appName = getattr(appModule, "appName", "")
	windowHandle = getattr(obj, "windowHandle", None)
	windowClassName = getattr(obj, "windowClassName", "")
	return f"screenshotTarget:{appName}:{windowHandle}:{windowClassName}:{role}:{name}:{location!r}"


def getFocusedScreenshotTargetKey() -> str | None:
	try:
		return getScreenshotTargetKey(api.getFocusObject())
	except Exception:
		return None


def getNavigatorScreenshotTargetKey() -> str | None:
	try:
		return getScreenshotTargetKey(api.getNavigatorObject())
	except Exception:
		return None


class AutoRecognitionController:
	"""Coordinates fast, non-blocking automatic recognition of focused image objects."""

	def __init__(self) -> None:
		self._token = 0
		self._activeKey: str | None = None
		self._activeCurrentKeyGetter: Callable[[], str | None] | None = None
		self._activeEngine: Any | None = None
		self._workerThread: Thread | None = None
		self._pendingTimer: wx.CallLater | None = None
		self._pendingKey: str | None = None
		self._pendingSerial = 0
		self._cache: OrderedDict[str, str] = OrderedDict()
		self._cacheLock = Lock()
		self._streamingSpeechPresenter = StreamingSpeechPresenter()

	@staticmethod
	def _resolveAutoRecognitionEngine(setting: str) -> tuple[Any, str, str] | None:
		if setting == AUTO_RECOGNITION_OFF:
			return None
		if setting.startswith(AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX):
			handler = ImageDescriberHandler
			prefix = AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX
		elif setting.startswith(AUTO_RECOGNITION_OCR_PREFIX):
			handler = CustomOCRHandler
			prefix = AUTO_RECOGNITION_OCR_PREFIX
		else:
			if _verboseDebugLogging():
				_debug(f"automatic recognition skipped: invalid engine setting {setting!r}.")
			return None
		engineName = setting.removeprefix(prefix)
		if engineName == AUTO_RECOGNITION_CURRENT_ENGINE_NAME:
			currentEngine = handler.getCurrentEngine()
			engineName = getattr(currentEngine, "name", "")
		if not engineName or engineName == "empty":
			if _verboseDebugLogging():
				_debug(f"automatic recognition skipped: no usable engine for {setting!r}.")
			return None
		enabledEngineNames = {name for name, _description in handler.getEngineList() if name != "empty"}
		if engineName not in enabledEngineNames:
			if _verboseDebugLogging():
				_debug(f"automatic recognition skipped: engine {engineName!r} is not enabled.")
			return None
		return handler, engineName, f"{prefix}{engineName}"

	def _getRecognitionKey(self) -> str | None:
		engineInfo = self._resolveAutoRecognitionEngine(getEffectiveAutoRecognitionEngine())
		if not engineInfo:
			return None
		_handler, _engineName, recognitionKey = engineInfo
		return recognitionKey

	def _resolveRecognitionScopedEngine(self, key: str) -> tuple[Any, str, str] | None:
		recognitionKey = getRecognitionKeyFromScopedKey(key)
		if not recognitionKey:
			_debug("automatic recognition task ignored: missing recognition key.")
			return None
		engineInfo = self._resolveAutoRecognitionEngine(recognitionKey)
		if not engineInfo:
			return None
		return engineInfo

	def _makeScopedTargetKey(self, targetKey: str) -> str | None:
		recognitionKey = self._getRecognitionKey()
		if not recognitionKey:
			return None
		return makeRecognitionScopedKey(recognitionKey, targetKey)

	@staticmethod
	def _makeTargetScopedContentKey(scopedKey: str, contentKey: str) -> str:
		return f"{scopedKey}{RECOGNITION_KEY_SEPARATOR}{contentKey}"

	def _makeCurrentTargetKeyGetter(
		self,
		currentTargetKeyGetter: Callable[[], str | None],
	) -> Callable[[], str | None]:
		def getCurrentTargetKey() -> str | None:
			targetKey = currentTargetKeyGetter()
			return self._makeScopedTargetKey(targetKey) if targetKey else None

		return getCurrentTargetKey

	def _getCachedResult(self, key: str) -> str | None:
		with self._cacheLock:
			cachedResult = self._cache.get(key)
			if cachedResult:
				self._cache.move_to_end(key)
			return cachedResult

	def _storeCachedResult(self, key: str, resultText: str) -> None:
		with self._cacheLock:
			self._cache[key] = resultText
			self._cache.move_to_end(key)
			while len(self._cache) > MAX_CACHE_ENTRIES:
				self._cache.popitem(last=False)

	def _skipAndCancel(self, message: str) -> None:
		_debug(message)
		self.cancel()

	def handleFocus(self, obj: Any) -> None:
		if getattr(obj, "role", None) == controlTypes.Role.GRAPHIC:
			self.handleObject(
				obj,
				source="focus",
				currentSrcGetter=getFocusedImageSrc,
				currentScreenshotKeyGetter=getFocusedGraphicScreenshotTargetKey,
			)
			return
		self.handleScreenshotObject(
			obj,
			source="focus screenshot",
			currentKeyGetter=getFocusedScreenshotTargetKey,
			allowWebContent=False,
		)

	def handleObject(
		self,
		obj: Any,
		source: str,
		currentSrcGetter: Callable[[], str | None],
		currentScreenshotKeyGetter: Callable[[], str | None] | None = None,
	) -> None:
		if getattr(obj, "role", None) != controlTypes.Role.GRAPHIC:
			self._skipAndCancel(f"{source} skipped: role is {getattr(obj, 'role', None)!r}, not graphic.")
			return
		src = getImageSrcFromObject(obj)
		if not src:
			if currentScreenshotKeyGetter and isWebContentObject(obj):
				self.handleGraphicScreenshotFallback(
					obj,
					source=f"{source} screenshot fallback",
					currentKeyGetter=currentScreenshotKeyGetter,
				)
				return
			self._skipAndCancel(f"{source} skipped: graphic object has no supported image src.")
			return
		self.handleGraphicSrc(
			obj,
			src,
			source=source,
			currentSrcGetter=currentSrcGetter,
		)

	def handleNavigatorObject(self, obj: Any) -> None:
		if getattr(obj, "role", None) == controlTypes.Role.GRAPHIC:
			self.handleObject(
				obj,
				source="navigator",
				currentSrcGetter=getNavigatorImageSrc,
				currentScreenshotKeyGetter=getNavigatorGraphicScreenshotTargetKey,
			)
			return
		self.handleScreenshotObject(
			obj,
			source="navigator screenshot",
			currentKeyGetter=getNavigatorScreenshotTargetKey,
			allowWebContent=True,
		)

	def handleBrowseModeMove(self, cursorManager: Any) -> None:
		obj, src = getImageObjectAndSrcFromBrowseModeCaret(cursorManager)
		if not src:
			if obj and isWebContentObject(obj):
				self.handleGraphicScreenshotFallback(
					obj,
					source="browse mode screenshot fallback",
					currentKeyGetter=lambda: getBrowseModeGraphicScreenshotTargetKey(cursorManager),
				)
				return
			self._skipAndCancel("browse mode skipped: no supported graphic src at virtual caret.")
			return

		def currentSrcGetter() -> str | None:
			return getImageSrcFromBrowseModeCaret(cursorManager)

		if obj:
			self.handleGraphicSrc(
				obj,
				src,
				source="browse mode",
				currentSrcGetter=currentSrcGetter,
			)
		else:
			self.handleSrc(src, source="browse mode", currentSrcGetter=currentSrcGetter)

	def handleGraphicScreenshotFallback(
		self,
		obj: Any,
		source: str,
		currentKeyGetter: Callable[[], str | None],
	) -> None:
		location = getObjectLocation(obj)
		if not location:
			self.cancel()
			return
		key = self._makeScopedTargetKey(makeScreenshotTargetKey(obj, location))
		if not key:
			self._skipAndCancel(f"{source} skipped: no automatic recognition engine is configured.")
			return
		self._startScreenshotDescription(
			key,
			location,
			source,
			self._makeCurrentTargetKeyGetter(currentKeyGetter),
		)

	def handleGraphicSrc(
		self,
		obj: Any,
		src: str,
		source: str,
		currentSrcGetter: Callable[[], str | None],
	) -> None:
		src = _normalizeImageSrc(src) or ""
		if not src:
			self._skipAndCancel(f"{source} skipped: unsupported image URL.")
			return
		if _preferScreenshotForWebImages() and isWebContentObject(obj):
			self.handleGraphicScreenshotFirst(
				obj,
				src,
				source=source,
				currentSrcGetter=currentSrcGetter,
			)
			return
		self.handleSrc(src, source=source, currentSrcGetter=currentSrcGetter)

	def handleGraphicScreenshotFirst(
		self,
		obj: Any,
		src: str,
		source: str,
		currentSrcGetter: Callable[[], str | None],
	) -> None:
		location = getObjectLocation(obj)
		if not location:
			_debug(f"{source} screenshot first unavailable: falling back to image URL.")
			self.handleSrc(src, source=f"{source} src fallback", currentSrcGetter=currentSrcGetter)
			return
		urlKey = self._makeScopedTargetKey(makeUrlKey(src))
		if not urlKey:
			self._skipAndCancel(f"{source} skipped: no automatic recognition engine is configured.")
			return
		currentUrlKeyGetter = self._makeCurrentUrlKeyGetter(currentSrcGetter)
		if urlKey == self._activeKey and self._hasActiveTask():
			if _verboseDebugLogging():
				_debug(f"{source} skipped: already recognizing {_urlForLog(src)}")
			return
		self._startScreenshotDescription(
			urlKey,
			location,
			f"{source} screenshot first",
			currentUrlKeyGetter,
			fallbackSrc=src,
			fallbackCurrentKeyGetter=currentUrlKeyGetter,
		)

	def handleSrc(
		self,
		src: str,
		source: str,
		currentSrcGetter: Callable[[], str | None],
	) -> None:
		src = _normalizeImageSrc(src) or ""
		if not src:
			self._skipAndCancel(f"{source} skipped: unsupported image URL.")
			return
		key = self._makeScopedTargetKey(makeUrlKey(src))
		if not key:
			self._skipAndCancel(f"{source} skipped: no automatic recognition engine is configured.")
			return
		currentKeyGetter = self._makeCurrentUrlKeyGetter(currentSrcGetter)
		self._startUrlDescription(key, src, source, currentKeyGetter)

	def _makeCurrentUrlKeyGetter(
		self,
		currentSrcGetter: Callable[[], str | None],
	) -> Callable[[], str | None]:
		def getCurrentUrlKey() -> str | None:
			src = currentSrcGetter()
			src = _normalizeImageSrc(src) if src else None
			return self._makeScopedTargetKey(makeUrlKey(src)) if src else None

		return getCurrentUrlKey

	def handleScreenshotObject(
		self,
		obj: Any,
		source: str,
		currentKeyGetter: Callable[[], str | None],
		allowWebContent: bool,
	) -> None:
		if not isScreenshotCandidateObject(obj):
			self._skipAndCancel(
				f"{source} skipped: role={getattr(obj, 'role', None)!r}, "
				f"name={getattr(obj, 'name', None)!r}.",
			)
			return
		if not allowWebContent and isWebContentObject(obj):
			self._skipAndCancel(f"{source} skipped: object appears to be web content.")
			return
		location = getObjectLocation(obj)
		if not location:
			self.cancel()
			return
		key = self._makeScopedTargetKey(makeScreenshotTargetKey(obj, location))
		if not key:
			self._skipAndCancel(f"{source} skipped: no automatic recognition engine is configured.")
			return
		self._startScreenshotDescription(
			key,
			location,
			source,
			self._makeCurrentTargetKeyGetter(currentKeyGetter),
		)

	def _startUrlDescription(
		self,
		key: str,
		src: str,
		source: str,
		currentKeyGetter: Callable[[], str | None],
	) -> None:
		if key == self._activeKey and self._hasActiveTask():
			if _verboseDebugLogging():
				_debug(f"{source} skipped: already recognizing {_urlForLog(src)}")
			return
		targetDescription = _urlForLog(src) if _verboseDebugLogging() else ""
		self._scheduleDescriptionStart(
			key,
			source,
			targetDescription,
			lambda startedAt: self._beginUrlDescription(key, src, source, currentKeyGetter, startedAt),
		)

	def _beginUrlDescription(
		self,
		key: str,
		src: str,
		source: str,
		currentKeyGetter: Callable[[], str | None],
		startedAt: float,
	) -> None:
		if not self._currentKeyMatchesGetter(currentKeyGetter, key, source):
			return
		self.cancel()
		self._token += 1
		token = self._token
		self._activeKey = key
		self._activeCurrentKeyGetter = currentKeyGetter
		if _verboseDebugLogging():
			_debug(f"{source} triggered: {_urlForLog(src)}")
		_playStartTone()
		self._workerThread = Thread(
			name="VisAwareAutoRecognition",
			target=self._downloadAndDescribe,
			args=(token, key, src, startedAt),
			daemon=True,
		)
		self._workerThread.start()

	def _startScreenshotDescription(
		self,
		key: str,
		location: tuple[int, int, int, int],
		source: str,
		currentKeyGetter: Callable[[], str | None],
		fallbackSrc: str | None = None,
		fallbackCurrentKeyGetter: Callable[[], str | None] | None = None,
	) -> None:
		if key == self._activeKey and self._hasActiveTask():
			if _verboseDebugLogging():
				_debug(f"{source} skipped: already recognizing location={location!r}")
			return
		targetDescription = f"location={location!r}" if _verboseDebugLogging() else ""
		self._scheduleDescriptionStart(
			key,
			source,
			targetDescription,
			lambda startedAt: self._beginScreenshotDescription(
				key,
				location,
				source,
				currentKeyGetter,
				fallbackSrc,
				fallbackCurrentKeyGetter,
				startedAt,
			),
		)

	def _beginScreenshotDescription(
		self,
		key: str,
		location: tuple[int, int, int, int],
		source: str,
		currentKeyGetter: Callable[[], str | None],
		fallbackSrc: str | None,
		fallbackCurrentKeyGetter: Callable[[], str | None] | None,
		startedAt: float,
	) -> None:
		if not self._currentKeyMatchesGetter(currentKeyGetter, key, source):
			return
		self.cancel()
		self._token += 1
		token = self._token
		self._activeKey = key
		self._activeCurrentKeyGetter = currentKeyGetter
		if _verboseDebugLogging():
			_debug(f"{source} triggered: location={location!r}")
		self._workerThread = Thread(
			name="VisAwareAutoRecognition",
			target=self._captureAndDescribe,
			args=(token, key, location, startedAt, fallbackSrc, fallbackCurrentKeyGetter),
			daemon=True,
		)
		self._workerThread.start()

	def _scheduleDescriptionStart(
		self,
		key: str,
		source: str,
		targetDescription: str,
		start: Callable[[float], None],
	) -> None:
		self.cancel()
		self._pendingSerial += 1
		serial = self._pendingSerial
		self._pendingKey = key
		startedAt = time.perf_counter()
		if _verboseDebugLogging():
			_debug(
				f"{source} scheduled: {targetDescription}, delay={DESCRIPTION_DEBOUNCE_MS}ms",
			)
		self._pendingTimer = wx.CallLater(
			DESCRIPTION_DEBOUNCE_MS,
			self._runPendingStart,
			serial,
			key,
			source,
			start,
			startedAt,
		)

	def _runPendingStart(
		self,
		serial: int,
		key: str,
		source: str,
		start: Callable[[float], None],
		startedAt: float,
	) -> None:
		if serial != self._pendingSerial or key != self._pendingKey:
			if _verboseDebugLogging():
				_debug(f"{source} delayed start ignored: task is no longer pending.")
			return
		self._pendingTimer = None
		self._pendingKey = None
		if _verboseDebugLogging():
			_debug(f"{source} debounce elapsed: starting.")
		start(startedAt)

	def _cancelPendingStart(self) -> None:
		self._pendingSerial += 1
		if self._pendingTimer:
			try:
				self._pendingTimer.Stop()
			except Exception:
				log.debugWarning("Could not stop automatic recognition debounce timer.", exc_info=True)
		self._pendingTimer = None
		self._pendingKey = None

	def cancel(self) -> bool:
		hadTask = bool(self._pendingKey or self._activeKey or self._hasActiveTask())
		self._cancelPendingStart()
		self._token += 1
		self._activeKey = None
		self._activeCurrentKeyGetter = None
		if self._streamingSpeechPresenter.isActive:
			self._streamingSpeechPresenter.cancel()
		if self._activeEngine:
			try:
				self._activeEngine.cancel(isUserInitiated=False)
			except Exception:
				log.debugWarning("Could not cancel automatic recognition.", exc_info=True)
		self._activeEngine = None
		return hadTask

	def terminate(self) -> None:
		self.cancel()

	def _createRecognitionEngine(self, token: int, key: str) -> Any | None:
		engineInfo = self._resolveRecognitionScopedEngine(key)
		if not engineInfo:
			return None
		handler, engineName, _resolvedRecognitionKey = engineInfo
		engine = handler.getEngineInstance(engineName)
		applyAutoRecognitionOverrides = getattr(engine, "applyAutoRecognitionOverrides", None)
		if callable(applyAutoRecognitionOverrides):
			applyAutoRecognitionOverrides()
		engine.textResult = True
		engine.streamResult = bool(
			getattr(engine, "supportsStreaming", False) and getattr(engine, "isStreaming", False),
		)
		if not self._isCurrent(token, key):
			return None
		self._activeEngine = engine
		if hasattr(engine, "prefetchAuthHeaders"):
			engine.prefetchAuthHeaders()
		return engine

	def _downloadAndDescribe(self, token: int, key: str, src: str, startedAt: float) -> None:
		try:
			engine = self._createRecognitionEngine(token, key)
			if not engine:
				_debug("download task ignored: task is no longer current.")
				return
			self._downloadAndDescribeWithEngine(engine, token, key, src, startedAt)
		except Exception:
			if self._isCurrent(token, key):
				log.debugWarning("Automatic recognition could not start.", exc_info=True)
				self._clearActive(token, key)

	def _downloadAndDescribeWithEngine(
		self,
		engine: Any,
		token: int,
		key: str,
		src: str,
		startedAt: float,
	) -> None:
		isDebug = _verboseDebugLogging()
		downloadStartedAt = time.perf_counter() if isDebug else 0
		if isDebug:
			_debug(f"downloading image: {_urlForLog(src)}")
		image = self._downloadImage(src, shouldCancel=lambda: not self._isCurrent(token, key))
		if isDebug:
			_debug(f"download completed: elapsed={time.perf_counter() - downloadStartedAt:.3f}s")
		if not self._isCurrent(token, key):
			_debug("download result ignored: task is no longer current.")
			return
		self._recognizeImage(engine, image, token, key, startedAt)

	def _captureAndDescribe(
		self,
		token: int,
		key: str,
		location: tuple[int, int, int, int],
		startedAt: float,
		fallbackSrc: str | None,
		fallbackCurrentKeyGetter: Callable[[], str | None] | None,
	) -> None:
		try:
			left, top, width, height = location
			isDebug = _verboseDebugLogging()
			captureStartedAt = time.perf_counter() if isDebug else 0
			image = ImageGrab.grab(bbox=(left, top, left + width, top + height))
			if isDebug:
				_debug(
					f"object screenshot captured: size={image.width}x{image.height}, "
					f"elapsed={time.perf_counter() - captureStartedAt:.3f}s",
				)
			if not self._isCurrent(token, key):
				_debug("screenshot result ignored: task is no longer current.")
				return
			contentKey, image = makeScreenshotContentKey(image)
			contentKey = self._makeTargetScopedContentKey(key, contentKey)
			cachedResult = self._getCachedResult(contentKey)
			if cachedResult:
				wx.CallAfter(
					self._onScreenshotCacheHit,
					token,
					key,
					contentKey,
					cachedResult,
					location,
					startedAt,
				)
				return
			engine = self._createRecognitionEngine(token, key)
			if not engine:
				_debug("screenshot task ignored: task is no longer current.")
				return
			_playStartTone()
			self._recognizeImage(
				engine,
				image,
				token,
				key,
				startedAt,
				resultCacheKey=contentKey,
				resultValidator=self._makeScreenshotContentValidator(key, location, contentKey),
			)
		except Exception as e:
			if self._isCurrent(token, key):
				if fallbackSrc and fallbackCurrentKeyGetter:
					engine = self._createRecognitionEngine(token, key)
					if not engine:
						_debug("image URL fallback ignored: task is no longer current.")
						return
					_debug(f"object screenshot failed; falling back to image URL: {e!r}")
					_playStartTone()
					self._fallbackToSrc(
						engine,
						token,
						key,
						fallbackSrc,
						fallbackCurrentKeyGetter,
						startedAt,
					)
					return
				log.debugWarning("Automatic object screenshot recognition could not start.", exc_info=True)
				self._clearActive(token, key)

	def _fallbackToSrc(
		self,
		engine: Any,
		token: int,
		key: str,
		src: str,
		currentKeyGetter: Callable[[], str | None],
		startedAt: float,
	) -> None:
		if not self._isCurrent(token, key):
			return
		self._activeCurrentKeyGetter = currentKeyGetter
		if _verboseDebugLogging():
			_debug(f"starting image URL fallback: {_urlForLog(src)}")
		try:
			self._downloadAndDescribeWithEngine(engine, token, key, src, startedAt)
		except Exception:
			if self._isCurrent(token, key):
				log.debugWarning("Automatic image URL fallback recognition could not start.", exc_info=True)
				self._clearActive(token, key)

	def _onScreenshotCacheHit(
		self,
		token: int,
		key: str,
		contentKey: str,
		resultText: str,
		location: tuple[int, int, int, int],
		startedAt: float,
	) -> None:
		try:
			if not self._isCurrent(token, key):
				_debug("screenshot cache hit ignored: task is no longer current.")
				return
			if not self._currentKeyMatches(key):
				_debug("screenshot cache hit ignored: current target changed.")
				return
			if not self._screenshotContentMatches(key, location, contentKey):
				_debug("screenshot cache hit ignored: content changed.")
				return
			if _verboseDebugLogging():
				_debug(
					f"screenshot content cache hit: key={contentKey[-12:]}, "
					f"totalElapsed={time.perf_counter() - startedAt:.3f}s",
				)
			ui.message(resultText)
		finally:
			self._clearActive(token, key)

	def _makeScreenshotContentValidator(
		self,
		key: str,
		location: tuple[int, int, int, int],
		expectedContentKey: str,
	) -> Callable[[], bool]:
		isValidated = False

		def validate() -> bool:
			nonlocal isValidated
			if isValidated:
				return True
			if not self._screenshotContentMatches(key, location, expectedContentKey):
				return False
			isValidated = True
			return True

		return validate

	def _screenshotContentMatches(
		self,
		key: str,
		location: tuple[int, int, int, int],
		expectedContentKey: str,
	) -> bool:
		try:
			left, top, width, height = location
			image = ImageGrab.grab(bbox=(left, top, left + width, top + height))
			contentKey, _image = makeScreenshotContentKey(image)
			contentKey = self._makeTargetScopedContentKey(key, contentKey)
		except Exception:
			log.debugWarning("Could not verify automatic screenshot content.", exc_info=True)
			return False
		if contentKey != expectedContentKey:
			_debug("automatic screenshot content verification failed: content changed.")
			return False
		return True

	def _recognizeImage(
		self,
		engine: Any,
		image: Image.Image,
		token: int,
		key: str,
		startedAt: float,
		resultCacheKey: str | None = None,
		resultValidator: Callable[[], bool] | None = None,
	) -> None:
		if _verboseDebugLogging():
			_debug(
				f"starting {getattr(engine, 'name', 'unknown')} automatic recognition: "
				f"{image.width}x{image.height}",
			)
		if hasattr(engine, "recognizeImage"):
			engine.recognizeImage(
				image,
				lambda result: self._onResult(token, key, result, startedAt, resultCacheKey, resultValidator),
			)
		else:
			image = image.convert("RGB")
			imageInfo = RecogImageInfo(0, 0, image.width, image.height, 1)
			pixels = image.tobytes("raw", "BGRX")
			engine.recognize(
				pixels,
				imageInfo,
				lambda result: self._onResult(token, key, result, startedAt, resultCacheKey, resultValidator),
			)

	def _downloadImage(self, src: str, shouldCancel: Callable[[], bool]) -> Image.Image:
		if shouldCancel():
			raise CancellationError("Image download was cancelled.")
		with sendRequest(
			"GET",
			src,
			headers=IMAGE_REQUEST_HEADERS,
			stream=True,
			timeout=DOWNLOAD_TIMEOUT,
		) as response:
			if shouldCancel():
				raise CancellationError("Image download was cancelled.")
			response.raise_for_status()
			contentType = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
			if contentType == "image/svg+xml":
				raise ValueError("Focused image URL returned unsupported SVG content.")
			if contentType and not contentType.startswith("image/"):
				raise ValueError(f"Focused image URL returned non-image content type: {contentType}")
			contentLength = response.headers.get("content-length")
			if contentLength and int(contentLength) > MAX_IMAGE_BYTES:
				raise ValueError("Focused image is too large to download.")
			data = bytearray()
			for chunk in response.iter_content(DOWNLOAD_CHUNK_SIZE):
				if shouldCancel():
					raise CancellationError("Image download was cancelled.")
				if not chunk:
					continue
				data.extend(chunk)
				if len(data) > MAX_IMAGE_BYTES:
					raise ValueError("Focused image exceeded the download size limit.")
			if not data:
				raise ValueError("Focused image download returned no data.")
		image = Image.open(BytesIO(data))
		width, height = image.size
		if width <= 0 or height <= 0:
			raise ValueError("Focused image has invalid dimensions.")
		if width * height > MAX_IMAGE_PIXELS:
			raise ValueError("Focused image dimensions are too large to load.")
		image.load()
		if _verboseDebugLogging():
			_debug(f"downloaded image: bytes={len(data)}, size={image.width}x{image.height}")
		return image

	def _onResult(
		self,
		token: int,
		key: str,
		result: Any,
		startedAt: float,
		resultCacheKey: str | None = None,
		resultValidator: Callable[[], bool] | None = None,
	) -> None:
		shouldClearActive = True
		try:
			if not self._isCurrent(token, key):
				_debug("automatic recognition result ignored: task is no longer current.")
				return
			if not self._currentKeyMatches(key):
				_debug("automatic recognition result ignored: current target changed.")
				self.cancel()
				shouldClearActive = False
				return
			if isinstance(result, Exception):
				if not isinstance(result, CancellationError):
					log.debugWarning(f"Automatic recognition failed: {result!r}")
				if self._streamingSpeechPresenter.isActive:
					self._streamingSpeechPresenter.cancel()
				return
			if resultValidator and not resultValidator():
				_debug("automatic recognition result ignored: screenshot content changed.")
				self.cancel()
				shouldClearActive = False
				return
			if isinstance(result, StreamText):
				shouldClearActive = False
				if result.replace:
					self._streamingSpeechPresenter.cancel()
					self._streamingSpeechPresenter.start()
				elif not self._streamingSpeechPresenter.isActive:
					self._streamingSpeechPresenter.start()
				self._streamingSpeechPresenter.addText(result.text)
				return
			if isinstance(result, StreamFinished):
				self._onStreamingFinished(result, key, startedAt, resultCacheKey)
				return
			if not isinstance(result, RecognitionResult):
				log.debugWarning(f"Automatic recognition returned unexpected result: {type(result)}")
				if self._streamingSpeechPresenter.isActive:
					self._streamingSpeechPresenter.cancel()
				return
			resultText = getattr(result, "text", "").strip()
			if not resultText:
				return
			historyEntry = recogHistory.getAttachedEntry(result)
			if historyEntry:
				recogHistory.addEntry(historyEntry, result=result)
			if resultCacheKey:
				self._storeCachedResult(resultCacheKey, resultText)
			if _verboseDebugLogging():
				_debug(
					f"automatic recognition result accepted: chars={len(resultText)}, "
					f"totalElapsed={time.perf_counter() - startedAt:.3f}s",
				)
			ui.message(resultText)
		finally:
			if shouldClearActive:
				self._clearActive(token, key)

	def _onStreamingFinished(
		self,
		result: StreamFinished,
		key: str,
		startedAt: float,
		resultCacheKey: str | None,
	) -> None:
		resultText = result.text.strip()
		if not resultText:
			if self._streamingSpeechPresenter.isActive:
				self._streamingSpeechPresenter.cancel()
			return
		if result.historyEntry:
			recogHistory.addEntry(result.historyEntry, text=resultText)
		if resultCacheKey:
			self._storeCachedResult(resultCacheKey, resultText)
		if _verboseDebugLogging():
			_debug(
				f"automatic streaming recognition result accepted: chars={len(resultText)}, "
				f"totalElapsed={time.perf_counter() - startedAt:.3f}s",
			)
		if self._streamingSpeechPresenter.isActive:
			self._streamingSpeechPresenter.finish()
		else:
			ui.message(resultText)

	def _isCurrent(self, token: int, key: str) -> bool:
		return token == self._token and key == self._activeKey

	def _currentKeyMatches(self, key: str) -> bool:
		if not self._activeCurrentKeyGetter:
			return False
		return self._currentKeyMatchesGetter(
			self._activeCurrentKeyGetter,
			key,
			"active automatic recognition",
		)

	def _currentKeyMatchesGetter(
		self,
		currentKeyGetter: Callable[[], str | None],
		key: str,
		source: str,
	) -> bool:
		try:
			currentKey = currentKeyGetter()
		except Exception:
			_debug(f"{source} could not verify current automatic recognition target.")
			return False
		if currentKey != key:
			_debug(f"{source} delayed start ignored: current target changed.")
			return False
		return True

	def _clearActive(self, token: int, key: str) -> None:
		if not self._isCurrent(token, key):
			return
		self._activeKey = None
		self._activeCurrentKeyGetter = None
		self._activeEngine = None

	def _hasActiveTask(self) -> bool:
		return bool(
			(self._workerThread and self._workerThread.is_alive())
			or (
				self._activeEngine
				and self._activeEngine._recognitionThread
				and self._activeEngine._recognitionThread.is_alive()
			),
		)
