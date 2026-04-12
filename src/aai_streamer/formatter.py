"""Caption text formatting for OBS text sources."""

from __future__ import annotations

import time
import textwrap

from src.aai_streamer.caption_state import CaptionSnapshot
from src.aai_streamer.config import CaptionConfig


class CaptionFormatter:
    """Converts CaptionSnapshots into display text for an OBS Text source.

    During partials (turn_is_formatted=False): builds display text from the
    words array so each word appears the instant it's recognized.

    When a formatted turn arrives (turn_is_formatted=True): swaps to the
    formatted transcript and holds it on screen for the configured hold
    duration.

    Between turns: keeps showing the last text until the next turn's first
    word arrives, so there's never a blank flash.
    """

    def __init__(self, config: CaptionConfig):
        self._config = config
        self._last_turn_order = -1
        self._turn_end_time: float | None = None
        self._last_text: str = ""  # persists between turns to avoid blanks
        self._previous_final: str = ""  # previous turn's final text
        self._previous_final_time: float | None = None
        self._current_final_text: str = ""  # current turn's final, promoted on next turn

    def format(self, snapshot: CaptionSnapshot) -> str:
        """Return the text to display in the OBS source, or empty string to clear."""
        now = time.monotonic()

        # Nothing has ever been transcribed yet
        if snapshot.is_empty:
            return self._last_text

        # Partial (unformatted) — show live word-by-word text
        if not snapshot.turn_is_formatted:
            # New turn started — save the last final as previous
            if snapshot.turn_order != self._last_turn_order:
                self._save_previous_final(now)
                self._last_turn_order = snapshot.turn_order
            self._turn_end_time = None

            text = self._format_live(snapshot)
            text = self._with_previous_final(text, now)
            self._last_text = text
            return text

        # Formatted turn arrived — show the formatted transcript and start hold timer
        if snapshot.turn_order != self._last_turn_order:
            self._save_previous_final(now)
            self._turn_end_time = now
            self._last_turn_order = snapshot.turn_order
            # Remember this final so it can become the previous later
            self._current_final_text = self._format_final(snapshot)

        # During hold period, show the final formatted text
        if self._turn_end_time is not None:
            elapsed = now - self._turn_end_time
            if elapsed < self._config.fade_out_seconds:
                text = self._format_final(snapshot)
                self._current_final_text = text
                text = self._with_previous_final(text, now)
                self._last_text = text
                return text

            # Hold expired — clear
            self._last_text = ""
            return ""

        # Shouldn't get here, but be safe
        return self._last_text

    def _save_previous_final(self, now: float) -> None:
        """Promote the current final to previous final for display."""
        if not self._config.show_previous_final:
            return
        current = getattr(self, "_current_final_text", "")
        if current:
            self._previous_final = current
            self._previous_final_time = now
            self._current_final_text = ""

    def _with_previous_final(self, current_text: str, now: float) -> str:
        """Prepend the previous final text if enabled and still within hold time."""
        if not self._config.show_previous_final:
            return current_text
        if not self._previous_final or self._previous_final_time is None:
            return current_text
        elapsed = now - self._previous_final_time
        if elapsed >= self._config.fade_out_seconds:
            self._previous_final = ""
            self._previous_final_time = None
            return current_text
        if not current_text:
            return self._previous_final
        return self._previous_final + "\n" + current_text

    def _format_live(self, snapshot: CaptionSnapshot) -> str:
        """Format from the words array for real-time word-by-word display."""
        if self._config.mode == "wordpop":
            return self._format_wordpop(snapshot)

        # Subtitle: build text from words for freshest display
        if snapshot.words:
            text = " ".join(w.text for w in snapshot.words)
        else:
            text = snapshot.transcript

        return self._wrap(text)

    def _format_final(self, snapshot: CaptionSnapshot) -> str:
        """Format the finalized transcript (punctuated/formatted by AAI)."""
        if self._config.mode == "wordpop":
            return self._format_wordpop(snapshot)

        return self._wrap(snapshot.transcript)

    def _wrap(self, text: str) -> str:
        """Word-wrap text to configured max lines."""
        if not text:
            return ""
        lines = textwrap.wrap(text, width=self._config.chars_per_line)
        if len(lines) > self._config.max_lines:
            lines = lines[-self._config.max_lines:]
        return "\n".join(lines)

    def _format_wordpop(self, snapshot: CaptionSnapshot) -> str:
        """Show a window of recent words."""
        words = snapshot.words
        if not words:
            return ""
        visible = words[-self._config.max_words:]
        return " ".join(w.text for w in visible)
