"""Telegram bot scaffold (Phase 9 / v1.1).

Owner-only Telegram bot that runs locally via long-polling. Phase 9 just adds
the wiring; Phase 10 layers voice-memo transcription and the agent loop on top.
The package is import-safe: importing :mod:`tempo.bot` never starts the bot,
never touches the network, and never calls :func:`logging.basicConfig` --
that only happens inside :func:`tempo.bot.app.run`.

Modules:

* :mod:`tempo.bot.app`      -- :func:`build_application` (PTB ``Application``
  builder gated on the owner chat id, with a ``post_init`` hook that calls
  ``delete_webhook`` to dodge the 409-Conflict pitfall) and :func:`run` (the
  blocking ``run_polling`` entrypoint the CLI wraps).
* :mod:`tempo.bot.handlers` -- :func:`start_handler` and the fixed
  :data:`GREETING` string (the only handler in this plan; Phase 10 adds
  voice + text handlers).
"""

from tempo.bot.app import build_application, run
from tempo.bot.handlers import GREETING, start_handler

__all__ = [
    "GREETING",
    "build_application",
    "run",
    "start_handler",
]
