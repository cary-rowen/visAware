# Copyright (C) 2026 hwf1324 <1398969445@qq.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Provides a full-screen dialog for selecting a screen region with the mouse."""

import addonHandler
import api
import ui
import wx
from contentRecog import RecogImageInfo
from mouseHandler import getTotalWidthAndHeightAndMinimumPosition
from PIL import Image, ImageGrab

addonHandler.initTranslation()


def pilToBitmap(pilImage: Image.Image, keepAlpha: bool = False) -> wx.Bitmap:
	"""Convert a PIL image to a :class:`wx.Bitmap`.

	:param pilImage: The source image, typically an RGB screenshot.
	:param keepAlpha: When True, preserve transparency via an RGBA buffer.
	:returns: A bitmap suitable for :class:`wx.DC` drawing operations.
	"""
	if keepAlpha:
		rgbaImage = pilImage.convert("RGBA")
		return wx.Bitmap.FromBufferRGBA(rgbaImage.width, rgbaImage.height, rgbaImage.tobytes())
	if pilImage.mode != "RGB":
		pilImage = pilImage.convert("RGB")
	return wx.Bitmap.FromBuffer(pilImage.width, pilImage.height, pilImage.tobytes())


def captureScreenshot() -> Image.Image:
	"""Capture the whole virtual screen as an RGB image.

	:raises RuntimeError: When the screen cannot be captured.
	"""
	try:
		image = ImageGrab.grab(all_screens=True)
		# ImageGrab returns RGB on Windows; convert only if needed so the
		# common path avoids a full-screen pixel conversion.
		return image if image.mode == "RGB" else image.convert("RGB")
	except Exception as exc:
		raise RuntimeError("Failed to capture the screen for region selection.") from exc


def normalizedRect(first: wx.Point, second: wx.Point) -> wx.Rect:
	"""Return the rectangle spanning two points, normalized for any drag direction.

	:param first: One corner of the rectangle.
	:param second: The opposite corner of the rectangle.
	:returns: A rectangle with non-negative width and height covering both points.
	"""
	return wx.Rect(
		min(first.x, second.x),
		min(first.y, second.y),
		abs(first.x - second.x),
		abs(first.y - second.y),
	)


class ScreenCaptureDialog(wx.Dialog):
	"""A borderless full-screen dialog for selecting a region to capture.

	The dialog overlays the desktop with a dimmed screenshot. Drag the mouse
	to select a rectangular area, release the button to confirm, press Escape
	to cancel, or right-click to clear the current selection.

	A magnifier lens follows the cursor so low-vision users can inspect
	pixel-level detail: press M to toggle it, use the mouse wheel or +/- to
	change its zoom, press C to hear the colour under the cursor and P to hear
	its position in screen coordinates, or hold Control with either key to copy
	that value to the clipboard instead.
	"""

	# Minimum selection size, in pixels, below which a drag is treated as a stray click.
	_MIN_SELECTION_WIDTH = 5
	_MIN_SELECTION_HEIGHT = 5
	# Opacity of the dim overlay applied over the screenshot.
	_DIM_ALPHA = 100
	# Style of the selection outline.
	_SELECTION_COLOUR = wx.Colour(255, 0, 0)
	_SELECTION_BORDER_WIDTH = 2
	# Magnifier lens: available zoom levels and its base geometry in logical
	# pixels. The geometry is scaled by the DPI of the display under the cursor so
	# the lens keeps a consistent physical size when monitors use different DPI.
	_ZOOM_STEPS = (1.5, 2.0, 3.0, 4.0, 6.0, 10.0)
	_DEFAULT_ZOOM = 2.0
	_BASE_DPI = 96
	_LENS_SIZE = 150
	_LENS_OFFSET = 20
	_LENS_BORDER_COLOUR = wx.Colour(0, 174, 255)
	_LENS_BORDER_WIDTH = 2
	_CROSSHAIR_COLOUR = wx.Colour(255, 0, 0)
	# Info panel shown next to the lens. Colours are opaque because plain GDI
	# drawing cannot blend alpha values, and are kept low-contrast so the panel
	# does not glare against the dimmed screenshot.
	_PANEL_COLOUR = wx.Colour(45, 45, 48)
	_PANEL_TEXT_COLOUR = wx.Colour(215, 215, 215)
	_PANEL_PADDING_X = 6
	_PANEL_PADDING_Y = 4
	_PANEL_GAP = 4
	_PANEL_FONT_SIZE = 10

	def __init__(self, parent: wx.Window | None) -> None:
		# Capture the screen before creating the window so a capture failure
		# leaves no partially-initialized dialog behind.
		self._fullScreenshot = captureScreenshot()

		super().__init__(parent, style=wx.NO_BORDER | wx.STAY_ON_TOP)

		# Let wxPython know we handle all background painting ourselves.
		self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
		# Suppress the default background erase to avoid flicker while dragging.
		self.Bind(wx.EVT_ERASE_BACKGROUND, self.onEraseBackground)

		# Cover the bounding box of all connected displays.
		self.displays = [wx.Display(i).GetGeometry() for i in range(wx.Display.GetCount())]
		self._screenWidth, self._screenHeight, minPos = getTotalWidthAndHeightAndMinimumPosition(
			self.displays,
		)
		self._screenLeft = minPos.x
		self._screenTop = minPos.y
		self.SetSize(self._screenLeft, self._screenTop, self._screenWidth, self._screenHeight)

		# Build a dimmed copy of the screenshot for the overlay. Image.blend
		# operates on RGB and is faster than an RGBA alpha composite.
		self._dimScreenshot = Image.blend(
			self._fullScreenshot,
			Image.new("RGB", self._fullScreenshot.size, (0, 0, 0)),
			self._DIM_ALPHA / 255,
		)

		# Pre-render the screenshots as bitmaps for fast painting.
		self._screenshotBitmap = pilToBitmap(self._fullScreenshot)
		self._backgroundBitmap = pilToBitmap(self._dimScreenshot)

		self._anchorPos: wx.Point | None = None
		self._currentPos: wx.Point | None = None
		self._isDragging = False
		self._selectionActive = False
		self._resultRect: tuple[int, int, int, int] | None = None

		# Magnifier lens state. Positions are kept in client coordinates, which
		# also index the screenshot because the dialog covers the virtual screen.
		self._lensEnabled = True
		self._lensZoom = self._DEFAULT_ZOOM
		self._lensCursorPos = self.ScreenToClient(wx.GetMousePosition())
		self._lensScaleFactor = 1.0
		# Index of the display the cached scale factor belongs to; -1 forces the
		# first lookup.
		self._lensScaleDisplayIndex = -1
		self._lensBitmap: wx.Bitmap | None = None
		self._lensSourceRect: tuple[int, int, int, int] | None = None
		self._panelFont = wx.Font(
			self._PANEL_FONT_SIZE,
			wx.FONTFAMILY_DEFAULT,
			wx.FONTSTYLE_NORMAL,
			wx.FONTWEIGHT_BOLD,
		)
		self._updateLensScaleFactor()

		self.Bind(wx.EVT_PAINT, self.onPaint)
		self.Bind(wx.EVT_LEFT_DOWN, self.onMouseLeftDown)
		self.Bind(wx.EVT_MOTION, self.onMouseMove)
		self.Bind(wx.EVT_LEFT_UP, self.onMouseLeftUp)
		self.Bind(wx.EVT_RIGHT_UP, self.onMouseRightUp)
		self.Bind(wx.EVT_KEY_UP, self.onKeyUp)
		self.Bind(wx.EVT_MOUSEWHEEL, self.onMouseWheel)

		# Use a crosshair cursor to indicate selection mode.
		self.SetCursor(wx.Cursor(wx.CURSOR_CROSS))

	def onEraseBackground(self, event: wx.EraseEvent) -> None:
		"""Suppress background erasing; the paint handler redraws the whole window."""

	def onPaint(self, event: wx.PaintEvent) -> None:
		"""Draw the dimmed desktop, the active selection and the magnifier lens."""
		dc = wx.AutoBufferedPaintDC(self)
		dc.DrawBitmap(self._backgroundBitmap, 0, 0)

		if self._selectionActive and self._anchorPos and self._currentPos:
			rect = self._getSelectionRect()
			# Restore the un-dimmed screenshot inside the selection bounds.
			dc.SetClippingRegion(rect)
			dc.DrawBitmap(self._screenshotBitmap, 0, 0)
			dc.DestroyClippingRegion()
			# Outline the selection in red with plain DC drawing.
			dc.SetPen(wx.Pen(self._SELECTION_COLOUR, self._SELECTION_BORDER_WIDTH))
			dc.SetBrush(wx.TRANSPARENT_BRUSH)
			dc.DrawRectangle(rect.x, rect.y, rect.width, rect.height)

		if self._lensEnabled and self._lensCursorPos:
			self._drawLens(dc)

	def onMouseLeftDown(self, event: wx.MouseEvent) -> None:
		"""Record the mouse-down position and start a drag selection."""
		self._anchorPos = event.GetPosition()
		self._currentPos = None
		self._isDragging = True
		self._selectionActive = False
		self.CaptureMouse()

	def onMouseMove(self, event: wx.MouseEvent) -> None:
		"""Move the magnifier lens and update the selection while the mouse moves.

		The selection only becomes visible once the drag exceeds the minimum
		size, which filters out accidental clicks.
		"""
		self._moveLens(event.GetPosition())
		if not (self._isDragging and event.Dragging()):
			return
		self._currentPos = event.GetPosition()
		if not self._selectionActive:
			rect = self._getSelectionRect()
			if rect.width <= self._MIN_SELECTION_WIDTH or rect.height <= self._MIN_SELECTION_HEIGHT:
				return
			self._selectionActive = True
		self.Refresh(eraseBackground=False)

	def onMouseLeftUp(self, event: wx.MouseEvent) -> None:
		"""Confirm the selection on mouse release, if it is large enough."""
		if not self._isDragging:
			return
		self._releaseCapture()
		self._isDragging = False

		if not self._selectionActive:
			self._resetSelection()
			return
		rect = self._getSelectionRect()
		if rect.width <= self._MIN_SELECTION_WIDTH or rect.height <= self._MIN_SELECTION_HEIGHT:
			self._resetSelection()
			return
		# Positions are client coordinates, which align with the screenshot's
		# origin because the dialog covers the whole virtual screen.
		self._resultRect = (rect.x, rect.y, rect.width, rect.height)
		self.EndModal(wx.ID_OK)

	def onMouseRightUp(self, event: wx.MouseEvent) -> None:
		"""Clear the current selection without closing the dialog."""
		if not self._isDragging:
			return
		self._releaseCapture()
		self._resetSelection()

	def onKeyUp(self, event: wx.KeyEvent) -> None:
		"""Handle the magnifier shortcuts, Escape to cancel an in-progress selection."""
		keyCode = event.GetKeyCode()
		# C and P report the value under the cursor; holding Control copies it instead.
		controlDown = event.ControlDown()
		if keyCode == ord("M"):
			self._lensEnabled = not self._lensEnabled
			# Force the cached lens bitmap to be rebuilt when it is shown again.
			self._lensSourceRect = None
			self.Refresh(eraseBackground=False)
			return
		if keyCode == ord("C"):
			if controlDown:
				self._copyCursorColour()
			else:
				self._reportCursorColour()
			return
		if keyCode == ord("P"):
			if controlDown:
				self._copyCursorPosition()
			else:
				self._reportCursorPosition()
			return
		if self._lensEnabled and keyCode in (ord("+"), ord("="), wx.WXK_NUMPAD_ADD):
			self._changeZoom(1)
			return
		if self._lensEnabled and keyCode in (ord("-"), wx.WXK_NUMPAD_SUBTRACT):
			self._changeZoom(-1)
			return
		if keyCode != wx.WXK_ESCAPE:
			event.Skip()
			return

		if self._isDragging:
			self._releaseCapture()
			self._resetSelection()
		else:
			self.EndModal(wx.ID_CANCEL)

	def _releaseCapture(self) -> None:
		"""Release the mouse capture if this window currently owns it."""
		if self.HasCapture():
			self.ReleaseMouse()

	def _resetSelection(self) -> None:
		"""Clear the in-progress selection and repaint the overlay."""
		self._anchorPos = None
		self._currentPos = None
		self._isDragging = False
		self._selectionActive = False
		self.Refresh(eraseBackground=False)

	def _getSelectionRect(self) -> wx.Rect:
		"""Return the normalized rectangle between the anchor and the cursor."""
		if not self._anchorPos or not self._currentPos:
			return wx.Rect(0, 0, 0, 0)
		return normalizedRect(self._anchorPos, self._currentPos)

	def _moveLens(self, position: wx.Point) -> None:
		"""Track the cursor, moving the magnifier lens when it is shown."""
		if self._lensCursorPos == position and self._lensBitmap is not None:
			return
		self._lensCursorPos = position
		# The cursor may have moved to a monitor with a different DPI.
		self._updateLensScaleFactor()
		if self._lensEnabled:
			self.Refresh(eraseBackground=False)

	def _changeZoom(self, step: int) -> None:
		"""Move the magnifier one step up or down the available zoom levels."""
		try:
			index = self._ZOOM_STEPS.index(self._lensZoom)
		except ValueError:
			index = 0
		index = max(0, min(index + step, len(self._ZOOM_STEPS) - 1))
		self._lensZoom = self._ZOOM_STEPS[index]
		# Force the cached lens bitmap to be regenerated at the new zoom.
		self._lensSourceRect = None
		if self._lensCursorPos:
			self.Refresh(eraseBackground=False)

	def onMouseWheel(self, event: wx.MouseEvent) -> None:
		"""Zoom the magnifier lens with the mouse wheel."""
		if not self._lensEnabled or event.GetWheelAxis() != wx.MOUSE_WHEEL_VERTICAL:
			event.Skip()
			return
		self._changeZoom(1 if event.GetWheelRotation() >= 0 else -1)

	def _getCursorDisplayIndex(self) -> int | None:
		"""Return the index of the display holding the cursor, if it can be found."""
		screenPosition = self._getCursorScreenPosition()
		if not screenPosition:
			return None
		for index, display in enumerate(self.displays):
			if display.Contains(screenPosition):
				return index
		return None

	def _getCursorScaleFactor(self, displayIndex: int | None) -> float:
		"""Return the DPI scale factor of the given display, or 1.0 if unknown.

		Falls back to 1.0 when the display does not report its DPI, so the lens
		still works where that information is unavailable.
		"""
		if displayIndex is None:
			return 1.0
		try:
			ppi = wx.Display(displayIndex).GetPPI()
		except Exception:
			# Display DPI is unavailable; keep the base geometry.
			return 1.0
		return max(1.0, ppi.GetWidth() / self._BASE_DPI)

	def _updateLensScaleFactor(self) -> None:
		"""Refresh the cached DPI scale factor when the cursor changes display.

		Querying the display DPI on every mouse move is wasteful, so the result is
		only recomputed when the cursor moves to a different display.
		"""
		displayIndex = self._getCursorDisplayIndex()
		if displayIndex == self._lensScaleDisplayIndex:
			return
		self._lensScaleDisplayIndex = displayIndex
		self._lensScaleFactor = self._getCursorScaleFactor(displayIndex)

	def _getLensSize(self) -> int:
		"""Return the lens edge length, scaled for the display under the cursor."""
		return max(1, round(self._LENS_SIZE * self._lensScaleFactor))

	def _getLensOffset(self) -> int:
		"""Return the lens distance from the cursor, scaled for its display."""
		return max(1, round(self._LENS_OFFSET * self._lensScaleFactor))

	def _getCursorDisplayBounds(self) -> wx.Rect | None:
		"""Return the bounds of the display holding the cursor, in client coordinates.

		Returns ``None`` when the display geometry cannot be trusted, which happens
		when Windows reports display sizes in a different DPI context, as on
		multi-monitor setups with mixed DPI.
		"""
		if not self._lensCursorPos:
			return None
		if (
			self._screenWidth != self._fullScreenshot.width
			or self._screenHeight != self._fullScreenshot.height
		):
			return None
		for display in self.displays:
			# Display bounds are in screen coordinates, so shift them into the
			# dialog's client coordinate space before comparing.
			bounds = wx.Rect(
				display.x - self._screenLeft,
				display.y - self._screenTop,
				display.width,
				display.height,
			)
			if bounds.Contains(self._lensCursorPos):
				return bounds
		return None

	def _getLensBounds(self) -> wx.Rect:
		"""Return the region the lens must stay inside, in client coordinates.

		The bounds of the display under the cursor are preferred so the lens does
		not spill onto a neighbouring monitor. When those bounds cannot be
		trusted, the screenshot bounds are used instead: they always line up with
		the dialog's client coordinates, whatever DPI context Windows reports.
		"""
		displayBounds = self._getCursorDisplayBounds()
		if displayBounds:
			return displayBounds
		return wx.Rect(0, 0, self._fullScreenshot.width, self._fullScreenshot.height)

	def _getLensRect(self) -> wx.Rect:
		"""Return the lens rectangle, kept fully inside the display under the cursor."""
		if not self._lensCursorPos:
			return wx.Rect(0, 0, 0, 0)
		size = self._getLensSize()
		offset = self._getLensOffset()
		bounds = self._getLensBounds()
		# Place the lens below and to the right of the cursor by default.
		left = self._lensCursorPos.x + offset
		top = self._lensCursorPos.y + offset
		# Flip to the other side of the cursor when there is no room.
		if left + size > bounds.right:
			left = self._lensCursorPos.x - offset - size
		if top + size > bounds.bottom:
			top = self._lensCursorPos.y - offset - size
		# Clamp so the lens stays fully on screen, even in a corner.
		left = max(bounds.left, min(left, bounds.right - size))
		top = max(bounds.top, min(top, bounds.bottom - size))
		return wx.Rect(left, top, size, size)

	def _getLensSourceRect(self) -> tuple[int, int, int, int]:
		"""Return the square screenshot region shown in the lens, centred on the cursor.

		The region may reach past the screenshot near an edge; the missing strips are
		filled in when the lens bitmap is built, so the cursor pixel always stays in
		the middle of the lens. The side length is odd so that the cursor pixel sits
		exactly on the centre of the region.
		"""
		if not self._lensCursorPos:
			return (0, 0, 0, 0)
		half = int(self._getLensSize() / (2 * self._lensZoom))
		side = max(1, 2 * half + 1)
		left = self._lensCursorPos.x - half
		top = self._lensCursorPos.y - half
		return (left, top, side, side)

	def _getLensBitmap(self) -> wx.Bitmap | None:
		"""Return the magnified lens bitmap, regenerating it only when it changed."""
		sourceRect = self._getLensSourceRect()
		if sourceRect == self._lensSourceRect and self._lensBitmap is not None:
			return self._lensBitmap
		left, top, side, _ = sourceRect
		if side <= 0:
			return None
		# Copy the part of the region that lies inside the screenshot into a square
		# buffer; the remainder stays black. Keeping the buffer square, and as large
		# as the region, is what keeps both the aspect ratio and the cursor centred
		# when the cursor is at a screen edge.
		source = Image.new("RGB", (side, side))
		imageWidth, imageHeight = self._fullScreenshot.size
		clipLeft = max(0, left)
		clipTop = max(0, top)
		clipRight = min(imageWidth, left + side)
		clipBottom = min(imageHeight, top + side)
		if clipRight > clipLeft and clipBottom > clipTop:
			source.paste(
				self._fullScreenshot.crop((clipLeft, clipTop, clipRight, clipBottom)),
				(clipLeft - left, clipTop - top),
			)
		lensSize = self._getLensSize()
		self._lensBitmap = pilToBitmap(source.resize((lensSize, lensSize), Image.Resampling.NEAREST))
		self._lensSourceRect = sourceRect
		return self._lensBitmap

	def _getLensCrosshairPosition(self, lensRect: wx.Rect) -> wx.Point | None:
		"""Return the lens point that matches the centre of the cursor pixel."""
		if not self._lensCursorPos:
			return None
		left, top, width, height = self._getLensSourceRect()
		if width <= 0 or height <= 0:
			return None
		# The source region is scaled up to fill the lens, so map the centre of
		# the cursor pixel through the same scale to keep the crosshair on it.
		scaleX = self._getLensSize() / width
		scaleY = self._getLensSize() / height
		return wx.Point(
			lensRect.x + round((self._lensCursorPos.x - left + 0.5) * scaleX),
			lensRect.y + round((self._lensCursorPos.y - top + 0.5) * scaleY),
		)

	def _getCursorScreenPosition(self) -> wx.Point | None:
		"""Return the cursor position in absolute screen coordinates."""
		if not self._lensCursorPos:
			return None
		return wx.Point(
			self._screenLeft + self._lensCursorPos.x,
			self._screenTop + self._lensCursorPos.y,
		)

	def _getCursorColourHex(self) -> str | None:
		"""Return the hexadecimal colour of the pixel under the cursor, if any."""
		if not self._lensCursorPos:
			return None
		x, y = self._lensCursorPos.x, self._lensCursorPos.y
		if not (0 <= x < self._fullScreenshot.width and 0 <= y < self._fullScreenshot.height):
			return None
		red, green, blue = self._fullScreenshot.getpixel((x, y))[:3]
		return f"#{red:02X}{green:02X}{blue:02X}"

	def _reportCursorColour(self) -> None:
		"""Report the colour of the pixel under the cursor."""
		colourHex = self._getCursorColourHex()
		if colourHex:
			# Translators: Reported when the colour under the magnifier cursor is requested.
			ui.message(_("Colour: {colour}").format(colour=colourHex))

	def _copyCursorColour(self) -> None:
		"""Copy the colour of the pixel under the cursor to the clipboard."""
		colourHex = self._getCursorColourHex()
		if colourHex:
			api.copyToClip(colourHex, notify=True)

	def _reportCursorPosition(self) -> None:
		"""Report the position of the cursor in screen coordinates."""
		position = self._getCursorScreenPosition()
		if position:
			# Translators: Reported when the position of the magnifier cursor is requested.
			ui.message(_("Position: {x}, {y}").format(x=position.x, y=position.y))

	def _copyCursorPosition(self) -> None:
		"""Copy the position of the cursor in screen coordinates to the clipboard."""
		position = self._getCursorScreenPosition()
		if position:
			api.copyToClip(f"{position.x}, {position.y}", notify=True)

	def _getInfoPanelLines(self) -> list[str]:
		"""Return the text lines shown in the magnifier info panel."""
		screenPosition = self._getCursorScreenPosition()
		if not screenPosition:
			return []
		# Translators: Zoom factor shown in the magnifier info panel.
		lines = [_("Zoom: {zoom}x").format(zoom=f"{self._lensZoom:g}")]
		# Translators: Cursor position in screen coordinates shown in the magnifier info panel.
		lines.append(_("Pos: ({x}, {y})").format(x=screenPosition.x, y=screenPosition.y))
		colourHex = self._getCursorColourHex()
		if colourHex:
			# Translators: Hexadecimal colour of the pixel under the cursor.
			lines.append(_("RGB: {colour}").format(colour=colourHex))
		if len(self.displays) > 1:
			for index, display in enumerate(self.displays):
				if display.Contains(screenPosition):
					# Translators: Index of the display containing the cursor.
					lines.append(_("Display: {index}").format(index=index + 1))
					break
		if self._selectionActive:
			rect = self._getSelectionRect()
			# Translators: Size in pixels of the current selection.
			lines.append(_("Selection: {width}x{height}").format(width=rect.width, height=rect.height))
		return lines

	def _drawInfoPanel(self, dc: wx.DC, lensRect: wx.Rect) -> None:
		"""Draw the zoom, cursor position and pixel colour details next to the lens."""
		lines = self._getInfoPanelLines()
		if not lines:
			return
		dc.SetFont(self._panelFont)
		# Scale the panel's pixel dimensions to match the lens on this display.
		paddingX = round(self._PANEL_PADDING_X * self._lensScaleFactor)
		paddingY = round(self._PANEL_PADDING_Y * self._lensScaleFactor)
		gap = round(self._PANEL_GAP * self._lensScaleFactor)
		lineHeight = dc.GetTextExtent("Ag").height + 2
		textWidth = max(dc.GetTextExtent(line).width for line in lines)
		panelWidth = textWidth + 2 * paddingX
		panelHeight = lineHeight * len(lines) + 2 * paddingY

		# Place the panel below the lens, flipping above it when there is no room.
		clientWidth, clientHeight = self.GetClientSize()
		panelLeft = lensRect.x
		panelTop = lensRect.y + lensRect.height + gap
		if panelTop + panelHeight > clientHeight:
			panelTop = lensRect.y - panelHeight - gap
		panelLeft = max(0, min(panelLeft, clientWidth - panelWidth))
		panelTop = max(0, min(panelTop, clientHeight - panelHeight))

		# A plain dark panel; it has no outline so it does not stand out from the
		# overlay it is drawn on top of.
		dc.SetPen(wx.TRANSPARENT_PEN)
		dc.SetBrush(wx.Brush(self._PANEL_COLOUR))
		dc.DrawRoundedRectangle(panelLeft, panelTop, panelWidth, panelHeight, 4)

		dc.SetTextForeground(self._PANEL_TEXT_COLOUR)
		textTop = panelTop + paddingY
		for line in lines:
			dc.DrawText(line, panelLeft + paddingX, textTop)
			textTop += lineHeight

	def _drawLens(self, dc: wx.DC) -> None:
		"""Draw the magnifier lens, its crosshair and the info panel.

		Plain DC drawing is used because a wx.GraphicsContext obtained from a
		buffered paint DC does not reliably render its output.
		"""
		if not self._lensCursorPos:
			return
		lensBitmap = self._getLensBitmap()
		if lensBitmap is None:
			return
		lensRect = self._getLensRect()
		dc.DrawBitmap(lensBitmap, lensRect.x, lensRect.y)

		# Crosshair marking the pixel under the cursor.
		crosshair = self._getLensCrosshairPosition(lensRect)
		if crosshair:
			dc.SetPen(wx.Pen(self._CROSSHAIR_COLOUR, 1))
			dc.DrawLine(lensRect.x, crosshair.y, lensRect.right, crosshair.y)
			dc.DrawLine(crosshair.x, lensRect.y, crosshair.x, lensRect.bottom)

		# Blue border around the lens.
		dc.SetPen(wx.Pen(self._LENS_BORDER_COLOUR, self._LENS_BORDER_WIDTH))
		dc.SetBrush(wx.TRANSPARENT_BRUSH)
		dc.DrawRectangle(lensRect.x, lensRect.y, lensRect.width, lensRect.height)

		self._drawInfoPanel(dc, lensRect)

	def getCapturedImage(self) -> tuple[RecogImageInfo, Image.Image] | None:
		"""Return the cropped image for the confirmed selection.

		:returns: A ``(RecogImageInfo, PIL.Image.Image)`` tuple, or ``None`` when
			no valid selection was confirmed.
		"""
		if not self._resultRect:
			return None

		x, y, width, height = self._resultRect
		# Clamp the crop to the screenshot bounds as a safety measure.
		right = min(x + width, self._fullScreenshot.width)
		bottom = min(y + height, self._fullScreenshot.height)
		if right <= x or bottom <= y:
			return None

		croppedImage = self._fullScreenshot.crop((x, y, right, bottom))
		# Report the location in absolute screen coordinates by adding the
		# virtual screen origin, matching other recognition sources.
		imageInfo = RecogImageInfo(self._screenLeft + x, self._screenTop + y, right - x, bottom - y, 1)
		return imageInfo, croppedImage
