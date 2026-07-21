# Copyright (C) 2025 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Manages a history of recognition results."""

from dataclasses import dataclass
from typing import Any
from logHandler import log

_HISTORY: list[dict[str, Any]] = []
MAX_HISTORY: int = 1
_RESULT_HISTORY_ATTR = "_visAwareHistoryEntry"


@dataclass(frozen=True)
class HistoryEntryPayload:
	"""Recognition history data captured by a worker thread."""

	engine: Any
	image: Any
	response: Any

	def asDict(self) -> dict[str, Any]:
		"""Returns the dictionary shape consumed by history callers."""
		return {
			"engine": self.engine,
			"image": self.image,
			"response": self.response,
		}


def createEntry(engine: Any, image: Any, response: Any) -> HistoryEntryPayload:
	"""
	Creates a pending recognition history entry.

	The caller should add this to the global history only after the recognition
	result is accepted on the main thread.
	"""
	return HistoryEntryPayload(engine, image, response)


def attachEntry(result: Any, entry: HistoryEntryPayload | None) -> Any:
	"""Attaches a pending history entry to a recognition result."""
	if entry is not None:
		setattr(result, _RESULT_HISTORY_ATTR, entry)
	return result


def getAttachedEntry(result: Any) -> HistoryEntryPayload | None:
	"""Returns a pending history entry attached to a recognition result."""
	return getattr(result, _RESULT_HISTORY_ATTR, None)


def _addDisplayData(historyEntry: dict[str, Any], result: Any | None, text: str | None) -> None:
	"""Adds reusable display data from an accepted recognition result."""
	if result is not None:
		if callable(getattr(result, "makeTextInfo", None)):
			historyEntry["result"] = result
		resultText = getattr(result, "text", None)
		if isinstance(resultText, str):
			historyEntry["text"] = resultText
		lineResult = getattr(result, "data", None)
		imageInfo = getattr(result, "imageInfo", None)
		if lineResult is not None and imageInfo is not None:
			historyEntry["lineResult"] = lineResult
			historyEntry["imageInfo"] = imageInfo
		if getattr(result, "forceVirtualDocument", False):
			historyEntry["forceVirtualDocument"] = True
	if text is not None:
		historyEntry["text"] = text


def addEntry(entry: HistoryEntryPayload, result: Any | None = None, text: str | None = None) -> None:
	"""
	Adds a recognition result to the history.

	The history is capped at `MAX_HISTORY` entries.

	:param entry: Pending history data captured while producing an accepted result.
	:param result: Optional accepted result object used to replay the previous result.
	:param text: Optional text result for accepted streaming recognitions.
	"""
	historyEntry = entry.asDict()
	_addDisplayData(historyEntry, result, text)
	_HISTORY.append(historyEntry)
	if len(_HISTORY) > MAX_HISTORY:
		_HISTORY.pop(0)  # pyright: ignore[reportUnusedCallResult]
	log.debug(f"recogHistory: Entry added. History size is now {len(_HISTORY)}")


def getPreviousResult() -> dict[str, Any] | None:
	"""
	Retrieves the most recent recognition result from history.

	:returns: The last result dictionary, or None if history is empty.
	"""
	return _HISTORY[-1] if _HISTORY else None
