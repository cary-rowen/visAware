# Copyright (C) 2025 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Helper classes for creating GUI settings panels for recognition engines."""

import wx
from dataclasses import dataclass, field
from gui.nvdaControls import CustomCheckListBox
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from .abstractEngine import AbstractEngine


class EngineSettingChanger:
	"""Functor which acts as a callback for GUI events to change numeric settings."""

	def __init__(self, setting: "EngineSetting", engine: "AbstractEngine"):
		"""
		Initializes the setting changer.

		:param setting: The engine setting this changer is for.
		:param engine: The engine instance to modify.
		"""
		self.engine = engine
		self.setting = setting

	def __call__(self, evt: wx.CommandEvent) -> None:
		"""
		Handles the GUI event to update the setting.

		:param evt: The wx event.
		"""
		evt.Skip()
		val = evt.GetSelection()
		setattr(self.engine, self.setting.name, val)


class TextInputEngineSettingChanger(EngineSettingChanger):
	"""Functor for GUI events to change text input settings."""

	def __call__(self, evt: wx.CommandEvent) -> None:
		"""
		Handles the GUI event to update the setting.

		:param evt: The wx event.
		"""
		evt.Skip()
		setattr(self.engine, self.setting.name, evt.GetString())


class StringEngineSettingChanger(EngineSettingChanger):
	"""Functor for GUI events to change string (choice) settings."""

	def __init__(self, setting: "ChoiceEngineSetting", engine: "AbstractEngine", panel: wx.Panel):
		"""
		Initializes the setting changer.

		:param setting: The engine setting this changer is for.
		:param engine: The engine instance to modify.
		:param panel: The parent panel containing the control.
		"""
		self.panel = panel
		super().__init__(setting, engine)

	def __call__(self, evt: wx.CommandEvent) -> None:
		"""
		Handles the GUI event to update the setting.

		:param evt: The wx event.
		"""
		evt.Skip()
		try:
			# Use the explicitly provided property name.
			availableValues = getattr(self.engine, self.setting.optionsPropertyName).values()
			newValue = list(availableValues)[evt.GetSelection()].id
			setattr(self.engine, self.setting.name, newValue)
			if hasattr(self.panel, "updateDriverSettings"):
				self.panel.updateDriverSettings(self.setting.name)
		except AttributeError:
			pass


class CheckListEngineSettingChanger(EngineSettingChanger):
	"""Functor for GUI events to change checklist settings."""

	def __init__(
		self,
		setting: "CheckListEngineSetting",
		engine: "AbstractEngine",
		checkListBox: CustomCheckListBox,
	):
		"""
		Initializes the setting changer.

		:param setting: The engine setting this changer is for.
		:param engine: The engine instance to modify.
		:param checkListBox: The checklist box control.
		"""
		super().__init__(setting, engine)
		self.checkList = checkListBox

	def __call__(self, evt: wx.CommandEvent) -> None:
		"""
		Handles the GUI event to update the setting.

		:param evt: The wx event.
		"""
		evt.Skip()
		checkedStrings = self.checkList.GetCheckedStrings()
		# Use the explicitly provided property name.
		availableSettings = getattr(self.engine, self.setting.optionsPropertyName).values()
		descToId = {opt.displayName: opt.id for opt in availableSettings}
		result = [descToId[s] for s in checkedStrings]
		setattr(self.engine, self.setting.name, result)


@dataclass
class EngineSetting:
	"""Represents a basic engine setting."""

	name: str
	displayNameWithAccelerator: str
	configSpec: str = field(init=False, default="string(default=None)")


@dataclass
class ChoiceEngineSetting:
	"""Represents an engine setting with a list of choices."""

	name: str
	displayNameWithAccelerator: str
	optionsPropertyName: str
	configSpec: str = field(init=False, default="string(default=None)")


@dataclass
class EditableChoiceEngineSetting(ChoiceEngineSetting):
	"""Represents an engine setting with suggested choices and manual input."""


@dataclass
class TextInputEngineSetting:
	"""Represents an engine setting for text input, like an API key."""

	name: str
	displayNameWithAccelerator: str
	configSpec: str = field(init=False, default="string(default=None)")


@dataclass
class ReadOnlyEngineSetting:
	"""Represents a read-only engine setting, like API quota."""

	name: str
	displayNameWithAccelerator: str
	configSpec: str = field(init=False, default="string(default=None)")


@dataclass
class NumericEngineSetting:
	"""Represents a numeric engine setting, like image quality."""

	name: str
	displayNameWithAccelerator: str
	minStep: int = 1
	largeStep: int = 10
	minVal: int = field(init=False, default=0)
	maxVal: int = field(init=False, default=100)
	configSpec: str = field(init=False, default="integer(default=50,min=0,max=100)")

	def __post_init__(self):
		self.largeStep = max(self.largeStep, self.minStep)


@dataclass
class BooleanEngineSetting:
	"""Represents a boolean engine setting."""

	name: str
	displayNameWithAccelerator: str
	configSpec: str = field(init=False, default="boolean(default=False)")


@dataclass
class CheckListEngineSetting:
	"""Represents an engine setting for a series of optional features."""

	name: str
	displayNameWithAccelerator: str
	optionsPropertyName: str
	configSpec: str = field(init=False, default="list(default=list())")


@dataclass
class ButtonEngineSetting:
	"""Represents an engine setting changed via a button-activated dialog."""

	name: str
	displayNameWithAccelerator: str
	configSpec: str = field(init=False, default="string(default=None)")
