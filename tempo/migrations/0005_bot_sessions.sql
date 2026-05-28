-- Migration 0005: bot_session (per-chat Claude Code session-id store) (Phase 11).
--
-- Phases 1-6 built the data layer (raw, structured, spine, journal, wellness).
-- Phase 9 wired the Telegram bot scaffold; Phase 10 added voice transcription.
-- Phase 11 routes every voice memo and text message through Claude Code via
-- claude-agent-sdk, which spawns the `claude` Node CLI as a subprocess. To
-- preserve a multi-turn conversation per chat we persist the resume-able Claude
-- Code session id and the time of the last turn:
--
--   * `bot_session`    -- one row per Telegram chat id. `session_id` is the
--                         opaque Claude Code session uuid we pass to
--                         `ClaudeAgentOptions(resume=<id>)`. `last_message_at`
--                         drives the 4-hour resume window (after which we start
--                         a fresh session); `started_at` records when the
--                         current session began (rotated when session_id flips).
--                         All writes go through tempo.bot.sessions; the schema
--                         is intentionally minimal -- this store can drift
--                         from Claude Code's on-disk session log
--                         (`~/.claude/projects/...`) and that drift is
--                         accepted per 11-CONTEXT.md `<specifics>`
--                         (VOICE-08; 11-CONTEXT.md Implementation Decisions).
--
-- No FK constraint on `chat_id`: the value comes from Telegram, not internal
-- state, so there is nothing to reference. The `daily_summary` view does NOT
-- need rebuilding -- bot_session does not participate in the daily rollup
-- (per 11-CONTEXT.md Migration section).
--
-- Applied in a single transaction by db.migrate(), which then bumps
-- PRAGMA user_version to 5.

-- == BOT SESSION: one row per Telegram chat with the resumable Claude Code id =
-- `chat_id` is Telegram's chat id (a positive integer for 1-on-1 chats). All
-- timestamps are ISO 8601 UTC strings (`datetime.now(UTC).isoformat()`) -- the
-- project convention from tempo.journal.service. The 4-hour resume window is
-- evaluated in Python, not SQL, so the window is a code-side knob (see
-- tempo.bot.sessions). Sessions persist indefinitely until ``/clear``.
CREATE TABLE bot_session (
    chat_id          INTEGER PRIMARY KEY,        -- Telegram chat id (no FK; external)
    session_id       TEXT NOT NULL,              -- opaque Claude Code session uuid
    last_message_at  TEXT NOT NULL,              -- ISO 8601 UTC; drives the resume window
    started_at       TEXT NOT NULL               -- ISO 8601 UTC; reset on a new session_id
);

-- Explicit index for symmetry with prior migrations (ix_wellness_day,
-- ix_journal_day). The PRIMARY KEY already implies an index on chat_id; the
-- explicit index keeps the migration-file shape consistent across phases.
CREATE INDEX ix_bot_session_chat_id ON bot_session(chat_id);
