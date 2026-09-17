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


def cropToImage(image: Image.Image, left: int, top: int, right: int, bottom: int) -> Image.Image | None:
	"""Crop an image to the given region, clamped to its bounds.

	:param image: The image to crop.
	:param left: Left edge of the region.
	:param top: Top edge of the region.
	:param right: Right edge of the region.
	:param bottom: Bottom edge of the region.
	:returns: The cropped image, or ``None`` when the region is empty.
	"""
	left = max(0, left)
	top = max(0, top)
	right = min(right, image.width)
	bottom = min(bottom, image.height)
	if right <= left or bottom <= top:
		return None
	return image.crop((left, top, right, bottom))


# Answers of :func:`fontFaceAvailable`, keyed by face name.
_fontFaceAvailability: dict[str, bool] = {}


def fontFaceAvailable(face: str) -> bool:
	"""Return whether the given font face is installed.

	wx reports back the face name it was asked for even when it quietly
	substitutes another font, so the list of installed faces has to be consulted
	instead. The answer is cached because enumerating fonts is expensive enough
	to notice when it runs on every change of display.

	:param face: The face name to look for, such as ``"Consolas"``.
	:returns: True when a font with that face name is installed.
	"""
	available = _fontFaceAvailability.get(face)
	if available is None:
		installed = {name.casefold() for name in wx.FontEnumerator.GetFacenames()}
		available = face.casefold() in installed
		_fontFaceAvailability[face] = available
	return available


class ScreenCaptureDialog(wx.Dialog):
	"""A borderless full-screen dialog for selecting a region to capture.

	The dialog overlays the desktop with a dimmed screenshot and starts with the
	area of NVDA's mouse object selected, when it reports one. Drag the mouse to
	select a different area, release the button or press Enter to confirm, click
	inside the selection to accept it as it stands, press Escape to cancel, or
	right-click to clear the current selection.

	A magnifier lens follows the cursor so low-vision users can inspect
	pixel-level detail: press M to toggle it, use the mouse wheel or +/- to
	change its zoom, press C to hear the colour under the cursor and P to hear
	its position in screen coordinates, or hold Control with either key to copy
	that value to the clipboard instead. Press Ctrl+Shift+C to copy the selected
	region itself to the clipboard as an image.
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
	_PANEL_COLUMN_GAP = 12
	_PANEL_CORNER_RADIUS = 4
	# Panel font height in pixels at 100 % scaling, scaled like the rest of the
	# panel geometry so the text grows with the DPI of the display in use.
	_PANEL_FONT_PIXEL_HEIGHT = 13
	# Fixed-pitch face used for the panel. Requesting the "modern" family alone
	# resolves to an embedded-bitmap face on some systems, whose strokes look
	# rough once it is scaled to a DPI-dependent pixel height, so a scalable face
	# is asked for by name and the system UI font is used when it is unavailable.
	_PANEL_FONT_FACE = "Consolas"

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
		self._dragStartPos: wx.Point | None = None
		self._isDragging = False
		self._selectionActive = False
		self._keepSelectionOnClick = False
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
		self._panelFont = self._createPanelFont()
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

		# Start with the area of NVDA's mouse object selected, so the region the
		# user last pointed at can be captured without drawing it again.
		self._seedSelectionFromMouseObject()

	def _seedSelectionFromMouseObject(self) -> None:
		"""Select the area of NVDA's mouse object, when it reports one."""
		mouseLocation = getattr(api.getMouseObject(), "location", None)
		if not mouseLocation:
			return
		topLeft = self.ScreenToClient(wx.Point(mouseLocation.left, mouseLocation.top))
		bottomRight = self.ScreenToClient(
			wx.Point(mouseLocation.left + mouseLocation.width, mouseLocation.top + mouseLocation.height)
		)
		if not self._isSelectionUsable(normalizedRect(topLeft, bottomRight)):
			return
		self._anchorPos = topLeft
		self._currentPos = bottomRight
		self._selectionActive = True

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
		"""Start a drag selection, or arm a click that accepts the current one."""
		position = event.GetPosition()
		self._dragStartPos = position
		self._isDragging = True
		# Clicking inside the current selection leaves it in place, so releasing
		# the button without dragging confirms it.
		self._keepSelectionOnClick = self._selectionActive and self._getSelectionRect().Contains(position)
		if not self._keepSelectionOnClick:
			self._startNewSelection(position)
		self.CaptureMouse()

	def onMouseMove(self, event: wx.MouseEvent) -> None:
		"""Move the magnifier lens and update the selection while the mouse moves.

		The selection only becomes visible once the drag exceeds the minimum
		size, which filters out accidental clicks.
		"""
		position = event.GetPosition()
		self._moveLens(position)
		if not (self._isDragging and event.Dragging()):
			return
		if self._keepSelectionOnClick:
			# Small movements still count as a click that accepts the current
			# selection; only a deliberate drag replaces it.
			startPos = self._dragStartPos
			if startPos is None or not self._isSelectionUsable(normalizedRect(startPos, position)):
				return
			self._startNewSelection(startPos)
		self._currentPos = position
		if not self._selectionActive:
			if not self._isSelectionUsable(self._getSelectionRect()):
				return
			self._selectionActive = True
		self.Refresh(eraseBackground=False)

	def onMouseLeftUp(self, event: wx.MouseEvent) -> None:
		"""Confirm the selection on mouse release, if it is large enough."""
		if not self._isDragging:
			return
		self._releaseCapture()
		self._isDragging = False

		if self._keepSelectionOnClick:
			# A click inside the current selection accepts it as it stands.
			self._keepSelectionOnClick = False
			self._confirmCurrentSelection()
			return

		if not self._selectionActive:
			self._resetSelection()
			return
		rect = self._getSelectionRect()
		if not self._isSelectionUsable(rect):
			self._resetSelection()
			return
		# Positions are client coordinates, which align with the screenshot's
		# origin because the dialog covers the whole virtual screen.
		self._resultRect = (rect.x, rect.y, rect.width, rect.height)
		self.EndModal(wx.ID_OK)

	def onMouseRightUp(self, event: wx.MouseEvent) -> None:
		"""Clear the current selection without closing the dialog.

		This also drops a selection that was established before the dialog was
		opened, so it must not depend on a left-button drag being in progress.
		"""
		self._releaseCapture()
		self._resetSelection()

	def onKeyUp(self, event: wx.KeyEvent) -> None:
		"""Handle the magnifier shortcuts, Enter to confirm and Escape to cancel."""
		keyCode = event.GetKeyCode()
		# C and P report the value under the cursor; holding Control copies it instead.
		controlDown = event.ControlDown()
		if controlDown and event.ShiftDown() and keyCode == ord("C"):
			self._copySelectionImage()
			return
		if keyCode in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self._confirmCurrentSelection()
			return
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

	def _startNewSelection(self, position: wx.Point) -> None:
		"""Begin a new selection anchored at the given position."""
		self._keepSelectionOnClick = False
		self._anchorPos = position
		self._currentPos = None
		self._selectionActive = False

	def _resetSelection(self) -> None:
		"""Clear the in-progress selection and repaint the overlay."""
		self._keepSelectionOnClick = False
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

	def _isSelectionUsable(self, rect: wx.Rect) -> bool:
		"""Return True when the rectangle is large enough to be a deliberate selection."""
		return rect.width > self._MIN_SELECTION_WIDTH and rect.height > self._MIN_SELECTION_HEIGHT

	def _confirmCurrentSelection(self) -> None:
		"""Confirm the current selection and close the dialog, when it is large enough."""
		if not self._selectionActive:
			return
		rect = self._getSelectionRect()
		if not self._isSelectionUsable(rect):
			return
		# Positions are client coordinates, which align with the screenshot's
		# origin because the dialog covers the whole virtual screen.
		self._releaseCapture()
		self._isDragging = False
		self._resultRect = (rect.x, rect.y, rect.width, rect.height)
		self.EndModal(wx.ID_OK)

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
		# The panel font is sized in pixels, so it has to follow the same scale.
		self._panelFont = self._createPanelFont()

	def _createPanelFont(self) -> wx.Font:
		"""Return the info panel font, sized for the display under the cursor.

		A fixed-pitch face keeps the numeric readouts aligned. The height is given
		in pixels instead of points so it does not depend on the DPI wxPython
		resolves a point size against, which is the system DPI and is therefore
		too small on monitors that use a higher DPI.
		"""
		pixelHeight = max(1, round(self._PANEL_FONT_PIXEL_HEIGHT * self._lensScaleFactor))
		if fontFaceAvailable(self._PANEL_FONT_FACE):
			family = wx.FONTFAMILY_MODERN
			faceName = self._PANEL_FONT_FACE
		else:
			# The preferred face is not installed, so use the system UI font
			# rather than whatever the "modern" family would substitute.
			family = wx.FONTFAMILY_DEFAULT
			faceName = ""
		return wx.Font(
			wx.Size(0, pixelHeight),
			family,
			wx.FONTSTYLE_NORMAL,
			wx.FONTWEIGHT_BOLD,
			faceName=faceName,
		)

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

	def _getSelectionImage(self) -> Image.Image | None:
		"""Return the screenshot cropped to the current selection, if there is one."""
		if not (self._selectionActive and self._anchorPos and self._currentPos):
			return None
		rect = self._getSelectionRect()
		return cropToImage(self._fullScreenshot, rect.left, rect.top, rect.right, rect.bottom)

	def _copySelectionImage(self) -> None:
		"""Copy the currently selected screen region to the clipboard as an image."""
		image = self._getSelectionImage()
		if image is None:
			# Translators: Reported when the selection image is requested but nothing is selected.
			ui.message(_("No selection to copy"))
			return
		copied = False
		if wx.TheClipboard.Open():
			try:
				copied = bool(wx.TheClipboard.SetData(wx.BitmapDataObject(pilToBitmap(image))))
			finally:
				wx.TheClipboard.Close()
		if copied:
			# Translators: Reported after the selected screen region has been copied as an image.
			ui.message(_("Selection copied as an image"))
		else:
			# Translators: Reported when the selected screen region could not be copied.
			ui.message(_("Could not copy the selection image"))

	def _getInfoPanelRows(self) -> list[tuple[str, str]]:
		"""Return the label and value pairs shown in the magnifier info panel."""
		screenPosition = self._getCursorScreenPosition()
		if not screenPosition:
			return []
		# Translators: Label of the zoom factor shown in the magnifier info panel.
		rows = [(_("Zoom:"), f"{self._lensZoom:g}x")]
		# Translators: Label of the cursor position in screen coordinates.
		rows.append((_("Pos:"), f"({screenPosition.x}, {screenPosition.y})"))
		colourHex = self._getCursorColourHex()
		if colourHex:
			# Translators: Label of the hexadecimal colour of the pixel under the cursor.
			rows.append((_("RGB:"), colourHex))
		if len(self.displays) > 1:
			for index, display in enumerate(self.displays):
				if display.Contains(screenPosition):
					# Translators: Label of the index of the display containing the cursor.
					rows.append((_("Display:"), str(index + 1)))
					break
		if self._selectionActive:
			rect = self._getSelectionRect()
			# Translators: Label of the position of the selection origin in screen coordinates.
			rows.append((_("Origin:"), f"({self._screenLeft + rect.x}, {self._screenTop + rect.y})"))
			# Translators: Label of the size in pixels of the current selection.
			rows.append((_("Selection:"), f"{rect.width}x{rect.height}"))
		return rows

	def _drawInfoPanel(self, dc: wx.DC, lensRect: wx.Rect) -> None:
		"""Draw the zoom, cursor position and pixel colour details next to the lens.

		Each row puts its label against the left edge of the panel and its value
		against the right edge, so the values form a column that is easy to scan.
		"""
		rows = self._getInfoPanelRows()
		if not rows:
			return
		dc.SetFont(self._panelFont)
		# Scale the panel's pixel dimensions to match the lens on this display.
		paddingX = round(self._PANEL_PADDING_X * self._lensScaleFactor)
		paddingY = round(self._PANEL_PADDING_Y * self._lensScaleFactor)
		gap = round(self._PANEL_GAP * self._lensScaleFactor)
		columnGap = round(self._PANEL_COLUMN_GAP * self._lensScaleFactor)
		lineHeight = dc.GetTextExtent("Ag").height + 2
		widestRow = max(
			dc.GetTextExtent(label).width + dc.GetTextExtent(value).width + columnGap for label, value in rows
		)
		# Keep the panel at least as wide as the lens so both elements look
		# consistent on every display, while long details can still widen it.
		panelWidth = max(widestRow + 2 * paddingX, lensRect.width)
		panelHeight = lineHeight * len(rows) + 2 * paddingY

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
		cornerRadius = max(1, round(self._PANEL_CORNER_RADIUS * self._lensScaleFactor))
		dc.DrawRoundedRectangle(panelLeft, panelTop, panelWidth, panelHeight, cornerRadius)

		dc.SetTextForeground(self._PANEL_TEXT_COLOUR)
		textLeft = panelLeft + paddingX
		textRight = panelLeft + panelWidth - paddingX
		textTop = panelTop + paddingY
		for label, value in rows:
			dc.DrawText(label, textLeft, textTop)
			dc.DrawText(value, textRight - dc.GetTextExtent(value).width, textTop)
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
		croppedImage = cropToImage(self._fullScreenshot, x, y, x + width, y + height)
		if croppedImage is None:
			return None
		# Report the location in absolute screen coordinates by adding the
		# virtual screen origin, matching other recognition sources.
		imageInfo = RecogImageInfo(
			self._screenLeft + x,
			self._screenTop + y,
			croppedImage.width,
			croppedImage.height,
			1,
		)
		return imageInfo, croppedImage
