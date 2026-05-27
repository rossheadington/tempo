"""Telegram bot scaffold (Phase 9 / v1.1) + voice transcription (Phase 10).

Owner-only Telegram bot that runs locally via long-polling. Phase 9 added the
wiring; Phase 10 layers local faster-whisper transcription (this plan, 10-01)
and the voice-handler + agent loop (plans 10-02 / 11) on top. The package is
import-safe: importing :mod:`tempo.bot` never starts the bot, never touches
the network, never downloads a Whisper model, and never calls
:func:`logging.basicConfig` -- the model is only loaded when
:func:`warm_model` is called from :func:`tempo.bot.app._post_init` at startup.

Modules:

* :mod:`tempo.bot.app`        -- :func:`build_application` (PTB ``Application``
  builder gated on the owner chat id, with a ``post_init`` hook that calls
  ``delete_webhook`` and warms the Whisper model) and :func:`run` (the
  blocking ``run_polling`` entrypoint the CLI wraps).
* :mod:`tempo.bot.handlers`   -- :func:`start_handler` and the fixed
  :data:`GREETING` string (Phase 10's voice + text handlers land in
  Plan 10-02).
* :mod:`tempo.bot.transcribe` -- :func:`warm_model` / :func:`get_model` /
  :func:`transcribe_file`: the module-level WhisperModel singleton and the
  text-from-ogg helper that consumes the segments generator eagerly.
"""

from tempo.bot.app import build_application, run
from tempo.bot.handlers import GREETING, start_handler
from tempo.bot.transcribe import get_model, transcribe_file, warm_model

__all__ = [
    "GREETING",
    "build_application",
    "get_model",
    "run",
    "start_handler",
    "transcribe_file",
    "warm_model",
]
