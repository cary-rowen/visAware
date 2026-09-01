# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Kimi (Moonshot AI) computer-use agent engine."""

from __future__ import annotations

import config
from threading import Thread
from typing import Any

import addonHandler
import ui
import wx

from ...engineGUIHelper import (
	ButtonEngineSetting,
	ChoiceEngineSetting,
	EditableChoiceEngineSetting,
	TextInputEngineSetting,
)
from ...kimiModels import (
	DEFAULT_KIMI_AGENT_MODEL,
	DEFAULT_KIMI_BASE_URL,
	KIMI_REASONING_EFFORTS,
	getDefaultKimiModel,
	getKimiModelChoices,
	getKimiReasoningEffortChoices,
	isKimiK26Model,
	isKimiK3Model,
	normalizeKimiReasoningEffort,
	normalizeKimiReasoningEffortForModel,
)
from ..kimi import KimiAgentClient, KimiAgentSettings
from ..settings import BaseAgentEngine

addonHandler.initTranslation()

_KNOWN_MODELS_CONFIG_KEY = "knownModels"


class AgentEngine(BaseAgentEngine):
	"""Kimi-backed computer-use agent engine."""

	name = "kimi"
	# Translators: The description of the Kimi Agent engine. "Moonshot AI" is a proper noun
	# and should not be translated.
	description = _("Kimi (Moonshot AI)")
	engineConfigSpec = {
		_KNOWN_MODELS_CONFIG_KEY: "list(default=list())",
	}

	_apiKey: str = ""
	_baseUrl: str = DEFAULT_KIMI_BASE_URL
	_model: str = DEFAULT_KIMI_AGENT_MODEL
	_reasoningEffort: str = "high"
	_knownModels: list[str]

	@property
	def supportedSettings(self) -> list[Any]:
		return [
			TextInputEngineSetting(
				name="apiKey",
				# Translators: The label for the Kimi API key used by the Agent.
				displayNameWithAccelerator=_("API &key"),
			),
			TextInputEngineSetting(
				name="baseUrl",
				# Translators: The label for the Kimi-compatible base URL used by the Agent.
				displayNameWithAccelerator=_("Base &URL"),
				refreshSettingsOnChange=True,
			),
			ButtonEngineSetting(
				name="fetchModels",
				# Translators: The label for the button that fetches Kimi model names.
				displayNameWithAccelerator=_("&Fetch models"),
			),
			EditableChoiceEngineSetting(
				name="model",
				# Translators: The label for the Kimi model used by the Agent.
				displayNameWithAccelerator=_("&Model"),
				optionsPropertyName="availableModels",
			),
			ChoiceEngineSetting(
				name="reasoningEffort",
				# Translators: The label for Kimi K3 reasoning effort.
				displayNameWithAccelerator=_("&Thinking effort"),
				optionsPropertyName="reasoningEffortChoices",
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
		baseUrl = value.strip().rstrip("/") or DEFAULT_KIMI_BASE_URL
		if baseUrl != self._baseUrl:
			self._knownModels = []
		self._baseUrl = baseUrl
		if self.model not in getKimiModelChoices(self._baseUrl):
			self._model = getDefaultKimiModel(self._baseUrl)
			self._normalizeReasoningEffort()

	@property
	def model(self) -> str:
		return self._model

	@model.setter
	def model(self, value: str) -> None:
		value = value.strip()
		self._model = value or getDefaultKimiModel(self.baseUrl)
		self._normalizeReasoningEffort()

	@property
	def reasoningEffort(self) -> str:
		return self._reasoningEffort

	@reasoningEffort.setter
	def reasoningEffort(self, value: str) -> None:
		self._reasoningEffort = normalizeKimiReasoningEffort(value)
		self._normalizeReasoningEffort()

	@property
	def availableModels(self) -> dict:
		models = {modelName: modelName for modelName in self._getKnownModels()}
		if self.model and self.model not in models:
			models[self.model] = self.model
		return self.generateStringSettings(models)

	@property
	def reasoningEffortChoices(self) -> dict:
		choices = getKimiReasoningEffortChoices(self.model)
		return self.generateStringSettings(choices or KIMI_REASONING_EFFORTS)

	def _normalizeReasoningEffort(self) -> None:
		self._reasoningEffort = normalizeKimiReasoningEffortForModel(self.model, self._reasoningEffort)

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

	def isSupported(self, settingName: str) -> bool:
		if settingName == "reasoningEffort":
			return isKimiK3Model(self.model) or isKimiK26Model(self.model)
		return super().isSupported(settingName)

	def createClient(self) -> KimiAgentClient:
		return KimiAgentClient(
			KimiAgentSettings(
				apiKey=self.apiKey,
				baseUrl=self.baseUrl,
				model=self.model,
				imageQuality=self.imageQuality,
				reasoningEffort=self.reasoningEffort,
				source="Agent Kimi engine settings",
			),
		)

	def fetchModelsChanger(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		button = evt.GetEventObject()
		parent = button.GetParent()
		button.Disable()
		# Translators: Reported while fetching the Kimi model list.
		ui.message(_("Fetching Kimi model list"))
		Thread(
			name="VisAwareKimiFetchModels-agent",
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
			# Translators: Reported when Kimi returns no models.
			ui.message(_("No Kimi models were found."))
			return
		self._setKnownModels(modelNames)
		if not self.model:
			self.model = modelNames[0]
		# Translators: Reported after fetching Kimi models. {count} is the number of models.
		ui.message(_("Kimi models loaded: {count}").format(count=len(modelNames)))
		self._refreshSettingsPanel(parent)

	def _onFetchModelsFailed(self, button: wx.Button, message: str) -> None:
		button.Enable()
		# Translators: Reported when fetching Kimi models fails. {message} is the error message.
		ui.message(_("Could not fetch Kimi models: {message}").format(message=message))

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
