"""Configuration for the transcription engine."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class AudioConfig:
    device: Optional[int] = None
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 100


@dataclass
class TranscriptionConfig:
    api_key: str = ""
    speech_model: str = "u3-rt-pro"
    format_turns: bool = True
    # Advanced WebSocket parameters (None = use server defaults)
    end_of_turn_confidence_threshold: Optional[float] = None
    min_turn_silence: Optional[int] = None  # ms
    max_turn_silence: Optional[int] = None  # ms
    vad_threshold: Optional[float] = None
    filter_profanity: bool = False


@dataclass
class CaptionConfig:
    mode: str = "subtitle"
    max_words: int = 5  # wordpop: how many words to show at once
    fade_out_seconds: float = 4.0
    max_lines: int = 2
    chars_per_line: int = 40


def api_key_from_env() -> str:
    return os.environ.get("ASSEMBLYAI_API_KEY", "")
