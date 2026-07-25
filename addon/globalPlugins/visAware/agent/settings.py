# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Agent engine settings."""

from __future__ import annotations

import addonHandler

from ..abstractEngine import AbstractEngine, AbstractEngineHandler, AbstractEngineSettingsPanel
from ..engineGUIHelper import NumericEngineSetting
from . import engines as agentEngines
from .actions import JPEG_QUALITY

addonHandler.initTranslation()

AGENT_CONFIG_SECTION = "visAwareAgent"


class BaseAgentEngine(AbstractEngine):
	"""Base class for computer-use agent engines."""

	configSectionName = AGENT_CONFIG_SECTION
	_imageQuality: int = JPEG_QUALITY

	@property
	def imageQuality(self) -> int:
		return self._imageQuality

	@imageQuality.setter
	def imageQuality(self, value: int) -> None:
		try:
			self._imageQuality = max(1, min(int(value), 95))
		except (TypeError, ValueError):
			self._imageQuality = JPEG_QUALITY

	@staticmethod
	def _imageQualitySetting() -> NumericEngineSetting:
		# Translators: The label for the JPEG quality used for Agent screenshots.
		setting = NumericEngineSetting("imageQuality", _("Screenshot quality"))
		setting.minVal = 1
		setting.maxVal = 95
		setting.configSpec = f"integer(default={JPEG_QUALITY},min=1,max=95)"
		return setting

	def createClient(self):
		"""Creates a runtime client for this Agent engine."""
		raise NotImplementedError


class AgentHandler(AbstractEngineHandler):
	"""Handler for computer-use agent engines."""

	engineClass = BaseAgentEngine
	enginePackageName = ".agent.engines"
	enginePackage = agentEngines
	configSectionName = AGENT_CONFIG_SECTION
	defaultEnginePriorityList = ["gemini"]
	mandatoryClassName = "AgentEngine"


class AgentPanel(AbstractEngineSettingsPanel):
	"""Settings panel for computer-use agent engines."""

	# Translators: The title of the Agent settings panel.
	title = _("AI Agent")
	handler = AgentHandler
