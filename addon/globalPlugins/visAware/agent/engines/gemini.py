# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Gemini computer-use agent engine."""

from __future__ import annotations

from typing import Any

import addonHandler

from ...engineGUIHelper import ChoiceEngineSetting, TextInputEngineSetting
from ...geminiModels import (
	DEFAULT_GEMINI_MEDIA_RESOLUTION,
	DEFAULT_GEMINI_MODEL,
	getGeminiMediaResolutionChoices,
	getGeminiModelChoices,
)
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
				displayNameWithAccelerator=_("API &Key"),
			),
			ChoiceEngineSetting(
				name="model",
				# Translators: The label for the Gemini model used by the Agent.
				displayNameWithAccelerator=_("&Model"),
				optionsPropertyName="availableModels",
			),
			ChoiceEngineSetting(
				name="mediaResolution",
				# Translators: The label for the Gemini media resolution used by the Agent.
				displayNameWithAccelerator=_("Media &resolution"),
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
