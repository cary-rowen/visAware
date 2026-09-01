# Copyright (C) 2026 Cary-rowen <cary-rowen@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Conversation context helpers for follow-up questions."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import addonHandler

from .exceptions import ApiError

addonHandler.initTranslation()

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


@dataclass
class ConversationTurn:
	"""One turn in a follow-up conversation."""

	role: str
	text: str
	response: dict[str, Any] | None = None


@dataclass(frozen=True)
class QuestionStreamText:
	"""One text fragment from a streaming follow-up answer."""

	text: str
	replace: bool = False


@dataclass(frozen=True)
class QuestionStreamFinished:
	"""The completed follow-up answer from a streaming or non-streaming engine."""

	text: str
	incompleteReason: str | None = None
	response: dict[str, Any] | None = None


QuestionStreamEvent = QuestionStreamText | QuestionStreamFinished


@dataclass
class ConversationContext:
	"""State needed to ask follow-up questions about one recognition result."""

	engine: Any
	image: Any
	response: dict[str, Any]
	initialText: str
	engineName: str
	engineDescription: str
	turns: list[ConversationTurn] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)

	def addExchange(
		self,
		question: str,
		answer: str,
		response: dict[str, Any] | None = None,
	) -> None:
		"""
		Appends a completed question-answer exchange.

		:param question: The user's question.
		:param answer: The engine's answer.
		:param response: The provider response for the assistant turn, when available.
		"""
		self.turns.append(ConversationTurn(ROLE_USER, question))
		self.turns.append(
			ConversationTurn(
				ROLE_ASSISTANT,
				answer,
				copy.deepcopy(response) if isinstance(response, dict) else None,
			),
		)


def makeConversationContext(historyEntry: dict[str, Any]) -> ConversationContext:
	"""
	Creates a follow-up conversation context from a recognition history entry.

	:param historyEntry: A dictionary produced by recogHistory.addEntry.
	:returns: A context suitable for AskQuestionFrame.
	:raises ApiError: If the history entry cannot support follow-up questions.
	"""
	engine = historyEntry.get("engine")
	if not engine or not getattr(engine, "supportsQuestions", False):
		# Translators: Reported when the previous recognition engine cannot answer follow-up questions.
		raise ApiError(_("The previous recognition engine does not support follow-up questions."))
	response = historyEntry.get("response")
	if not isinstance(response, dict):
		# Translators: Reported when the saved recognition result cannot be reused for follow-up questions.
		raise ApiError(_("The previous recognition result cannot be used for follow-up questions."))
	try:
		contextEngine = copy.copy(engine)
	except Exception:
		contextEngine = engine
	try:
		initialText = contextEngine.extractText(response).strip()
	except Exception as e:
		# Translators: Reported when the saved recognition result cannot be converted to text.
		raise ApiError(_("The previous recognition result cannot be converted to text.")) from e
	if not initialText:
		# Translators: Reported when there is no text in the previous result to use as conversation context.
		raise ApiError(_("The previous recognition result is blank."))

	image = historyEntry.get("image")
	if hasattr(image, "copy"):
		image = image.copy()

	return ConversationContext(
		engine=contextEngine,
		image=image,
		response=copy.deepcopy(response),
		initialText=initialText,
		engineName=getattr(contextEngine, "name", ""),
		engineDescription=getattr(contextEngine, "description", ""),
	)
