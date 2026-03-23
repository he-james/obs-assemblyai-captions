"""Thread-safe caption state shared between transcription and OBS timer threads."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


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
    end_of_turn: bool = False
    turn_order: int = 0
    timestamp: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.transcript


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
        end_of_turn: bool,
        turn_order: int,
    ) -> None:
        snap = CaptionSnapshot(
            transcript=transcript,
            words=tuple(words),
            end_of_turn=end_of_turn,
            turn_order=turn_order,
            timestamp=time.monotonic(),
        )
        with self._lock:
            self._snapshot = snap

    def get(self) -> CaptionSnapshot:
        with self._lock:
            return self._snapshot
