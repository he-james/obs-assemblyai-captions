"""Configuration for the transcription engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# Streaming speech models (https://www.assemblyai.com/docs/streaming/select-the-speech-model)
DEFAULT_SPEECH_MODEL = "universal-3-6-pro"
SPEECH_MODELS = {
    "universal-3-6-pro": "Universal-3.6 Pro (recommended, 32 languages)",
    "universal-3-5-pro": "Universal-3.5 Pro",
    "universal-streaming-english": "Universal-Streaming English (word-by-word partials)",
    "universal-streaming-multilingual": "Universal-Streaming Multilingual (en/es/de/fr/pt/it)",
}

# Which models accept which parameters. Parameters sent to a model that
# doesn't support them are ignored by the server with a warning, so we
# only send what applies.
PRO_MODELS = {"universal-3-6-pro", "universal-3-5-pro"}
UNIVERSAL_STREAMING_MODELS = {"universal-streaming-english", "universal-streaming-multilingual"}
MODE_MODELS = {"universal-3-5-pro"}
LANGUAGE_DETECTION_MODELS = PRO_MODELS | {"universal-streaming-multilingual"}

# Streaming endpoints and data zones
REGIONS = {
    "global": "streaming.assemblyai.com",
    "us": "streaming.us.assemblyai.com",
    "eu": "streaming.eu.assemblyai.com",
}


@dataclass
class AudioConfig:
    device: Optional[int] = None
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 100


@dataclass
class TranscriptionConfig:
    api_key: str = ""
    speech_model: str = DEFAULT_SPEECH_MODEL
    region: str = "global"
    # Language
    language_codes: list[str] = field(default_factory=list)  # Pro models: steer toward these languages
    language_detection: bool = False
    # Customization
    keyterms_prompt: list[str] = field(default_factory=list)
    prompt: Optional[str] = None  # Pro models
    domain: Optional[str] = None  # "medical-v1" for Medical Mode
    mode: Optional[str] = None  # 3.5 Pro: "balanced", "min_latency", "max_accuracy"
    # Speakers / audio
    speaker_labels: bool = False
    max_speakers: Optional[int] = None
    voice_focus: Optional[str] = None  # Pro models: "near-field" or "far-field"
    voice_focus_threshold: Optional[float] = None
    # Output filtering
    filter_profanity: bool = False
    redact_pii: bool = False
    redact_pii_sub: Optional[str] = None  # "hash" or "entity_name"
    # Turn detection (None = server default)
    end_of_turn_confidence_threshold: Optional[float] = None  # Universal-Streaming only
    min_turn_silence: Optional[int] = None  # ms
    max_turn_silence: Optional[int] = None  # ms
    vad_threshold: Optional[float] = None


@dataclass
class CaptionConfig:
    mode: str = "subtitle"
    max_words: int = 5  # wordpop: how many words to show at once
    fade_out_seconds: float = 4.0
    max_lines: int = 2
    chars_per_line: int = 40
    show_previous_final: bool = False
    show_speaker_labels: bool = False


def api_key_from_env() -> str:
    return os.environ.get("ASSEMBLYAI_API_KEY", "")


def parse_list(value: str) -> list[str]:
    """Split a comma- or newline-separated settings string into a clean list."""
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
