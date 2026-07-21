# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Shared settings behavior for Ollama engines."""

from __future__ import annotations

import addonHandler
import config
from threading import Thread
from typing import Any
import ui
import wx

from ._ollamaClient import DEFAULT_OLLAMA_API_BASE_URL, OllamaClient
from .exceptions import ApiError

addonHandler.initTranslation()

_KNOWN_MODELS_CONFIG_KEY = "knownModels"


class OllamaEngineMixin:
	"""Common configurable settings and model discovery for Ollama engines."""

	engineConfigSpec = {
		_KNOWN_MODELS_CONFIG_KEY: "list(default=list())",
	}

	_apiBaseUrl: str = DEFAULT_OLLAMA_API_BASE_URL
	_apiKey: str = ""
	_model: str = ""
	_useStreaming: bool = False
	_knownModels: list[str]

	@property
	def apiBaseUrl(self) -> str:
		return self._apiBaseUrl

	@apiBaseUrl.setter
	def apiBaseUrl(self, value: str) -> None:
		self._apiBaseUrl = value.strip() if value else DEFAULT_OLLAMA_API_BASE_URL

	@property
	def apiKey(self) -> str:
		return self._apiKey

	@apiKey.setter
	def apiKey(self, value: str) -> None:
		self._apiKey = value.strip() if value else ""

	@property
	def model(self) -> str:
		return self._model

	@model.setter
	def model(self, value: str) -> None:
		self._model = value.strip() if value else ""

	@property
	def useStreaming(self) -> bool:
		return self._useStreaming

	@useStreaming.setter
	def useStreaming(self, value: bool) -> None:
		self._useStreaming = bool(value)
		self.isStreaming = bool(value)

	@property
	def availableModels(self) -> dict:
		"""
		Provides available Ollama models for the settings UI.

		:returns: A dictionary of model IDs to display names.
		"""
		models = {modelName: modelName for modelName in self._getKnownModels()}
		if self.model and self.model not in models:
			models[self.model] = self.model
		if not models:
			# Translators: A placeholder shown in the Ollama model list before models are fetched.
			models[""] = _("Fetch model list first")
		return self.generateStringSettings(models)

	def loadSettings(self, onlyChanged: bool = False) -> None:
		"""
		Loads common Ollama settings and remembered model names.

		:param onlyChanged: If True, only changed settings are applied.
		"""
		super().loadSettings(onlyChanged=onlyChanged)
		self._loadKnownModelsFromConfig()

	def saveSettings(self) -> None:
		"""Saves common Ollama settings and remembered model names."""
		super().saveSettings()
		if not self.configSectionName:
			return
		conf = config.conf[self.configSectionName][self.name]
		conf[_KNOWN_MODELS_CONFIG_KEY] = self._getKnownModels()

	def fetchModelsChanger(self, evt: wx.CommandEvent) -> None:
		"""
		Fetches local Ollama models and refreshes the model list control.

		:param evt: The button event.
		"""
		evt.Skip()
		button = evt.GetEventObject()
		parent = button.GetParent()
		button.Disable()
		# Translators: Reported while fetching the Ollama model list.
		ui.message(_("Fetching Ollama model list"))
		Thread(
			name=f"VisAwareOllamaFetchModels-{self.name}",
			target=self._fetchModelsWorker,
			args=(parent, button),
			daemon=True,
		).start()

	def _fetchModelsWorker(self, parent: wx.Window, button: wx.Button) -> None:
		try:
			modelNames = self._fetchModelNames()
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
			# Translators: Reported when Ollama has no local models.
			ui.message(_("No Ollama models were found."))
			return
		self._setKnownModels(modelNames)
		if not self.model or self.model not in modelNames:
			self.model = modelNames[0]
		# Translators: Reported after fetching Ollama models. {count} is the number of models.
		ui.message(_("Ollama models loaded: {count}").format(count=len(modelNames)))
		self._refreshSettingsPanel(parent)

	def _onFetchModelsFailed(self, button: wx.Button, message: str) -> None:
		button.Enable()
		# Translators: Reported when fetching Ollama models fails. {message} is the error message.
		ui.message(_("Could not fetch Ollama models: {message}").format(message=message))

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

	def _makeOllamaClient(self) -> OllamaClient:
		return OllamaClient(self.apiBaseUrl, self.apiKey)

	def _fetchModelNames(self) -> list[str]:
		return self._makeOllamaClient().getModelNames()

	def _resolveModel(self) -> str:
		if self.model:
			return self.model
		modelNames = self._fetchModelNames()
		if not modelNames:
			# Translators: An error message when Ollama has no local models to use.
			raise ApiError(_("No Ollama models are available. Pull a vision model in Ollama first."))
		self._setKnownModels(modelNames)
		self.model = modelNames[0]
		return self.model

	def _buildOllamaRequestParams(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
		return self._makeOllamaClient().buildRequestParams(endpoint, payload)
