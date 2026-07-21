# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Gemini computer-use agent engine."""

from __future__ import annotations

from typing import Any

import addonHandler

from ...engineGUIHelper import ChoiceEngineSetting, NumericEngineSetting, TextInputEngineSetting
from ...geminiModels import (
	DEFAULT_GEMINI_MEDIA_RESOLUTION,
	DEFAULT_GEMINI_MODEL,
	getGeminiMediaResolutionChoices,
	getGeminiModelChoices,
)
from ..actions import JPEG_QUALITY
from ..gemini import GeminiAgentClient, GeminiAgentSettings
from ..settings import BaseAgentEngine

addonHandler.initTranslation()


class AgentEngine(BaseAgentEngine):
	"""Gemini-backed computer-use agent engine."""

	name = "gemini"
	# Translators: The description of the Google Gemini Agent engine.
	description = _("Google Gemini")

	_apiKey: str = ""
	_model: str = DEFAULT_GEMINI_MODEL
	_imageQuality: int = JPEG_QUALITY
	_mediaResolution: str = DEFAULT_GEMINI_MEDIA_RESOLUTION

	@property
	def supportedSettings(self) -> list[Any]:
		"""
		Defines the user-configurable settings for this Agent engine.

		:returns: A list of engine setting objects.
		"""
		return [
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the Gemini API key used by the Agent.
				displayNameWithAccelerator=_("API &Key:"),
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for the Gemini model used by the Agent.
				displayNameWithAccelerator=_("&Model:"),
				optionsPropertyName="availableModels",
			),
			ChoiceEngineSetting(
				name="mediaResolution",
				# Translators: The label for the Gemini media resolution used by the Agent.
				displayNameWithAccelerator=_("Media &resolution:"),
				optionsPropertyName="availableMediaResolutions",
			),
			self._imageQualitySetting(),
		]

	@property
	def apiKey(self) -> str:
		return self._apiKey

	@apiKey.setter
	def apiKey(self, value: str) -> None:
		self._apiKey = value

	@property
	def model(self) -> str:
		return self._model

	@model.setter
	def model(self, value: str) -> None:
		if value in self.availableModels:
			self._model = value

	@property
	def imageQuality(self) -> int:
		return self._imageQuality

	@imageQuality.setter
	def imageQuality(self, value: int) -> None:
		try:
			self._imageQuality = max(1, min(int(value), 95))
		except (TypeError, ValueError):
			self._imageQuality = JPEG_QUALITY

	@property
	def mediaResolution(self) -> str:
		return self._mediaResolution

	@mediaResolution.setter
	def mediaResolution(self, value: str) -> None:
		if value in self.availableMediaResolutions:
			self._mediaResolution = value

	@property
	def availableModels(self) -> dict:
		"""
		Provides model choices for the Agent engine settings.

		:returns: A dictionary of model IDs to display names.
		"""
		return self.generateStringSettings(getGeminiModelChoices())

	@property
	def availableMediaResolutions(self) -> dict:
		"""
		Provides media-resolution choices for the Agent engine settings.

		:returns: A dictionary of media-resolution IDs to display names.
		"""
		return self.generateStringSettings(getGeminiMediaResolutionChoices())

	@classmethod
	def check(cls) -> bool:
		"""
		Checks whether this Agent engine can be used.

		:returns: True because Gemini is configured entirely through settings.
		"""
		return True

	def createClient(self) -> GeminiAgentClient:
		"""Creates a Gemini Agent runtime client."""
		return GeminiAgentClient(
			GeminiAgentSettings(
				apiKey=self.apiKey,
				model=self.model,
				imageQuality=self.imageQuality,
				mediaResolution=self.mediaResolution,
				source="Agent Gemini engine settings",
			),
		)

	@staticmethod
	def _imageQualitySetting() -> NumericEngineSetting:
		# Translators: The label for the JPEG quality used for Agent screenshots.
		setting = NumericEngineSetting("imageQuality", _("Screenshot quality"))
		setting.minVal = 1
		setting.maxVal = 95
		setting.configSpec = f"integer(default={JPEG_QUALITY},min=1,max=95)"
		return setting
