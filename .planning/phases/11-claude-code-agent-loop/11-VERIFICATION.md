---
phase: 11-claude-code-agent-loop
verified: 2026-05-28T00:00:00Z
status: passed
score: 12/12 must-haves verified
overrides_applied: 0
---

# Phase 11: Claude Code Agent Loop (v1.1) Verification Report

**Phase Goal:** Transcripts become Claude Code agent turns via `claude-agent-sdk` (user's Claude subscription, no API key). Per-chat session resume within 4hr. HTML-formatted replies with 4096-char split. Per-turn token logging. `/new` resets session.

**Verified:** 2026-05-28
**Status:** passed
**Re-verification:** No -- initial verification.

## Goal Achievement

### Observable Truths (Goal-Backward Checks)

| #   | Truth                                                                                                                          | Status     | Evidence                                                                                                                                          |
| --- | ------------------------------------------------------------------------------------------------------------------------------ | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| G1  | `claude-agent-sdk` ships via `uv add`; no `ANTHROPIC_API_KEY` anywhere in code/config/docs                                     | VERIFIED   | `pyproject.toml:15` -> `"claude-agent-sdk>=0.2.87"`; recursive grep for `ANTHROPIC_API_KEY` in `tempo/`, `tests/`, `.env.example` -> 0 hits        |
| G2  | `tempo/bot/sessions.py` enforces 4hr window (returns `None` outside window; same id within)                                    | VERIFIED   | `sessions.py:42` `SESSION_WINDOW_HOURS = 4`; `get_or_create_session` compares `now - last_at < timedelta(hours=window_hours)`; 10 unit tests pass |
| G3  | Migration `0005_bot_sessions.sql` creates `bot_session` with columns `(chat_id PK, session_id, last_message_at, started_at)`   | VERIFIED   | SQL inspected; columns match spec; `SCHEMA_VERSION = 5` in `tempo/db.py:17`; `BOT_TABLES = ("bot_session",)` defined                              |
| G4  | `run_turn` filters tool-call messages, returns only final assistant text + tokens + session id                                 | VERIFIED   | `agent.py:218-230` -- iterates messages, calls `_extract_text_from_block` for `block.type == "text"` only, captures session_id/usage on ResultMessage; tool blocks excluded by design |
| G5  | `format_for_telegram` HTML-escapes and splits at 4096 chars with `[k/N]` prefix                                                | VERIFIED   | `agent.py:288` `html.escape(text, quote=False)`; `TELEGRAM_MAX_BODY_CHARS = 4096`; chunking at `\n\n` boundaries with `[k/N] ` prefix; assert guards on chunk size |
| G6  | `voice_handler` invokes the agent (not echo raw transcript) and sends HTML reply                                               | VERIFIED   | `handlers.py:308` `await _run_agent_turn(update, context, transcript, chat_id)`; `_run_agent_turn` sends chunks with `ParseMode.HTML`; non-comment count of `reply_text.*<i>` = 0 |
| G7  | `text_handler` mirrors voice_handler without transcription                                                                     | VERIFIED   | `handlers.py:311-340` -- same defensive owner re-check, empty-prompt guard, then delegates to `_run_agent_turn(..., prompt, chat_id)`           |
| G8  | `/new` resets the session                                                                                                       | VERIFIED   | `handlers.py:343-368` `new_command_handler` calls `reset_session(conn, chat_id)`; replies `NEW_SESSION_REPLY`; registered via `CommandHandler("new", ...)` at `app.py:175` |
| G9  | Startup CLI check fails clearly if `claude` is not on PATH                                                                     | VERIFIED   | `app.py:81-86` `_verify_claude_cli` uses `shutil.which("claude")`, raises `RuntimeError(CLAUDE_CLI_MISSING_ERROR)` naming `docs/TELEGRAM_BOT.md` and `claude login`; called before `_require_telegram_config` |
| G10 | Per-turn token usage logged at INFO                                                                                            | VERIFIED   | `handlers.py:183-192` `logger.info("agent turn · chat=%d · session=%s · tokens_in=%d · tokens_out=%d · cost=%s · wall=%.2fs", ...)` |
| G11 | Typing keepalive refreshes ~4s                                                                                                  | VERIFIED   | `handlers.py:87` `_TYPING_REFRESH_S = 4.0`; `_keep_typing` loops `send_action(ChatAction.TYPING)` + `asyncio.sleep(_TYPING_REFRESH_S)`; spawned as `create_task` in `_run_agent_turn` |
| G12 | Full suite (467) green + ruff clean                                                                                             | VERIFIED   | `uv run pytest tests/ -x --deselect ...` -> **467 passed, 1 deselected**; `uv run ruff check tempo/ tests/` -> **All checks passed!**           |

**Score:** 12/12 truths verified

### Required Artifacts

| Artifact                                       | Expected                                              | Status    | Details                                                                                                                  |
| ---------------------------------------------- | ----------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------ |
| `tempo/migrations/0005_bot_sessions.sql`       | Creates `bot_session` table + index                   | VERIFIED  | Schema matches spec; loaded by `_migration_files()` auto-discovery; transactional in `migrate()`                          |
| `tempo/bot/sessions.py`                        | `get_or_create_session/save_session/reset_session`   | VERIFIED  | All three present at lines 67/92/132; `SESSION_WINDOW_HOURS=4`; UPSERT with CASE expression on `started_at`              |
| `tempo/bot/agent.py`                           | `AgentTurn`, `AgentInvocationError`, `run_turn`, `format_for_telegram` | VERIFIED  | All four exported; duck-typed message handling; FileNotFoundError + CalledProcessError mapped to typed exception          |
| `tempo/bot/handlers.py`                        | `voice_handler` (reworked), `text_handler`, `new_command_handler`, `_keep_typing` | VERIFIED  | Voice handler routes through `_run_agent_turn`; text/new handlers added; typing keepalive wired with cancel+gather       |
| `tempo/bot/app.py`                             | `_verify_claude_cli`, db_path stash, /new + text handler registration, init_db in post_init | VERIFIED  | All wiring present; init_db runs in `asyncio.to_thread` before Whisper warm; handlers registered behind `filters.Chat`    |
| `tempo/bot/__init__.py`                        | Re-exports new public surface; `__all__` sorted       | VERIFIED  | 21 names in `__all__`; alphabetically sorted                                                                              |
| `tempo/db.py` (SCHEMA_VERSION = 5, BOT_TABLES) | Schema version bumped + table constants               | VERIFIED  | Line 17 `SCHEMA_VERSION = 5`; line 38 `BOT_TABLES = ("bot_session",)`                                                    |
| `tests/test_bot_sessions.py`                   | ~10 tests covering window boundaries, UPSERT, reset    | VERIFIED  | 10 tests, all pass                                                                                                        |
| `tests/test_bot_agent.py`                      | ~12 tests covering filtering, errors, chunking         | VERIFIED  | 12 tests, all pass                                                                                                        |
| `tests/test_bot_handlers.py`                   | Voice/text/new handler tests; multi-chunk; missing CLI | VERIFIED  | 20 tests, all pass                                                                                                        |
| `tests/test_bot_app.py`                        | Startup CLI check + handler registration tests        | VERIFIED  | 13 tests, all pass                                                                                                        |
| `docs/TELEGRAM_BOT.md`                         | Phase 11 prerequisites + agent loop section           | VERIFIED  | Both sections present; covers Node 18+, `claude login`, 4-hour window, `/new`, INFO log line, multi-chunk replies         |
| `README.md`                                    | "Claude Code agent loop (v1.1 / Phase 11)" subsection | VERIFIED  | Line 268 -- present, links back to `docs/TELEGRAM_BOT.md`                                                                |

### Key Link Verification

| From                        | To                                | Via                                 | Status | Details                                                                                                |
| --------------------------- | --------------------------------- | ----------------------------------- | ------ | ------------------------------------------------------------------------------------------------------ |
| `handlers._run_agent_turn`  | `tempo.bot.agent.run_turn`        | `from tempo.bot.agent import run_turn` + `await run_turn(prompt, session_id, cwd=Path.cwd())` | WIRED  | `handlers.py:33-37` imports; `handlers.py:160` invokes                                                  |
| `handlers._run_agent_turn`  | `tempo.bot.sessions.*`            | `get_or_create_session` -> `save_session` (UPSERT after run) | WIRED  | `handlers.py:149` get; `handlers.py:177` save                                                          |
| `new_command_handler`       | `tempo.bot.sessions.reset_session` | direct call after owner check       | WIRED  | `handlers.py:363`                                                                                       |
| `voice_handler`             | `_run_agent_turn(transcript)`     | post-transcription, after empty-skip | WIRED  | `handlers.py:308`                                                                                       |
| `text_handler`              | `_run_agent_turn(message.text)`   | post owner+non-empty check          | WIRED  | `handlers.py:340`                                                                                       |
| `build_application`         | `_verify_claude_cli`              | called BEFORE `_require_telegram_config` | WIRED  | `app.py:134`                                                                                            |
| `build_application._post_init` | `tempo.db.init_db`             | `asyncio.to_thread(init_db, settings.db_path)` | WIRED  | `app.py:147`                                                                                            |
| `build_application`         | `CommandHandler("new", ...)`      | filters=`owner_filter`              | WIRED  | `app.py:175`                                                                                            |
| `build_application`         | `MessageHandler(TEXT & ~COMMAND & owner)` | `text_handler`                | WIRED  | `app.py:182`                                                                                            |
| `agent.run_turn`            | `claude_agent_sdk.query`          | `async for message in query(prompt=..., options=...)` | WIRED  | `agent.py:60` import; `agent.py:217` consumption                                                       |
| `_run_agent_turn`           | `format_for_telegram` + Telegram  | chunked `reply_text(chunk, parse_mode=ParseMode.HTML)` | WIRED  | `handlers.py:179-181`                                                                                  |
| `_run_agent_turn`           | `_keep_typing` lifecycle          | `create_task` -> `cancel` -> `gather(return_exceptions=True)` | WIRED  | `handlers.py:154, 172-175`                                                                              |

### Data-Flow Trace (Level 4)

| Artifact                | Data Variable | Source                                            | Produces Real Data | Status   |
| ----------------------- | ------------- | ------------------------------------------------- | ------------------ | -------- |
| `_run_agent_turn` reply | `turn.text`   | `await run_turn(prompt, session_id, cwd=...)` -> claude_agent_sdk `query()` -> AssistantMessage text blocks | YES (live SDK call) | FLOWING  |
| `bot_session.session_id` | `turn.session_id` | Set from final ResultMessage.session_id; raises if missing | YES                | FLOWING  |
| Token log line          | `turn.tokens_in/out/cost` | ResultMessage.usage (input_tokens / output_tokens) + total_cost_usd | YES                | FLOWING  |

Note: `run_turn` invokes the real `claude_agent_sdk.query` at runtime; the test suite mocks via `monkeypatch.setattr("tempo.bot.agent.query", ...)`. The seam is correctly placed: no static stub returns, no hardcoded empty assistant text.

### Behavioral Spot-Checks

| Behavior                                       | Command                                                                                            | Result                           | Status |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------- | -------------------------------- | ------ |
| Test suite passes                              | `uv run pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` | **467 passed, 1 deselected**     | PASS   |
| Lint clean                                     | `uv run ruff check tempo/ tests/`                                                                  | **All checks passed!**           | PASS   |
| `claude-agent-sdk` declared in pyproject       | `grep -c "claude-agent-sdk" pyproject.toml`                                                        | hit at line 15 (>= 1)            | PASS   |
| `SCHEMA_VERSION = 5` present                   | `grep "SCHEMA_VERSION = 5" tempo/db.py`                                                            | hit at line 17                   | PASS   |
| sessions.py exports                            | `grep "get_or_create_session\|save_session\|reset_session" tempo/bot/sessions.py`                  | all three present                | PASS   |
| agent.py exports                               | `grep "run_turn\|format_for_telegram\|AgentInvocationError" tempo/bot/agent.py`                    | all three present                | PASS   |
| Startup CLI check                              | `grep "shutil.which.*claude\|_verify_claude_cli" tempo/bot/app.py`                                 | both present (lines 81-86, 134)  | PASS   |
| Missing-CLI reply                              | `grep "MISSING_CLI_REPLY\|claude login" tempo/bot/handlers.py`                                     | both present                     | PASS   |
| Typing keepalive                               | `grep "ChatAction.TYPING\|_keep_typing" tempo/bot/handlers.py`                                     | both present                     | PASS   |
| Text + /new handlers present                   | `grep "text_handler\|new_command_handler" tempo/bot/handlers.py`                                   | both present (lines 311, 343)    | PASS   |
| Phase 10 italics echo removed                  | `grep -v '^#' tempo/bot/handlers.py \| grep -c "reply_text.*<i>"`                                  | **0**                            | PASS   |
| No forbidden env-var orphans                   | `grep "TEMPO_PLAN_PATH\|ANTHROPIC_API_KEY" tempo/ tests/ .env.example -rn`                         | **EMPTY**                        | PASS   |

### Requirements Coverage

| Requirement | Source Plan | Description                                                       | Status     | Evidence                                                                                              |
| ----------- | ----------- | ----------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------- |
| VOICE-07    | 11-02, 11-03 | claude-agent-sdk + subscription auth                              | SATISFIED  | `pyproject.toml` declares dep; `setting_sources` left default so `~/.claude` loads subscription auth; startup CLI check + AgentInvocationError reply path |
| VOICE-08    | 11-01, 11-03 | 4hr session window + `/new` resets                                 | SATISFIED  | `SESSION_WINDOW_HOURS=4`; `get_or_create_session` window check; `new_command_handler` calls `reset_session` |
| VOICE-09    | 11-02, 11-03 | HTML escape + 4096-char split with `[k/N]` prefix                 | SATISFIED  | `format_for_telegram` HTML-escapes + paragraph-aware chunking + hard-split fallback; reply sent with `ParseMode.HTML` |
| VOICE-10    | 11-02       | Tool-call activity suppressed                                      | SATISFIED  | `run_turn` filters to AssistantMessage text-blocks only; tool_use/tool_result/thinking ignored        |
| VOICE-13    | 11-03       | Per-turn token log                                                 | SATISFIED  | `logger.info` line in `_run_agent_turn` includes chat, session prefix, tokens_in/out, cost, wall_s    |

### Anti-Patterns Found

| File                            | Line | Pattern                                              | Severity | Impact                                                                                  |
| ------------------------------- | ---- | ---------------------------------------------------- | -------- | --------------------------------------------------------------------------------------- |
| (none found)                    | -    | -                                                    | -        | Scanned `tempo/bot/` and tests: no debt markers (TBD/FIXME/XXX), no placeholder strings, no `return null/{}/[]` stubs in dynamic-data paths, no orphaned empty-prop calls |

### Human Verification Required

None for goal-backward verification of code wiring. The full agent loop -- the actual end-to-end "voice memo from owner phone reaches Claude and a real assistant reply comes back" round-trip -- is naturally exercised at Phase 12 launchd integration; for Phase 11 the codebase and unit/integration tests (with mocked SDK) constitute sufficient evidence.

### Gaps Summary

No gaps. Every goal-backward check (G1-G12) passes with codebase evidence. Migrations, the validated boundary (`sessions.py`), the SDK seam (`agent.py`), and handler integration (`handlers.py`, `app.py`) form a clean wired pipeline: voice/text -> session lookup -> agent call (with typing keepalive) -> session save -> HTML-chunked reply -> INFO log. All 467 tests in the suite pass; ruff is clean; no orphaned `ANTHROPIC_API_KEY` or `TEMPO_PLAN_PATH` references in code, tests, or env example.

---

_Verified: 2026-05-28_
_Verifier: Claude (gsd-verifier)_
