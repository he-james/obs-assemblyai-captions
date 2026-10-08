"""AssemblyAI v3 streaming transcription provider."""

from __future__ import annotations

import logging
import threading

from assemblyai.streaming.v3 import (
    BeginEvent,
    RealTimeError,
    RealTimeEvents,
    RealTimeParameters,
    RealTimeSessionParameters,
    RealTimeTranscriber,
    RealTimeTranscriberOptions,
    TerminationEvent,
    TurnEvent,
    WarningEvent,
)

from .caption_state import CaptionState, WordInfo
from .config import (
    LANGUAGE_DETECTION_MODELS,
    MODE_MODELS,
    PRO_MODELS,
    REGIONS,
    UNIVERSAL_STREAMING_MODELS,
    AudioConfig,
    TranscriptionConfig,
)
from .microphone import MicrophoneSource

log = logging.getLogger(__name__)


def build_parameters(config: TranscriptionConfig, sample_rate: int) -> RealTimeParameters:
    """Build connection parameters, sending only options the model supports."""
    model = config.speech_model
    params: dict = dict(
        sample_rate=sample_rate,
        speech_model=model,
        **session_parameters(config),
    )

    if model in UNIVERSAL_STREAMING_MODELS:
        # Pro models always format turns; Universal-Streaming needs to opt in.
        params["format_turns"] = True
    if config.language_detection and model in LANGUAGE_DETECTION_MODELS:
        params["language_detection"] = True
    if config.mode and model in MODE_MODELS:
        params["mode"] = config.mode
    if config.domain:
        params["domain"] = config.domain
    if config.speaker_labels:
        params["speaker_labels"] = True
        if config.max_speakers:
            params["max_speakers"] = config.max_speakers
    if config.voice_focus and model in PRO_MODELS:
        params["voice_focus"] = config.voice_focus
        if config.voice_focus_threshold is not None:
            params["voice_focus_threshold"] = config.voice_focus_threshold
    if config.filter_profanity:
        params["filter_profanity"] = True
    if config.redact_pii:
        params["redact_pii"] = True
        # PII redaction turns partials off by default; captions need them.
        params["include_partial_turns"] = True
        if config.redact_pii_sub:
            params["redact_pii_sub"] = config.redact_pii_sub

    return RealTimeParameters(**params)


def session_parameters(config: TranscriptionConfig) -> dict:
    """Options that can be set at connect time and changed mid-stream."""
    model = config.speech_model
    params: dict = {}

    if config.keyterms_prompt:
        params["keyterms_prompt"] = config.keyterms_prompt
    if model in PRO_MODELS:
        if config.prompt:
            params["prompt"] = config.prompt
        if config.language_codes:
            params["language_codes"] = config.language_codes
    if config.end_of_turn_confidence_threshold is not None and model in UNIVERSAL_STREAMING_MODELS:
        params["end_of_turn_confidence_threshold"] = config.end_of_turn_confidence_threshold
    if config.min_turn_silence is not None:
        params["min_turn_silence"] = config.min_turn_silence
    if config.max_turn_silence is not None:
        params["max_turn_silence"] = config.max_turn_silence
    if config.vad_threshold is not None:
        params["vad_threshold"] = config.vad_threshold

    return params


class TranscriptionEngine:
    """Manages mic capture + AssemblyAI streaming on a background thread."""

    def __init__(
        self,
        transcription_config: TranscriptionConfig,
        audio_config: AudioConfig,
    ):
        self._tx_config = transcription_config
        self._audio_config = audio_config
        self._mic: MicrophoneSource | None = None
        self._client: RealTimeTranscriber | None = None
        self._thread: threading.Thread | None = None
        self._caption_state: CaptionState | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, caption_state: CaptionState) -> None:
        if self._running:
            return
        self._caption_state = caption_state
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="obs-assemblyai-captions-transcription", daemon=True
        )
        self._thread.start()

    def update_configuration(self, config: TranscriptionConfig) -> None:
        """Send mid-stream updates (keyterms, prompt, languages, turn detection)."""
        if not self._running or self._client is None:
            log.warning("Captions are not running; nothing to update")
            return
        # The speech model can't change mid-stream, so keep the session's.
        config.speech_model = self._tx_config.speech_model
        params = session_parameters(config)
        if config.speech_model in PRO_MODELS:
            # Empty lists clear the previous keyterms / language steering.
            params.setdefault("keyterms_prompt", [])
            params.setdefault("language_codes", [])
        elif "keyterms_prompt" in params:
            # Universal-Streaming only accepts keyterms at connect time.
            del params["keyterms_prompt"]
            log.warning("Keyterms can't be changed mid-stream on %s; restart captions", config.speech_model)
        if not params:
            log.info("No updatable settings to send")
            return
        self._client.set_params(RealTimeSessionParameters(**params))
        self._tx_config = config
        log.info("Sent configuration update: %s", ", ".join(sorted(params)))

    def force_endpoint(self) -> None:
        """End the current turn immediately."""
        if self._running and self._client is not None:
            self._client.force_endpoint()

    def _run(self) -> None:
        self._mic = MicrophoneSource(self._audio_config)
        try:
            self._mic.open()
        except Exception:
            log.exception("Failed to open microphone")
            self._running = False
            return

        try:
            params = build_parameters(self._tx_config, self._audio_config.sample_rate)
        except Exception:
            log.exception("Invalid transcription settings")
            self._mic.close()
            self._running = False
            return

        options = RealTimeTranscriberOptions(
            api_key=self._tx_config.api_key,
            api_host=REGIONS.get(self._tx_config.region, REGIONS["global"]),
        )
        self._client = RealTimeTranscriber(options)

        self._client.on(RealTimeEvents.Begin, self._on_begin)
        self._client.on(RealTimeEvents.Turn, self._on_turn)
        self._client.on(RealTimeEvents.Warning, self._on_warning)
        self._client.on(RealTimeEvents.Error, self._on_error)
        self._client.on(RealTimeEvents.Termination, self._on_termination)

        log.info("Connecting to AssemblyAI streaming (%s, %s)...", params.speech_model, options.api_host)
        try:
            self._client.connect(params)
        except Exception:
            log.exception("Failed to connect to AssemblyAI")
            self._mic.close()
            self._running = False
            return

        log.info("Streaming audio...")
        try:
            self._client.stream(self._mic)
        except Exception:
            if self._running:
                log.exception("Audio streaming error")

        try:
            self._client.disconnect(terminate=True)
        except Exception:
            pass
        self._mic.close()
        self._running = False
        log.info("Transcription session ended")

    def _on_begin(self, client: RealTimeTranscriber, event: BeginEvent) -> None:
        model = event.configuration.model if event.configuration else None
        log.info("Transcription session started (id=%s, model=%s)", event.id, model)

    def _on_turn(self, client: RealTimeTranscriber, event: TurnEvent) -> None:
        words = [
            WordInfo(
                text=w.text,
                start_ms=w.start,
                end_ms=w.end,
                is_final=w.word_is_final,
                confidence=w.confidence,
            )
            for w in event.words
        ]

        log.debug(
            "Turn %d (formatted=%s, end=%s, %d words): %s",
            event.turn_order,
            event.turn_is_formatted,
            event.end_of_turn,
            len(words),
            event.transcript[:80],
        )
        if event.end_of_turn and event.language_code:
            log.info(
                "Turn %d language: %s (%.2f)",
                event.turn_order,
                event.language_code,
                event.language_confidence or 0.0,
            )

        if self._caption_state is not None:
            self._caption_state.update(
                transcript=event.transcript,
                words=words,
                turn_is_formatted=event.turn_is_formatted,
                end_of_turn=event.end_of_turn,
                turn_order=event.turn_order,
                speaker_label=event.speaker_label,
                language_code=event.language_code,
            )

    def _on_warning(self, client: RealTimeTranscriber, event: WarningEvent) -> None:
        log.warning("AssemblyAI warning %s: %s", event.warning_code, event.warning)

    def _on_error(self, client: RealTimeTranscriber, error: RealTimeError) -> None:
        log.error("Transcription error: %s (code=%s)", error, error.code)

    def _on_termination(self, client: RealTimeTranscriber, event: TerminationEvent) -> None:
        log.info(
            "Transcription session terminated (%ss of audio)",
            event.audio_duration_seconds,
        )

    def stop(self) -> None:
        self._running = False
        if self._mic is not None:
            self._mic.close()
        if self._client is not None:
            try:
                self._client.disconnect(terminate=True)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        log.info("Transcription engine stopped")
