"""Warmed faster-whisper singleton + the ``transcribe_file`` helper.

This module owns the **one** :class:`faster_whisper.WhisperModel` instance per
bot process. It is loaded ONCE by :func:`warm_model` -- called from the
:func:`runos.bot.app._post_init` hook so the first voice memo does not pay
the multi-second model load (VOICE-06 warmup requirement).

NOT thread-safe by design: faster-whisper's :meth:`WhisperModel.transcribe`
holds the model state internally. Voice memos arrive sequentially per user, so
single-threaded serial use is fine; if the bot ever grows concurrent transcribe
demand, wrap calls in :func:`asyncio.to_thread` (Plan 10-02 already does for
``transcribe_file`` itself).

**The most-reported faster-whisper foot-gun (research.md "Pitfalls"):**
``model.transcribe()`` returns ``(segments, info)`` where ``segments`` is a
*generator* -- inference does NOT run until it is iterated. Returning ``info``
without consuming the generator silently produces an empty transcript. The
:func:`transcribe_file` helper iterates eagerly so callers cannot trip on this.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from faster_whisper import WhisperModel

from runos.config import Settings

logger = logging.getLogger(__name__)

# Module-level singleton. Populated by ``warm_model``; read by ``get_model``.
_MODEL: WhisperModel | None = None
_MODEL_NAME: str | None = None


def warm_model(settings: Settings) -> WhisperModel:
    """Load the :class:`WhisperModel` singleton (idempotent).

    Constructs the model from ``settings.whisper_model_name`` /
    ``whisper_device`` / ``whisper_compute_type`` with ``cpu_threads=4`` to
    cap thread oversubscription on M-series CPUs (research.md "Pitfalls").
    Logs the wall-clock load time so the startup log line provides the
    VOICE-06 "verified by a log line on startup" evidence.

    Second and later calls are no-ops: they return the already-loaded
    instance without touching faster-whisper. Tests can assert this by
    monkey-patching :class:`WhisperModel` in this module and counting
    constructor calls.

    Args:
        settings: RunOS settings (provides the three whisper_* fields).

    Returns:
        The loaded :class:`WhisperModel` singleton.
    """
    global _MODEL, _MODEL_NAME
    if _MODEL is not None:
        return _MODEL
    start = time.monotonic()
    _MODEL = WhisperModel(
        settings.whisper_model_name,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        cpu_threads=4,
    )
    _MODEL_NAME = settings.whisper_model_name
    logger.info(
        "Whisper model warmed -- model=%s device=%s compute=%s load_s=%.2f",
        settings.whisper_model_name,
        settings.whisper_device,
        settings.whisper_compute_type,
        time.monotonic() - start,
    )
    return _MODEL


def get_model() -> WhisperModel:
    """Return the warmed singleton, or raise :class:`RuntimeError`.

    Raised before :func:`warm_model` has been called so that a missing
    startup-warmup is a loud, immediate failure rather than a per-memo
    cold-start tax. Plan 10-02's voice handler relies on this contract.
    """
    if _MODEL is None:
        raise RuntimeError("WhisperModel not warmed -- call warm_model(settings) at startup first")
    return _MODEL


def transcribe_file(path: Path) -> str:
    """Transcribe a local ``.ogg`` / ``.oga`` voice file to plain text.

    Calls the warmed singleton with ``language="en"``, ``beam_size=5``,
    ``vad_filter=True`` (the LOCKED Phase 10 choices). The segments
    generator is iterated eagerly here so callers cannot trip the
    faster-whisper "you forgot to consume segments" foot-gun.

    Args:
        path: Path to a local audio file. faster-whisper bundles PyAV
            which decodes Opus-in-OGG natively -- no system ffmpeg required.

    Returns:
        Joined, single-spaced transcript text. May be an empty string if
        VAD filtered out everything (silence / pure tone) -- callers should
        treat empty as a real outcome, not an error.
    """
    model = get_model()
    segments, _info = model.transcribe(
        str(path),
        language="en",
        beam_size=5,
        vad_filter=True,
    )
    # CRITICAL: segments is a generator. Iterate eagerly or inference never
    # runs (the most-reported faster-whisper foot-gun -- see research.md
    # Pitfalls and Plan 10-CONTEXT "Transcription function shape").
    parts: list[str] = [seg.text for seg in segments]
    return " ".join(p.strip() for p in parts if p.strip())


def _reset_for_tests() -> None:
    """Drop the module-level singleton.

    Tests use this in a teardown fixture so the warmed ``_MODEL`` does not
    leak between tests. Production code never calls this.
    """
    global _MODEL, _MODEL_NAME
    _MODEL = None
    _MODEL_NAME = None
