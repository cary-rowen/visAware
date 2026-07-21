# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Agent client selection."""

from __future__ import annotations

import addonHandler

from ..exceptions import ApiError
from .settings import AgentHandler

addonHandler.initTranslation()


def createAgentClient():
	"""Creates the configured Agent client."""
	if not AgentHandler.isInitialized:
		AgentHandler.initialize()
	engine = AgentHandler.getCurrentEngine()
	if not engine:
		# Translators: Reported when no Agent engine can be loaded.
		raise ApiError(_("No Agent engine is available."))
	return engine.createClient()
