# Copyright (C) 2026 hwf1324 <1398969445@qq.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Provides a full-screen dialog for selecting a screen region with the mouse."""

import wx
from contentRecog import RecogImageInfo
from mouseHandler import getTotalWidthAndHeightAndMinimumPosition
from PIL import Image, ImageGrab


def pilToBitmap(pilImage: Image.Image, keepAlpha: bool = False) -> wx.Bitmap:
	"""Convert a PIL image to a :class:`wx.Bitmap`.

	:param pilImage: The source image, typically an RGB screenshot.
	:param keepAlpha: When True, preserve transparency via an RGBA buffer.
	:returns: A bitmap suitable for :class:`wx.DC` drawing operations.
	"""
	if keepAlpha:
		rgbaImage = pilImage.convert("RGBA")
		return wx.Bitmap.FromBufferRGBA(
			rgbaImage.width, rgbaImage.height, rgbaImage.tobytes()
		)
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
	"""

	# Minimum selection size, in pixels, below which a drag is treated as a stray click.
	_MIN_SELECTION_WIDTH = 5
	_MIN_SELECTION_HEIGHT = 5
	# Opacity of the dim overlay applied over the screenshot.
	_DIM_ALPHA = 100
	# Style of the selection outline.
	_SELECTION_COLOUR = wx.Colour(255, 0, 0)
	_SELECTION_BORDER_WIDTH = 2

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
		self._screenWidth, self._screenHeight, minPos = getTotalWidthAndHeightAndMinimumPosition(self.displays)
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

		self.Bind(wx.EVT_PAINT, self.onPaint)
		self.Bind(wx.EVT_LEFT_DOWN, self.onMouseLeftDown)
		self.Bind(wx.EVT_MOTION, self.onMouseMove)
		self.Bind(wx.EVT_LEFT_UP, self.onMouseLeftUp)
		self.Bind(wx.EVT_RIGHT_UP, self.onMouseRightUp)
		self.Bind(wx.EVT_KEY_UP, self.onKeyUp)

		# Use a crosshair cursor to indicate selection mode.
		self.SetCursor(wx.Cursor(wx.CURSOR_CROSS))

	def onEraseBackground(self, event: wx.EraseEvent) -> None:
		"""Suppress background erasing; the paint handler redraws the whole window."""

	def onPaint(self, event: wx.PaintEvent) -> None:
		"""Draw the dimmed desktop overlay with the active selection highlighted."""
		dc = wx.AutoBufferedPaintDC(self)
		dc.DrawBitmap(self._backgroundBitmap, 0, 0)

		if not (self._selectionActive and self._anchorPos and self._currentPos):
			return
		rect = self._getSelectionRect()

		# Restore the un-dimmed screenshot inside the selection bounds.
		dc.SetClippingRegion(rect)
		dc.DrawBitmap(self._screenshotBitmap, 0, 0)
		dc.DestroyClippingRegion()

		# Outline the selection in red with plain DC drawing.
		dc.SetPen(wx.Pen(self._SELECTION_COLOUR, self._SELECTION_BORDER_WIDTH))
		dc.SetBrush(wx.TRANSPARENT_BRUSH)
		dc.DrawRectangle(rect.x, rect.y, rect.width, rect.height)

	def onMouseLeftDown(self, event: wx.MouseEvent) -> None:
		"""Record the mouse-down position and start a drag selection."""
		self._anchorPos = event.GetPosition()
		self._currentPos = None
		self._isDragging = True
		self._selectionActive = False
		self.CaptureMouse()

	def onMouseMove(self, event: wx.MouseEvent) -> None:
		"""Update the selection rectangle while the mouse is dragged.

		The selection only becomes visible once the drag exceeds the minimum
		size, which filters out accidental clicks.
		"""
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
		"""Cancel an in-progress selection on Escape, or close the dialog."""
		if event.GetKeyCode() != wx.WXK_ESCAPE:
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
		imageInfo = RecogImageInfo(
			self._screenLeft + x, self._screenTop + y, right - x, bottom - y, 1
		)
		return imageInfo, croppedImage
