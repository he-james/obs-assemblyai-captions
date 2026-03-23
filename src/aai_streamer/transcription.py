"""AssemblyAI v3 streaming transcription provider."""

from __future__ import annotations

import logging
import threading

from assemblyai.streaming.v3 import (
    SpeechModel,
    StreamingClient,
    StreamingClientOptions,
    StreamingEvents,
    StreamingParameters,
    TurnEvent,
)

from src.aai_streamer.caption_state import CaptionState, WordInfo
from src.aai_streamer.config import AudioConfig, TranscriptionConfig
from src.aai_streamer.microphone import MicrophoneSource

log = logging.getLogger(__name__)


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
        self._client: StreamingClient | None = None
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
            target=self._run, name="aai-transcription", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        self._mic = MicrophoneSource(self._audio_config)
        try:
            self._mic.open()
        except Exception:
            log.exception("Failed to open microphone")
            self._running = False
            return

        options = StreamingClientOptions(api_key=self._tx_config.api_key)
        self._client = StreamingClient(options)

        self._client.on(StreamingEvents.Turn, self._on_turn)
        self._client.on(StreamingEvents.Error, self._on_error)
        self._client.on(StreamingEvents.Begin, self._on_begin)
        self._client.on(StreamingEvents.Termination, self._on_termination)

        speech_model = SpeechModel(self._tx_config.speech_model)

        # Build params, only including advanced options that are set
        params_kwargs = dict(
            sample_rate=self._audio_config.sample_rate,
            format_turns=self._tx_config.format_turns,
            speech_model=speech_model,
        )
        if self._tx_config.end_of_turn_confidence_threshold is not None:
            params_kwargs["end_of_turn_confidence_threshold"] = self._tx_config.end_of_turn_confidence_threshold
        if self._tx_config.min_turn_silence is not None:
            params_kwargs["min_turn_silence"] = self._tx_config.min_turn_silence
        if self._tx_config.max_turn_silence is not None:
            params_kwargs["max_turn_silence"] = self._tx_config.max_turn_silence
        if self._tx_config.vad_threshold is not None:
            params_kwargs["vad_threshold"] = self._tx_config.vad_threshold
        if self._tx_config.filter_profanity:
            params_kwargs["filter_profanity"] = True

        params = StreamingParameters(**params_kwargs)

        log.info("Connecting to AssemblyAI streaming...")
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

    def _on_begin(self, client: StreamingClient, event) -> None:
        log.info("Transcription session started")

    def _on_turn(self, client: StreamingClient, event: TurnEvent) -> None:
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

        log.info(
            "Turn %d (final=%s, %d words): %s",
            event.turn_order,
            event.end_of_turn,
            len(words),
            event.transcript[:80],
        )

        if self._caption_state is not None:
            self._caption_state.update(
                transcript=event.transcript,
                words=words,
                end_of_turn=event.end_of_turn,
                turn_order=event.turn_order,
            )

    def _on_error(self, client: StreamingClient, event) -> None:
        log.error("Transcription error: %s", event)

    def _on_termination(self, client: StreamingClient, event) -> None:
        log.info("Transcription session terminated")

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
