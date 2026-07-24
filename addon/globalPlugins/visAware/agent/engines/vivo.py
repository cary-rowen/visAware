# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Vivo BlueLM Vision computer-use agent engine."""

from __future__ import annotations

from typing import Any

import addonHandler

from ...engineGUIHelper import BooleanEngineSetting
from ..settings import BaseAgentEngine
from ..vivo import VivoAgentClient, VivoAgentSettings

addonHandler.initTranslation()


class AgentEngine(BaseAgentEngine):
	"""Vivo-backed computer-use agent engine."""

	name = "vivo"
	# Translators: The description of the Vivo Agent engine.
	description = _("Vivo BlueLM Vision (NVDACN)")

	_enableThinking: bool = False

	@property
	def supportedSettings(self) -> list[Any]:
		"""
		Defines the user-configurable settings for this Agent engine.

		:returns: A list of engine setting objects.
		"""
		return [
			self._imageQualitySetting(),
			BooleanEngineSetting(
				name="enableThinking",
				# Translators: The label for enabling model thinking in the Vivo Agent engine.
				displayNameWithAccelerator=_("Enable model &thinking"),
			),
		]

	@property
	def enableThinking(self) -> bool:
		return self._enableThinking

	@enableThinking.setter
	def enableThinking(self, value: bool) -> None:
		self._enableThinking = bool(value)

	@classmethod
	def check(cls) -> bool:
		"""
		Checks whether this Agent engine can be used.

		:returns: True because Vivo is configured through NVDACN settings.
		"""
		return True

	def createClient(self) -> VivoAgentClient:
		"""Creates a Vivo Agent runtime client."""
		return VivoAgentClient(
			VivoAgentSettings(
				imageQuality=self.imageQuality,
				enableThinking=self.enableThinking,
				source="Agent Vivo engine settings",
			),
		)
