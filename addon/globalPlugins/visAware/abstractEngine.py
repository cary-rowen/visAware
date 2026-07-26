# Copyright (C) 2025 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Abstract base classes for engine handlers and engines."""

from abc import ABC, abstractmethod
import addonHandler
import baseObject
import config
import gui
import importlib
import inspect
import pkgutil
from configobj.validate import is_boolean, is_integer, is_list
from gui import guiHelper
from gui.guiHelper import BoxSizerHelper
from gui.nvdaControls import CustomCheckListBox
from gui.settingsDialogs import SettingsPanel, SettingsDialog
from logHandler import log
from synthDriverHandler import StringParameterInfo
import wx
from wx.lib.expando import ExpandoTextCtrl
from typing import List, Tuple, Optional, Any, Type, Callable
import types
from .engineGUIHelper import (
	EngineSetting,
	EngineSettingChanger,
	BooleanEngineSetting,
	ButtonEngineSetting,
	CheckListEngineSetting,
	CheckListEngineSettingChanger,
	ChoiceEngineSetting,
	EditableChoiceEngineSetting,
	NumericEngineSetting,
	ReadOnlyEngineSetting,
	StringEngineSettingChanger,
	TextInputEngineSetting,
	TextInputEngineSettingChanger,
)
from gui.nvdaControls import EnhancedInputSlider

addonHandler.initTranslation()


class AbstractEngineHandler(baseObject.AutoPropertyObject):
	"""
	Manages the lifecycle and configuration of a collection of engines of a specific type.

	This class is responsible for discovering, loading, and switching between different
	engine implementations (e.g., OCR engines, image describer engines).
	"""

	engineList: Optional[List[Tuple[str, str]]] = None
	enginePackageName: str
	enginePackage: types.ModuleType
	configSectionName: str
	engineClass: Type["AbstractEngine"]
	isInitialized: bool = False
	currentEngine: Optional["AbstractEngine"] = None
	mandatoryClassName: Optional[str] = None
	engineClassList: Optional[List[Type["AbstractEngine"]]] = None
	defaultEnginePriorityList: List[str] = ["empty"]
	configSpec: dict = {
		"engine": "string(default=auto)",
		"disabledEngines": "list(default=list())",
	}

	@classmethod
	def _getSafeEngineName(cls, requestedName: str) -> str:
		"""
		Validates the requested engine name against the list of available engines.

		:param requestedName: The name of the engine to validate.
		:returns: The validated name, or "auto" if the name is not found.
		"""
		availableEngineNames = [e[0] for e in cls.getEngineList()]
		return requestedName if requestedName in availableEngineNames else "auto"

	@classmethod
	def initialize(cls) -> None:
		"""
		Initializes the engine handler.

		This method sets up the Python path, registers for config changes,
		discovers available engines, initializes configuration, and loads the
		configured engine.
		"""
		addonHandler.packaging.addDirsToPythonPackagePath(cls.enginePackage)
		config.post_configProfileSwitch.register(cls.handlePostConfigProfileSwitch)
		cls.getAllEngineList()
		cls._initConfig()
		engineNameFromConfig = config.conf[cls.configSectionName]["engine"]
		safeEngineName = cls._getSafeEngineName(engineNameFromConfig)
		cls.setCurrentEngine(safeEngineName)
		cls.isInitialized = True

	@classmethod
	def terminate(cls) -> None:
		"""
		Terminates the engine handler and the current engine.

		This unregisters from config changes and ensures the current engine is
		properly shut down.
		"""
		config.post_configProfileSwitch.unregister(cls.handlePostConfigProfileSwitch)
		if cls.currentEngine:
			cls.currentEngine.terminate()
			cls.currentEngine = None
		cls.isInitialized = False

	@classmethod
	def _initConfig(cls) -> None:
		"""Initializes the configuration specification for the handler and all its engines."""
		config.conf.spec[cls.configSectionName] = cls.configSpec
		for engineClass in cls.engineClassList or []:
			try:
				engineInstance = engineClass()
				engineName = engineInstance.name
				engineSpec = engineInstance.getConfigSpec()
				if engineSpec:
					config.conf.spec[cls.configSectionName][engineName] = engineSpec
			except Exception:
				log.error(f"Failed to get config spec for engine class {engineClass.__name__}", exc_info=True)

	@classmethod
	def getAllEngineList(cls) -> List[Tuple[str, str]]:
		"""
		Returns a list of installed and supported engines, discovering them if necessary.

		The list contains tuples of (engine_name, engine_description).

		:returns: A list of installed engines that pass their availability checks.
		"""
		if cls.engineList is not None:
			return cls.engineList
		cls.engineList = []
		cls.engineClassList = []
		emptyEngine: Optional[Tuple[str, str]] = None
		for loader, name, isPkg in pkgutil.iter_modules(cls.enginePackage.__path__):
			if name.startswith("_"):
				continue
			try:
				engineClass = cls.getEngine(name)
				if engineClass.check():
					if engineClass.name == "empty":
						emptyEngine = (engineClass.name, engineClass.description)
					else:
						cls.engineList.append((engineClass.name, engineClass.description))
						cls.engineClassList.append(engineClass)
				else:
					log.debugWarning(f"Engine '{name}' failed check, excluding from list.")
			except Exception:
				log.error(f"Error importing or checking engine {name}", exc_info=True)
		cls.engineList.sort(key=lambda s: s[1].lower())
		if emptyEngine:
			cls.engineList.append(emptyEngine)
		return cls.engineList

	@classmethod
	def getEngineList(cls) -> List[Tuple[str, str]]:
		"""
		Returns the user-enabled engines.

		This is the high-level list used by engine selection, engine cycling, and
		automatic fallback. Disabled engines remain installed and configurable, but
		they are not offered for normal use.

		:returns: A filtered list of enabled engines.
		"""
		disabledEngineNames = set(cls.getDisabledEngineNames())
		return [
			(name, description)
			for name, description in cls.getAllEngineList()
			if name == "empty" or name not in disabledEngineNames
		]

	@classmethod
	def getConfigurableEngineList(cls) -> List[Tuple[str, str]]:
		"""
		Returns installed engines that can be enabled or disabled by the user.

		The empty placeholder engine is a fallback implementation detail, not a
		user-configurable engine.

		:returns: A list of installed real engines.
		"""
		return [(name, description) for name, description in cls.getAllEngineList() if name != "empty"]

	@classmethod
	def getDisabledEngineNames(cls) -> List[str]:
		"""
		Returns configured disabled engine names after removing stale entries.

		:returns: A list of disabled engine names known to this handler.
		"""
		try:
			disabledEngineNames = config.conf[cls.configSectionName]["disabledEngines"]
		except (KeyError, AttributeError):
			return []
		if isinstance(disabledEngineNames, str):
			disabledEngineNames = [disabledEngineNames]
		availableEngineNames = {name for name, _description in cls.getConfigurableEngineList()}
		result: List[str] = []
		for engineName in disabledEngineNames:
			engineName = str(engineName)
			if engineName in availableEngineNames and engineName not in result:
				result.append(engineName)
		return result

	@classmethod
	def setDisabledEngineNames(cls, disabledEngineNames: List[str]) -> None:
		"""
		Saves the disabled engine list.

		:param disabledEngineNames: Engine names to exclude from normal use.
		"""
		availableEngineNames = [name for name, _description in cls.getConfigurableEngineList()]
		disabledSet = set(disabledEngineNames)
		normalizedDisabledEngineNames = [name for name in availableEngineNames if name in disabledSet]
		config.conf[cls.configSectionName]["disabledEngines"] = normalizedDisabledEngineNames

	@classmethod
	def isEngineEnabled(cls, engineName: str) -> bool:
		"""
		Returns whether an engine is enabled for normal use.

		:param engineName: The engine name to check.
		:returns: True when the engine is available for selection and cycling.
		"""
		return engineName == "empty" or engineName not in cls.getDisabledEngineNames()

	@classmethod
	def _getAutoEngineCandidateNames(cls) -> List[str]:
		"""Returns enabled engine names in automatic selection order."""
		enabledEngineNames = [name for name, _description in cls.getEngineList()]
		enabledNormalEngineNames = [name for name in enabledEngineNames if name != "empty"]
		priorityNames = [name for name in cls.defaultEnginePriorityList if name in enabledNormalEngineNames]
		remainingNames = [name for name in enabledNormalEngineNames if name not in priorityNames]
		candidateNames = priorityNames + remainingNames
		if not candidateNames and "empty" in enabledEngineNames:
			candidateNames.append("empty")
		return candidateNames

	@classmethod
	def getEngineInstance(cls, name: str) -> "AbstractEngine":
		"""
		Instantiates an engine class by name and initializes its settings.

		:param name: The name of the engine to instantiate.
		:returns: An instance of the requested engine.
		"""
		return cls._getEngineInstance(name)

	@classmethod
	def setCurrentEngine(cls, name: str, isFallback: bool = False) -> bool:
		"""
		Sets the active engine by name.

		Handles "auto" mode by selecting from a default priority list and provides
		fallback logic if an engine fails to load.

		:param name: The name of the engine to set as current.
		:param isFallback: Whether this is a fallback attempt.
		:returns: True if the engine was successfully loaded, False otherwise.
		"""
		if name == "auto":
			candidateEngineNames = cls._getAutoEngineCandidateNames()
		elif not cls.isEngineEnabled(name):
			log.warning(f"Engine '{name}' is disabled for {cls.configSectionName}. Falling back to auto.")
			candidateEngineNames = cls._getAutoEngineCandidateNames()
		else:
			candidateEngineNames = [name]
		prevEngineName = cls.currentEngine.name if cls.currentEngine else None
		if (
			prevEngineName
			and prevEngineName not in candidateEngineNames
			and cls.isEngineEnabled(prevEngineName)
		):
			candidateEngineNames.append(prevEngineName)
		for candidateName in cls._getAutoEngineCandidateNames():
			if candidateName not in candidateEngineNames:
				candidateEngineNames.append(candidateName)
		if not candidateEngineNames:
			log.warning(f"Cannot set engine for {cls.configSectionName} with no enabled engines.")
			if cls.currentEngine:
				cls.currentEngine.cancel()
				cls.currentEngine.terminate()
				cls.currentEngine = None
			if not isFallback:
				config.conf[cls.configSectionName]["engine"] = "auto"
			return False
		triedEngineNames: set[str] = set()
		for candidateName in candidateEngineNames:
			if candidateName in triedEngineNames:
				continue
			triedEngineNames.add(candidateName)
			if prevEngineName == candidateName:
				if not isFallback:
					config.conf[cls.configSectionName]["engine"] = candidateName
				return True
			try:
				newEngine = cls._getEngineInstance(candidateName)
				if cls.currentEngine:
					cls.currentEngine.cancel()
					cls.currentEngine.terminate()
				cls.currentEngine = newEngine
				if not isFallback:
					config.conf[cls.configSectionName]["engine"] = candidateName
				log.debug(f"Successfully loaded engine {candidateName}")
				return True
			except Exception:
				log.warning(f"Failed to load engine '{candidateName}'.", exc_info=True)
		log.warning(f"No available engines could be loaded for {cls.configSectionName}.")
		if cls.currentEngine:
			cls.currentEngine.terminate()
			cls.currentEngine = None
		return False

	@classmethod
	def _getEngineInstance(cls, name: str) -> "AbstractEngine":
		"""
		Instantiates an engine class by name and initializes its settings.

		:param name: The name of the engine to instantiate.
		:returns: An instance of the requested engine.
		"""
		engineClass = cls.getEngine(name)
		newEngine = engineClass()
		if not config.conf[cls.configSectionName].isSet(name):
			config.conf[cls.configSectionName][name] = {}
			c = config.conf[cls.configSectionName][name]
			c.spec = newEngine.getConfigSpec()
			newEngine.saveSettings()
		else:
			newEngine.loadSettings()
		return newEngine

	@classmethod
	def getEngine(cls, name: str) -> Type["AbstractEngine"]:
		"""
		Retrieves the engine class for a given engine name by importing its module.

		:param name: The name of the engine module.
		:returns: The engine class.
		:raises ImportError: If a valid engine class cannot be found in the module.
		"""
		engineModule = cls._importModule(cls.enginePackageName, name)
		if cls.mandatoryClassName:
			return getattr(engineModule, cls.mandatoryClassName)
		for item in dir(engineModule):
			moduleAttribute = getattr(engineModule, item)
			if inspect.isclass(moduleAttribute) and issubclass(moduleAttribute, cls.engineClass):
				return moduleAttribute
		raise ImportError(f"Could not find a valid engine class in module {name}")

	@classmethod
	def getCurrentEngine(cls) -> Optional["AbstractEngine"]:
		"""
		Returns the currently active engine instance.

		:returns: The current engine instance or None.
		"""
		return cls.currentEngine

	@classmethod
	def _importModule(cls, packageName: str, moduleName: str) -> Any:
		"""
		Imports a module dynamically.

		:param packageName: The name of the package containing the module.
		:param moduleName: The name of the module to import.
		:returns: The imported module object.
		"""
		if packageName.startswith("."):
			return importlib.import_module(f"{packageName}.{moduleName}", __package__)
		else:
			fullName = f"{packageName}.{moduleName}"
			return importlib.import_module(fullName)

	@classmethod
	def handlePostConfigProfileSwitch(cls) -> None:
		"""Handles configuration profile switches by reloading the engine if necessary."""
		conf = config.conf[cls.configSectionName]
		safeEngineName = cls._getSafeEngineName(conf["engine"])
		currentEngineName = cls.currentEngine.name if cls.currentEngine else None
		if safeEngineName != currentEngineName:
			cls.setCurrentEngine(safeEngineName)
		elif cls.currentEngine:
			cls.currentEngine.loadSettings(onlyChanged=True)


class AbstractEngine(baseObject.AutoPropertyObject, ABC):
	"""Abstract base class for a single recognition engine."""

	name: str = "empty"
	description: str = ""
	configSectionName: Optional[str] = None
	engineConfigSpec: dict = {}

	@property
	@abstractmethod
	def supportedSettings(self) -> List[EngineSetting]:
		"""
		A list of settings supported by this engine.

		Subclasses must implement this property to expose their configurable options.
		:returns: A list of `EngineSetting` objects.
		"""
		pass

	@staticmethod
	def generateStringSettings(settingsDict: dict) -> dict:
		"""
		Generates a dictionary of StringParameterInfo objects from a simple dict.

		:param settingsDict: A dictionary of {id: displayName}.
		:returns: A dictionary of {id: StringParameterInfo(id, displayName)}.
		"""
		return {key: StringParameterInfo(key, value) for key, value in settingsDict.items()}

	@classmethod
	@abstractmethod
	def check(cls) -> bool:
		"""
		Checks whether this engine can be used on the current system.

		:returns: True if the engine is available, False otherwise.
		"""
		pass

	def cancel(self) -> None:
		"""Cancels any ongoing recognition task."""
		pass

	def terminate(self) -> None:
		"""Terminates the engine, cleaning up any resources."""
		pass

	def saveSettings(self) -> None:
		"""Saves the engine's current settings to the configuration file."""
		if not self.configSectionName:
			return
		conf = config.conf[self.configSectionName][self.name]
		for setting in self.supportedSettings:
			if isinstance(setting, ButtonEngineSetting):
				continue
			conf[setting.name] = getattr(self, setting.name)

	def loadSettings(self, onlyChanged: bool = False) -> None:
		"""
		Loads the engine's settings from the configuration file.

		:param onlyChanged: If True, only settings that have changed will be applied.
		"""
		if not self.configSectionName:
			return
		conf = config.conf[self.configSectionName][self.name]
		for s in self.supportedSettings:
			if isinstance(s, ButtonEngineSetting):
				continue
			try:
				val = conf[s.name]
			except KeyError:
				continue
			specStr = s.configSpec
			try:
				if specStr.startswith("boolean"):
					val = is_boolean(val)
				elif specStr.startswith("integer"):
					val = is_integer(val)
				elif specStr.startswith("list"):
					if isinstance(val, str):
						from ast import literal_eval

						val = literal_eval(val)
					val = is_list(val)
			except (ValueError, TypeError, SyntaxError):
				log.debugWarning(
					f"Could not convert setting '{s.name}' with value '{val!r}' based on spec '{specStr}'. Skipping.",
				)
				continue
			if val is None:
				continue
			if onlyChanged and getattr(self, s.name, None) == val:
				continue
			setattr(self, s.name, val)

	def getConfigSpec(self) -> dict:
		"""
		Returns the configuration specification for this engine.

		:returns: A dictionary compatible with `configobj`.
		"""
		spec = self.engineConfigSpec.copy()
		for setting in self.supportedSettings:
			spec[setting.name] = setting.configSpec
		return spec

	def isSupported(self, settingName: str) -> bool:
		"""
		Checks if a specific setting is supported by this engine.

		:param settingName: The name of the setting to check.
		:returns: True if the setting is supported, False otherwise.
		"""
		return any(s.name == settingName for s in self.supportedSettings)


class SpecificEnginePanel(SettingsPanel):
	"""A settings panel for a specific engine's settings."""

	# Translators: The title of the engine-specific settings panel.
	title = _("Engine settings")
	handler: Type[AbstractEngineHandler]
	_getEngine: Callable[[], Optional[AbstractEngine]]
	_engine: Optional[AbstractEngine] = None
	_loadedEngines: List[AbstractEngine]
	_sizerDict: dict
	_lastControl: Optional[wx.Window]

	def __init__(
		self,
		parent: wx.Window,
		handler: Type[AbstractEngineHandler],
		getEngine: Callable[[], Optional[AbstractEngine]],
	):
		"""
		Initializes the panel for a specific engine handler.

		:param parent: The parent window.
		:param handler: The engine handler this panel is for.
		:param getEngine: A callable returning the engine currently selected in the settings panel.
		"""
		self.handler = handler
		self._getEngine = getEngine
		self._loadedEngines = []
		self._sizerDict = {}
		self._lastControl = None
		self._controlBuilders = {
			NumericEngineSetting: self._makeNumericSettingControl,
			BooleanEngineSetting: self._makeBooleanSettingControl,
			TextInputEngineSetting: self._makeTextInputSettingControl,
			ReadOnlyEngineSetting: self._makeReadOnlySettingControl,
			CheckListEngineSetting: self._makeCheckListSettingControl,
			ButtonEngineSetting: self._makeButtonSettingControl,
			ChoiceEngineSetting: self._makeStringSettingControl,
			EditableChoiceEngineSetting: self._makeEditableChoiceSettingControl,
		}
		self._controlUpdaters = {
			NumericEngineSetting: lambda s, e: getattr(self, f"{s.name}Slider").SetValue(getattr(e, s.name)),
			BooleanEngineSetting: lambda s, e: getattr(self, f"{s.name}Checkbox").SetValue(
				getattr(e, s.name),
			),
			TextInputEngineSetting: lambda s, e: getattr(self, f"{s.name}TextCtrl").SetValue(
				str(getattr(e, s.name)),
			),
			ReadOnlyEngineSetting: lambda s, e: getattr(self, f"{s.name}ExpandoTextCtrl").SetValue(
				str(getattr(e, s.name)),
			),
			CheckListEngineSetting: self._updateCheckListBox,
			ChoiceEngineSetting: self._updateChoiceControl,
			EditableChoiceEngineSetting: self._updateEditableChoiceControl,
			ButtonEngineSetting: lambda s, e: getattr(self, f"{s.name}Button").SetLabel(
				s.displayNameWithAccelerator,
			),
		}
		self._eventUnbinders = {
			CheckListEngineSetting: lambda s: getattr(self, f"{s.name}CheckListBox").Unbind(
				wx.EVT_CHECKLISTBOX,
			),
			ChoiceEngineSetting: lambda s: getattr(self, f"{s.name}List").Unbind(wx.EVT_CHOICE),
			EditableChoiceEngineSetting: self._unbindEditableChoiceControl,
		}
		super().__init__(parent)

	def makeSettings(self, settingsSizer: wx.BoxSizer) -> None:
		"""
		Creates the settings controls for the current engine.

		:param settingsSizer: The sizer to which controls will be added.
		"""
		self._sizerDict.clear()
		self._lastControl = None
		self.updateDriverSettings()

	def onPanelActivated(self) -> None:
		"""Refreshes the panel when it becomes active."""
		engine = self._getEngine()
		if engine and engine is not self._engine:
			log.debug("Engine changed, refreshing specific engine settings panel.")
			self.settingsSizer.Clear(delete_windows=True)
			self.makeSettings(self.settingsSizer)
		super().onPanelActivated()

	def updateDriverSettings(self, changedSetting: Optional[str] = None) -> None:
		"""Updates the GUI controls to reflect the current engine's settings."""
		engine = self._engine = self._getEngine()
		if not engine:
			return
		self._rememberLoadedEngine(engine)

		for name, sizer in self._sizerDict.items():
			if name != changedSetting and not engine.isSupported(name):
				self.settingsSizer.Hide(sizer)

		for setting in engine.supportedSettings:
			if setting.name.startswith("autoRecognition"):
				continue
			if setting.name == changedSetting:
				continue
			if setting.name in self._sizerDict:
				self._updateControlValue(setting, engine)
			else:
				self._createControl(setting, engine)

		self.settingsSizer.Layout()

	def _createControl(self, setting: EngineSetting, engine: AbstractEngine) -> None:
		"""
		Creates and adds a new control for a given setting.

		:param setting: The `EngineSetting` to create a control for.
		:param engine: The current engine instance.
		"""
		sHelper = BoxSizerHelper(self, sizer=self.settingsSizer)
		builder = self._controlBuilders.get(type(setting))
		if not builder:
			builder = self._makeStringSettingControl

		controlSizer = builder(setting, engine)
		self._sizerDict[setting.name] = controlSizer
		sHelper.addItem(controlSizer)

	def _updateControlValue(self, setting: EngineSetting, engine: AbstractEngine) -> None:
		"""
		Updates the value of an existing control.

		:param setting: The `EngineSetting` for the control.
		:param engine: The current engine instance.
		"""
		self.settingsSizer.Show(self._sizerDict[setting.name])
		updater = self._controlUpdaters.get(type(setting))
		if updater:
			updater(setting, engine)

	def _updateCheckListBox(self, setting: CheckListEngineSetting, engine: AbstractEngine) -> None:
		"""Updates a checklist box control."""
		paramIds = getattr(engine, setting.name, [])
		try:
			options = getattr(engine, setting.optionsPropertyName).values()
			idToDescription = {opt.id: opt.displayName for opt in options}
			checkedStrings = [idToDescription[id] for id in paramIds if id in idToDescription]
			getattr(self, f"{setting.name}CheckListBox").SetCheckedStrings(checkedStrings)
		except (AttributeError, ValueError):
			pass

	def _updateChoiceControl(self, setting: ChoiceEngineSetting, engine: AbstractEngine) -> None:
		"""Updates a choice (combobox) control."""
		try:
			options = list(getattr(engine, setting.optionsPropertyName).values())
			choiceCtrl = getattr(self, f"{setting.name}List")
			choiceCtrl.Clear()
			choiceCtrl.AppendItems([opt.displayName for opt in options])
			currentValue = getattr(engine, setting.name)
			index = [opt.id for opt in options].index(currentValue)
			choiceCtrl.SetSelection(index)
		except (AttributeError, ValueError):
			pass

	def _updateEditableChoiceControl(
		self,
		setting: EditableChoiceEngineSetting,
		engine: AbstractEngine,
	) -> None:
		"""Updates an editable choice control."""
		try:
			options = list(getattr(engine, setting.optionsPropertyName).values())
			choiceCtrl = getattr(self, f"{setting.name}ComboBox")
			choiceCtrl.Clear()
			choiceCtrl.AppendItems([opt.displayName for opt in options])
			choiceCtrl.SetValue(str(getattr(engine, setting.name)))
		except AttributeError:
			pass

	def _makeNumericSettingControl(
		self,
		setting: NumericEngineSetting,
		engine: AbstractEngine,
	) -> wx.BoxSizer:
		sizer = wx.BoxSizer(wx.HORIZONTAL)
		label = wx.StaticText(self, label=f"{setting.displayNameWithAccelerator}:")
		slider = EnhancedInputSlider(self, minValue=setting.minVal, maxValue=setting.maxVal)
		setattr(self, f"{setting.name}Slider", slider)
		slider.Bind(wx.EVT_SLIDER, EngineSettingChanger(setting, engine))
		slider.SetLineSize(setting.minStep)
		slider.SetPageSize(setting.largeStep)
		slider.SetValue(getattr(engine, setting.name))
		sizer.Add(label, flag=wx.ALIGN_CENTER_VERTICAL)
		sizer.Add(slider, proportion=1, flag=wx.EXPAND | wx.LEFT, border=5)
		if self._lastControl:
			slider.MoveAfterInTabOrder(self._lastControl)
		self._lastControl = slider
		return sizer

	def _makeStringSettingControl(self, setting: ChoiceEngineSetting, engine: AbstractEngine) -> wx.BoxSizer:
		labelText = f"{setting.displayNameWithAccelerator}:"
		choices = [x.displayName for x in getattr(engine, setting.optionsPropertyName).values()]
		labeledControl = guiHelper.LabeledControlHelper(self, labelText, wx.Choice, choices=choices)
		choiceCtrl = labeledControl.control
		setattr(self, f"{setting.name}List", choiceCtrl)
		self._updateChoiceControl(setting, engine)
		choiceCtrl.Bind(wx.EVT_CHOICE, StringEngineSettingChanger(setting, engine, self))
		if self._lastControl:
			choiceCtrl.MoveAfterInTabOrder(self._lastControl)
		self._lastControl = choiceCtrl
		return labeledControl.sizer

	def _makeEditableChoiceSettingControl(
		self,
		setting: EditableChoiceEngineSetting,
		engine: AbstractEngine,
	) -> wx.BoxSizer:
		labelText = f"{setting.displayNameWithAccelerator}:"
		choices = [x.displayName for x in getattr(engine, setting.optionsPropertyName).values()]
		labeledControl = guiHelper.LabeledControlHelper(
			self,
			labelText,
			wx.ComboBox,
			choices=choices,
			style=wx.CB_DROPDOWN,
		)
		choiceCtrl = labeledControl.control
		setattr(self, f"{setting.name}ComboBox", choiceCtrl)
		choiceCtrl.SetValue(str(getattr(engine, setting.name)))
		changer = TextInputEngineSettingChanger(setting, engine)
		choiceCtrl.Bind(wx.EVT_TEXT, changer)
		choiceCtrl.Bind(wx.EVT_COMBOBOX, changer)
		if self._lastControl:
			choiceCtrl.MoveAfterInTabOrder(self._lastControl)
		self._lastControl = choiceCtrl
		return labeledControl.sizer

	def _unbindEditableChoiceControl(self, setting: EditableChoiceEngineSetting) -> None:
		choiceCtrl = getattr(self, f"{setting.name}ComboBox")
		choiceCtrl.Unbind(wx.EVT_TEXT)
		choiceCtrl.Unbind(wx.EVT_COMBOBOX)

	def _makeButtonSettingControl(self, setting: ButtonEngineSetting, engine: AbstractEngine) -> wx.Sizer:
		sizer = wx.BoxSizer(wx.HORIZONTAL)
		button = wx.Button(self, label=setting.displayNameWithAccelerator)
		setattr(self, f"{setting.name}Button", button)
		button.Bind(wx.EVT_BUTTON, getattr(engine, f"{setting.name}Changer"))
		sizer.Add(button)
		if self._lastControl:
			button.MoveAfterInTabOrder(self._lastControl)
		self._lastControl = button
		return sizer

	def _makeCheckListSettingControl(
		self,
		setting: CheckListEngineSetting,
		engine: AbstractEngine,
	) -> wx.Sizer:
		sizer = wx.WrapSizer(wx.HORIZONTAL)
		label = wx.StaticText(self, label=f"{setting.displayNameWithAccelerator}:")
		sizer.Add(label)
		items = [x.displayName for x in getattr(engine, setting.optionsPropertyName).values()]
		checkListBox = CustomCheckListBox(self, choices=items)
		sizer.Add(checkListBox, flag=wx.EXPAND | wx.LEFT, border=5)
		setattr(self, f"{setting.name}CheckListBox", checkListBox)
		self._updateCheckListBox(setting, engine)
		checkListBox.Select(0)
		checkListBox.Bind(wx.EVT_CHECKLISTBOX, CheckListEngineSettingChanger(setting, engine, checkListBox))
		if self._lastControl:
			checkListBox.MoveAfterInTabOrder(self._lastControl)
		self._lastControl = checkListBox
		return sizer

	def _makeBooleanSettingControl(self, setting: BooleanEngineSetting, engine: AbstractEngine) -> wx.Sizer:
		sizer = wx.BoxSizer(wx.HORIZONTAL)
		checkbox = wx.CheckBox(self, label=setting.displayNameWithAccelerator)
		setattr(self, f"{setting.name}Checkbox", checkbox)
		checkbox.Bind(wx.EVT_CHECKBOX, lambda evt: setattr(engine, setting.name, evt.IsChecked()))
		checkbox.SetValue(bool(getattr(engine, setting.name)))
		sizer.Add(checkbox)
		if self._lastControl:
			checkbox.MoveAfterInTabOrder(self._lastControl)
		self._lastControl = checkbox
		return sizer

	def _makeTextInputSettingControl(
		self,
		setting: TextInputEngineSetting,
		engine: AbstractEngine,
	) -> wx.BoxSizer:
		labelText = f"{setting.displayNameWithAccelerator}:"
		labeledControl = guiHelper.LabeledControlHelper(
			self,
			labelText,
			wx.TextCtrl,
			size=(self.scaleSize(250), -1),
		)
		textCtrl = labeledControl.control
		setattr(self, f"{setting.name}TextCtrl", textCtrl)
		textCtrl.SetValue(str(getattr(engine, setting.name)))
		textCtrl.Bind(wx.EVT_TEXT, TextInputEngineSettingChanger(setting, engine))
		if self._lastControl:
			textCtrl.MoveAfterInTabOrder(self._lastControl)
		self._lastControl = textCtrl
		return labeledControl.sizer

	def _makeReadOnlySettingControl(
		self,
		setting: ReadOnlyEngineSetting,
		engine: AbstractEngine,
	) -> wx.BoxSizer:
		labelText = f"{setting.displayNameWithAccelerator}:"
		labeledControl = guiHelper.LabeledControlHelper(
			self,
			labelText,
			ExpandoTextCtrl,
			style=wx.TE_READONLY,
		)
		expandoTextCtrl = labeledControl.control
		setattr(self, f"{setting.name}ExpandoTextCtrl", expandoTextCtrl)
		expandoTextCtrl.SetValue(str(getattr(engine, setting.name)))
		if self._lastControl:
			expandoTextCtrl.MoveAfterInTabOrder(self._lastControl)
		self._lastControl = expandoTextCtrl
		return labeledControl.sizer

	def _rememberLoadedEngine(self, engine: AbstractEngine) -> None:
		"""Tracks loaded engine instances so unsaved in-memory edits can be discarded."""
		if not any(engine is loadedEngine for loadedEngine in self._loadedEngines):
			self._loadedEngines.append(engine)

	def _unbindCurrentControls(self) -> None:
		"""Unbinds events for controls belonging to the currently displayed engine."""
		engine = self._engine
		if not engine:
			return
		for setting in engine.supportedSettings:
			try:
				unbinder = self._eventUnbinders.get(type(setting))
				if unbinder:
					unbinder(setting)
			except AttributeError:
				pass

	def _discardLoadedEngines(self, savedEngine: Optional[AbstractEngine] = None) -> None:
		"""Reloads settings for loaded engine instances except the one just saved."""
		for engine in self._loadedEngines:
			if engine is not savedEngine:
				engine.loadSettings()
		self._loadedEngines = [savedEngine] if savedEngine else []

	def onDiscard(self) -> None:
		"""Discards changes made in the panel."""
		currentEngine = self._getEngine()
		if currentEngine:
			self._rememberLoadedEngine(currentEngine)
		self._unbindCurrentControls()
		self._discardLoadedEngines()

	def onSave(self) -> None:
		"""Saves the settings from the panel."""
		engine = self._getEngine()
		if engine:
			self._saveSettingsWithoutAutomaticRecognitionOverrides(engine)
		self._discardLoadedEngines(savedEngine=engine)

	def _saveSettingsWithoutAutomaticRecognitionOverrides(self, engine: AbstractEngine) -> None:
		"""Saves regular engine settings without overwriting automatic recognition overrides."""
		if not engine.configSectionName:
			engine.saveSettings()
			return
		conf = config.conf[engine.configSectionName][engine.name]
		preservedValues = {
			setting.name: conf[setting.name]
			for setting in engine.supportedSettings
			if setting.name.startswith("autoRecognition") and setting.name in conf
		}
		engine.saveSettings()
		for name, value in preservedValues.items():
			conf[name] = value


class AbstractEngineSettingsPanel(SettingsPanel, ABC):
	"""Abstract base panel for engine selection and configuration."""

	# Translators: The default title for an engine settings panel.
	title = _("Engine")
	handler: Type[AbstractEngineHandler]
	_engineSettingPanel: "SpecificEnginePanel"
	_descEngineNameCtrl: ExpandoTextCtrl
	_selectedEngine: Optional[AbstractEngine] = None
	_enableEngineCheckBox: Optional[wx.CheckBox] = None
	_pendingDisabledEngineNames: set[str]

	def makeSettings(self, settingsSizer: wx.BoxSizer) -> None:
		"""
		Creates the settings controls for the panel.

		:param settingsSizer: The sizer to which controls will be added.
		"""
		self._enableEngineCheckBox = None
		self._pendingDisabledEngineNames = set(self.handler.getDisabledEngineNames())
		self._selectedEngine = self._getInitialSelectedEngine()
		settingsSizerHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		# Translators: The label for the group box to select an engine.
		engineLabel = _("Engines")
		engineBox = wx.StaticBox(self, label=engineLabel)
		engineGroupSizer = wx.StaticBoxSizer(engineBox, wx.HORIZONTAL)
		engineGroupHelper = guiHelper.BoxSizerHelper(self, sizer=engineGroupSizer)
		settingsSizerHelper.addItem(engineGroupHelper)

		engineDesc = self._selectedEngine.description if self._selectedEngine else ""
		self._descEngineNameCtrl = ExpandoTextCtrl(
			self,
			size=(self.scaleSize(250), -1),
			value=engineDesc,
			style=wx.TE_READONLY,
		)
		self._descEngineNameCtrl.Bind(wx.EVT_CHAR_HOOK, self._onEnterTriggersChangeEngine)

		# Translators: The label for a button to change the current engine.
		changeEngineBtn = wx.Button(self, label=_("C&hange..."))
		engineGroupHelper.addItem(guiHelper.associateElements(self._descEngineNameCtrl, changeEngineBtn))
		changeEngineBtn.Bind(wx.EVT_BUTTON, self._onChangeEngine)

		self._enableEngineCheckBox = wx.CheckBox(
			self,
			# Translators: The label for a checkbox that enables or disables the selected engine.
			label=_("&Enable this engine"),
		)
		engineGroupHelper.addItem(self._enableEngineCheckBox)
		self._updateEnableEngineCheckBox()

		self._engineSettingPanel = SpecificEnginePanel(self, self.handler, self._getSelectedEngine)
		settingsSizerHelper.addItem(self._engineSettingPanel)
		self.makeGeneralSettings(settingsSizerHelper)

	def _getInitialSelectedEngine(self) -> Optional[AbstractEngine]:
		"""Returns the engine initially selected for editing in this settings panel."""
		currentEngine = self.handler.getCurrentEngine()
		if currentEngine and currentEngine.name != "empty":
			return currentEngine
		for engineName, _description in self.handler.getConfigurableEngineList():
			try:
				return self.handler.getEngineInstance(engineName)
			except Exception:
				log.warning(f"Failed to load engine '{engineName}' for settings.", exc_info=True)
		return currentEngine

	def _getSelectedEngine(self) -> Optional[AbstractEngine]:
		"""Returns the engine currently selected for editing."""
		return self._selectedEngine

	def makeGeneralSettings(self, settingsSizerHelper: guiHelper.BoxSizerHelper) -> None:
		"""
		A placeholder for subclasses to add general settings below engine-specific ones.

		:param settingsSizerHelper: The sizer helper for adding controls.
		"""
		pass

	def _onEnterTriggersChangeEngine(self, evt: wx.KeyEvent) -> None:
		"""Handles the Enter key on the engine name control to open the selection dialog."""
		if evt.KeyCode == wx.WXK_RETURN:
			self._onChangeEngine(evt)
		else:
			evt.Skip()

	def _onChangeEngine(self, evt: wx.Event) -> None:
		"""Opens the engine selection dialog."""
		self._recordSelectedEngineEnabledState()
		initialEngineName = self._selectedEngine.name if self._selectedEngine else None
		dialog = EnginesSelectionDialog(
			self,
			self.handler,
			multiInstanceAllowed=True,
			disabledEngineNames=self._pendingDisabledEngineNames,
			initialEngineName=initialEngineName,
		)
		if dialog.ShowModal() == wx.ID_OK:
			self.Freeze()
			if dialog.selectedEngineName and self._selectEngine(dialog.selectedEngineName):
				self._engineSettingPanel.onPanelActivated()
				self._sendLayoutUpdatedEvent()
			self.Thaw()
		dialog.Destroy()

	def _selectEngine(self, engineName: str) -> bool:
		"""
		Selects an engine for editing without changing runtime engine enablement.

		:param engineName: The engine to display in this settings panel.
		:returns: True if the engine was loaded for editing.
		"""
		currentEngine = self.handler.getCurrentEngine()
		try:
			if currentEngine and currentEngine.name == engineName:
				self._selectedEngine = currentEngine
			else:
				self._selectedEngine = self.handler.getEngineInstance(engineName)
		except Exception:
			# Translators: An error message shown when an engine fails to load.
			gui.messageBox(
				_("Could not load the %s engine.") % engineName,
				# Translators: The title of an error dialog.
				_("Engine Error"),
				wx.OK | wx.ICON_WARNING,
				self,
			)
			return False
		self.updateCurrentEngine()
		return True

	def updateCurrentEngine(self) -> None:
		"""Updates the displayed selected engine name."""
		engine = self._selectedEngine
		self._descEngineNameCtrl.SetValue(engine.description if engine else "")
		self._updateEnableEngineCheckBox()

	def _updateEnableEngineCheckBox(self) -> None:
		"""Updates the current engine enablement checkbox."""
		if not self._enableEngineCheckBox:
			return
		engine = self._selectedEngine
		isConfigurableEngine = bool(engine and engine.name != "empty")
		self._enableEngineCheckBox.Enable(isConfigurableEngine)
		self._enableEngineCheckBox.SetValue(
			bool(isConfigurableEngine and engine.name not in self._pendingDisabledEngineNames),
		)

	def _recordSelectedEngineEnabledState(self) -> None:
		"""Stores the current checkbox state in the pending disabled engine set."""
		if not self._enableEngineCheckBox:
			return
		engine = self._selectedEngine
		if not engine or engine.name == "empty":
			return
		if self._enableEngineCheckBox.GetValue():
			self._pendingDisabledEngineNames.discard(engine.name)
		else:
			self._pendingDisabledEngineNames.add(engine.name)

	def _getSelectedEnabledEngineName(self) -> Optional[str]:
		"""Returns the selected engine name when it can be made active."""
		engine = self._selectedEngine
		if not engine or engine.name == "empty" or engine.name in self._pendingDisabledEngineNames:
			return None
		return engine.name

	def isValid(self) -> bool:
		"""Validates current engine enablement before settings are saved."""
		return self._ensurePendingEngineEnablementIsValid()

	def _ensurePendingEngineEnablementIsValid(self) -> bool:
		"""
		Ensures pending engine enablement leaves a usable current engine.

		:returns: True when saving can continue.
		"""
		self._recordSelectedEngineEnabledState()
		configurableEngineNames = [name for name, _description in self.handler.getConfigurableEngineList()]
		enabledEngineNames = [
			name for name in configurableEngineNames if name not in self._pendingDisabledEngineNames
		]
		if not enabledEngineNames:
			self._updateEnableEngineCheckBox()
			gui.messageBox(
				# Translators: A warning shown when trying to disable every engine in a category.
				_("At least one engine must remain enabled."),
				# Translators: The title of a warning shown for invalid engine enablement settings.
				_("Engine Configuration"),
				wx.OK | wx.ICON_WARNING,
				self,
			)
			return False
		selectedEngine = self._selectedEngine
		if (
			selectedEngine
			and selectedEngine.name != "empty"
			and selectedEngine.name in self._pendingDisabledEngineNames
		):
			gui.messageBox(
				# Translators: Shown when the selected engine is disabled and cannot be saved as the active engine.
				_(
					"The selected engine {engine} is disabled. Please choose another enabled engine, "
					"or re-enable it.",
				).format(
					engine=selectedEngine.description,
				),
				# Translators: The title of a warning shown for invalid engine enablement settings.
				_("Engine Configuration"),
				wx.OK | wx.ICON_WARNING,
				self,
			)
			return False
		currentEngine = self.handler.getCurrentEngine()
		if (
			currentEngine
			and currentEngine.name != "empty"
			and currentEngine.name in self._pendingDisabledEngineNames
		):
			if self._getSelectedEnabledEngineName():
				return True
			gui.messageBox(
				# Translators: Shown when the current engine is disabled and the user must choose or enable an engine before saving.
				_(
					"The current engine {engine} is disabled. Please choose another enabled engine, "
					"or re-enable it.",
				).format(
					engine=currentEngine.description,
				),
				# Translators: The title of a warning shown for invalid engine enablement settings.
				_("Engine Configuration"),
				wx.OK | wx.ICON_WARNING,
				self,
			)
			return False
		return True

	def onPanelActivated(self) -> None:
		"""Refreshes the engine-specific panel when this panel is activated."""
		self._engineSettingPanel.onPanelActivated()
		super().onPanelActivated()

	def onPanelDeactivated(self) -> None:
		"""Notifies the engine-specific panel when this panel is deactivated."""
		self._engineSettingPanel.onPanelDeactivated()
		super().onPanelDeactivated()

	def onSave(self) -> None:
		"""Saves settings from the engine-specific panel."""
		if not self._ensurePendingEngineEnablementIsValid():
			raise ValueError("Invalid engine enablement configuration.")
		self._engineSettingPanel.onSave()
		oldDisabledEngineNames = self.handler.getDisabledEngineNames()
		self.handler.setDisabledEngineNames(list(self._pendingDisabledEngineNames))
		newCurrentEngineName = self._getSelectedEnabledEngineName()
		if newCurrentEngineName:
			if not self.handler.setCurrentEngine(newCurrentEngineName):
				self.handler.setDisabledEngineNames(oldDisabledEngineNames)
				self._pendingDisabledEngineNames = set(oldDisabledEngineNames)
				# Translators: An error message shown when an engine fails to load.
				gui.messageBox(
					_("Could not load the %s engine.") % newCurrentEngineName,
					# Translators: The title of an error dialog.
					_("Engine Error"),
					wx.OK | wx.ICON_WARNING,
					self,
				)
				raise ValueError(f"Could not load engine {newCurrentEngineName}")
		self._pendingDisabledEngineNames = set(self.handler.getDisabledEngineNames())
		if newCurrentEngineName:
			currentEngine = self.handler.getCurrentEngine()
			if currentEngine and currentEngine.name != "empty":
				self._selectedEngine = currentEngine
		self.updateCurrentEngine()
		self._engineSettingPanel.onPanelActivated()

	def onDiscard(self) -> None:
		"""Discards changes from the engine-specific panel."""
		self._engineSettingPanel.onDiscard()
		self._pendingDisabledEngineNames = set(self.handler.getDisabledEngineNames())
		self._updateEnableEngineCheckBox()


class EnginesSelectionDialog(SettingsDialog):
	"""A dialog to select an engine from a list."""

	# Translators: The title of the engine selection dialog.
	title = _("Select Engine")
	_engineNames: List[str] = []
	_engineListCtrl: Optional[wx.Choice] = None
	handler: Type[AbstractEngineHandler]
	selectedEngineName: Optional[str] = None

	def __init__(
		self,
		parent: wx.Window,
		handler: Type[AbstractEngineHandler],
		multiInstanceAllowed: bool = True,
		disabledEngineNames: Optional[set[str]] = None,
		initialEngineName: Optional[str] = None,
	):
		"""
		Initializes the dialog.

		:param parent: The parent window.
		:param handler: The engine handler for which to select an engine.
		:param multiInstanceAllowed: Whether to allow multiple instances of this dialog.
		:param disabledEngineNames: Engine names treated as disabled in this selection session.
		:param initialEngineName: Engine name to preselect.
		"""
		self.handler = handler
		self._disabledEngineNames = disabledEngineNames or set()
		self._initialEngineName = initialEngineName
		self.selectedEngineName = None
		super().__init__(parent, multiInstanceAllowed=multiInstanceAllowed)

	def makeSettings(self, settingsSizer: wx.BoxSizer) -> None:
		"""
		Creates the settings controls for the dialog.

		:param settingsSizer: The sizer to which controls will be added.
		"""
		settingsSizerHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		# Translators: The label for the choice control to select an engine.
		engineListLabelText = _("&Engines:")
		self._engineListCtrl = settingsSizerHelper.addLabeledControl(
			engineListLabelText,
			wx.Choice,
			choices=[],
		)
		self._updateEngineList()

	def postInit(self) -> None:
		"""Sets focus to the engine list after initialization."""
		if self._engineListCtrl:
			self._engineListCtrl.SetFocus()

	def _updateEngineList(self) -> None:
		"""Populates the engine list control with available engines."""
		if not self._engineListCtrl:
			return
		driverList = self.handler.getConfigurableEngineList()
		self._engineNames = [x[0] for x in driverList]
		options = [self._formatEngineOption(name, description) for name, description in driverList]
		self._engineListCtrl.Clear()
		self._engineListCtrl.AppendItems(options)
		if self._initialEngineName:
			try:
				index = self._engineNames.index(self._initialEngineName)
				self._engineListCtrl.SetSelection(index)
			except ValueError:
				pass
		if self._engineListCtrl.GetSelection() == wx.NOT_FOUND and self._engineNames:
			self._engineListCtrl.SetSelection(0)

	def _formatEngineOption(self, engineName: str, engineDescription: str) -> str:
		"""
		Formats an engine option with its pending enablement status.

		:param engineName: The engine name.
		:param engineDescription: The localized engine description.
		:returns: The option shown in the selection control.
		"""
		if engineName in self._disabledEngineNames:
			# Translators: A disabled engine option in the engine selection dialog.
			return _("{engine} (disabled)").format(engine=engineDescription)
		# Translators: An enabled engine option in the engine selection dialog.
		return _("{engine} (enabled)").format(engine=engineDescription)

	def onOk(self, evt: wx.CommandEvent) -> None:
		"""Handles the OK button press to set and save the selected engine."""
		if not self._engineNames or not self._engineListCtrl:
			return
		newEngineName = self._engineNames[self._engineListCtrl.GetSelection()]
		try:
			self.handler.getEngine(newEngineName)
		except Exception:
			# Translators: An error message shown when an engine fails to load.
			gui.messageBox(
				_("Could not load the %s engine.") % newEngineName,
				# Translators: The title of an error dialog.
				_("Engine Error"),
				wx.OK | wx.ICON_WARNING,
				self,
			)
			return

		self.selectedEngineName = newEngineName
		super().onOk(evt)
