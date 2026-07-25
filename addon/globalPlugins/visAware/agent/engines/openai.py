# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""OpenAI Responses API computer-use agent engine."""

from __future__ import annotations

import config
from threading import Thread
from typing import Any

import addonHandler
import ui
import wx

from ...engineGUIHelper import (
	ButtonEngineSetting,
	EditableChoiceEngineSetting,
	TextInputEngineSetting,
)
from ..openai import (
	DEFAULT_OPENAI_BASE_URL,
	DEFAULT_OPENAI_MODEL,
	OpenAIAgentClient,
	OpenAIAgentSettings,
)
from ..settings import BaseAgentEngine

addonHandler.initTranslation()

_KNOWN_MODELS_CONFIG_KEY = "knownModels"


class AgentEngine(BaseAgentEngine):
	"""OpenAI-backed computer-use agent engine."""

	name = "openai"
	# Translators: The description of the OpenAI Agent engine.
	description = _("OpenAI")
	engineConfigSpec = {
		_KNOWN_MODELS_CONFIG_KEY: "list(default=list())",
	}

	_apiKey: str = ""
	_baseUrl: str = DEFAULT_OPENAI_BASE_URL
	_model: str = DEFAULT_OPENAI_MODEL
	_knownModels: list[str]

	@property
	def supportedSettings(self) -> list[Any]:
		return [
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the OpenAI API key used by the Agent.
				displayNameWithAccelerator=_("API &key"),
			),
			TextInputEngineSetting(
				name="baseUrl",
				# Translators: The label for the OpenAI-compatible base URL used by the Agent.
				displayNameWithAccelerator=_("Base &URL"),
			),
			ButtonEngineSetting(
				name="fetchModels",
				# Translators: The label for the button that fetches OpenAI model names.
				displayNameWithAccelerator=_("&Fetch models"),
			),
			EditableChoiceEngineSetting(
				name="model",
				# Translators: The label for the OpenAI model used by the Agent.
				displayNameWithAccelerator=_("&Model"),
				optionsPropertyName="availableModels",
			),
			self._imageQualitySetting(),
		]

	@property
	def apiKey(self) -> str:
		return self._apiKey

	@apiKey.setter
	def apiKey(self, value: str) -> None:
		self._apiKey = value.strip()

	@property
	def baseUrl(self) -> str:
		return self._baseUrl

	@baseUrl.setter
	def baseUrl(self, value: str) -> None:
		self._baseUrl = value.strip() or DEFAULT_OPENAI_BASE_URL

	@property
	def model(self) -> str:
		return self._model

	@model.setter
	def model(self, value: str) -> None:
		self._model = value.strip() or DEFAULT_OPENAI_MODEL

	@property
	def availableModels(self) -> dict:
		models = {modelName: modelName for modelName in self._getKnownModels()}
		if self.model and self.model not in models:
			models[self.model] = self.model
		return self.generateStringSettings(models)

	def loadSettings(self, onlyChanged: bool = False) -> None:
		super().loadSettings(onlyChanged=onlyChanged)
		self._loadKnownModelsFromConfig()

	def saveSettings(self) -> None:
		super().saveSettings()
		if not self.configSectionName:
			return
		config.conf[self.configSectionName][self.name][_KNOWN_MODELS_CONFIG_KEY] = self._getKnownModels()

	@classmethod
	def check(cls) -> bool:
		return True

	def createClient(self) -> OpenAIAgentClient:
		return OpenAIAgentClient(
			OpenAIAgentSettings(
				apiKey=self.apiKey,
				baseUrl=self.baseUrl,
				model=self.model,
				imageQuality=self.imageQuality,
				source="Agent OpenAI engine settings",
			),
		)

	def fetchModelsChanger(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		button = evt.GetEventObject()
		parent = button.GetParent()
		button.Disable()
		# Translators: Reported while fetching the OpenAI model list.
		ui.message(_("Fetching OpenAI model list"))
		Thread(
			name="VisAwareOpenAIFetchModels-agent",
			target=self._fetchModelsWorker,
			args=(parent, button),
			daemon=True,
		).start()

	def _fetchModelsWorker(self, parent: wx.Window, button: wx.Button) -> None:
		try:
			modelNames = self.createClient().listModels()
		except Exception as e:
			wx.CallAfter(self._onFetchModelsFailed, button, str(e))
			return
		wx.CallAfter(self._onFetchModelsFinished, parent, button, modelNames)

	def _onFetchModelsFinished(
		self,
		parent: wx.Window,
		button: wx.Button,
		modelNames: list[str],
	) -> None:
		button.Enable()
		if not modelNames:
			# Translators: Reported when OpenAI returns no models.
			ui.message(_("No OpenAI models were found."))
			return
		self._setKnownModels(modelNames)
		if not self.model:
			self.model = modelNames[0]
		# Translators: Reported after fetching OpenAI models. {count} is the number of models.
		ui.message(_("OpenAI models loaded: {count}").format(count=len(modelNames)))
		self._refreshSettingsPanel(parent)

	def _onFetchModelsFailed(self, button: wx.Button, message: str) -> None:
		button.Enable()
		# Translators: Reported when fetching OpenAI models fails. {message} is the error message.
		ui.message(_("Could not fetch OpenAI models: {message}").format(message=message))

	def _refreshSettingsPanel(self, parent: wx.Window) -> None:
		try:
			if hasattr(parent, "updateDriverSettings"):
				parent.updateDriverSettings()
		except RuntimeError:
			pass

	def _loadKnownModelsFromConfig(self) -> None:
		if not self.configSectionName:
			return
		try:
			knownModels = config.conf[self.configSectionName][self.name][_KNOWN_MODELS_CONFIG_KEY]
		except (KeyError, AttributeError):
			return
		if isinstance(knownModels, str):
			knownModels = [knownModels]
		self._setKnownModels([str(modelName) for modelName in knownModels])

	def _setKnownModels(self, modelNames: list[str]) -> None:
		knownModels: list[str] = []
		seenModelNames: set[str] = set()
		for modelName in modelNames:
			modelName = modelName.strip()
			if not modelName or modelName in seenModelNames:
				continue
			seenModelNames.add(modelName)
			knownModels.append(modelName)
		self._knownModels = knownModels

	def _getKnownModels(self) -> list[str]:
		return list(getattr(self, "_knownModels", []))
