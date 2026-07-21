# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Local desktop actions for the Vis Aware computer-use agent."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import io
import math
import re
from threading import Event
import time
from typing import Any

import addonHandler
import api
import gui
import keyboardHandler
from logHandler import log
import mouseHandler
import winUser
import wx

addonHandler.initTranslation()

JPEG_QUALITY = 90
NORMALIZED_SCALE = 1000
DEFAULT_SCROLL_DELTA = winUser.WHEEL_DELTA * 4
CLICK_RELEASE_DELAY_SECONDS = 0.04
CLICK_SEQUENCE_DELAY_SECONDS = 0.12
DRAG_MIN_STEPS = 8
DRAG_MAX_STEPS = 40
DRAG_PIXELS_PER_STEP = 16
DRAG_START_DELAY_SECONDS = 0.18
DRAG_HOLD_DELAY_SECONDS = 0.18
DRAG_STEP_DELAY_SECONDS = 0.025
DRAG_RELEASE_DELAY_SECONDS = 0.2
DRAG_CURVE_MAX_PIXELS = 3
SCROLL_STEP_DELAY_SECONDS = 0.04
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXSCREEN = 0
SM_CYSCREEN = 1
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_END = 0x23
VK_V = 0x56
TEXT_CLEAR_BACKSPACE_COUNT = 30
CLIPBOARD_RETRY_COUNT = 3
CLIPBOARD_RETRY_DELAY_SECONDS = 0.05
GMEM_MOVEABLE = 0x0002
_clipboardApisInitialized = False
_heldKeyGestures: dict[str, keyboardHandler.KeyboardInputGesture] = {}
_leftMouseHeld = False
CheckCancelled = Callable[[], None]


class ActionExecutionError(Exception):
	"""Raised when a local desktop action cannot be executed."""


@dataclass
class AgentAction:
	name: str
	arguments: dict[str, Any]
	message: str = ""


@dataclass
class BoundWindow:
	hwnd: int
	processID: int
	appName: str
	title: str
	left: int
	top: int
	width: int
	height: int


@dataclass
class Screenshot:
	imageBase64: str
	mimeType: str
	width: int
	height: int
	screenLeft: int
	screenTop: int
	screenWidth: int
	screenHeight: int
	digest: str
	window: BoundWindow


@dataclass
class _ClipboardFormatData:
	formatID: int
	data: bytes


def formatScreenshotPromptContext(screenshot: Screenshot) -> str:
	left, top, right, bottom = _normalizedWindowBounds(screenshot)
	window = screenshot.window
	title = " ".join(window.title.split())
	titleText = f"Foreground window title: {title[:160]!r}. " if title else ""
	return (
		f"Screenshot is full screen ({screenshot.screenWidth}x{screenshot.screenHeight}). "
		f"{titleText}"
		f"Foreground window bounds: pixels left={window.left}, top={window.top}, "
		f"right={window.left + window.width}, bottom={window.top + window.height}; "
		f"normalized x={left}-{right}, y={top}-{bottom}."
	)


def getForegroundWindowInfo() -> BoundWindow:
	hwnd = winUser.getForegroundWindow()
	processID = 0
	title = ""
	left = top = width = height = 0
	if hwnd:
		try:
			processID, _threadID = winUser.getWindowThreadProcessID(hwnd)
			left, top, width, height = _getWindowBounds(hwnd)
			title = winUser.getWindowText(hwnd)
		except Exception:
			log.debugWarning("Could not get foreground window info.", exc_info=True)
	if width <= 0 or height <= 0:
		left, top, width, height = _getDesktopBounds()
	return BoundWindow(
		hwnd=hwnd or 0,
		processID=processID,
		appName=_getForegroundAppName(),
		title=title,
		left=left,
		top=top,
		width=width,
		height=height,
	)


def getActiveAgentWindow() -> BoundWindow:
	return getForegroundWindowInfo()


def captureScreen(window: BoundWindow, quality: int = JPEG_QUALITY) -> Screenshot:
	if not wx.IsMainThread():
		return _callOnMainThread(_captureScreenOnMainThread, window, quality)
	return _captureScreenOnMainThread(window, quality)


def _captureScreenOnMainThread(window: BoundWindow, quality: int = JPEG_QUALITY) -> Screenshot:
	screenLeft, screenTop, screenWidth, screenHeight = _getDesktopBounds()
	bbox = (
		screenLeft,
		screenTop,
		screenLeft + screenWidth,
		screenTop + screenHeight,
	)
	quality = max(1, min(int(quality or JPEG_QUALITY), 95))
	log.debug(
		f"Agent capturing screen: hwnd={window.hwnd}, app={window.appName!r}, "
		f"title={window.title!r}, screenBounds={bbox}, foregroundBounds="
		f"({window.left}, {window.top}, {window.width}, {window.height}), quality={quality}",
	)
	try:
		bitmap = wx.Bitmap(screenWidth, screenHeight)
		memoryDC = wx.MemoryDC(bitmap)
		memoryDC.Blit(0, 0, screenWidth, screenHeight, wx.ScreenDC(), screenLeft, screenTop)
		memoryDC.SelectObject(wx.NullBitmap)
		image = bitmap.ConvertToImage()
		image.SetOption("quality", quality)
		buffer = io.BytesIO()
		image.SaveFile(buffer, wx.BITMAP_TYPE_JPEG)
	except Exception as e:
		log.error("Agent screenshot failed", exc_info=True)
		raise ActionExecutionError(_("Failed to capture the foreground window.")) from e
	imageWidth = image.GetWidth()
	imageHeight = image.GetHeight()
	rawImage = buffer.getvalue()
	return Screenshot(
		imageBase64=base64.b64encode(rawImage).decode("ascii"),
		mimeType="image/jpeg",
		width=imageWidth,
		height=imageHeight,
		screenLeft=screenLeft,
		screenTop=screenTop,
		screenWidth=screenWidth,
		screenHeight=screenHeight,
		digest=hashlib.sha1(rawImage).hexdigest(),
		window=window,
	)


def _callOnMainThread(func, *args):
	doneEvent = Event()
	result: dict[str, Any] = {}

	def run():
		try:
			result["value"] = func(*args)
		except Exception as e:
			result["error"] = e
		finally:
			doneEvent.set()

	wx.CallAfter(run)
	if not doneEvent.wait(10):
		raise ActionExecutionError(_("Failed to capture the foreground window."))
	if "error" in result:
		raise result["error"]
	return result["value"]


def executeAction(
	action: AgentAction,
	screenshot: Screenshot,
	checkCancelled: CheckCancelled | None = None,
) -> None:
	if checkCancelled is None:
		checkCancelled = _checkNotCancelled
	checkCancelled()
	name = action.name
	args = action.arguments
	log.debug(f"Executing agent action: {name}, args={_safeActionArgsForLog(args)}")
	if name in ("click_at", "click"):
		_clickAt(screenshot, args, checkCancelled=checkCancelled)
	elif name in ("double_click_at", "double_click"):
		_clickAt(screenshot, args, clickCount=2, checkCancelled=checkCancelled)
	elif name in ("triple_click_at", "triple_click"):
		_clickAt(screenshot, args, clickCount=3, checkCancelled=checkCancelled)
	elif name in ("right_click_at", "right_click"):
		_clickAt(screenshot, args, secondary=True, checkCancelled=checkCancelled)
	elif name in ("middle_click_at", "middle_click"):
		_clickAt(screenshot, args, middle=True, checkCancelled=checkCancelled)
	elif name in ("hover_at", "move_to", "move"):
		_moveTo(screenshot, args, checkCancelled)
	elif name == "type_text_at":
		_typeTextAt(screenshot, args, checkCancelled)
	elif name in ("key_combination", "press_key", "hotkey"):
		_sendKeyCombination(str(args.get("keys") or args.get("key") or ""))
	elif name == "key_down":
		_sendKeyDown(str(args.get("key") or ""))
	elif name == "key_up":
		_sendKeyUp(str(args.get("key") or ""))
	elif name in ("scroll_at", "scroll_document", "scroll"):
		_scroll(screenshot, args, checkCancelled)
	elif name in ("drag_and_drop", "drag_to"):
		_dragAndDrop(screenshot, args, checkCancelled)
	elif name == "drag_by":
		_dragBy(screenshot, args, checkCancelled)
	elif name in ("mouse_down", "left_mouse_down"):
		_moveTo(screenshot, args, checkCancelled)
		_mouseDown()
	elif name in ("mouse_up", "left_mouse_up"):
		if "x" in args and "y" in args:
			_moveTo(screenshot, args, checkCancelled)
		_mouseUp()
	elif name in ("wait", "wait_5_seconds"):
		_sleep(float(args.get("seconds") or 5), checkCancelled)
	elif name == "go_back":
		_sendKeyCombination("alt+leftArrow")
	elif name == "go_forward":
		_sendKeyCombination("alt+rightArrow")
	elif name == "navigate":
		_navigate(args, checkCancelled)
	else:
		raise ActionExecutionError(_("Unsupported agent action: {}").format(name))
	log.debug(f"Agent action completed: {name}")


def _checkNotCancelled() -> None:
	pass


def _sleep(seconds: float, checkCancelled: CheckCancelled) -> None:
	endTime = time.time() + max(0.0, seconds)
	while True:
		remaining = endTime - time.time()
		if remaining <= 0:
			return
		checkCancelled()
		time.sleep(min(0.05, remaining))


def releaseHeldInputs() -> None:
	"""Releases keys or mouse buttons pressed by Agent down-only actions."""
	global _leftMouseHeld
	for key, gesture in list(_heldKeyGestures.items()):
		try:
			_sendGestureKeyEvent(gesture, isDown=False)
		except Exception:
			log.debugWarning(f"Could not release held Agent key: {key}", exc_info=True)
		finally:
			_heldKeyGestures.pop(key, None)
	if _leftMouseHeld:
		try:
			mouseHandler.executeMouseEvent(winUser.MOUSEEVENTF_LEFTUP, 0, 0)
		except Exception:
			log.debugWarning("Could not release held Agent mouse button.", exc_info=True)
		finally:
			_leftMouseHeld = False


def _safeActionArgsForLog(args: dict[str, Any]) -> dict[str, Any]:
	return {
		key: f"<{len(str(value))} chars>" if key in {"text", "url"} else value for key, value in args.items()
	}


def _getWindowBounds(hwnd: int) -> tuple[int, int, int, int]:
	rect = wintypes.RECT()
	if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
		raise ctypes.WinError()
	return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _getDesktopBounds() -> tuple[int, int, int, int]:
	left = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
	top = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
	width = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
	height = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
	if width <= 0 or height <= 0:
		left = 0
		top = 0
		width = ctypes.windll.user32.GetSystemMetrics(SM_CXSCREEN)
		height = ctypes.windll.user32.GetSystemMetrics(SM_CYSCREEN)
	if width <= 0 or height <= 0:
		raise ActionExecutionError(_("Failed to capture the foreground window."))
	return left, top, width, height


def _getForegroundAppName() -> str:
	try:
		return api.getForegroundObject().appModule.appName or ""
	except Exception:
		return ""


def _pointFromArgs(
	screenshot: Screenshot,
	args: dict[str, Any],
	xName: str = "x",
	yName: str = "y",
) -> tuple[int, int]:
	try:
		x = int(float(args[xName]))
		y = int(float(args[yName]))
	except (KeyError, TypeError, ValueError) as e:
		raise ActionExecutionError(_("Agent action is missing screen coordinates.")) from e
	x = min(max(x, 0), NORMALIZED_SCALE)
	y = min(max(y, 0), NORMALIZED_SCALE)
	screenX = screenshot.screenLeft + min(
		int(x * screenshot.screenWidth / NORMALIZED_SCALE),
		max(screenshot.screenWidth - 1, 0),
	)
	screenY = screenshot.screenTop + min(
		int(y * screenshot.screenHeight / NORMALIZED_SCALE),
		max(screenshot.screenHeight - 1, 0),
	)
	log.info(
		f"Agent coordinate resolved: normalized=({x}, {y})/{NORMALIZED_SCALE}, "
		f"screen=({screenX}, {screenY}), screenBounds="
		f"({screenshot.screenLeft}, {screenshot.screenTop}, {screenshot.screenWidth}, {screenshot.screenHeight}), "
		f"foregroundBounds=({screenshot.window.left}, {screenshot.window.top}, "
		f"{screenshot.window.width}, {screenshot.window.height}), "
		f"insideForeground={_pointInWindow(screenshot.window, screenX, screenY)}",
	)
	return screenX, screenY


def _normalizedWindowBounds(screenshot: Screenshot) -> tuple[int, int, int, int]:
	window = screenshot.window
	return (
		_screenScalarToNormalized(window.left, screenshot.screenLeft, screenshot.screenWidth),
		_screenScalarToNormalized(window.top, screenshot.screenTop, screenshot.screenHeight),
		_screenScalarToNormalized(window.left + window.width, screenshot.screenLeft, screenshot.screenWidth),
		_screenScalarToNormalized(window.top + window.height, screenshot.screenTop, screenshot.screenHeight),
	)


def _screenScalarToNormalized(value: int, origin: int, size: int) -> int:
	if size <= 0:
		return 0
	return min(max(round((value - origin) * NORMALIZED_SCALE / size), 0), NORMALIZED_SCALE)


def _pointInWindow(window: BoundWindow, x: int, y: int) -> bool:
	return window.left <= x < window.left + window.width and window.top <= y < window.top + window.height


def _moveTo(
	screenshot: Screenshot,
	args: dict[str, Any],
	checkCancelled: CheckCancelled,
) -> tuple[int, int]:
	checkCancelled()
	x, y = _pointFromArgs(screenshot, args)
	if _leftMouseHeld:
		try:
			startX, startY = winUser.getCursorPos()
		except Exception:
			startX, startY = x, y
		log.info(f"Agent mouse move while held: start=({startX}, {startY}), end=({x}, {y})")
		_dragMouseTo(startX, startY, x, y, checkCancelled)
	else:
		log.info(f"Agent mouse move: target=({x}, {y})")
		winUser.setCursorPos(x, y)
		_sleep(0.5, checkCancelled)
	return x, y


def _clickAt(
	screenshot: Screenshot,
	args: dict[str, Any],
	clickCount: int = 1,
	secondary: bool = False,
	middle: bool = False,
	checkCancelled: CheckCancelled = _checkNotCancelled,
) -> None:
	targetX, targetY = _moveTo(screenshot, args, checkCancelled)
	buttonName = _clickButtonName(secondary, middle)
	log.info(
		f"Agent mouse click sequence: button={buttonName}, count={clickCount}, target=({targetX}, {targetY})",
	)
	for index in range(clickCount):
		checkCancelled()
		try:
			actualX, actualY = winUser.getCursorPos()
		except Exception:
			actualX, actualY = targetX, targetY
		log.info(
			f"Agent mouse click: button={buttonName}, index={index + 1}/{clickCount}, "
			f"position=({actualX}, {actualY})",
		)
		_clickMouseButton(secondary, middle, checkCancelled)
		if index < clickCount - 1:
			_sleep(CLICK_SEQUENCE_DELAY_SECONDS, checkCancelled)


def _clickButtonName(secondary: bool, middle: bool) -> str:
	if middle:
		return "middle"
	if secondary:
		return "secondary"
	return "primary"


def _clickMouseButton(secondary: bool, middle: bool, checkCancelled: CheckCancelled) -> None:
	if middle:
		mouseHandler.executeMouseEvent(winUser.MOUSEEVENTF_MIDDLEDOWN, 0, 0)
		try:
			_sleep(CLICK_RELEASE_DELAY_SECONDS, checkCancelled)
		finally:
			mouseHandler.executeMouseEvent(winUser.MOUSEEVENTF_MIDDLEUP, 0, 0)
	elif secondary:
		mouseHandler.doSecondaryClick(releaseDelay=CLICK_RELEASE_DELAY_SECONDS)
	else:
		mouseHandler.doPrimaryClick(releaseDelay=CLICK_RELEASE_DELAY_SECONDS)


def _typeTextAt(screenshot: Screenshot, args: dict[str, Any], checkCancelled: CheckCancelled) -> None:
	if "x" in args and "y" in args:
		_clickAt(screenshot, args, checkCancelled=checkCancelled)
		_sleep(0.8, checkCancelled)
	if args.get("clear_before_typing", True):
		_clearTextField(checkCancelled)
	text = str(args.get("text") or "")
	if text:
		_pasteText(text, checkCancelled)
	if args.get("press_enter", False):
		_sleep(0.5, checkCancelled)
		_tapVirtualKey(VK_RETURN)


def _pasteText(text: str, checkCancelled: CheckCancelled) -> None:
	oldClipboard = _snapshotClipboard()
	oldText = None if oldClipboard is not None else _getClipboardText()
	try:
		checkCancelled()
		_setClipboardText(text)
		_sleep(0.5, checkCancelled)
		_pasteClipboard(checkCancelled)
		_sleep(0.4, checkCancelled)
	finally:
		if oldClipboard is not None:
			try:
				_restoreClipboard(oldClipboard)
			except Exception:
				log.debugWarning("Could not restore clipboard after agent paste.", exc_info=True)
		elif oldText is not None:
			try:
				_setClipboardText(oldText)
			except Exception:
				log.debugWarning("Could not restore clipboard text after agent paste.", exc_info=True)


def _clearTextField(checkCancelled: CheckCancelled) -> None:
	# ponytail: WeChat-style edit boxes can ignore Ctrl+A; bounded Backspace matches VA's stable path.
	_tapVirtualKey(VK_END, flags=1)
	_sleep(0.1, checkCancelled)
	for _index in range(TEXT_CLEAR_BACKSPACE_COUNT):
		checkCancelled()
		_tapVirtualKey(VK_BACK)
		_sleep(0.01, checkCancelled)


def _pasteClipboard(checkCancelled: CheckCancelled) -> None:
	winUser.keybd_event(VK_CONTROL, 0, 0, 0)
	try:
		_sleep(0.15, checkCancelled)
		_tapVirtualKey(VK_V)
		_sleep(0.15, checkCancelled)
	finally:
		winUser.keybd_event(VK_CONTROL, 0, 2, 0)


def _tapVirtualKey(vkCode: int, flags: int = 0) -> None:
	winUser.keybd_event(vkCode, 0, flags, 0)
	winUser.keybd_event(vkCode, 0, flags | 2, 0)


def _getClipboardText() -> str | None:
	try:
		return api.getClipData()
	except Exception:
		return None


def _snapshotClipboard() -> list[_ClipboardFormatData] | None:
	_ensureClipboardApiTypes()
	for attempt in range(CLIPBOARD_RETRY_COUNT):
		try:
			with winUser.openClipboard(gui.mainFrame.Handle):
				formats: list[_ClipboardFormatData] = []
				formatID = 0
				while True:
					formatID = ctypes.windll.user32.EnumClipboardFormats(formatID)
					if not formatID:
						break
					handle = ctypes.windll.user32.GetClipboardData(formatID)
					if not handle:
						continue
					size = ctypes.windll.kernel32.GlobalSize(handle)
					if not size:
						continue
					address = ctypes.windll.kernel32.GlobalLock(handle)
					if not address:
						continue
					try:
						formats.append(_ClipboardFormatData(formatID, ctypes.string_at(address, size)))
					finally:
						ctypes.windll.kernel32.GlobalUnlock(handle)
				return formats
		except PermissionError:
			if attempt == CLIPBOARD_RETRY_COUNT - 1:
				log.debugWarning("Could not snapshot clipboard before agent paste.", exc_info=True)
				return None
			time.sleep(CLIPBOARD_RETRY_DELAY_SECONDS)
		except Exception:
			log.debugWarning("Could not snapshot clipboard before agent paste.", exc_info=True)
			return None
	return None


def _restoreClipboard(formats: list[_ClipboardFormatData]) -> None:
	_ensureClipboardApiTypes()
	for attempt in range(CLIPBOARD_RETRY_COUNT):
		try:
			with winUser.openClipboard(gui.mainFrame.Handle):
				winUser.emptyClipboard()
				for item in formats:
					try:
						_setClipboardFormatData(item)
					except Exception:
						log.debugWarning(
							f"Could not restore clipboard format {item.formatID}.",
							exc_info=True,
						)
			return
		except PermissionError:
			if attempt == CLIPBOARD_RETRY_COUNT - 1:
				raise
			time.sleep(CLIPBOARD_RETRY_DELAY_SECONDS)


def _ensureClipboardApiTypes() -> None:
	global _clipboardApisInitialized
	if _clipboardApisInitialized:
		return
	ctypes.windll.user32.EnumClipboardFormats.argtypes = (wintypes.UINT,)
	ctypes.windll.user32.EnumClipboardFormats.restype = wintypes.UINT
	ctypes.windll.user32.GetClipboardData.argtypes = (wintypes.UINT,)
	ctypes.windll.user32.GetClipboardData.restype = wintypes.HANDLE
	ctypes.windll.user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
	ctypes.windll.user32.SetClipboardData.restype = wintypes.HANDLE
	ctypes.windll.kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
	ctypes.windll.kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
	ctypes.windll.kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
	ctypes.windll.kernel32.GlobalFree.restype = wintypes.HGLOBAL
	ctypes.windll.kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
	ctypes.windll.kernel32.GlobalLock.restype = wintypes.LPVOID
	ctypes.windll.kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
	ctypes.windll.kernel32.GlobalUnlock.restype = wintypes.BOOL
	ctypes.windll.kernel32.GlobalSize.argtypes = (wintypes.HGLOBAL,)
	ctypes.windll.kernel32.GlobalSize.restype = ctypes.c_size_t
	_clipboardApisInitialized = True


def _setClipboardFormatData(item: _ClipboardFormatData) -> None:
	handle = ctypes.windll.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(item.data))
	if not handle:
		raise ctypes.WinError()
	address = ctypes.windll.kernel32.GlobalLock(handle)
	if not address:
		ctypes.windll.kernel32.GlobalFree(handle)
		raise ctypes.WinError()
	try:
		ctypes.memmove(address, item.data, len(item.data))
	finally:
		ctypes.windll.kernel32.GlobalUnlock(handle)
	if not ctypes.windll.user32.SetClipboardData(item.formatID, handle):
		ctypes.windll.kernel32.GlobalFree(handle)
		raise ctypes.WinError()


def _setClipboardText(text: str) -> None:
	for attempt in range(CLIPBOARD_RETRY_COUNT):
		try:
			with winUser.openClipboard(gui.mainFrame.Handle):
				winUser.emptyClipboard()
				winUser.setClipboardData(winUser.CF_UNICODETEXT, text)
			return
		except PermissionError:
			if attempt == CLIPBOARD_RETRY_COUNT - 1:
				raise
			time.sleep(CLIPBOARD_RETRY_DELAY_SECONDS)


def _sendKeyCombination(keys: str) -> None:
	keys = _normalizeKeyCombination(keys)
	if not keys:
		raise ActionExecutionError(_("Agent action is missing a key name."))
	try:
		keyboardHandler.KeyboardInputGesture.fromName(keys).send()
	except Exception as e:
		raise ActionExecutionError(_("Could not press key: {}").format(keys)) from e


def _getKeyGesture(key: str) -> tuple[str, keyboardHandler.KeyboardInputGesture]:
	key = _normalizeKeyCombination(key)
	if "+" in key:
		raise ActionExecutionError(_("Agent key down/up only supports a single key."))
	try:
		gesture = keyboardHandler.KeyboardInputGesture.fromName(key)
	except Exception as e:
		raise ActionExecutionError(_("Could not press key: {}").format(key)) from e
	return key, gesture


def _sendKeyDown(key: str) -> None:
	key, gesture = _getKeyGesture(key)
	_sendGestureKeyEvent(gesture, isDown=True)
	_heldKeyGestures[key] = gesture


def _sendKeyUp(key: str) -> None:
	key, gesture = _getKeyGesture(key)
	_sendGestureKeyEvent(_heldKeyGestures.pop(key, gesture), isDown=False)


def _sendGestureKeyEvent(gesture: keyboardHandler.KeyboardInputGesture, isDown: bool) -> None:
	flag = 0 if isDown else 2
	winUser.keybd_event(gesture.vkCode, gesture.scanCode, gesture.isExtended + flag, 0)


def _mouseDown() -> None:
	global _leftMouseHeld
	try:
		x, y = winUser.getCursorPos()
	except Exception:
		x = y = None
	log.info(f"Agent mouse down: position=({x}, {y})")
	mouseHandler.executeMouseEvent(winUser.MOUSEEVENTF_LEFTDOWN, 0, 0)
	_leftMouseHeld = True


def _mouseUp() -> None:
	global _leftMouseHeld
	try:
		x, y = winUser.getCursorPos()
	except Exception:
		x = y = None
	log.info(f"Agent mouse up: position=({x}, {y})")
	mouseHandler.executeMouseEvent(winUser.MOUSEEVENTF_LEFTUP, 0, 0)
	_leftMouseHeld = False


def _normalizeKeyCombination(keys: str) -> str:
	replacements = {
		"ctrl": "control",
		"cmd": "windows",
		"command": "windows",
		"meta": "windows",
		"win": "windows",
		"esc": "escape",
		"return": "enter",
		"del": "delete",
		"spacebar": "space",
		"pgup": "pageUp",
		"pgdn": "pageDown",
		"left": "leftArrow",
		"right": "rightArrow",
		"up": "upArrow",
		"down": "downArrow",
	}
	parts = [part.strip() for part in re.split(r"\+", keys.replace(" ", "")) if part.strip()]
	normalizedParts = []
	for part in parts:
		lowered = part.lower()
		normalizedParts.append(replacements.get(lowered, part))
	return "+".join(normalizedParts)


def _scroll(screenshot: Screenshot, args: dict[str, Any], checkCancelled: CheckCancelled) -> None:
	if "x" in args and "y" in args:
		targetX, targetY = _moveTo(screenshot, args, checkCancelled)
	else:
		targetX = screenshot.screenLeft + screenshot.screenWidth // 2
		targetY = screenshot.screenTop + screenshot.screenHeight // 2
		winUser.setCursorPos(
			targetX,
			targetY,
		)
	direction = str(args.get("direction") or "down").lower()
	magnitude = int(args.get("magnitude") or DEFAULT_SCROLL_DELTA)
	ticks = max(1, round(max(0, magnitude) / 1000 * (DEFAULT_SCROLL_DELTA / winUser.WHEEL_DELTA)))
	delta = ticks * winUser.WHEEL_DELTA
	if direction in ("down", "right"):
		delta = -delta
	isVertical = direction not in ("left", "right")
	log.info(
		f"Agent mouse scroll: target=({targetX}, {targetY}), direction={direction}, "
		f"magnitude={magnitude}, ticks={ticks}, totalDelta={delta}, isVertical={isVertical}",
	)
	for stepDelta in _iterScrollDeltas(delta):
		checkCancelled()
		log.debug(f"Agent mouse scroll step: delta={stepDelta}, isVertical={isVertical}")
		mouseHandler.scrollMouseWheel(stepDelta, isVertical=isVertical)
		_sleep(SCROLL_STEP_DELAY_SECONDS, checkCancelled)


def _iterScrollDeltas(delta: int) -> Iterator[int]:
	sign = 1 if delta >= 0 else -1
	remaining = abs(delta)
	while remaining:
		stepDelta = min(winUser.WHEEL_DELTA, remaining)
		remaining -= stepDelta
		yield stepDelta * sign


def _dragAndDrop(screenshot: Screenshot, args: dict[str, Any], checkCancelled: CheckCancelled) -> None:
	startX, startY = _pointFromArgs(screenshot, args)
	endX, endY = _pointFromArgs(screenshot, args, "destination_x", "destination_y")
	_dragFromScreenPoints(
		startX,
		startY,
		endX,
		endY,
		f"startNorm=({args.get('x')}, {args.get('y')}), "
		f"endNorm=({args.get('destination_x')}, {args.get('destination_y')})",
		checkCancelled,
	)


def _dragBy(screenshot: Screenshot, args: dict[str, Any], checkCancelled: CheckCancelled) -> None:
	if "delta_x" not in args and "delta_y" not in args:
		raise ActionExecutionError(_("Agent action is missing screen coordinates."))
	startX, startY = _pointFromArgs(screenshot, args)
	try:
		deltaX = int(float(args.get("delta_x") or 0))
		deltaY = int(float(args.get("delta_y") or 0))
	except (TypeError, ValueError) as e:
		raise ActionExecutionError(_("Agent action is missing screen coordinates.")) from e
	endX = startX + deltaX
	endY = startY + deltaY
	endX, endY = _clampScreenPoint(screenshot, endX, endY)
	_dragFromScreenPoints(
		startX,
		startY,
		endX,
		endY,
		f"startNorm=({args.get('x')}, {args.get('y')}), deltaPixels=({deltaX}, {deltaY})",
		checkCancelled,
	)


def _clampScreenPoint(screenshot: Screenshot, x: int, y: int) -> tuple[int, int]:
	return (
		min(max(x, screenshot.screenLeft), screenshot.screenLeft + max(screenshot.screenWidth - 1, 0)),
		min(max(y, screenshot.screenTop), screenshot.screenTop + max(screenshot.screenHeight - 1, 0)),
	)


def _dragFromScreenPoints(
	startX: int,
	startY: int,
	endX: int,
	endY: int,
	details: str,
	checkCancelled: CheckCancelled,
) -> None:
	distance = round(math.hypot(endX - startX, endY - startY), 1)
	steps = _dragStepCount(startX, startY, endX, endY)
	startTime = time.time()
	released = False
	log.info(
		f"Agent mouse drag: {details}, "
		f"startScreen=({startX}, {startY}), endScreen=({endX}, {endY}), "
		f"distance={distance}, steps={steps}",
	)
	checkCancelled()
	winUser.setCursorPos(startX, startY)
	_sleep(DRAG_START_DELAY_SECONDS, checkCancelled)
	_mouseDown()
	try:
		_sleep(DRAG_HOLD_DELAY_SECONDS, checkCancelled)
		_dragMouseTo(startX, startY, endX, endY, checkCancelled)
		_sleep(DRAG_RELEASE_DELAY_SECONDS, checkCancelled)
	finally:
		try:
			_mouseUp()
			released = True
		finally:
			log.info(
				f"Agent mouse drag completed: duration={time.time() - startTime:.3f}s, released={released}",
			)


def _dragMouseTo(
	startX: int,
	startY: int,
	endX: int,
	endY: int,
	checkCancelled: CheckCancelled,
) -> None:
	currentX = startX
	currentY = startY
	points = list(_iterDragPoints(startX, startY, endX, endY))
	for stepIndex, (targetX, targetY) in enumerate(points, start=1):
		checkCancelled()
		deltaX = targetX - currentX
		deltaY = targetY - currentY
		if deltaX or deltaY:
			log.debug(
				f"Agent mouse drag move: step={stepIndex}/{len(points)}, "
				f"target=({targetX}, {targetY}), delta=({deltaX}, {deltaY})",
			)
			winUser.setCursorPos(targetX, targetY)
			currentX = targetX
			currentY = targetY
		_sleep(DRAG_STEP_DELAY_SECONDS, checkCancelled)
	try:
		actualX, actualY = winUser.getCursorPos()
	except Exception:
		actualX = actualY = None
	log.info(f"Agent mouse drag reached: intended=({endX}, {endY}), actual=({actualX}, {actualY})")


def _iterDragPoints(startX: int, startY: int, endX: int, endY: int) -> Iterator[tuple[int, int]]:
	steps = _dragStepCount(startX, startY, endX, endY)
	deltaX = endX - startX
	deltaY = endY - startY
	distance = math.hypot(deltaX, deltaY)
	curveAmplitude = _dragCurveAmplitude(distance)
	curveSign = -1 if (startX + startY + endX + endY) % 2 else 1
	perpendicularX = -deltaY / distance if distance else 0
	perpendicularY = deltaX / distance if distance else 0
	for stepIndex in range(1, steps + 1):
		progress = stepIndex / steps
		easedProgress = progress * progress * (3 - 2 * progress)
		curveOffset = math.sin(math.pi * progress) * curveAmplitude * curveSign
		yield (
			round(startX + deltaX * easedProgress + perpendicularX * curveOffset),
			round(startY + deltaY * easedProgress + perpendicularY * curveOffset),
		)


def _dragStepCount(startX: int, startY: int, endX: int, endY: int) -> int:
	distance = max(abs(endX - startX), abs(endY - startY))
	if distance <= 0:
		return 1
	return min(DRAG_MAX_STEPS, max(DRAG_MIN_STEPS, distance // DRAG_PIXELS_PER_STEP))


def _dragCurveAmplitude(distance: float) -> int:
	if distance < 80:
		return 0
	return min(DRAG_CURVE_MAX_PIXELS, max(1, round(distance / 250)))


def _navigate(args: dict[str, Any], checkCancelled: CheckCancelled) -> None:
	url = str(args.get("url") or args.get("text") or "").strip()
	if not url:
		raise ActionExecutionError(_("Agent action is missing a URL."))
	_sendKeyCombination("control+l")
	_pasteText(url, checkCancelled)
	_sendKeyCombination("enter")
