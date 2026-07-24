# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Agent session loop for foreground-window computer use."""

from __future__ import annotations

from collections.abc import Callable
import config
from threading import Event, Thread
import time

import addonHandler
from logHandler import log
import ui
import wx

from ..exceptions import ApiError, AuthenticationError, CancellationError, NetworkError
from .actions import (
	ActionExecutionError,
	AgentAction,
	NORMALIZED_SCALE,
	captureScreen,
	executeAction,
	getActiveAgentWindow,
	getForegroundWindowInfo,
	releaseHeldInputs,
)
from .client import createAgentClient
from .decision import AgentDecision

addonHandler.initTranslation()

MAX_STEPS = 25
UNCHANGED_SCREEN_LIMIT = 3
ACTION_DELAY_SECONDS = 2.5
ACTION_FAILURE_LIMIT = 3
WAIT_FOR_CHANGE_POLL_SECONDS = 0.25
REQUEST_THREAD_JOIN_TIMEOUT_SECONDS = 0.2
AskUserCallback = Callable[[str, Event], str | None]


class AgentSession:
	def __init__(
		self,
		goal: str,
		onDone: Callable[["AgentSession"], None] | None = None,
		askUser: AskUserCallback | None = None,
	) -> None:
		self.goal = goal
		self._onDone = onDone
		self._askUser = askUser
		self._cancelEvent = Event()
		self._thread: Thread | None = None
		self._requestThread: Thread | None = None

	def start(self) -> None:
		self._thread = Thread(target=self._run, daemon=True)
		self._thread.start()

	def cancel(self) -> bool:
		wasCancelling = self._cancelEvent.is_set()
		self._cancelEvent.set()
		releaseHeldInputs()
		return not wasCancelling

	@property
	def isRunning(self) -> bool:
		return self._thread is not None and self._thread.is_alive()

	def _run(self) -> None:
		try:
			log.info("Vis Aware agent session starting.")
			self._beep(600, 80)
			self._message(_("Agent started."))
			client = createAgentClient()
			boundWindow = getForegroundWindowInfo()
			log.info(
				f"Vis Aware agent bound to hwnd={boundWindow.hwnd}, app={boundWindow.appName!r}, "
				f"title={boundWindow.title!r}, goalLength={len(self.goal)}",
			)
			history: list[str] = []
			lastDigest = ""
			lastWindowKey = None
			previousActionName = ""
			unchangedCount = 0
			actionFailureCount = 0
			for stepIndex in range(1, MAX_STEPS + 1):
				self._checkCancelled()
				activeWindow = getActiveAgentWindow()
				log.info(f"Agent step {stepIndex}/{MAX_STEPS}: capturing screen.")
				screenshot = captureScreen(activeWindow, quality=client.imageQuality)
				screenChanged = None
				if lastDigest:
					screenChanged = screenshot.digest != lastDigest
					windowKey = _windowKey(screenshot.window)
					windowChanged = windowKey != lastWindowKey
					log.info(
						f"Agent observation after action={previousActionName or 'unknown'}: "
						f"screenChanged={screenChanged}, "
						f"windowChanged={windowChanged}, "
						f"digest={screenshot.digest[:12]}, "
						f"foreground={screenshot.window.appName!r}/{screenshot.window.title!r}",
					)
					history.append(
						_formatObservationHistoryEntry(
							previousActionName,
							screenChanged,
							windowChanged,
							unchangedCount,
						),
					)
				if screenshot.digest == lastDigest:
					unchangedCount += 1
				else:
					unchangedCount = 0
				lastDigest = screenshot.digest
				lastWindowKey = _windowKey(screenshot.window)
				if _verboseDebugLogging():
					log.debug(
						f"Agent screenshot: size={screenshot.width}x{screenshot.height}, "
						f"screenBounds=({screenshot.screenLeft}, {screenshot.screenTop}, "
						f"{screenshot.screenWidth}, {screenshot.screenHeight}), "
						f"digest={screenshot.digest}, unchangedCount={unchangedCount}",
					)
				if unchangedCount >= UNCHANGED_SCREEN_LIMIT:
					log.warning("Agent stopped because the screen did not change.")
					self._beep(260, 180)
					self._message(_("Agent stopped because the screen did not change."))
					return
				self._beep(520, 50)
				self._message(_("Analyzing screen."))
				log.info(f"Agent step {stepIndex}/{MAX_STEPS}: requesting next action.")
				decision = self._requestNextAction(client, screenshot, history)
				self._checkCancelled()
				actions = _decisionActions(decision)
				log.info(
					f"Agent step {stepIndex}/{MAX_STEPS}: status={decision.status}, "
					f"actions={','.join(action.name for action in actions) or None}, "
					f"message={decision.message!r}",
				)
				if decision.status == "finish":
					self._beep(900, 150)
					self._message(decision.message or _("Agent finished."))
					return
				if decision.status == "ask_user":
					if self._handleUserQuestion(
						decision.message or _("Agent needs more information."),
						history,
					):
						boundWindow = self._getReboundWindow(boundWindow)
						previousActionName = "ask_user"
						continue
					return
				if not actions:
					log.warning("Agent returned action status without an action.")
					self._beep(260, 180)
					self._message(_("Agent did not return an action."))
					return
				self._beep(760, 50)
				executedActions = []
				for action in actions:
					self._message(_formatActionProgress(action, action.message or decision.message))
					try:
						self._executeAction(action, screenshot, client.imageQuality, stepIndex)
					except ActionExecutionError as e:
						actionFailureCount += 1
						log.warning("Agent action failed; asking model to recover.", exc_info=True)
						history.append(_formatFailureHistoryEntry(action, e))
						self._message(str(e))
						if actionFailureCount >= ACTION_FAILURE_LIMIT:
							raise
						lastDigest = ""
						break
					executedActions.append(action)
					history.append(_formatHistoryEntry(stepIndex, action, screenshot))
					previousActionName = action.name
				else:
					actionFailureCount = 0
				if len(executedActions) != len(actions):
					continue
				if decision.finishAfterAction:
					history.append(
						"The previous action was marked final. Verify completion before finishing.",
					)
				if executedActions[-1].name not in ("wait", "wait_for_change"):
					self._sleepAfterAction()
				self._message(_("Checking result."))
				boundWindow = self._getReboundWindow(boundWindow)
			self._beep(260, 180)
			log.warning("Agent stopped after reaching the maximum number of steps.")
			self._message(_("Agent stopped after reaching the maximum number of steps."))
		except CancellationError:
			log.info("Vis Aware agent session cancelled.")
			self._beep(300, 120)
			self._message(_("Agent stopped."))
		except (AuthenticationError, NetworkError, ApiError, ActionExecutionError) as e:
			log.warning("Vis Aware agent session stopped with an expected error.", exc_info=True)
			self._beep(220, 220)
			self._message(str(e))
		except Exception:
			log.error("Unexpected agent error", exc_info=True)
			self._beep(180, 260)
			self._message(_("Agent failed."))
		finally:
			releaseHeldInputs()
			self._joinPendingRequestThread()
			log.info("Vis Aware agent session ended.")
			if self._onDone:
				wx.CallAfter(self._onDone, self)

	def _checkCancelled(self) -> None:
		if self._cancelEvent.is_set():
			raise CancellationError("Agent session was cancelled.", self._cancelEvent)

	def _requestNextAction(self, client, screenshot, history: list[str]) -> AgentDecision:
		doneEvent = Event()
		result: dict[str, AgentDecision | Exception] = {}

		def request() -> None:
			try:
				result["decision"] = client.nextAction(self.goal, screenshot, history)
			except Exception as e:
				result["error"] = e
			finally:
				doneEvent.set()

		requestThread = Thread(target=request, name="VisAwareAgentRequest", daemon=True)
		self._requestThread = requestThread
		requestThread.start()
		try:
			while not doneEvent.wait(0.05):
				self._checkCancelled()
			error = result.get("error")
			if isinstance(error, Exception):
				raise error
			decision = result.get("decision")
			if isinstance(decision, AgentDecision):
				return decision
			raise ApiError(_("Agent did not return an action."))
		finally:
			if doneEvent.is_set() and self._requestThread is requestThread:
				self._requestThread = None

	def _joinPendingRequestThread(self) -> None:
		requestThread = self._requestThread
		if not requestThread:
			return
		requestThread.join(REQUEST_THREAD_JOIN_TIMEOUT_SECONDS)
		if requestThread.is_alive():
			log.warning("Agent request thread is still running after session ended.")
		if self._requestThread is requestThread:
			self._requestThread = None

	def _handleUserQuestion(self, question: str, history: list[str]) -> bool:
		self._beep(650, 180)
		self._message(question)
		if not self._askUser:
			return False
		answer = self._askUser(question, self._cancelEvent)
		self._checkCancelled()
		if not answer:
			self._message(_("Agent stopped."))
			return False
		history.append(f"Agent asked user: {question}")
		history.append(f"User answered: {answer}")
		return True

	def _executeAction(self, action: AgentAction, screenshot, imageQuality: int, stepIndex: int) -> None:
		if action.name == "wait":
			self._sleepForSeconds(float(action.arguments.get("seconds") or 5))
			return
		if action.name == "wait_for_change":
			changed = self._waitForScreenChange(
				screenshot,
				float(action.arguments.get("seconds") or 5),
				imageQuality,
				stepIndex,
			)
			action.arguments["screen_changed"] = changed
			return
		executeAction(action, screenshot, checkCancelled=self._checkCancelled)

	def _waitForScreenChange(self, screenshot, seconds: float, imageQuality: int, stepIndex: int) -> bool:
		deadline = time.time() + max(0.0, seconds)
		checkCount = 0
		while True:
			remaining = deadline - time.time()
			if remaining <= 0:
				log.info(f"Agent wait_for_change ended: changed=False, checks={checkCount}")
				return False
			self._checkCancelled()
			time.sleep(min(WAIT_FOR_CHANGE_POLL_SECONDS, remaining))
			activeWindow = getActiveAgentWindow()
			newScreenshot = captureScreen(activeWindow, quality=imageQuality)
			checkCount += 1
			changed = newScreenshot.digest != screenshot.digest
			log.info(
				f"Agent wait_for_change check: changed={changed}, check={checkCount}, "
				f"digest={newScreenshot.digest[:12]}, "
				f"foreground={newScreenshot.window.appName!r}/{newScreenshot.window.title!r}",
			)
			if changed:
				return True

	def _sleepAfterAction(self) -> None:
		self._sleepForSeconds(ACTION_DELAY_SECONDS)

	def _sleepForSeconds(self, seconds: float) -> None:
		endTime = time.time() + max(0.0, seconds)
		while True:
			remaining = endTime - time.time()
			if remaining <= 0:
				return
			self._checkCancelled()
			time.sleep(min(0.05, remaining))

	def _getReboundWindow(self, previousWindow):
		newWindow = getForegroundWindowInfo()
		if (
			newWindow.hwnd != previousWindow.hwnd
			or newWindow.processID != previousWindow.processID
			or newWindow.appName != previousWindow.appName
		):
			log.info(
				f"Agent rebound foreground window after action: "
				f"{previousWindow.appName!r}/{previousWindow.title!r} -> "
				f"{newWindow.appName!r}/{newWindow.title!r}",
			)
		return newWindow

	def _message(self, text: str) -> None:
		if text:
			wx.CallAfter(ui.message, text)

	def _beep(self, frequency: int, duration: int) -> None:
		try:
			from tones import beep

			wx.CallAfter(beep, frequency, duration)
		except Exception:
			log.debugWarning("Agent beep failed.", exc_info=True)


def _verboseDebugLogging() -> bool:
	try:
		return bool(config.conf["visAwareGeneral"]["verboseDebugLogging"])
	except Exception:
		return False


def _decisionActions(decision: AgentDecision) -> list[AgentAction]:
	if decision.actions:
		return decision.actions
	if decision.action:
		return [decision.action]
	return []


def _formatHistoryEntry(stepIndex: int, action: AgentAction, screenshot) -> str:
	args = action.arguments
	if action.name == "type_text_at":
		summary = (
			f"type_text_at: textLength={len(str(args.get('text') or ''))}, "
			f"pressEnter={bool(args.get('press_enter', False))}"
		)
	elif action.name in ("click_at", "double_click_at", "right_click_at"):
		summary = (
			f"{action.name}: x={args.get('x')}, y={args.get('y')}, "
			f"{_formatActionScreenDetails(action, screenshot)}, message={action.message}"
		)
	elif action.name in ("drag_and_drop", "drag_by"):
		summary = (
			f"{action.name}: x={args.get('x')}, y={args.get('y')}, "
			f"destination=({args.get('destination_x')}, {args.get('destination_y')}), "
			f"delta=({args.get('delta_x')}, {args.get('delta_y')}), "
			f"{_formatDragScreenDistance(action, screenshot)}"
		)
	elif action.name in ("hover_at", "mouse_down", "mouse_up"):
		summary = (
			f"{action.name}: x={args.get('x')}, y={args.get('y')}, "
			f"{_formatActionScreenDetails(action, screenshot)}, message={action.message}"
		)
	elif action.name in ("key_combination", "press_key", "hotkey"):
		summary = f"{action.name}: keys={args.get('keys') or args.get('key') or ''}"
	elif action.name == "wait_for_change":
		summary = (
			f"wait_for_change: seconds={args.get('seconds')}, screenChanged={args.get('screen_changed')}"
		)
	else:
		summary = f"{action.name}: {action.message}"
	return f"{stepIndex}. Action performed. Checking result. Previous action: {summary}"


def _formatActionScreenDetails(action: AgentAction, screenshot) -> str:
	args = action.arguments
	try:
		point = _normalizedPointToScreen(screenshot, args.get("x"), args.get("y"))
	except (TypeError, ValueError):
		return "screenPoint=unknown"
	return f"screenPoint=({point[0]}, {point[1]})"


def _formatDragScreenDistance(action: AgentAction, screenshot) -> str:
	args = action.arguments
	try:
		startX, startY = _normalizedPointToScreen(
			screenshot,
			args.get("x"),
			args.get("y"),
		)
		if action.name == "drag_by":
			deltaX = round(float(args.get("delta_x") or 0))
			deltaY = round(float(args.get("delta_y") or 0))
			endX = startX + deltaX
			endY = startY + deltaY
		else:
			endX, endY = _normalizedPointToScreen(
				screenshot,
				args.get("destination_x"),
				args.get("destination_y"),
			)
	except (TypeError, ValueError):
		return "screenDistance=unknown"
	return (
		f"screenStart=({startX}, {startY}), screenEnd=({endX}, {endY}), "
		f"screenDelta=({endX - startX}, {endY - startY})"
	)


def _normalizedPointToScreen(screenshot, xValue, yValue) -> tuple[int, int]:
	x = min(max(int(float(xValue)), 0), NORMALIZED_SCALE)
	y = min(max(int(float(yValue)), 0), NORMALIZED_SCALE)
	return (
		screenshot.screenLeft
		+ min(
			int(x * screenshot.screenWidth / NORMALIZED_SCALE),
			max(screenshot.screenWidth - 1, 0),
		),
		screenshot.screenTop
		+ min(
			int(y * screenshot.screenHeight / NORMALIZED_SCALE),
			max(screenshot.screenHeight - 1, 0),
		),
	)


def _windowKey(window) -> tuple[str, str]:
	return str(window.appName or ""), str(window.title or "")


def _formatObservationHistoryEntry(
	actionName: str,
	screenChanged: bool,
	windowChanged: bool,
	unchangedCount: int,
) -> str:
	windowText = "foreground window changed" if windowChanged else "foreground window stayed the same"
	if actionName in ("drag_and_drop", "drag_by"):
		if screenChanged:
			return (
				f"Observation after drag: full screenshot changed and {windowText}, "
				"but the task has not visibly finished. "
				"The change may be unrelated page motion, animation, or a refreshed state. "
				"Re-estimate the draggable center, target position, and relative offset from the current "
				"screenshot instead of repeating the same start point and distance."
			)
		return (
			f"Observation after drag: no visible screen change and {windowText}. "
			"The handle may not have been grabbed; "
			"use the exact visible handle center or a different drag strategy."
		)
	if (
		actionName
		in (
			"click_at",
			"double_click_at",
			"triple_click_at",
			"right_click_at",
			"middle_click_at",
		)
		and screenChanged
		and not windowChanged
	):
		return (
			f"Observation after {actionName}: screenshot changed but foreground window stayed the same. "
			"This may only be selection, focus, hover, or animation. "
			"If the intended result is not visible, do not repeat the same coordinates; "
			"retarget the current visible control center or use a different action."
		)
	if screenChanged:
		return (
			f"Observation after {actionName or 'previous action'}: screen changed and {windowText}. "
			"Do not infer completion from screen change alone; verify the intended visible state."
		)
	return (
		f"Observation after {actionName or 'previous action'}: no visible screen change "
		f"and {windowText} (repeat {unchangedCount + 1}). "
		"Try a different target, drag distance, or strategy."
	)


def _formatFailureHistoryEntry(action: AgentAction, error: Exception) -> str:
	return (
		f"Previous action failed: action={action.name}, "
		f"args={action.arguments!r}, error={error}. "
		"Return a different valid action. If the action needs coordinates, include x and y as integers 0-1000."
	)


def _formatActionProgress(action: AgentAction, message: str) -> str:
	if message:
		# Translators: Reported before the Agent performs an action. {} is the action description.
		return _("Executing: {}").format(message)
	if action.name == "type_text_at":
		# Translators: Reported before the Agent types text.
		return _("Typing text.")
	if "click" in action.name:
		# Translators: Reported before the Agent clicks the mouse.
		return _("Clicking.")
	if action.name in ("key_combination", "press_key", "hotkey", "key_down", "key_up"):
		# Translators: Reported before the Agent presses keys.
		return _("Pressing keys.")
	if action.name in ("scroll_at", "scroll_document", "scroll"):
		# Translators: Reported before the Agent scrolls.
		return _("Scrolling.")
	if action.name in ("wait", "wait_for_change"):
		# Translators: Reported when the Agent waits.
		return _("Waiting.")
	# Translators: Reported before the Agent performs an unspecified action.
	return _("Executing action.")
