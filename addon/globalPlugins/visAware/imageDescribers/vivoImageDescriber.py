# Copyright (C) 2025 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""An image description engine that uses the VIVO Image Describer API via NVDACN."""

import addonHandler
import time
import uuid
from collections import OrderedDict
from threading import Lock, Thread
from typing import List, Any

from ..recogHandler import BaseDescriber, RecognitionRequest
from ..contentRecognizers import _vivo_auth
from ..engineGUIHelper import NumericEngineSetting, CheckListEngineSetting
from PIL import Image
from io import BytesIO

addonHandler.initTranslation()


class CustomContentRecognizer(BaseDescriber):
	"""An image description engine that uses the Vivo Cloud API via NVDACN."""

	name = "vivoImageDescriber"
	# Translators: The description of the Vivo Image Describer engine.
	description = _("Vivo Image Describer (NVDACN)")

	_domain = "api-ai.vivo.com.cn"
	_uri = "/get_single_image_cn_brief_caption"
	_method = "POST"

	uploadBase64EncodeImage = False
	_authPrefetchMaxAge = 30.0
	_authPrefetchJoinTimeout = 0.05
	_authPrefetchLock = Lock()
	_authPrefetchThread: Thread | None = None
	_prefetchedHeaders: dict | None = None
	_prefetchException: Exception | None = None
	_prefetchedAt: float = 0.0
	_authPrefetchGeneration = 0

	# Constants for filter options.
	FILTER_BAD_ID = "bad"
	FILTER_LOW_SCORE_ID = "low_score"
	FILTER_BLUR_ID = "blur"

	@property
	def supportedSettings(self) -> List[Any]:
		"""
		Define the user-configurable settings for this engine.

		:returns: A list of `EngineSetting` objects.
		"""
		return [
			NumericEngineSetting(
				"minScore",
				# Translators: Label for a setting to set the minimum confidence score.
				_("Minimum confidence score (0-100)"),
			),
			CheckListEngineSetting(
				"filters",
				# Translators: Label for a setting to configure automatic filters.
				_("Automatic filters"),
				optionsPropertyName="availableFilters",
			),
		]

	_minScore: int = 80
	_filters: List[str] = [FILTER_BAD_ID, FILTER_LOW_SCORE_ID, FILTER_BLUR_ID]

	@property
	def minScore(self) -> int:
		return self._minScore

	@minScore.setter
	def minScore(self, value: int) -> None:
		self._minScore = value

	@property
	def filters(self) -> List[str]:
		return self._filters

	@filters.setter
	def filters(self, value: List[str]) -> None:
		self._filters = value

	@property
	def availableFilters(self) -> dict:
		"""
		Provides the items for the 'filters' checklist in the settings UI.

		:returns: A dictionary of filter IDs to display names.
		"""
		filterOptions = OrderedDict(
			{
				# Translators: A filter option for Vivo Image Describer.
				self.FILTER_BAD_ID: _("Filter bad descriptions"),
				# Translators: A filter option for Vivo Image Describer.
				self.FILTER_LOW_SCORE_ID: _("Filter low score descriptions"),
				# Translators: A filter option for Vivo Image Describer.
				self.FILTER_BLUR_ID: _("Filter descriptions of blurry images"),
			},
		)
		return self.generateStringSettings(filterOptions)

	@classmethod
	def check(cls) -> bool:
		"""
		Checks if the engine is available.

		:returns: Always True as it's a cloud service.
		"""
		return True

	def _toRgb(self, image: Image.Image) -> Image.Image:
		if image.mode == "P" and "transparency" in image.info:
			image = image.convert("RGBA")
		if image.mode in ("RGBA", "LA"):
			rgbaImage = image.convert("RGBA")
			background = Image.new("RGBA", rgbaImage.size, (255, 255, 255, 255))
			background.alpha_composite(rgbaImage)
			return background.convert("RGB")
		return image.convert("RGB")

	def _prepareImageObjectFromImage(self, image: Image.Image, imageInfo) -> Image.Image:
		"""Prepares an existing image while preserving generic size limits."""
		self.originalImage = self._toRgb(image)
		return self._checkAndResizeImage(self.originalImage, imageInfo)

	def _prepareImageObject(self, pixels: bytes, imageInfo) -> Image.Image:
		"""Prepares raw captured pixels while preserving generic size limits."""
		image = self._getConvertedImage(pixels, imageInfo)
		self.originalImage = self._toRgb(image)
		return self._checkAndResizeImage(self.originalImage, imageInfo)

	def prefetchAuthHeaders(self) -> None:
		"""Starts fetching Vivo auth headers in parallel with image preparation."""
		engineClass = type(self)

		def fetchHeaders(generation: int) -> None:
			try:
				user, password = _vivo_auth.getNvdacnCredentials()
				headers = _vivo_auth.genSignHeaders(user, password, self._method, self._uri, {})
				with engineClass._authPrefetchLock:
					if generation != engineClass._authPrefetchGeneration:
						return
					engineClass._prefetchedHeaders = headers
					engineClass._prefetchedAt = time.monotonic()
			except Exception as e:
				with engineClass._authPrefetchLock:
					if generation != engineClass._authPrefetchGeneration:
						return
					engineClass._prefetchException = e

		with engineClass._authPrefetchLock:
			if engineClass._hasFreshPrefetchedHeaders():
				return
			if engineClass._authPrefetchThread and engineClass._authPrefetchThread.is_alive():
				return
			engineClass._prefetchedHeaders = None
			engineClass._prefetchException = None
			engineClass._prefetchedAt = 0.0
			engineClass._authPrefetchGeneration += 1
			generation = engineClass._authPrefetchGeneration
			thread = Thread(
				name="VisAwareVivoImageDescriberAuthPrefetch",
				target=fetchHeaders,
				args=(generation,),
				daemon=True,
			)
			engineClass._authPrefetchThread = thread
		thread.start()

	@classmethod
	def _hasFreshPrefetchedHeaders(cls) -> bool:
		return bool(
			cls._prefetchedHeaders and time.monotonic() - cls._prefetchedAt <= cls._authPrefetchMaxAge,
		)

	def _getAuthHeaders(self) -> dict:
		engineClass = type(self)
		with engineClass._authPrefetchLock:
			thread = engineClass._authPrefetchThread
		if thread:
			thread.join(engineClass._authPrefetchJoinTimeout)
			if thread.is_alive():
				with engineClass._authPrefetchLock:
					if engineClass._authPrefetchThread is thread:
						engineClass._authPrefetchGeneration += 1
						engineClass._authPrefetchThread = None
						engineClass._prefetchedHeaders = None
						engineClass._prefetchException = None
						engineClass._prefetchedAt = 0.0
				return self._generateAuthHeaders()
			with engineClass._authPrefetchLock:
				if engineClass._authPrefetchThread is thread:
					engineClass._authPrefetchThread = None
		with engineClass._authPrefetchLock:
			if engineClass._prefetchException:
				engineClass._prefetchException = None
				engineClass._prefetchedHeaders = None
				engineClass._prefetchedAt = 0.0
			if engineClass._hasFreshPrefetchedHeaders():
				headers = engineClass._prefetchedHeaders
				engineClass._prefetchedHeaders = None
				engineClass._prefetchedAt = 0.0
				return dict(headers)
			engineClass._prefetchedHeaders = None
			engineClass._prefetchedAt = 0.0
		return self._generateAuthHeaders()

	def _generateAuthHeaders(self) -> dict:
		user, password = _vivo_auth.getNvdacnCredentials()
		return _vivo_auth.genSignHeaders(user, password, self._method, self._uri, {})

	def _serializeImageForUpload(self, image: Image.Image) -> bytes:
		"""
		Resizes the image to have a short side of 256 pixels, as per API docs.

		:param image: The source image.
		:returns: The JPEG image byte content to upload.
		"""
		width, height = image.size
		if width < height:
			newWidth = 256
			newHeight = round(height * (newWidth / width))
		else:
			newHeight = 256
			newWidth = round(width * (newHeight / height))
		image = image.resize((newWidth, newHeight), Image.Resampling.LANCZOS)
		output = BytesIO()
		image.convert("RGB").save(output, format="JPEG", quality=self.quality)
		return output.getvalue()

	def _prepareImageContent(self, image: Image.Image, imageInfo) -> bytes:
		"""Prepares the image as the final JPEG upload payload for Vivo."""
		return self._serializeImageForUpload(image)

	def _buildRequestParams(self, imageContent: bytes, request: RecognitionRequest) -> dict:
		"""
		Builds the parameters dictionary for the network request.

		:param imageContent: The raw byte content of the image.
		:returns: A dictionary of parameters for `requests`.
		:raises AuthenticationError: If NVDACN credentials are not configured.
		"""
		headers = self._getAuthHeaders()
		files = {"image": ("image.jpg", imageContent, "image/jpeg")}
		data = {"request_id": str(uuid.uuid4())}

		return {
			"method": self._method,
			"url": f"https://{self._domain}{self._uri}",
			"headers": headers,
			"files": files,
			"data": data,
		}

	def processApiResult(self, result: bytes) -> str | bool:
		"""
		Handles error checking from the API response.

		:param result: The raw byte response from the server.
		:returns: An error message string on failure, or False on success.
		"""
		responseJson = self._convertToJson(result)
		if responseJson.get("code") != 0:
			errorMessage = responseJson.get("msg", "Unknown Vivo API error")
			return f"Vivo API Error: {errorMessage} (Code: {responseJson.get('code')})"
		return False

	def extractText(self, apiResult: dict) -> str:
		"""
		Extracts and filters the plain text result from the parsed API response.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: The filtered image description or a filter notification message.
		"""
		caption = apiResult.get("caption", "")
		activeFilters = self.filters
		if self.FILTER_BAD_ID in activeFilters and apiResult.get("is_bad"):
			# Translators: A message indicating the image description was filtered because it was marked as bad.
			return _("Description filtered (marked as bad).")
		if self.FILTER_LOW_SCORE_ID in activeFilters and apiResult.get("is_low_score"):
			# Translators: A message indicating the image description was filtered due to a low confidence score.
			return _("Description filtered (low score).")
		if self.FILTER_BLUR_ID in activeFilters and apiResult.get("is_blur"):
			# Translators: A message indicating the image description was filtered because the image may be blurry.
			return _("Description filtered (image may be blurry).")
		scoreThreshold = self.minScore / 100.0
		if apiResult.get("score", 0.0) < scoreThreshold:
			# Translators: A message indicating the image description was filtered because its confidence score was below the user-defined threshold.
			return _("Description filtered (confidence score below threshold).")

		return caption

	def _convertToLineResultFormat(self, apiResult: dict) -> list:
		"""
		Converts the API response into NVDA's rich format.

		:param apiResult: The parsed JSON dictionary from the API.
		:returns: A list of lines, where each line contains a single word dictionary.
		"""
		text = self.extractText(apiResult)
		return [[{"x": 0, "y": 0, "width": 1, "height": 1, "text": text}]]
