"""Sounddevice microphone capture with queue-based iterator."""

from __future__ import annotations

import logging
import queue
from typing import Iterator

import numpy as np
import sounddevice as sd

from src.aai_streamer.config import AudioConfig

log = logging.getLogger(__name__)


def list_microphones() -> list[dict]:
    """Return a list of input devices with index, name, channels, and sample rate."""
    devices = sd.query_devices()
    result = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            result.append(
                {
                    "index": i,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "sample_rate": int(dev["default_samplerate"]),
                    "is_default": i == sd.default.device[0],
                }
            )
    return result


class MicrophoneSource:
    def __init__(self, config: AudioConfig):
        self._config = config
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._stopped = False

    def _callback(self, indata: np.ndarray, frames: int, time_info, status):
        if status:
            log.warning("Audio status: %s", status)
        self._queue.put(bytes(indata))

    def open(self) -> None:
        chunk_samples = int(
            self._config.sample_rate * self._config.chunk_duration_ms / 1000
        )
        self._stream = sd.InputStream(
            samplerate=self._config.sample_rate,
            channels=self._config.channels,
            dtype="int16",
            blocksize=chunk_samples,
            device=self._config.device,
            callback=self._callback,
        )
        self._stream.start()
        self._stopped = False
        log.info(
            "Microphone opened: device=%s, %d Hz, %d ch",
            self._config.device,
            self._config.sample_rate,
            self._config.channels,
        )

    def close(self) -> None:
        self._stopped = True
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            log.info("Microphone closed")

    def __iter__(self) -> Iterator[bytes]:
        while not self._stopped:
            try:
                chunk = self._queue.get(timeout=0.5)
                yield chunk
            except queue.Empty:
                continue
