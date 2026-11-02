# Copyright (C) 2025 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

import addonHandler
import api
import copy
import core
import NVDAObjects
import config
import globalPluginHandler
import globalVars
import gui
import wx
import inputCore
from logHandler import log
from gui.settingsDialogs import NVDASettingsDialog, SettingsPanel
import functools
from threading import Event
from typing import Any, TYPE_CHECKING
from collections.abc import Callable
from scriptHandler import script
from contentRecog import (
	RecogImageInfo,
	LinesWordsResult,
	RecognitionResult,
	SimpleTextResult,
)
import ui
import vision

from ._autoRecognition import AutoRecognitionController, getNextAutoRecognitionSetting
from ._screenCapture import isScreenCurtainCaptureSupported
from ._taskCues import TaskCueManager
from .askDialog import AskQuestionFrame
from .agent.engine import AgentHandler
from .agent.settings import AgentPanel
from .agent.controller import AgentController
from .exceptions import ApiError
from . import recogHistory
from .conversation import ConversationContext, makeConversationContext
from .markdownRenderer import showMarkdownBrowseableMessage
from .recognitionRuntime import RecognitionRuntime
from .recogHandler import (
	CustomOCRHandler,
	ImageDescriberHandler,
	getCycleSourceTypes,
	getAutoRecognitionTypeAndEngine,
	getAutoRecognitionTypeChoices,
	getConfigChoiceValue,
	getEffectiveAutoRecognitionEngine,
	getValidSourceType,
	isAutomaticRecognitionEnabled,
	ENGINE_TYPES,
)
from .recognitionSettings import AutomaticRecognitionPanel, CustomOCRPanel, ImageDescriberPanel, OCRPanel
from .streamingSpeech import StreamingSpeechPresenter

if TYPE_CHECKING:
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
	"sourceType": 'option("navigatorObject", "clipboardImage", "wholeDesktop", "foreGroundWindow", default="navigatorObject")',
	"excludedSourceTypes": "list(default=list())",
	"nvdacnUser": "string(default='')",
	"nvdacnPass": "string(default='')",
}


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
			if getattr(self, "_terminated", False):
				return
			if not hasattr(self, "_multiPressStates"):
				self._multiPressStates = {}
			stateKey = scriptFunc.__name__
			funcState = self._multiPressStates.setdefault(stateKey, {"count": 0, "timer": None})
			if funcState["timer"] and funcState["timer"].IsRunning():
				funcState["timer"].Stop()
			funcState["count"] += 1

			def finalize():
				"""Called after the timeout to execute the script with the final press count."""
				if getattr(self, "_terminated", False):
					funcState["count"] = 0
					funcState["timer"] = None
					return
				finalCount = funcState["count"]
				# Reset state for the next sequence.
				funcState["count"] = 0
				funcState["timer"] = None
				# Call the original script with the press count.
				scriptFunc(self, gesture, pressCount=finalCount)

			funcState["timer"] = wx.CallLater(timeout, finalize)

		return wrapper

	return decorator


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
		self._terminated = False
		self._taskCues = TaskCueManager()
		self._agentController = AgentController(self._taskCues, self._isScreenCurtainRunning)
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
		self._askBeforeDescribeDialogActive = False
		self._pendingDescribeContext: ConversationContext | None = None
		self._pendingDescribeQuestion: str | None = None
		self._pendingDescribeAwaiting = False
		self.ocrSettingMenuItem: wx.MenuItem | None = None
		self._recognitionRuntime = RecognitionRuntime(
			self._taskCues,
			self._streamingSpeechPresenter,
			None,
			None,
			self._cancelRecognitionOutsideRuntime,
		)
		self._recognitionRuntime.resultHook = self._handleRecognitionResultHook
		if globalVars.appArgs.secure or getattr(config, "isAppX", False):
			return
		config.conf.spec["visAwareGeneral"] = GENERAL_CONFIG_SPEC
		CustomOCRHandler.initialize()
		self.ocrHandler = CustomOCRHandler
		ImageDescriberHandler.initialize()
		self.descHandler = ImageDescriberHandler
		self._recognitionRuntime.setHandlers(self.ocrHandler, self.descHandler)
		AgentHandler.initialize()
		self._autoRecognitionController = AutoRecognitionController(self._taskCues)
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
		if self._terminated:
			return
		self._terminated = True
		self._stopMultiPressTimers()
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
		self._cancelRecognitionOutsideRuntime(isUserInitiated=False)
		if self._autoRecognitionController:
			self._autoRecognitionController.terminate()
			self._autoRecognitionController = None
		self._agentController.terminate()
		self._recognitionRuntime.terminate()
		self._taskCues.terminate()
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

	def _stopMultiPressTimers(self) -> None:
		for state in getattr(self, "_multiPressStates", {}).values():
			timer = state.get("timer")
			if timer and timer.IsRunning():
				timer.Stop()
			state["timer"] = None
			state["count"] = 0

	@staticmethod
	def openSettingsDialog(evt: wx.CommandEvent) -> None:
		gui.mainFrame.popupSettingsDialog(OCRMultiCategorySettingsDialog)

	def _registerAutoRecognitionEventHandlers(self) -> None:
		if self._terminated:
			return
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
		if isinstance(resultObject, SimpleTextResult) and getattr(
			resultObject,
			"forceBrowseableMessage",
			False,
		):
			# Translators: The title for the browsable message showing the recognition result.
			showMarkdownBrowseableMessage(resultObject.text, title=_("Recognition result"))
		elif not self._showHtmlRecognitionResult(resultObject):
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
			self._askQuestionFrame = AskQuestionFrame(gui.mainFrame, context, self._taskCues)
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

	@script(
		# Translators: Describes the command to ask a question before image description.
		description=_("Asks a question before image description"),
		category=CATEGORY_NAME,
		gestures=["kb:NVDA+shift+alt+space"],
	)
	def script_askQuestionBeforeDescribe(self, gesture: "inputCore.InputGesture") -> None:
		conf = config.conf["visAwareGeneral"]
		currentEngineType = getConfigChoiceValue(conf, "engineType", ENGINE_TYPES)
		if currentEngineType != "ImageDescriber":
			# Translators: Reported when asking a question before recognition is invoked outside of image description mode.
			ui.message(_("This operation is only supported in image description mode."))
			return
		engine = self.descHandler.getCurrentEngine() if hasattr(self, "descHandler") else None
		if not engine or not engine.isSupported("prompt"):
			# Translators: Reported when the active model does not support initial custom prompts.
			ui.message(_("This model doesn't support the operation."))
			return
		currentSource = getValidSourceType(conf)
		self._promptBeforeDescribe(currentSource)

	def _promptBeforeDescribe(self, currentSource: str) -> None:
		"""Shows the ask-question frame so the user can prompt the upcoming image description.

		The image is captured up front, before the frame becomes visible, so the
		panel itself never appears in the shot.
		"""
		frame = self._askQuestionFrame
		if frame is not None and frame.IsShown():
			if self._pendingDescribeAwaiting or frame.pendingDescribeActive:
				if self._pendingDescribeAwaiting:
					# Translators: Reported while a follow-up question is still being answered.
					ui.message(_("Waiting for answer."))
				frame.Raise()
				frame.focusQuestionInput()
				return
			frame.Hide()
			self._askBeforeDescribeDialogActive = False
		elif self._askBeforeDescribeDialogActive:
			if self._pendingDescribeAwaiting:
				# Translators: Reported while a follow-up question is still being answered.
				ui.message(_("Waiting for answer."))
				return
			self._askBeforeDescribeDialogActive = False
		engine = self.descHandler.getCurrentEngine() if hasattr(self, "descHandler") else None
		if not engine or engine.name == "empty":
			# Translators: A message indicating that no recognition engine is configured.
			ui.message(_("No recognition engine is configured."))
			return
		if (
			currentSource != "clipboardImage"
			and self._isScreenCurtainRunning()
			and not isScreenCurtainCaptureSupported()
		):
			# Translators: A message shown when trying to recognize with screen curtain enabled.
			ui.message(_("Please disable screen curtain before recognition."))
			return
		imageData = self._recognitionRuntime.getImageFromSource(currentSource, engine)
		if not imageData:
			return
		pendingContext = ConversationContext(
			engine=engine,
			image=None,
			response={},
			initialText="",
			engineName=getattr(engine, "name", ""),
			engineDescription=getattr(engine, "description", ""),
		)

		def onSend(question: str) -> None:
			if hasattr(engine, "_initialPromptForDescribe"):
				engine._initialPromptForDescribe = question
			self._pendingDescribeContext = pendingContext
			self._pendingDescribeQuestion = question
			self._pendingDescribeAwaiting = True
			wx.CallLater(
				250,
				self.executeRecognition,
				None,
				currentSource,
				"ImageDescriber",
				False,
				imageData,
			)

		self._askBeforeDescribeDialogActive = True
		self._askQuestionContext = None
		self._askQuestionHistoryEntry = None

		def showDialog():
			if not self._askBeforeDescribeDialogActive:
				return
			if not self._askQuestionFrame:
				self._askQuestionFrame = AskQuestionFrame(gui.mainFrame, None, self._taskCues)
			self._askQuestionFrame.setPendingDescribe(pendingContext, onSend)
			gui.mainFrame.prePopup()
			try:
				self._askQuestionFrame.Show()
				self._askQuestionFrame.Raise()
				self._askQuestionFrame.focusQuestionInput()
			except Exception:
				log.error("Could not show describe-before ask question frame.", exc_info=True)
				# Translators: Reported when the follow-up question dialog cannot be opened.
				ui.message(_("Could not open the follow-up question dialog."))
				self._askBeforeDescribeDialogActive = False
			finally:
				gui.mainFrame.postPopup()

		wx.CallAfter(showDialog)

	def _handleRecognitionResultHook(self, result: Any) -> bool:
		"""Intercepts recognition results when awaiting a describe-before question."""
		if not self._pendingDescribeAwaiting or not self._pendingDescribeContext:
			return False
		if isinstance(result, Exception):
			from .exceptions import AuthenticationError, NetworkError, CancellationError

			if isinstance(result, CancellationError):
				message = _("Recognition cancelled")
			elif isinstance(result, AuthenticationError):
				message = _("Authentication failed: {}").format(result)
			elif isinstance(result, NetworkError):
				message = _("Network error: {}").format(result)
			elif isinstance(result, ApiError):
				message = _("Recognition failed: {}").format(result)
			else:
				message = _("Recognition failed with an unexpected error.")
			self._failPendingDescribe(message)
			return False
		if not isinstance(result, RecognitionResult):
			self._failPendingDescribe(_("Recognition returned an unknown result type."))
			return False
		historyEntry = recogHistory.getAttachedEntry(result)
		if historyEntry:
			recogHistory.addEntry(historyEntry, result=result)
		conf = config.conf["visAwareGeneral"]
		if conf["copyToClipboard"]:
			api.copyToClip(result.text, notify=True)
		self._backfillPendingDescribeResult(result)
		return True

	def _backfillPendingDescribeResult(self, result: Any) -> None:
		"""Back-fills the recognition result into the ask-question frame."""
		if not self._pendingDescribeAwaiting or not self._pendingDescribeContext:
			return
		frame = self._askQuestionFrame
		if frame is None:
			return
		ctx = self._pendingDescribeContext
		payload = recogHistory.getAttachedEntry(result)
		if payload is not None:
			image = payload.image
			if hasattr(image, "copy"):
				image = image.copy()
			ctx.image = image
			response = payload.response
			ctx.response = copy.deepcopy(response) if isinstance(response, dict) else {}
		else:
			ctx.response = {}
		try:
			ctx.engineName = getattr(ctx.engine, "name", "")
			ctx.engineDescription = getattr(ctx.engine, "description", "")
			self._askQuestionContext = ctx
			self._askQuestionHistoryEntry = payload
			frame.backfillPendingDescribe(self._pendingDescribeQuestion or "", result)
		except Exception:
			log.error("Could not back-fill the describe-before result into the frame.", exc_info=True)
		finally:
			self._pendingDescribeAwaiting = False
			self._pendingDescribeQuestion = None
			self._pendingDescribeContext = None
			self._askBeforeDescribeDialogActive = False

	def _failPendingDescribe(self, message: str) -> None:
		"""Reports a failed describe-before recognition in the frame."""
		engine = self.descHandler.getCurrentEngine() if hasattr(self, "descHandler") else None
		if engine and hasattr(engine, "_initialPromptForDescribe"):
			engine._initialPromptForDescribe = None
		if not self._pendingDescribeAwaiting:
			return
		self._pendingDescribeAwaiting = False
		self._pendingDescribeQuestion = None
		self._pendingDescribeContext = None
		self._askBeforeDescribeDialogActive = False
		frame = self._askQuestionFrame
		if frame is not None:
			frame.failPendingDescribe(message, report=False)

	def _promptAndStartAgent(self) -> None:
		self._agentController.promptAndStart()

	def _startAgent(self, goal: str) -> None:
		self._agentController.start(goal)

	def _stopAgent(self, isUserInitiated: bool = True) -> bool:
		return self._agentController.stop(isUserInitiated)

	def _askAgentUser(self, question: str, cancelEvent: Event) -> str | None:
		return self._agentController.askUser(question, cancelEvent)

	def _closeAgentQuestionDialog(self) -> None:
		self._agentController.closeQuestionDialog()

	def _cancelRecognitionOutsideRuntime(self, isUserInitiated: bool) -> bool:
		if self._stopAgent(isUserInitiated=isUserInitiated):
			return True
		if self._autoRecognitionController and self._autoRecognitionController.cancel():
			if isUserInitiated:
				# Translators: Reported when a recognition task is cancelled by the user.
				ui.message(_("Recognition cancelled"))
			return True
		return False

	def _cancelCurrentRecognition(self, isUserInitiated: bool = True) -> bool:
		return self._cancelRecognitionOutsideRuntime(isUserInitiated) or self._recognitionRuntime.cancel(
			isUserInitiated,
		)

	def _getCurrentEngine(self, currentEngineType: str) -> Any | None:
		return self._recognitionRuntime.getCurrentEngine(currentEngineType)

	def _showRecognitionResultDocument(self, result: RecognitionResult, autoSayAll: bool = False) -> None:
		self._recognitionRuntime.showRecognitionResultDocument(result, autoSayAll)

	def _showHtmlRecognitionResult(self, result: RecognitionResult) -> bool:
		return self._recognitionRuntime.showHtmlRecognitionResult(result)

	def _isScreenCurtainRunning(self) -> bool:
		return self._recognitionRuntime.isScreenCurtainRunning()

	def _makeRecognitionCallback(self, recognitionSequence: int, streamResult: bool) -> Callable[[Any], None]:
		return self._recognitionRuntime.makeRecognitionCallback(recognitionSequence, streamResult)

	def executeRecognition(
		self,
		gesture: "inputCore.InputGesture | None",
		currentSource: str,
		currentEngineType: str,
		simpleText: bool,
		imageData: tuple | None = None,
	) -> None:
		self._recognitionRuntime.execute(
			gesture,
			currentSource,
			currentEngineType,
			simpleText,
			imageData=imageData,
		)

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
		self.startRecognition(gesture=gesture, simpleText=pressCount != 1)

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
		description=_("Cycles through automatic recognition modes: off, image description, and OCR"),
		category=CATEGORY_NAME,
		gestures=[],
	)
	def script_cycleAutomaticRecognitionMode(self, gesture: "inputCore.InputGesture") -> None:
		conf = config.conf["visAwareGeneral"]
		nextSetting = getNextAutoRecognitionSetting(getEffectiveAutoRecognitionEngine())
		conf["autoRecognitionEngine"] = nextSetting
		if self._autoRecognitionController:
			self._autoRecognitionController.cancel()
		nextType, _engineName = getAutoRecognitionTypeAndEngine(nextSetting)
		nextTypeDescription = dict(getAutoRecognitionTypeChoices())[nextType]
		ui.message(nextTypeDescription)

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
