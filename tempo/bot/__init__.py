"""Telegram bot scaffold (Phase 9 / v1.1) + voice transcription (Phase 10) + sessions (Phase 11) + agent loop (Phase 11-03).

Owner-only Telegram bot that runs locally via long-polling. Phase 9 added the
wiring; Phase 10 layers local faster-whisper transcription (Plan 10-01) and
the voice-memo handler (Plan 10-02) on top; Phase 11 adds the per-chat Claude
Code session-id store (Plan 11-01), the Claude Agent SDK wrapper (Plan 11-02),
and the handler integration that wires voice + text + ``/new`` through the
agent loop (Plan 11-03). The package is import-safe: importing :mod:`tempo.bot`
never starts the bot, never touches the network, never downloads a Whisper
model, never spawns the ``claude`` CLI, and never calls
:func:`logging.basicConfig` -- the model is only loaded when :func:`warm_model`
is called from :func:`tempo.bot.app._post_init` at startup, and the SDK is only
invoked when :func:`run_turn` is called from a handler.

Modules:

* :mod:`tempo.bot.app`        -- :func:`build_application` (PTB ``Application``
  builder gated on the owner chat id, with a ``post_init`` hook that calls
  ``delete_webhook``, runs migrations, and warms the Whisper model; the
  builder also verifies the ``claude`` CLI is on PATH at startup) and
  :func:`run` (the blocking ``run_polling`` entrypoint the CLI wraps).
* :mod:`tempo.bot.handlers`   -- :func:`start_handler` (``/start`` greeting),
  :func:`voice_handler` (owner-only voice-memo intake, post-Phase-11 routed
  through the agent loop), :func:`text_handler` (non-command text messages
  routed through the agent loop), :func:`new_command_handler` (``/new``
  resets the per-chat Claude Code session), and the :data:`MAX_VOICE_BYTES`
  20 MB pre-download guard constant.
* :mod:`tempo.bot.sessions`   -- :func:`get_or_create_session` /
  :func:`save_session` / :func:`reset_session` plus the
  :data:`SESSION_WINDOW_HOURS` 4-hour resume window (VOICE-08; backs the
  ``bot_session`` table added by migration 0005).
* :mod:`tempo.bot.transcribe` -- :func:`warm_model` / :func:`get_model` /
  :func:`transcribe_file`: the module-level WhisperModel singleton and the
  text-from-ogg helper that consumes the segments generator eagerly.
* :mod:`tempo.bot.agent`      -- :class:`AgentTurn` / :class:`AgentInvocationError`
  / :func:`run_turn` / :func:`format_for_telegram`: the Claude Agent SDK
  wrapper (VOICE-07/09/13) that Plan 11-03's handlers compose with the
  session store and warmed transcriber.
"""

from tempo.bot.agent import (
    AgentInvocationError,
    AgentTurn,
    format_for_telegram,
    run_turn,
)
from tempo.bot.app import CLAUDE_CLI_MISSING_ERROR, build_application, run
from tempo.bot.handlers import (
    GREETING,
    MAX_VOICE_BYTES,
    MISSING_CLI_REPLY,
    NEW_SESSION_REPLY,
    new_command_handler,
    start_handler,
    text_handler,
    voice_handler,
)
from tempo.bot.sessions import (
    SESSION_WINDOW_HOURS,
    get_or_create_session,
    reset_session,
    save_session,
)
from tempo.bot.transcribe import get_model, transcribe_file, warm_model

__all__ = [
    "AgentInvocationError",
    "AgentTurn",
    "CLAUDE_CLI_MISSING_ERROR",
    "GREETING",
    "MAX_VOICE_BYTES",
    "MISSING_CLI_REPLY",
    "NEW_SESSION_REPLY",
    "SESSION_WINDOW_HOURS",
    "build_application",
    "format_for_telegram",
    "get_model",
    "get_or_create_session",
    "new_command_handler",
    "reset_session",
    "run",
    "run_turn",
    "save_session",
    "start_handler",
    "text_handler",
    "transcribe_file",
    "voice_handler",
    "warm_model",
]
