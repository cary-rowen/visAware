# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

from typing import List, Tuple

import addonHandler
import config
from gui import guiHelper
from gui.guiHelper import BoxSizerHelper
from gui.nvdaControls import CustomCheckListBox
from gui.settingsDialogs import PANEL_DESCRIPTION_WIDTH, SettingsPanel
from logHandler import log
import wx

from .abstractEngine import AbstractEngine
from .engineSettingsUI import AbstractEngineSettingsPanel
from .recogHandler import (
	AUTO_RECOGNITION_OFF,
	AUTO_RECOGNITION_CURRENT_ENGINE_NAME,
	AUTO_RECOGNITION_IMAGE_DESCRIBER_MODE,
	AUTO_RECOGNITION_OCR_MODE,
	AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX,
	AUTO_RECOGNITION_OCR_PREFIX,
	CustomOCRHandler,
	ImageDescriberHandler,
	ENGINE_TYPES,
	SOURCE_TYPES,
	getAutoRecognitionTypeChoices,
	getAutoRecognitionEngineChoices,
	getAutoRecognitionEngineList,
	getAutoRecognitionTypeAndEngine,
	getAutoRecognitionHandler,
	getEffectiveAutoRecognitionEngine,
	getAutoRecognitionEngineSelections,
	saveAutoRecognitionEngineSelections,
	getConfigChoiceValue,
	getExcludedSourceTypes,
	setExcludedSourceTypes,
)

addonHandler.initTranslation()


class OCRPanel(AbstractEngineSettingsPanel):
	"""Settings panel for OCR engines."""

	# Translators: The title of the OCR engine settings panel.
	title = _("OCR")
	handler = CustomOCRHandler


class ImageDescriberPanel(AbstractEngineSettingsPanel):
	"""Settings panel for image describer engines."""

	# Translators: The title of the Image Describer engine settings panel.
	title = _("Image description")
	handler = ImageDescriberHandler


def getAutoRecognitionTypeAndEngineChoices() -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]], int, int]:
	"""Returns automatic recognition choices and initial selections for the settings panel."""
	autoRecognitionTypes = getAutoRecognitionTypeChoices()
	autoRecognitionTypeValues = [name for name, _description in autoRecognitionTypes]
	autoRecognitionType, engineName = getAutoRecognitionTypeAndEngine(getEffectiveAutoRecognitionEngine())
	try:
		typeSelection = autoRecognitionTypeValues.index(autoRecognitionType)
	except ValueError:
		autoRecognitionType = AUTO_RECOGNITION_OFF
		typeSelection = autoRecognitionTypeValues.index(AUTO_RECOGNITION_OFF)
	engineChoices = getAutoRecognitionEngineChoices(autoRecognitionType)
	engineValues = [name for name, _description in engineChoices]
	try:
		engineSelection = engineValues.index(engineName)
	except ValueError:
		typeSelection = autoRecognitionTypeValues.index(AUTO_RECOGNITION_OFF)
		engineChoices = []
		engineSelection = wx.NOT_FOUND
	return autoRecognitionTypes, engineChoices, typeSelection, engineSelection


class AutomaticRecognitionPanel(SettingsPanel):
	"""Automatic recognition settings panel for the Vis Aware add-on."""

	# Translators: The title of the automatic recognition settings panel for the add-on.
	title = _("Automatic recognition")

	def makeSettings(self, sizer: wx.BoxSizer) -> None:
		"""
		Creates the settings controls for the automatic recognition panel.

		:param sizer: The sizer to which controls will be added.
		"""
		settingsSizerHelper = BoxSizerHelper(self, sizer=sizer)
		conf = config.conf["visAwareGeneral"]
		self._autoRecognitionEngines = getAutoRecognitionEngineSelections()
		(
			self.autoRecognitionTypeChoices,
			self.autoRecognitionEngineChoices,
			typeSelection,
			engineSelection,
		) = getAutoRecognitionTypeAndEngineChoices()
		if (
			getEffectiveAutoRecognitionEngine() != AUTO_RECOGNITION_OFF
			and self.autoRecognitionTypeChoices[typeSelection][0] == AUTO_RECOGNITION_OFF
		):
			notice = settingsSizerHelper.addItem(
				wx.StaticText(
					self,
					# Translators: Explains why an old automatic recognition setting is shown as off.
					label=_(
						"The saved engine is unavailable for automatic recognition. "
						"Choose another engine or save with automatic recognition off.",
					),
				),
			)
			notice.Wrap(self.scaleSize(PANEL_DESCRIPTION_WIDTH))
		# Translators: The label for a choice control to select the kind of automatic recognition.
		self.autoRecognitionTypeList = settingsSizerHelper.addLabeledControl(
			_("Automatic recognition &type:"),
			wx.Choice,
			choices=[description for _name, description in self.autoRecognitionTypeChoices],
		)
		self.autoRecognitionTypeList.SetSelection(typeSelection)
		self.autoRecognitionTypeList.Bind(wx.EVT_CHOICE, self._onAutoRecognitionTypeChanged)
		# Translators: The label for a choice control to select the engine used for automatic recognition.
		self.autoRecognitionEngineList = settingsSizerHelper.addLabeledControl(
			_("Automatic recognition &engine:"),
			wx.Choice,
			choices=[],
		)
		self.autoRecognitionEngineList.Bind(wx.EVT_CHOICE, self._onAutoRecognitionEngineChanged)
		self._setAutoRecognitionEngineChoices(self.autoRecognitionEngineChoices, engineSelection)
		# Translators: The label for a text control to override the prompt used for automatic recognition.
		self.autoRecognitionPromptLabel = wx.StaticText(
			self,
			label=_("Automatic recognition &prompt:"),
		)
		self.autoRecognitionPromptCtrl = wx.TextCtrl(
			self,
			size=(-1, self.scaleSize(75)),
			style=wx.TE_MULTILINE,
		)
		autoRecognitionPromptSizer = wx.BoxSizer(wx.VERTICAL)
		autoRecognitionPromptSizer.Add(self.autoRecognitionPromptLabel)
		autoRecognitionPromptSizer.AddSpacer(guiHelper.SPACE_BETWEEN_ASSOCIATED_CONTROL_VERTICAL)
		autoRecognitionPromptSizer.Add(self.autoRecognitionPromptCtrl, proportion=1, flag=wx.EXPAND)
		settingsSizerHelper.addItem(autoRecognitionPromptSizer, flag=wx.EXPAND)
		# Translators: The label for a choice control to override the model used for automatic recognition.
		modelControlHelper = guiHelper.LabeledControlHelper(
			self,
			_("Automatic recognition &model:"),
			wx.Choice,
			choices=[],
		)
		self.autoRecognitionModelList = modelControlHelper.control
		modelSizer = wx.BoxSizer(wx.HORIZONTAL)
		modelSizer.Add(modelControlHelper.sizer, flag=wx.ALIGN_CENTER_VERTICAL)
		# Translators: The label for a button that fetches model names.
		self.autoRecognitionFetchModelsButton = wx.Button(self, label=_("&Fetch models"))
		modelSizer.Add(
			self.autoRecognitionFetchModelsButton,
			flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT,
			border=guiHelper.SPACE_BETWEEN_BUTTONS_HORIZONTAL,
		)
		settingsSizerHelper.addItem(modelSizer)
		self.autoRecognitionFetchModelsButton.Bind(wx.EVT_BUTTON, self._onFetchAutoRecognitionModels)
		# Translators: The label for a checkbox to prefer screenshots before downloading web image URLs.
		self.preferScreenshotForWebImagesCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("Prefer screenshots for &web images")),
		)
		self.preferScreenshotForWebImagesCheckBox.SetValue(conf["preferScreenshotForWebImages"])
		self._updateAutoRecognitionOverrideControls()
		self._updateAutoRecognitionControlState()

	def _getSelectedAutoRecognitionType(self) -> str:
		selection = self.autoRecognitionTypeList.GetSelection()
		if selection == wx.NOT_FOUND:
			return AUTO_RECOGNITION_OFF
		return self.autoRecognitionTypeChoices[selection][0]

	def _setAutoRecognitionEngineChoices(
		self,
		choices: List[Tuple[str, str]],
		selection: int = 0,
	) -> None:
		self.autoRecognitionEngineChoices = choices
		self.autoRecognitionEngineList.Clear()
		self.autoRecognitionEngineList.AppendItems([description for _name, description in choices])
		if choices:
			if selection == wx.NOT_FOUND or selection >= len(choices):
				selection = 0
			self.autoRecognitionEngineList.SetSelection(selection)
		self._updateAutoRecognitionControlState()

	def _updateAutoRecognitionControlState(self) -> None:
		isEnabled = self._getSelectedAutoRecognitionType() != AUTO_RECOGNITION_OFF
		self.autoRecognitionEngineList.Enable(isEnabled and bool(self.autoRecognitionEngineChoices))
		engine = getattr(self, "_autoRecognitionEngine", None)
		if hasattr(self, "autoRecognitionPromptCtrl"):
			promptEnabled = isEnabled and bool(engine and engine.isSupported("autoRecognitionPrompt"))
			self.autoRecognitionPromptLabel.Enable(promptEnabled)
			self.autoRecognitionPromptCtrl.Enable(promptEnabled)
		if hasattr(self, "autoRecognitionModelList"):
			self.autoRecognitionModelList.Enable(
				isEnabled and bool(engine and engine.isSupported("autoRecognitionModel")),
			)
		if hasattr(self, "autoRecognitionFetchModelsButton"):
			self.autoRecognitionFetchModelsButton.Enable(
				isEnabled
				and bool(engine and callable(getattr(engine, "fetchModelsChanger", None)))
				and not getattr(self.autoRecognitionFetchModelsButton, "_visAwareSettingsAction", False),
			)
		if hasattr(self, "preferScreenshotForWebImagesCheckBox"):
			self.preferScreenshotForWebImagesCheckBox.Enable(isEnabled)

	def _onAutoRecognitionTypeChanged(self, evt: wx.CommandEvent) -> None:
		autoRecognitionType = self._getSelectedAutoRecognitionType()
		choices = getAutoRecognitionEngineChoices(autoRecognitionType)
		engineName = self._autoRecognitionEngines.get(autoRecognitionType)
		selection = next(
			(index for index, (name, _description) in enumerate(choices) if name == engineName),
			0,
		)
		self._setAutoRecognitionEngineChoices(choices, selection)
		self._updateAutoRecognitionOverrideControls()

	def _onAutoRecognitionEngineChanged(self, evt: wx.CommandEvent) -> None:
		evt.Skip()
		self._autoRecognitionEngines[self._getSelectedAutoRecognitionType()] = (
			self._getSelectedAutoRecognitionEngineName()
		)
		self._updateAutoRecognitionOverrideControls()

	def _getSelectedAutoRecognitionEngineName(self) -> str:
		engineSelection = self.autoRecognitionEngineList.GetSelection()
		if engineSelection == wx.NOT_FOUND or not self.autoRecognitionEngineChoices:
			return AUTO_RECOGNITION_CURRENT_ENGINE_NAME
		return self.autoRecognitionEngineChoices[engineSelection][0]

	def _getSelectedAutoRecognitionEngine(self) -> AbstractEngine | None:
		handler = getAutoRecognitionHandler(self._getSelectedAutoRecognitionType())
		if not handler:
			return None
		engineName = self._getSelectedAutoRecognitionEngineName()
		if engineName == AUTO_RECOGNITION_CURRENT_ENGINE_NAME:
			currentEngine = handler.getCurrentEngine()
			engineName = currentEngine.name if currentEngine else ""
		if not engineName or engineName == "empty":
			return None
		try:
			if engineName not in dict(getAutoRecognitionEngineList(handler)):
				return None
			return handler.getEngineInstance(engineName)
		except Exception:
			log.debugWarning(f"Could not load automatic recognition engine {engineName!r}.", exc_info=True)
			return None

	def _getAutoRecognitionModelChoices(self, engine: AbstractEngine | None) -> List[Tuple[str, str]]:
		# Translators: A choice meaning automatic recognition should use the engine's regular model setting.
		choices = [("", _("Use the model selected in engine settings"))]
		if not engine or not engine.isSupported("model"):
			return choices
		seenModelNames = {""}
		try:
			availableModels = getattr(engine, "availableModels").values()
		except AttributeError:
			availableModels = []
		for modelInfo in availableModels:
			modelName = str(modelInfo.id)
			if not modelName or modelName in seenModelNames:
				continue
			seenModelNames.add(modelName)
			choices.append((modelName, modelInfo.displayName))
		return choices

	def _setAutoRecognitionModelChoices(
		self,
		choices: List[Tuple[str, str]],
		selectedModel: str,
	) -> None:
		if selectedModel and selectedModel not in dict(choices):
			choices = choices + [(selectedModel, selectedModel)]
		self.autoRecognitionModelChoices = choices
		self.autoRecognitionModelList.Clear()
		self.autoRecognitionModelList.AppendItems([description for _name, description in choices])
		modelNames = [name for name, _description in choices]
		try:
			selection = modelNames.index(selectedModel)
		except ValueError:
			selection = 0
		if choices:
			self.autoRecognitionModelList.SetSelection(selection)

	def _updateAutoRecognitionOverrideControls(self) -> None:
		self._autoRecognitionEngine = self._getSelectedAutoRecognitionEngine()
		engine = self._autoRecognitionEngine
		if engine and engine.isSupported("autoRecognitionPrompt"):
			self.autoRecognitionPromptCtrl.SetValue(engine.autoRecognitionPrompt)
		else:
			self.autoRecognitionPromptCtrl.SetValue("")
		selectedModel = str(getattr(engine, "autoRecognitionModel", "")).strip() if engine else ""
		self._setAutoRecognitionModelChoices(self._getAutoRecognitionModelChoices(engine), selectedModel)
		self._updateAutoRecognitionControlState()

	def updateDriverSettings(self) -> None:
		"""Refreshes dynamic automatic recognition controls after model discovery."""
		engine = getattr(self, "_autoRecognitionEngine", None)
		if not engine:
			self._updateAutoRecognitionOverrideControls()
			return
		selection = self.autoRecognitionModelList.GetSelection()
		selectedModel = (
			self.autoRecognitionModelChoices[selection][0]
			if 0 <= selection < len(self.autoRecognitionModelChoices)
			else str(getattr(engine, "autoRecognitionModel", "")).strip()
		)
		self._setAutoRecognitionModelChoices(self._getAutoRecognitionModelChoices(engine), selectedModel)
		self._updateAutoRecognitionControlState()

	def isEditingEngine(self, engine: AbstractEngine) -> bool:
		return getattr(self, "_autoRecognitionEngine", None) is engine

	def onDiscard(self) -> None:
		self._settingsActionGeneration = object()
		super().onDiscard()

	def _onFetchAutoRecognitionModels(self, evt: wx.CommandEvent) -> None:
		engine = getattr(self, "_autoRecognitionEngine", None)
		fetchModels = getattr(engine, "fetchModelsChanger", None)
		if callable(fetchModels):
			fetchModels(evt)
			return
		evt.Skip()

	def onSave(self) -> None:
		"""Saves the settings from the automatic recognition panel."""
		self._settingsActionGeneration = object()
		conf = config.conf["visAwareGeneral"]
		autoRecognitionType = self._getSelectedAutoRecognitionType()
		engineName = self._getSelectedAutoRecognitionEngineName()
		if autoRecognitionType == AUTO_RECOGNITION_IMAGE_DESCRIBER_MODE:
			autoRecognitionEngine = f"{AUTO_RECOGNITION_IMAGE_DESCRIBER_PREFIX}{engineName}"
		elif autoRecognitionType == AUTO_RECOGNITION_OCR_MODE:
			autoRecognitionEngine = f"{AUTO_RECOGNITION_OCR_PREFIX}{engineName}"
		else:
			autoRecognitionEngine = AUTO_RECOGNITION_OFF
		if autoRecognitionType != AUTO_RECOGNITION_OFF:
			self._autoRecognitionEngines[autoRecognitionType] = engineName
		saveAutoRecognitionEngineSelections(autoRecognitionEngine, self._autoRecognitionEngines)
		conf["preferScreenshotForWebImages"] = self.preferScreenshotForWebImagesCheckBox.GetValue()
		engine = getattr(self, "_autoRecognitionEngine", None) or self._getSelectedAutoRecognitionEngine()
		if not engine:
			return
		if engine.isSupported("autoRecognitionPrompt"):
			engine.autoRecognitionPrompt = self.autoRecognitionPromptCtrl.GetValue()
		if engine.isSupported("autoRecognitionModel"):
			modelSelection = self.autoRecognitionModelList.GetSelection()
			if modelSelection == wx.NOT_FOUND or not self.autoRecognitionModelChoices:
				engine.autoRecognitionModel = ""
			else:
				engine.autoRecognitionModel = self.autoRecognitionModelChoices[modelSelection][0]
		engine.saveSettings()


class CustomOCRPanel(SettingsPanel):
	"""General settings panel for the Vis Aware add-on."""

	# Translators: The title of the general settings panel for the add-on.
	title = _("General")

	def makeSettings(self, sizer: wx.BoxSizer) -> None:
		"""
		Creates the settings controls for the general panel.

		:param sizer: The sizer to which controls will be added.
		"""
		settingsSizerHelper = BoxSizerHelper(self, sizer=sizer)
		conf = config.conf["visAwareGeneral"]
		# Translators: The label for a checkbox to copy recognition results to the clipboard.
		self.copyToClipboardCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("Copy recognition result to the &clipboard")),
		)
		self.copyToClipboardCheckBox.SetValue(conf["copyToClipboard"])
		# Translators: The label for a checkbox to use a browseable message for text results.
		self.useBrowseableMessageCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("Show text results in a &browsable message")),
		)
		self.useBrowseableMessageCheckBox.SetValue(conf["useBrowseableMessage"])
		# Translators: The label for a checkbox to automatically read recognition results shown as documents.
		self.autoSayAllOnResultCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("Automatically &read recognition result documents")),
		)
		self.autoSayAllOnResultCheckBox.SetValue(conf["autoSayAllOnResult"])
		# Translators: The label for a checkbox to enable verbose debug logging.
		self.verboseDebugLoggingCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("&Enable more verbose logging for debug purposes")),
		)
		self.verboseDebugLoggingCheckBox.SetValue(conf["verboseDebugLogging"])

		sourceTypeChoices = [description for _name, description in SOURCE_TYPES]
		# Translators: The label for a checklist of recognition sources available in the cycle command.
		self.sourceTypesList = settingsSizerHelper.addLabeledControl(
			_("Recognition &sources included when cycling:"),
			CustomCheckListBox,
			choices=sourceTypeChoices,
		)
		excludedSourceTypes = set(getExcludedSourceTypes(conf))
		self.sourceTypesList.SetCheckedItems(
			[
				index
				for index, (name, _description) in enumerate(SOURCE_TYPES)
				if name not in excludedSourceTypes
			],
		)
		self.sourceTypesList.Select(0)

		engineTypeChoices = [desc for name, desc in ENGINE_TYPES]
		# Translators: The label for a choice control to select the Vis Aware operating mode.
		self.engineTypeList = settingsSizerHelper.addLabeledControl(
			_("Vis Aware &mode:"),
			wx.Choice,
			choices=engineTypeChoices,
		)
		engineType = getConfigChoiceValue(conf, "engineType", ENGINE_TYPES)
		self.engineTypeList.SetSelection([name for name, desc in ENGINE_TYPES].index(engineType))

		settingsSizerHelper.addItem(wx.StaticLine(self, style=wx.LI_HORIZONTAL))

		# Translators: The label for a group box for NVDACN account settings.
		nvdacnGroupLabel = _("NVDACN account (for supported engines like Vivo OCR)")
		nvdacnGroupSizer = wx.StaticBoxSizer(wx.VERTICAL, self, nvdacnGroupLabel)
		nvdacnGroupBox = nvdacnGroupSizer.GetStaticBox()
		nvdacnGroup = BoxSizerHelper(nvdacnGroupBox, sizer=nvdacnGroupSizer)
		settingsSizerHelper.addItem(nvdacnGroup)

		# Translators: The label for the username text control in the NVDACN settings.
		nvdacnUserLabel = _("Username:")
		self.nvdacnUserCtrl = nvdacnGroup.addLabeledControl(
			nvdacnUserLabel,
			wx.TextCtrl,
			size=(self.scaleSize(250), -1),
		)
		self.nvdacnUserCtrl.SetValue(conf["nvdacnUser"])

		# Translators: The label for the password text control in the NVDACN settings.
		nvdacnPassLabel = _("Password:")
		self.nvdacnPassCtrl = nvdacnGroup.addLabeledControl(
			nvdacnPassLabel,
			wx.TextCtrl,
			style=wx.TE_PASSWORD,
			size=(self.scaleSize(250), -1),
		)

		from .secure_storage import SecureStorageError, unprotectString

		try:
			self.originalDecryptedPassword = unprotectString(conf["nvdacnPass"])
		except SecureStorageError:
			log.warning("Stored NVDACN password could not be unprotected.", exc_info=True)
			self.originalDecryptedPassword = ""
		self.nvdacnPassCtrl.SetValue(self.originalDecryptedPassword)

	def onSave(self) -> None:
		"""Saves the settings from the general panel."""
		conf = config.conf["visAwareGeneral"]
		conf["copyToClipboard"] = self.copyToClipboardCheckBox.GetValue()
		conf["verboseDebugLogging"] = self.verboseDebugLoggingCheckBox.GetValue()
		conf["useBrowseableMessage"] = self.useBrowseableMessageCheckBox.GetValue()
		conf["autoSayAllOnResult"] = self.autoSayAllOnResultCheckBox.GetValue()
		conf["engineType"] = ENGINE_TYPES[self.engineTypeList.GetSelection()][0]
		enabledSourceTypes = [SOURCE_TYPES[index][0] for index in self.sourceTypesList.GetCheckedItems()]
		setExcludedSourceTypes(
			conf,
			[name for name, _description in SOURCE_TYPES if name not in enabledSourceTypes],
		)
		currentSourceType = getConfigChoiceValue(conf, "sourceType", SOURCE_TYPES)
		if enabledSourceTypes and currentSourceType not in enabledSourceTypes:
			conf["sourceType"] = enabledSourceTypes[0]

		from .secure_storage import SecureStorageError, protectString

		conf["nvdacnUser"] = self.nvdacnUserCtrl.GetValue()

		currentPasswordInput = self.nvdacnPassCtrl.GetValue()
		if currentPasswordInput != self.originalDecryptedPassword:
			try:
				conf["nvdacnPass"] = protectString(currentPasswordInput)
			except SecureStorageError:
				log.error("NVDACN password could not be protected.", exc_info=True)
				conf["nvdacnPass"] = ""

	def isValid(self) -> bool:
		"""Validates the general settings panel."""
		if not self.sourceTypesList.GetCheckedItems():
			self._validationErrorMessageBox(
				# Translators: Shown when no recognition source is available for the cycle source command.
				message=_("At least one recognition source must be selected."),
				# Translators: The setting name used in the validation error for the recognition source checklist.
				option=_("Recognition sources included when cycling"),
			)
			return False
		return super().isValid()
