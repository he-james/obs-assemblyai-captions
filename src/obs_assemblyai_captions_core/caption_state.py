"""Thread-safe caption state shared between transcription and OBS timer threads."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class WordInfo:
    text: str
    start_ms: int
    end_ms: int
    is_final: bool
    confidence: float = 1.0


@dataclass(frozen=True)
class CaptionSnapshot:
    """Immutable snapshot of current caption state."""

    transcript: str = ""
    words: tuple[WordInfo, ...] = ()
    turn_is_formatted: bool = False
    end_of_turn: bool = False
    turn_order: int = 0
    speaker_label: Optional[str] = None
    language_code: Optional[str] = None
    timestamp: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.transcript

    @property
    def is_final(self) -> bool:
        """True for the finished, formatted transcript of a turn.

        Universal-Streaming sends an unformatted end-of-turn message before
        the formatted one, and the multilingual model formats its partials,
        so neither flag is enough on its own.
        """
        return self.end_of_turn and self.turn_is_formatted


class CaptionState:
    """Thread-safe mutable state updated by the transcription thread.

    Lock held only for pointer swap (nanoseconds).
    OBS timer callback reads snapshot via .get() without contention.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = CaptionSnapshot()

    def update(
        self,
        transcript: str,
        words: list[WordInfo],
        turn_is_formatted: bool,
        end_of_turn: bool,
        turn_order: int,
        speaker_label: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> None:
        snap = CaptionSnapshot(
            transcript=transcript,
            words=tuple(words),
            turn_is_formatted=turn_is_formatted,
            end_of_turn=end_of_turn,
            turn_order=turn_order,
            speaker_label=speaker_label,
            language_code=language_code,
            timestamp=time.monotonic(),
        )
        with self._lock:
            self._snapshot = snap

    def get(self) -> CaptionSnapshot:
        with self._lock:
            return self._snapshot
