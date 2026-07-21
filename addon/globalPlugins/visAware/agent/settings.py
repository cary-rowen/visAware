# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Agent engine settings."""

from __future__ import annotations

import addonHandler

from ..abstractEngine import AbstractEngine, AbstractEngineHandler, AbstractEngineSettingsPanel
from . import engines as agentEngines

addonHandler.initTranslation()

AGENT_CONFIG_SECTION = "visAwareAgent"


class BaseAgentEngine(AbstractEngine):
	"""Base class for computer-use agent engines."""

	configSectionName = AGENT_CONFIG_SECTION

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
	title = _("Agent")
	handler = AgentHandler
