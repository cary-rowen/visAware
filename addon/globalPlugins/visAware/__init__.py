# Copyright (C) 2025 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

import addonHandler
import api
import core
import NVDAObjects
import config
import globalPluginHandler
import globalVars
import gui
import os
import wx
import inputCore
from logHandler import log
from gui.settingsDialogs import NVDASettingsDialog, SettingsPanel
import functools
from typing import Any, TYPE_CHECKING, override
from collections.abc import Callable
from threading import Event
from scriptHandler import script
from contentRecog import (
	RecogImageInfo,
	recogUi,
	LinesWordsResult,
	RecognitionResult,
	SimpleTextResult,
)
import ui
import vision
from PIL import Image, ImageGrab

from ._autoRecognition import AutoRecognitionController
from .askDialog import AskQuestionFrame
from .agent.settings import AgentHandler, AgentPanel
from .agent.session import AgentSession
from .exceptions import CancellationError, AuthenticationError, NetworkError, ApiError
from . import recogHistory
from .conversation import ConversationContext, makeConversationContext
from .markdownRenderer import showMarkdownBrowseableMessage
from .recogHandler import (
	AutomaticRecognitionPanel,
	CustomOCRHandler,
	CustomOCRPanel,
	ImageDescriberHandler,
	ImageDescriberPanel,
	OCRPanel,
	StreamFinished,
	StreamText,
	getCycleSourceTypes,
	getConfigChoiceValue,
	getValidSourceType,
	isAutomaticRecognitionEnabled,
	ENGINE_TYPES,
)
from .streamingSpeech import StreamingSpeechPresenter

if TYPE_CHECKING:
	from contentRecog import ContentRecognizer
	from .abstractEngine import AbstractEngineHandler

addonHandler.initTranslation()

# Translators: The name of the category for this add-on's commands in the Input Gestures dialog.
CATEGORY_NAME = _("Vis Aware")

GENERAL_CONFIG_SPEC = {
	"copyToClipboard": "boolean(default=False)",
	"useBrowseableMessage": "boolean(default=False)",
	"autoSayAllOnResult": "boolean(default=False)",
	"autoRecognitionEngine": "string(default='off')",
	"preferScreenshotForWebImages": "boolean(default=False)",
	"verboseDebugLogging": "boolean(default=False)",
	"engineType": 'option("OCR", "ImageDescriber", "Agent", default="OCR")',
	"sourceType": 'option("navigatorObject", "clipboardImage", "wholeDesktop", "foreGroundWindow", "mouseCaptureArea", default="navigatorObject")',
	"excludedSourceTypes": "list(default=list())",
	"nvdacnUser": "string(default='')",
	"nvdacnPass": "string(default='')",
}


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


def multiPressAction(timeout: int = 350):
	"""
	A decorator that counts consecutive presses of a gesture.

	The count is passed to the decorated script as the `pressCount` argument.
	A timer is used to determine the window for consecutive presses.

	:param timeout: The time in milliseconds to wait for another press.
	"""

	def decorator(scriptFunc: Callable[..., None]) -> Callable[..., None]:
		@functools.wraps(scriptFunc)
		def wrapper(self: "GlobalPlugin", gesture: "inputCore.InputGesture") -> None:
			if not hasattr(self, "_multiPressStates"):
				self._multiPressStates = {}
			stateKey = scriptFunc.__name__
			funcState = self._multiPressStates.setdefault(stateKey, {"count": 0, "timer": None})
			if funcState["timer"] and funcState["timer"].IsRunning():
				funcState["timer"].Stop()
			funcState["count"] += 1

			def finalize():
				"""Called after the timeout to execute the script with the final press count."""
				finalCount = funcState["count"]
				# Reset state for the next sequence.
				funcState["count"] = 0
				funcState["timer"] = None
				# Call the original script with the press count.
				scriptFunc(self, gesture, pressCount=finalCount)

			funcState["timer"] = wx.CallLater(timeout, finalize)

		return wrapper

	return decorator


class ScreenCaptureDialog(wx.Dialog):
	"""A full-screen, borderless dialog for selecting a screen region to capture.

	Displays a dimmed screenshot of the entire desktop. The user can drag to
	select a rectangular region, which is then returned as a cropped image.
	Press Escape to cancel, or right-click to reset the selection.
	"""

	# Minimum dimensions (in pixels) for a selection to be considered valid.
	_MIN_SELECTION_WIDTH = 5
	_MIN_SELECTION_HEIGHT = 5

	def __init__(self, parent: wx.Window | None) -> None:
		# Create a full-screen, borderless, always-on-top window.
		super().__init__(parent, style=wx.NO_BORDER | wx.STAY_ON_TOP)

		# Let wxPython know we handle all background painting ourselves.
		self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

		# Suppress the default background erase to eliminate flicker.
		self.Bind(wx.EVT_ERASE_BACKGROUND, self.onEraseBackground)

		# Obtain screen dimensions and resize to cover the entire display.
		self._screenLeft, self._screenTop, self._screenWidth, self._screenHeight = (
			wx.Display().GetGeometry()
		)
		self.SetSize((self._screenLeft, self._screenTop, self._screenWidth, self._screenHeight))

		# Capture a full-screen screenshot to use as the background.
		self._fullScreenshot = ImageGrab.grab(all_screens=True)

		# Convert the PIL screenshot to a wx.Bitmap for fast painting.
		self._backgroundBitmap = self._pilToBitmap(self._fullScreenshot)

		# Initialize selection state, starting from the current NVDA mouse object.
		mouseLocation = api.getMouseObject().location
		self._startPos = mouseLocation.topLeft
		self._currentPos = mouseLocation.bottomRight
		self._dragStartPos = None  # Saved on left-down; used when dragging begins.
		self._isSelecting = False
		self._resultRect: tuple[int, int, int, int] | None = None  # (x, y, w, h)

		# Bind input events.
		self.Bind(wx.EVT_PAINT, self.onPaint)
		self.Bind(wx.EVT_LEFT_DOWN, self.onMouseLeftDown)
		self.Bind(wx.EVT_MOTION, self.onMouseMove)
		self.Bind(wx.EVT_LEFT_UP, self.onMouseLeftUp)
		self.Bind(wx.EVT_RIGHT_UP, self.onMouseRightUp)
		self.Bind(wx.EVT_KEY_UP, self.onKeyUp)
		self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self.onMouseCaptureLost)

		# Use a crosshair cursor to indicate selection mode.
		self.SetCursor(wx.Cursor(wx.CURSOR_CROSS))

	def _pilToBitmap(self, pilImage: Image.Image) -> wx.Bitmap:
		"""Convert a PIL Image to a wx.Bitmap for rendering.

		:param pilImage: The source PIL image (typically an RGB screenshot).
		:returns: A wx.Bitmap suitable for use with wx.DC drawing operations.
		"""
		wxImage = wx.Image(pilImage.size[0], pilImage.size[1])
		wxImage.SetData(pilImage.convert("RGB").tobytes())
		return wxImage.ConvertToBitmap()

	def onEraseBackground(self, event: wx.EraseEvent) -> None:
		"""Intentionally skip background erasure to prevent white-flash flicker."""
		pass

	def onPaint(self, event: wx.PaintEvent) -> None:
		"""Paint the dimmed screenshot overlay and the current selection rectangle.

		Uses double-buffering (AutoBufferedPaintDC) for flicker-free rendering.
		A semi-transparent dark overlay is drawn on top of the screenshot.
		The area inside the current selection is drawn at full brightness and
		outlined with a red border.
		"""
		dc = wx.AutoBufferedPaintDC(self)

		# Draw the full screenshot as the base layer.
		dc.DrawBitmap(self._backgroundBitmap, 0, 0)

		# Draw a semi-transparent dark overlay on top of the screenshot.
		gc = wx.GraphicsContext.Create(dc)
		gc.SetBrush(wx.Brush(wx.Colour(0, 0, 0, 100)))  # Black at ~39 % opacity
		gc.DrawRectangle(0, 0, self._screenWidth, self._screenHeight)

		# If a selection is active, restore the original brightness inside it
		# and draw a red outline.
		if self._startPos and self._currentPos:
			rect = self._getSelectionRect()

			# Restore the un-dimmed screenshot within the selection bounds.
			dc.SetClippingRegion(rect)
			dc.DrawBitmap(self._backgroundBitmap, 0, 0)
			dc.DestroyClippingRegion()

			# Draw the red selection border.
			gc.SetPen(wx.Pen(wx.Colour(255, 0, 0), 2))
			gc.SetBrush(wx.NullBrush)
			gc.DrawRectangle(rect.x, rect.y, rect.width, rect.height)

	def onMouseLeftDown(self, event: wx.MouseEvent) -> None:
		"""Begin a drag selection at the current mouse position.
		The selection rectangle only appears once the mouse has been dragged
		past the minimum threshold (see :meth:`onMouseMove`).
		"""
		self._dragStartPos = event.GetPosition()
		self._isSelecting = True
		try:
			self.CaptureMouse()
		except Exception:
			log.debug("ScreenCaptureDialog: Could not capture mouse.", exc_info=True)

	def onMouseCaptureLost(self, event: wx.MouseCaptureLostEvent) -> None:
		"""Mouse capture was taken away (e.g. by the system or another window).
		Clean up the in-progress selection state so the C++ assertion in
		DoNotifyWindowAboutCaptureLost is satisfied.
		"""
		self._isSelecting = False
		self._dragStartPos = None

	def onMouseMove(self, event: wx.MouseEvent) -> None:
		"""Track the mouse during an active drag selection.
		A minimum drag distance is required before the selection rectangle
		updates, filtering out accidental micro-movements when clicking.
		"""
		if not (self._isSelecting and event.Dragging()):
			return
		curPos = event.GetPosition()
		dx = abs(curPos.x - self._dragStartPos.x)
		dy = abs(curPos.y - self._dragStartPos.y)
		if dx <= self._MIN_SELECTION_WIDTH and dy <= self._MIN_SELECTION_HEIGHT:
			return  # Ignore tiny drags — likely an accidental click.
		self._currentPos = curPos
		self._startPos = self._dragStartPos
		self.Refresh(eraseBackground=False)

	def onMouseLeftUp(self, event: wx.MouseEvent) -> None:
		"""Finalize the selection on mouse release."""
		if not self._isSelecting:
			return
		if self.HasCapture():
			self.ReleaseMouse()
		self._isSelecting = False

		rect = self._getSelectionRect()

		if rect.width > self._MIN_SELECTION_WIDTH and rect.height > self._MIN_SELECTION_HEIGHT:
			self._resultRect = (rect.x, rect.y, rect.width, rect.height)
			self.EndModal(wx.ID_OK)
		else:
			# Tiny selection — treat as a stray click and reset.
			self._startPos = None
			self._currentPos = None
			self.Refresh(eraseBackground=False)

	def onMouseRightUp(self, event: wx.MouseEvent) -> None:
		"""Cancel the current selection on right-click."""
		if not self._isSelecting:
			return
		if self.HasCapture():
			self.ReleaseMouse()
		self._isSelecting = False
		self._startPos = None
		self._currentPos = None
		self.Refresh(eraseBackground=False)

	def onKeyUp(self, event: wx.KeyEvent) -> None:
		"""Handle Escape: cancel the current selection, or dismiss the dialog."""
		if event.GetKeyCode() != wx.WXK_ESCAPE:
			event.Skip()
			return
		if self._isSelecting:
			# Cancel the in-progress selection without closing the dialog.
			if self.HasCapture():
				self.ReleaseMouse()
			self._isSelecting = False
			self._dragStartPos = None
			self._startPos = None
			self._currentPos = None
			self.Refresh(eraseBackground=False)
		else:
			self.EndModal(wx.ID_CANCEL)

	def _getSelectionRect(self) -> wx.Rect:
		"""Compute the normalized selection rectangle from start and current
		positions. Handles dragging in any direction (including reverse).
		"""
		if not self._startPos or not self._currentPos:
			return wx.Rect(0, 0, 0, 0)
		x1, y1 = self._startPos
		x2, y2 = self._currentPos
		return wx.Rect(min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2))

	def getCapturedImage(self) -> tuple[RecogImageInfo, Image.Image] | None:
		"""Return the cropped image and its metadata for the selected region.

		:returns: A (RecogImageInfo, PIL.Image) tuple, or None if no valid
			selection was made.
		"""
		if not self._resultRect:
			return None

		x, y, w, h = self._resultRect
		imageWidth, imageHeight = self._fullScreenshot.size

		# Clamp crop coordinates to stay within the screenshot bounds.
		# This guards against DPI scaling mismatches on Windows.
		x = max(0, x)
		y = max(0, y)
		w = min(w, imageWidth - x)
		h = min(h, imageHeight - y)

		cropImage = self._fullScreenshot.crop((x, y, x + w, y + h))
		info = RecogImageInfo(x, y, w, h, 1)
		return info, cropImage

	@override
	def Destroy(self):
		self._backgroundBitmap = None
		self._fullScreenshot = None
		return super().Destroy()

class OCRMultiCategorySettingsDialog(NVDASettingsDialog):
	# Translators: The title of the settings dialog for the Vis Aware add-on.
	title = _("Vis Aware Settings")

	def __init__(self, parent: wx.Window):
		"""
		Initializes the settings dialog, dynamically adding panels for configured engines.

		:param parent: The parent window for this dialog.
		"""
		categoryList: list[type[SettingsPanel]] = [CustomOCRPanel, AutomaticRecognitionPanel, AgentPanel]
		if CustomOCRHandler.getConfigurableEngineList():
			categoryList.append(OCRPanel)
		if ImageDescriberHandler.getConfigurableEngineList():
			categoryList.append(ImageDescriberPanel)
		self.categoryClasses = categoryList
		super().__init__(parent)

	def _enterActivatesOk_ctrlSActivatesApply(self, evt: wx.KeyEvent) -> None:
		focusedControl = wx.Window.FindFocus()
		if (
			evt.KeyCode in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
			and isinstance(focusedControl, wx.TextCtrl)
			and focusedControl.GetWindowStyleFlag() & wx.TE_MULTILINE
		):
			evt.Skip()
			return
		super()._enterActivatesOk_ctrlSActivatesApply(evt)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self):
		super().__init__()
		self._activeEngine = None
		self._activeRecognitionSequence = 0
		self._agentSession: AgentSession | None = None
		self._agentPromptDialogActive = False
		self._agentQuestionDialog: wx.Dialog | None = None
		self._streamingSpeechPresenter = StreamingSpeechPresenter()
		self._autoRecognitionController: AutoRecognitionController | None = None
		self._autoRecognitionExtensionPoints: Any | None = None
		self._autoRecognitionBrowseModeMoveRegistered = False
		self._autoRecognitionReviewMoveRegistered = False
		self._autoRecognitionConfigProfileSwitchRegistered = False
		self._autoRecognitionBrowseModeMoveSerial = 0
		self._autoRecognitionReviewMoveSerial = 0
		self._askQuestionFrame: AskQuestionFrame | None = None
		self._askQuestionHistoryEntry: dict[str, Any] | None = None
		self._askQuestionContext: ConversationContext | None = None
		self.ocrSettingMenuItem: wx.MenuItem | None = None
		if globalVars.appArgs.secure or config.isAppX:
			return
		config.conf.spec["visAwareGeneral"] = GENERAL_CONFIG_SPEC
		CustomOCRHandler.initialize()
		self.ocrHandler = CustomOCRHandler
		ImageDescriberHandler.initialize()
		self.descHandler = ImageDescriberHandler
		AgentHandler.initialize()
		self._autoRecognitionController = AutoRecognitionController()
		self._registerAutoRecognitionEventHandlers()
		config.post_configProfileSwitch.register(self._handleAutoRecognitionConfigProfileSwitch)
		self._autoRecognitionConfigProfileSwitchRegistered = True
		log.debug(f"OCR engine: {self.ocrHandler.currentEngine}")
		log.debug(f"Describe handler: {self.descHandler.currentEngine}")
		self.ocrSettingMenuItem = gui.mainFrame.sysTrayIcon.preferencesMenu.Append(
			wx.ID_NEW,
			# Translators: The label for the menu item to open the Vis Aware settings dialog.
			_("&Vis Aware settings..."),
			# Translators: The help text for the menu item to open the Vis Aware settings dialog.
			_("Vis Aware settings"),
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.openSettingsDialog, self.ocrSettingMenuItem)

	@staticmethod
	def _terminateHandlerIfInitialized(handler: "type[AbstractEngineHandler]") -> None:
		if not getattr(handler, "isInitialized", False):
			return
		try:
			handler.terminate()
		except Exception:
			log.debugWarning(
				f"Could not terminate Vis Aware handler {handler.configSectionName!r}.",
				exc_info=True,
			)

	def terminate(self) -> None:
		if self._autoRecognitionConfigProfileSwitchRegistered:
			try:
				config.post_configProfileSwitch.unregister(self._handleAutoRecognitionConfigProfileSwitch)
			except Exception:
				log.debugWarning(
					"Could not unregister Vis Aware automatic recognition config handler.",
					exc_info=True,
				)
			self._autoRecognitionConfigProfileSwitchRegistered = False
		self._unregisterAutoRecognitionEventHandlers()
		if self._autoRecognitionController:
			self._autoRecognitionController.terminate()
			self._autoRecognitionController = None
		self._stopAgent(isUserInitiated=False)
		self._closeAgentQuestionDialog()
		self._cancelCurrentRecognition(isUserInitiated=False)
		if self._askQuestionFrame:
			self._askQuestionFrame.Destroy()
			self._askQuestionFrame = None
		self._terminateHandlerIfInitialized(ImageDescriberHandler)
		self._terminateHandlerIfInitialized(CustomOCRHandler)
		self._terminateHandlerIfInitialized(AgentHandler)
		try:
			if self.ocrSettingMenuItem:
				gui.mainFrame.sysTrayIcon.preferencesMenu.Remove(self.ocrSettingMenuItem)
				self.ocrSettingMenuItem = None
		except Exception:
			# Ignore errors on termination, as the menu might already be gone.
			pass

	@staticmethod
	def openSettingsDialog(evt: wx.CommandEvent) -> None:
		gui.mainFrame.popupSettingsDialog(OCRMultiCategorySettingsDialog)

	def _registerAutoRecognitionEventHandlers(self) -> None:
		try:
			if not vision.handler or not vision.handler.extensionPoints:
				log.debug("Vis Aware automatic recognition could not register event handlers.")
				return
			extensionPoints = vision.handler.extensionPoints
			if extensionPoints is self._autoRecognitionExtensionPoints:
				return
			self._unregisterAutoRecognitionEventHandlers()
			self._autoRecognitionExtensionPoints = extensionPoints
			extensionPoints.post_browseModeMove.register(self._onBrowseModeMove)
			self._autoRecognitionBrowseModeMoveRegistered = True
			extensionPoints.post_reviewMove.register(self._onReviewMove)
			self._autoRecognitionReviewMoveRegistered = True
		except Exception:
			log.debugWarning("Could not register Vis Aware automatic recognition handlers.", exc_info=True)

	def _unregisterAutoRecognitionEventHandlers(self) -> None:
		try:
			extensionPoints = self._autoRecognitionExtensionPoints
			if extensionPoints:
				if self._autoRecognitionBrowseModeMoveRegistered:
					extensionPoints.post_browseModeMove.unregister(self._onBrowseModeMove)
				if self._autoRecognitionReviewMoveRegistered:
					extensionPoints.post_reviewMove.unregister(self._onReviewMove)
		except Exception:
			log.debugWarning("Could not unregister Vis Aware automatic recognition handlers.", exc_info=True)
		finally:
			self._autoRecognitionExtensionPoints = None
			self._autoRecognitionBrowseModeMoveRegistered = False
			self._autoRecognitionReviewMoveRegistered = False

	def _handleAutoRecognitionConfigProfileSwitch(self) -> None:
		try:
			core.callLater(0, self._registerAutoRecognitionEventHandlers)
		except Exception:
			log.debugWarning(
				"Could not schedule Vis Aware automatic recognition re-registration.",
				exc_info=True,
			)

	def event_gainFocus(self, obj: NVDAObjects.NVDAObject, nextHandler: Callable[[], None]) -> None:
		nextHandler()
		if not self._autoRecognitionController:
			return
		try:
			if not isAutomaticRecognitionEnabled():
				self._autoRecognitionController.cancel()
				return
			self._autoRecognitionController.handleFocus(obj)
		except Exception:
			log.debugWarning("Vis Aware automatic recognition focus handler failed.", exc_info=True)

	def _onBrowseModeMove(self, obj: Any) -> None:
		if not self._autoRecognitionController:
			return
		try:
			if not isAutomaticRecognitionEnabled():
				self._autoRecognitionBrowseModeMoveSerial += 1
				self._autoRecognitionController.cancel()
				return
			self._autoRecognitionBrowseModeMoveSerial += 1
			core.callLater(0, self._handleBrowseModeMove, self._autoRecognitionBrowseModeMoveSerial, obj)
		except Exception:
			log.debugWarning("Vis Aware automatic recognition browse mode handler failed.", exc_info=True)

	def _handleBrowseModeMove(self, serial: int, obj: Any) -> None:
		if serial != self._autoRecognitionBrowseModeMoveSerial or not self._autoRecognitionController:
			return
		try:
			if not isAutomaticRecognitionEnabled():
				self._autoRecognitionController.cancel()
				return
			self._autoRecognitionController.handleBrowseModeMove(obj)
		except Exception:
			log.debugWarning(
				"Vis Aware automatic recognition delayed browse mode handler failed.",
				exc_info=True,
			)

	def _onReviewMove(self, context: Any) -> None:
		if not self._autoRecognitionController:
			return
		try:
			if context != vision.constants.Context.NAVIGATOR:
				return
			if not isAutomaticRecognitionEnabled():
				self._autoRecognitionReviewMoveSerial += 1
				self._autoRecognitionController.cancel()
				return
			self._autoRecognitionReviewMoveSerial += 1
			core.callLater(0, self._handleNavigatorMove, self._autoRecognitionReviewMoveSerial)
		except Exception:
			log.debugWarning("Vis Aware automatic recognition navigator handler failed.", exc_info=True)

	def _handleNavigatorMove(self, serial: int) -> None:
		if serial != self._autoRecognitionReviewMoveSerial or not self._autoRecognitionController:
			return
		try:
			if not isAutomaticRecognitionEnabled():
				self._autoRecognitionController.cancel()
				return
			self._autoRecognitionController.handleNavigatorObject(api.getNavigatorObject())
		except Exception:
			log.debugWarning(
				"Vis Aware automatic recognition delayed navigator handler failed.",
				exc_info=True,
			)

	@script(
		# Translators: Describes a command in the Input Gestures dialog for the Vis Aware add-on.
		description=_("Describes the content of the current navigator object"),
		category=CATEGORY_NAME,
		gestures=[],
	)
	def script_describeNavigatorObject(self, gesture: "inputCore.InputGesture") -> None:
		self.executeRecognition(
			gesture=gesture,
			currentSource="navigatorObject",
			currentEngineType="ImageDescriber",
			simpleText=False,
		)

	@script(
		# Translators: Describes a command in the Input Gestures dialog for the Vis Aware add-on.
		description=_("Describes images on the clipboard"),
		category=CATEGORY_NAME,
		gestures=[],
	)
	@multiPressAction()
	def script_describeClipboardImage(self, gesture: "inputCore.InputGesture", pressCount: int) -> None:
		"""
		Describes an image from the clipboard.
		A double press provides a simple text result instead of a rich one.

		:param gesture: The input gesture that triggered the script.
		:param pressCount: The number of times the gesture was pressed consecutively.
		"""
		simpleText = pressCount >= 2
		self.executeRecognition(gesture, "clipboardImage", "ImageDescriber", simpleText=simpleText)

	@script(
		# Translators: Describes a command in the Input Gestures dialog for the Vis Aware add-on.
		description=_("Recognizes the content of the current navigator object using OCR"),
		category=CATEGORY_NAME,
		gestures=[],
	)
	def script_recognizeWithOCREngine(self, gesture: "inputCore.InputGesture") -> None:
		self.executeRecognition(
			gesture=gesture,
			currentSource="navigatorObject",
			currentEngineType="OCR",
			simpleText=False,
		)

	@script(
		# Translators: Describes a command in the Input Gestures dialog for the Vis Aware add-on.
		description=_("Recognizes text in images on the clipboard using OCR"),
		category=CATEGORY_NAME,
		gestures=[],
	)
	def script_recognizeClipboardImageWithOCREngine(self, gesture: "inputCore.InputGesture") -> None:
		self.executeRecognition(
			gesture=gesture,
			currentSource="clipboardImage",
			currentEngineType="OCR",
			simpleText=False,
		)

	@script(
		# Translators: Describes a command in the Input Gestures dialog for the Vis Aware add-on.
		description=_("Recognizes the text in the current mouse capture area with OCR engine."),
		category=CATEGORY_NAME,
		gestures=[],
	)
	def script_recognizeMouseCaptureAreaWithOCREngine(self, gesture: "inputCore.InputGesture") -> None:
		self.executeRecognition(gesture, "mouseCaptureArea", "OCR", simpleText=True)

	def _makePreviousResultObject(self, historyEntry: dict[str, Any]) -> RecognitionResult | None:
		"""Creates a display result from cached history data."""
		resultObject = historyEntry.get("result")
		if isinstance(resultObject, RecognitionResult):
			return resultObject
		lineResult = historyEntry.get("lineResult")
		imageInfo = historyEntry.get("imageInfo")
		if lineResult and imageInfo:
			return LinesWordsResult(lineResult, imageInfo)
		resultText = historyEntry.get("text")
		if isinstance(resultText, str) and resultText.strip():
			textResult = SimpleTextResult(resultText)
			if historyEntry.get("forceVirtualDocument"):
				textResult.forceVirtualDocument = True
			return textResult
		engine = historyEntry.get("engine")
		response = historyEntry.get("response")
		if engine is None or response is None:
			return None
		try:
			lineResult = engine._convertToLineResultFormat(response)
		except Exception:
			log.warning("Failed to replay previous recognition result as line data.", exc_info=True)
			lineResult = None
		dummyInfo = RecogImageInfo(0, 0, 1, 1, 1)
		if lineResult:
			return LinesWordsResult(lineResult, dummyInfo)
		try:
			resultText = engine.extractText(response)
		except Exception:
			log.warning("Failed to replay previous recognition result as text.", exc_info=True)
			return None
		if not resultText or resultText.isspace():
			return None
		return SimpleTextResult(resultText)

	@script(
		# Translators: Describes a command in the Input Gestures dialog for the Vis Aware add-on.
		description=_("Shows the previous recognition result"),
		category=CATEGORY_NAME,
		gestures=[],
	)
	def script_showPreviousResult(self, gesture: "inputCore.InputGesture") -> None:
		historyEntry = recogHistory.getPreviousResult()
		if not historyEntry:
			# Translators: Reported when there is no previous recognition result to show.
			ui.message(_("No previous recognition result"))
			return
		resultObject = self._makePreviousResultObject(historyEntry)
		if not resultObject:
			# Translators: Reported when the previous recognition result is blank.
			ui.message(_("Previous recognition result is blank."))
			return
		self._showRecognitionResultDocument(resultObject)

	@script(
		# Translators: Describes a command in the Input Gestures dialog for the Vis Aware add-on.
		description=_("Asks a follow-up question about the previous image description"),
		category=CATEGORY_NAME,
		gestures=["kb:NVDA+Alt+Q"],
	)
	def script_askQuestion(self, gesture: "inputCore.InputGesture") -> None:
		historyEntry = recogHistory.getPreviousResult()
		if not historyEntry:
			# Translators: Reported when there is no previous recognition result to ask about.
			ui.message(_("There is no previous recognition result to ask about."))
			return
		try:
			contextChanged = historyEntry is not self._askQuestionHistoryEntry
			if contextChanged:
				self._askQuestionContext = makeConversationContext(historyEntry)
				self._askQuestionHistoryEntry = historyEntry
			if not self._askQuestionContext:
				self._askQuestionContext = makeConversationContext(historyEntry)
				self._askQuestionHistoryEntry = historyEntry
		except ApiError as e:
			ui.message(str(e))
			return
		except Exception:
			log.error("Could not create follow-up question context.", exc_info=True)
			# Translators: Reported when the follow-up question dialog cannot be opened.
			ui.message(_("Could not open the follow-up question dialog."))
			return
		context = self._askQuestionContext
		if not self._askQuestionFrame:
			self._askQuestionFrame = AskQuestionFrame(gui.mainFrame, context)
			contextChanged = False
		elif contextChanged:
			self._askQuestionFrame.setContext(context)
		gui.mainFrame.prePopup()
		try:
			self._askQuestionFrame.Show()
			self._askQuestionFrame.Raise()
			self._askQuestionFrame.focusQuestionInput()
		finally:
			gui.mainFrame.postPopup()

	def _promptAndStartAgent(self) -> None:
		if self._stopAgent(isUserInitiated=True):
			return
		if self._agentPromptDialogActive:
			return
		if self._isScreenCurtainRunning():
			# Translators: A message shown when trying to use the AI agent with screen curtain enabled.
			ui.message(_("Please disable screen curtain before starting the AI Agent."))
			return
		try:
			from .agent.client import createAgentClient

			createAgentClient()
		except (AuthenticationError, NetworkError, ApiError) as e:
			ui.message(str(e))
			return
		self._agentPromptDialogActive = True

		def showDialog():
			goal = ""
			gui.mainFrame.prePopup()
			try:
				dialog = wx.TextEntryDialog(
					gui.mainFrame,
					# Translators: Prompt shown when starting the AI agent.
					_("What should the AI Agent do?"),
					# Translators: Title for the AI agent command dialog.
					_("Vis Aware Agent"),
				)
				try:
					if dialog.ShowModal() != wx.ID_OK:
						return
					goal = dialog.GetValue().strip()
				finally:
					dialog.Destroy()
			finally:
				gui.mainFrame.postPopup()
				self._agentPromptDialogActive = False
			if goal:
				wx.CallLater(250, self._startAgent, goal)

		wx.CallAfter(showDialog)

	def _startAgent(self, goal: str) -> None:
		if self._agentSession and self._agentSession.isRunning:
			return
		log.info("Starting Vis Aware agent from user prompt.")
		self._agentSession = AgentSession(goal, onDone=self._onAgentDone, askUser=self._askAgentUser)
		self._agentSession.start()

	def _stopAgent(self, isUserInitiated: bool = True) -> bool:
		if self._agentSession and self._agentSession.isRunning:
			if self._agentSession.cancel():
				log.info("Vis Aware agent stop requested.")
			else:
				log.info("Vis Aware agent stop already pending.")
			self._closeAgentQuestionDialog()
			if isUserInitiated:
				# Translators: Reported when the AI agent is being stopped by the user.
				ui.message(_("Agent is stopping."))
			return True
		self._agentSession = None
		return False

	def _onAgentDone(self, session: AgentSession) -> None:
		if self._agentSession is session:
			self._agentSession = None

	def _askAgentUser(self, question: str, cancelEvent: Event) -> str | None:
		doneEvent = Event()
		result: dict[str, str | None] = {"answer": None}

		def showDialog() -> None:
			if cancelEvent.is_set():
				doneEvent.set()
				return
			dialog = None
			gui.mainFrame.prePopup()
			try:
				dialog = wx.TextEntryDialog(
					gui.mainFrame,
					question,
					# Translators: Title for the AI agent command dialog.
					_("Vis Aware Agent"),
				)
				self._agentQuestionDialog = dialog
				if dialog.ShowModal() == wx.ID_OK and not cancelEvent.is_set():
					result["answer"] = dialog.GetValue().strip()
			finally:
				if self._agentQuestionDialog is dialog:
					self._agentQuestionDialog = None
				if dialog:
					dialog.Destroy()
				gui.mainFrame.postPopup()
				doneEvent.set()

		wx.CallAfter(showDialog)
		closeRequested = False
		while not doneEvent.wait(0.05):
			if cancelEvent.is_set() and not closeRequested:
				closeRequested = True
				wx.CallAfter(self._closeAgentQuestionDialog)
		return result["answer"]

	def _closeAgentQuestionDialog(self) -> None:
		dialog = self._agentQuestionDialog
		if not dialog:
			return
		try:
			if dialog.IsModal():
				dialog.EndModal(wx.ID_CANCEL)
			else:
				dialog.Close()
		except Exception:
			log.debugWarning("Could not close Agent question dialog.", exc_info=True)

	def _prepareImageFromObject(
		self,
		getObjectFunc: Callable[[], NVDAObjects],
		recognizer: "ContentRecognizer",
	) -> tuple[RecogImageInfo, Image.Image] | None:
		"""
		Validates an object, captures its screen image, and prepares recognition info.

		:param getObjectFunc: A function that returns the target NVDAObject.
		:param recognizer: The content recognizer instance to use for validation.
		:returns: A tuple of (RecogImageInfo, PIL.Image.Image) or None on failure.
		"""
		# Translators: Reported when content recognition (e.g. OCR) is attempted, but the content is not visible.
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
			bbox = (
				imageInfo.screenLeft,
				imageInfo.screenTop,
				imageInfo.screenLeft + imageInfo.screenWidth,
				imageInfo.screenTop + imageInfo.screenHeight,
			)
			recognizeImage = ImageGrab.grab(bbox=bbox)
		except Exception:
			log.error(f"Failed to grab image from object: {targetObject.name}", exc_info=True)
			# Translators: A message shown when screen capture fails.
			ui.message(_("Failed to capture screen image."))
			return None
		return imageInfo, recognizeImage

	def _cancelCurrentRecognition(self, isUserInitiated: bool = True) -> bool:
		"""
		Cancels the currently active recognition task, if any.

		:param isUserInitiated: True if the cancellation was triggered by a direct user action.
		:returns: True if a task was cancelled, False otherwise.
		"""
		if self._stopAgent(isUserInitiated=isUserInitiated):
			return True
		if self._autoRecognitionController and self._autoRecognitionController.cancel():
			if isUserInitiated:
				# Translators: Reported when a recognition task is cancelled by the user.
				ui.message(_("Recognition cancelled"))
			return True
		if (
			self._activeEngine
			and self._activeEngine._recognitionThread
			and self._activeEngine._recognitionThread.is_alive()
		):
			self._activeRecognitionSequence += 1
			activeEngine = self._activeEngine
			log.info(f"Cancelling active task from engine '{activeEngine.name}'.")
			activeEngine.cancel(isUserInitiated=isUserInitiated)
			activeEngine._recognitionThread.join(timeout=0.2)
			if hasattr(self, "_streamingSpeechPresenter"):
				self._streamingSpeechPresenter.cancel()
			self._activeEngine = None
			if isUserInitiated:
				# Translators: Reported when a recognition task is cancelled by the user.
				ui.message(_("Recognition cancelled"))
			return True
		if hasattr(self, "_streamingSpeechPresenter") and self._streamingSpeechPresenter.isActive:
			self._activeRecognitionSequence += 1
			log.info("Cancelling active streaming speech presenter without active recognition thread.")
			self._streamingSpeechPresenter.cancel()
			return True
		return False

	@script(
		# Translators: Describes the command to cancel an ongoing recognition task.
		description=_("Cancels the current recognition"),
		category=CATEGORY_NAME,
		gestures=[],
	)
	def script_cancelCurrentRecognition(self, gesture: "inputCore.InputGesture") -> None:
		if not self._cancelCurrentRecognition(isUserInitiated=True):
			# Translators: Reported when the cancel command is used but no recognition is in progress.
			ui.message(_("No recognition is in progress."))

	def startRecognition(self, gesture: "inputCore.InputGesture", simpleText: bool) -> None:
		"""
		Unified recognition starter that reads from config.
		Called by the multi-press decorator with appropriate parameters.

		:param gesture: The gesture that triggered the recognition.
		:param simpleText: Whether to request a simple text result.
		"""
		conf = config.conf["visAwareGeneral"]
		currentSource = getValidSourceType(conf)
		currentEngineType = getConfigChoiceValue(conf, "engineType", ENGINE_TYPES)
		if currentEngineType == "Agent":
			self._promptAndStartAgent()
			return
		self.executeRecognition(
			gesture=gesture,
			currentSource=currentSource,
			currentEngineType=currentEngineType,
			simpleText=simpleText,
		)

	@script(
		# Translators: Describes the main Vis Aware command.
		description=_(
			"Performs recognition using the current settings. In OCR and image description modes, press once "
			"for a recognition result document. Press twice to show the result in a browsable message "
			"or have NVDA announce it, depending on the General setting. In AI Agent mode, starts or "
			"stops the Agent",
		),
		category=CATEGORY_NAME,
		gestures=["kb:NVDA+Alt+space"],
	)
	@multiPressAction()
	def script_recognizeAccordingToSettings(self, gesture: "inputCore.InputGesture", pressCount: int) -> None:
		"""
		Performs recognition based on settings. Single press gives rich result, double press gives simple text.

		:param gesture: The gesture that triggered the recognition.
		:param pressCount: The number of times the gesture was pressed.
		"""
		from tones import beep

		if pressCount == 1:
			beep(700, 200)
			self.startRecognition(gesture=gesture, simpleText=False)
		else:
			beep(300, 500)
			self.startRecognition(gesture=gesture, simpleText=True)

	@script(
		# Translators: Describes a command in the Input Gestures dialog for the Vis Aware add-on.
		description=_("Cycles through recognition modes"),
		category=CATEGORY_NAME,
		gestures=["kb:nvda+alt+1"],
	)
	def script_cycleRecognitionEngineType(self, gesture: "inputCore.InputGesture") -> None:
		name = self._cycleThroughSettings(config.conf["visAwareGeneral"], "engineType", ENGINE_TYPES)
		ui.message(name)

	@script(
		# Translators: Describes a command in the Input Gestures dialog for the Vis Aware add-on.
		description=_("Cycles through recognition sources"),
		category=CATEGORY_NAME,
		gestures=["kb:nvda+alt+3"],
	)
	def script_cycleRecognitionTarget(self, gesture: "inputCore.InputGesture") -> None:
		conf = config.conf["visAwareGeneral"]
		if getConfigChoiceValue(conf, "engineType", ENGINE_TYPES) == "Agent":
			# Translators: Reported when recognition source cycling is requested while AI Agent mode is active.
			ui.message(_("Changing recognition source is not supported in AI Agent mode."))
			return
		name = self._cycleThroughSettings(conf, "sourceType", getCycleSourceTypes(conf))
		ui.message(name)

	@staticmethod
	def _cycleThroughSettings(configSection: Any, configName: str, configList: list) -> str:
		"""
		Cycles to the next value for a given setting in the config.

		:param configSection: The configuration section dictionary.
		:param configName: The name of the setting to cycle.
		:param configList: A list of (value, description) tuples for the setting.
		:returns: The description of the newly selected value.
		"""
		try:
			currentValue = configSection.__getitem__(configName, checkValidity=False)
		except KeyError:
			currentValue = None
		availableValues = [name for name, desc in configList]
		try:
			currentIndex = availableValues.index(currentValue)
			nextIndex = (currentIndex + 1) % len(availableValues)
		except ValueError:
			nextIndex = 0
		newValue = availableValues[nextIndex]
		configSection[configName] = newValue
		return configList[nextIndex][1]  # Return the description

	@classmethod
	def _getImageFromClipboard(cls) -> Image.Image | None:
		"""
		Retrieve an image from the clipboard.

		This function handles raw image data, copied image files, and file paths in text format.
		:returns: A PIL Image object on success, or None on failure.
		"""
		try:
			# This can return a PIL Image object or a list of file paths.
			clipboardContent = ImageGrab.grabclipboard()
			# Case 1: Raw image data in the clipboard.
			if isinstance(clipboardContent, Image.Image):
				log.debug("Found raw image data in clipboard.")
				return clipboardContent.convert("RGB")
			# Case 2: One or more files copied to the clipboard.
			if isinstance(clipboardContent, list) and clipboardContent:
				log.debug(f"Found file paths in clipboard from ImageGrab: {clipboardContent}")
				try:
					# If ImageGrab returns a file list, we prioritize it and consider the task done.
					return Image.open(clipboardContent[0]).convert("RGB")
				except (OSError, FileNotFoundError):
					log.warning(f"File from clipboard is not a valid image: {clipboardContent[0]}")
					# If the file path from ImageGrab is invalid, we assume there's no valid image.
					return None
		except Exception:
			# ImageGrab.grabclipboard can sometimes fail unexpectedly.
			# We log the error and proceed to check for a text path as a fallback.
			log.debug(
				"ImageGrab.grabclipboard failed, will try reading clipboard as text path.",
				exc_info=True,
			)
		# Case 3: A file path as plain text in the clipboard.
		try:
			path = api.getClipData()
			if path and os.path.isfile(path):
				log.debug(f"Found a text file path in clipboard: {path}")
				try:
					return Image.open(path).convert("RGB")
				except OSError:
					log.warning(f"Text path points to a non-image file: {path}")
		except OSError:
			# Expected when clipboard contains file(s), not text. Safely ignore.
			log.debug("api.getClipData() failed, likely due to non-text clipboard format.")
		return None

	def _getImageFromClipboardSource(self) -> tuple[RecogImageInfo, Image.Image] | None:
		"""
		Gets an image from the clipboard and prepares it for recognition.

		:returns: A tuple of (RecogImageInfo, PIL.Image.Image) or None on failure.
		"""
		recognizeImage = self._getImageFromClipboard()
		if not recognizeImage:
			# Translators: Reported when attempting to recognize a clipboard image, but no image is found.
			ui.message(_("No image in clipboard"))
			return None
		imageInfo = RecogImageInfo(0, 0, recognizeImage.width, recognizeImage.height, 1)
		return imageInfo, recognizeImage

	def _getImageFromMouseCaptureArea(self) -> tuple[RecogImageInfo, Image.Image] | None:
		"""Gets an image from the mouse capture area and prepares it for recognition."""
		gui.mainFrame.prePopup()
		dlg = ScreenCaptureDialog(None)
		try:
			if dlg.ShowModal() == wx.ID_OK:
				return dlg.getCapturedImage()
		finally:
			dlg.Destroy()
			gui.mainFrame.postPopup()
		return None

	def _getImageFromSource(
		self,
		currentSource: str,
		recognizer: "ContentRecognizer",
	) -> tuple[RecogImageInfo, Image.Image] | None:
		"""
		Dispatches the image acquisition based on the source type.

		:param currentSource: The configured source type (e.g., "clipboardImage").
		:param recognizer: The content recognizer instance.
		:returns: A tuple of (RecogImageInfo, PIL.Image.Image) or None on failure.
		"""
		sourceHandlers = {
			"clipboardImage": self._getImageFromClipboardSource,
			"mouseCaptureArea": self._getImageFromMouseCaptureArea,
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

	def _getCurrentEngine(self, currentEngineType: str) -> Any | None:
		"""
		Retrieves the currently configured engine instance based on the engine type.

		:param currentEngineType: The type of engine to retrieve ("OCR" or "ImageDescriber").
		:returns: An engine instance or None if not configured.
		"""
		if currentEngineType == "OCR":
			return self.ocrHandler.getCurrentEngine()
		elif currentEngineType == "ImageDescriber":
			return self.descHandler.getCurrentEngine()
		return None

	def _showRecognitionResultDocument(
		self,
		result: RecognitionResult,
		autoSayAll: bool = False,
	) -> None:
		"""
		Shows a recognition result in NVDA's virtual document UI.

		:param result: The recognition result to show.
		:param autoSayAll: True to start say-all after the result document receives focus.
		"""
		resultObjectClass = _AutoSayAllRecogResultNVDAObject if autoSayAll else recogUi.RecogResultNVDAObject
		displayObject = resultObjectClass(result=result)
		displayObject.setFocus()

	def _isScreenCurtainRunning(self) -> bool:
		from screenCurtain import screenCurtain as screenCurtainController

		return screenCurtainController is not None and screenCurtainController.enabled

	def _onRecognitionResult(self, result: Any) -> None:
		"""
		Handle results and exceptions from the worker thread. This runs on the main thread.

		:param result: The result from the recognition task, which can be a RecognitionResult or an Exception.
		"""
		try:
			if isinstance(result, Exception):
				if isinstance(result, CancellationError):
					if getattr(result.event, "is_user_initiated", True):
						# Translators: Reported when a recognition task is cancelled by the user.
						ui.message(_("Recognition cancelled"))
					return
				elif isinstance(result, AuthenticationError):
					# Translators: An error message for authentication failures. The placeholder is the specific error.
					message = _("Authentication failed: {}").format(result)
				elif isinstance(result, NetworkError):
					# Translators: An error message for network issues. The placeholder is the specific error.
					message = _("Network error: {}").format(result)
				elif isinstance(result, ApiError):
					# Translators: A generic error message for recognition failures from the API.
					message = _("Recognition failed: {}").format(result)
				else:
					log.error("An unexpected error occurred during recognition.", exc_info=result)
					# Translators: A message for an unexpected error during recognition.
					message = _("Recognition failed with an unexpected error.")
				ui.message(message)
				return
			if not isinstance(result, RecognitionResult):
				log.error(f"Received an unknown result type from recognition: {type(result)}")
				# Translators: An error message for an unknown result type.
				ui.message(_("Recognition returned an unknown result type."))
				return
			historyEntry = recogHistory.getAttachedEntry(result)
			if historyEntry:
				recogHistory.addEntry(historyEntry, result=result)
			conf = config.conf["visAwareGeneral"]
			if conf["copyToClipboard"]:
				api.copyToClip(result.text, notify=True)
			if isinstance(result, SimpleTextResult):
				if getattr(result, "forceVirtualDocument", False):
					self._showRecognitionResultDocument(
						result,
						autoSayAll=conf["autoSayAllOnResult"],
					)
				elif conf["useBrowseableMessage"]:
					# Translators: The title for the browsable message showing the recognition result.
					showMarkdownBrowseableMessage(result.text, title=_("Recognition result"))
				else:
					ui.message(result.text)
			else:
				# Use the standard NVDA UI for displaying navigable results.
				self._showRecognitionResultDocument(
					result,
					autoSayAll=conf["autoSayAllOnResult"],
				)
		finally:
			log.debug("Recognition task completed. Clearing active engine reference.")
			self._activeEngine = None

	def _onStreamingRecognitionResult(self, result: Any) -> None:
		"""
		Handle streaming recognition events. This runs on the main thread.

		:param result: A streaming event or an exception from the worker thread.
		"""
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
					# Translators: Reported after partial streaming output when the service stopped before completing.
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
		# Translators: An error message for an unknown result type.
		ui.message(_("Recognition returned an unknown result type."))
		self._activeEngine = None

	def _makeRecognitionCallback(
		self,
		recognitionSequence: int,
		streamResult: bool,
	) -> Callable[[Any], None]:
		"""
		Creates a callback that ignores results from superseded recognition tasks.

		:param recognitionSequence: The sequence number for the task being started.
		:param streamResult: Whether the task will deliver streaming events.
		:returns: A callback suitable for ContentRecognizer.recognize.
		"""

		def onResult(result: Any) -> None:
			if recognitionSequence != self._activeRecognitionSequence:
				log.debug(
					"Ignoring result from a superseded recognition task. "
					f"callbackSequence={recognitionSequence}, activeSequence={self._activeRecognitionSequence}, "
					f"streamResult={streamResult}, resultType={type(result)}",
				)
				return
			if streamResult:
				self._onStreamingRecognitionResult(result)
			else:
				self._onRecognitionResult(result)

		return onResult

	def executeRecognition(
		self,
		gesture: "inputCore.InputGesture",
		currentSource: str,
		currentEngineType: str,
		simpleText: bool,
	) -> None:
		"""
		Orchestrates the recognition process from initiation to result handling.

		:param gesture: The input gesture that triggered the recognition.
		:param currentSource: The configured source of the image.
		:param currentEngineType: The configured type of engine to use.
		:param simpleText: Whether to request a simple text result.
		"""
		try:
			self._cancelCurrentRecognition(isUserInitiated=False)
			engine = self._getCurrentEngine(currentEngineType)
			self._activeRecognitionSequence += 1
			recognitionSequence = self._activeRecognitionSequence
			self._activeEngine = engine
			if not engine or engine.name == "empty":
				# Translators: A message indicating that no recognition engine is configured.
				ui.message(_("No recognition engine is configured."))
				self._activeEngine = None
				return
			if currentSource != "clipboardImage" and self._isScreenCurtainRunning():
				# Translators: A message shown when trying to recognize with screen curtain enabled.
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
			# The unified image acquisition process
			imageData = self._getImageFromSource(currentSource, engine)
			if not imageData:
				engine.streamResult = False
				self._activeEngine = None
				return
			imageInfo, recognizeImage = imageData
			pixels = recognizeImage.tobytes("raw", "BGRX")
			# Translators: Reporting when content recognition (e.g. OCR) begins.
			ui.message(_("Recognizing"))
			if shouldStream:
				self._streamingSpeechPresenter.start()
			onResult = self._makeRecognitionCallback(recognitionSequence, shouldStream)
			engine.recognize(pixels, imageInfo, onResult)
		except Exception as e:
			log.error(f"Error preparing for recognition: {e!r}", exc_info=True)
			if "shouldStream" in locals() and shouldStream:
				self._streamingSpeechPresenter.cancel()
			if "engine" in locals() and engine:
				engine.streamResult = False
			# Translators: A generic error message before recognition starts.
			ui.message(
				_("An error occurred before starting recognition. Please check your settings and try again."),
			)
			self._activeEngine = None

	def _cycleEngine(self, handler: "AbstractEngineHandler") -> None:
		"""
		Cycles through the available engines for a given handler.

		:param handler: The engine handler (OCR or Describer) to cycle through.
		"""
		engineList = handler.getEngineList()
		availableEngines = [e for e in engineList if e[0] != "empty"]
		numEngines = len(availableEngines)
		if numEngines == 0:
			# Translators: Reported when there are no alternative engines to switch to.
			ui.message(_("No other engines to switch to."))
			return
		if numEngines == 1:
			engineDescription = availableEngines[0][1]
			# Translators: Reports the name of the only available engine.
			ui.message(_("Current engine: {engine_name}").format(engine_name=engineDescription))
			return
		nextIndex = 0
		currentEngine = handler.getCurrentEngine()
		if currentEngine:
			try:
				engineNames = [name for name, desc in availableEngines]
				currentIndex = engineNames.index(currentEngine.name)
				nextIndex = (currentIndex + 1) % numEngines
			except ValueError:
				# If current engine is not in the list, default to the first one.
				pass
		newEngineName, newEngineDescription = availableEngines[nextIndex]
		if handler.setCurrentEngine(newEngineName):
			ui.message(newEngineDescription)

	@script(
		# Translators: Describes a command to cycle through available recognition engines.
		description=_("Cycles through engines for the current mode"),
		category=CATEGORY_NAME,
		gestures=["kb:NVDA+Alt+2"],
	)
	def script_cycleActiveEngine(self, gesture: "inputCore.InputGesture") -> None:
		currentEngineType = getConfigChoiceValue(config.conf["visAwareGeneral"], "engineType", ENGINE_TYPES)
		if currentEngineType == "OCR":
			log.debug("Cycling through OCR engines.")
			self._cycleEngine(self.ocrHandler)
		elif currentEngineType == "ImageDescriber":
			log.debug("Cycling through Image Describer engines.")
			self._cycleEngine(self.descHandler)
		elif currentEngineType == "Agent":
			log.debug("Cycling through Agent engines.")
			self._cycleEngine(AgentHandler)
		else:
			log.warning(f"Unknown engine type for cycling: {currentEngineType}")
