---
phase: 11-claude-code-agent-loop
plan: 03
subsystem: bot
tags: [bot, handlers, agent, sessions, telegram, typing-indicator, docs]
requires:
  - runos.bot.sessions (Plan 11-01: get_or_create_session / save_session / reset_session, SESSION_WINDOW_HOURS)
  - runos.bot.agent (Plan 11-02: run_turn, AgentTurn, AgentInvocationError, format_for_telegram)
  - runos.bot.transcribe (Phase 10: warmed singleton, transcribe_file)
  - runos.db (connect, init_db, SCHEMA_VERSION=5)
  - python-telegram-bot v22 (Application, MessageHandler, CommandHandler, filters)
provides:
  - voice_handler reworked: post-transcription path -> agent loop (raw transcript echo REMOVED)
  - text_handler (new): non-command text -> agent loop
  - new_command_handler (new): /new resets the per-chat session
  - _keep_typing helper: 4s-refresh typing indicator while run_turn is in flight
  - MISSING_CLI_REPLY, NEW_SESSION_REPLY, EMPTY_TRANSCRIPT_REPLY module-level constants
  - build_application: _verify_claude_cli startup check + text/new handler registration + db_path stash + init_db in post_init
  - CLAUDE_CLI_MISSING_ERROR canonical message (re-exported from runos.bot)
  - docs/TELEGRAM_BOT.md "Phase 11: the agent loop" section
  - README.md "Claude Code agent loop (v1.1 / Phase 11)" subsection
affects:
  - Plan 12-XX (lifecycle): will add the top-level error boundary around handlers
    (this plan deliberately lets non-AgentInvocationError exceptions propagate)
  - Plan 12-XX (launchd plist): WorkingDirectory must match Path.cwd() expectation
    for the agent's `cwd` argument
tech-stack:
  added: []
  patterns:
    - "Handler -> validated boundary -> Claude Agent SDK composition: handlers
      open short-lived sqlite connections via runos.db.connect, route through
      runos.bot.sessions (validated boundary for bot_session writes), then call
      runos.bot.agent.run_turn (the only seam to claude_agent_sdk)."
    - "asyncio.create_task + cancel + gather(return_exceptions=True) for the
      typing keepalive lifecycle: absorbs the CancelledError so it never
      surfaces as 'Task exception was never retrieved'."
    - "AgentInvocationError as a single typed exception mapped to a single
      canonical user-facing string (MISSING_CLI_REPLY); all other exceptions
      propagate to PTB's default handler per 11-CONTEXT.md error-handling
      LIGHT decision."
    - "Mock-patch handler-module-rebound names (runos.bot.handlers.run_turn
      etc.) NOT the source module -- the handler does `from ... import ...`
      so the local name is the patch target."
key-files:
  created: []
  modified:
    - runos/bot/handlers.py (voice_handler reworked + text_handler + new_command_handler + _keep_typing + _run_agent_turn shared helper + 4 reply constants)
    - runos/bot/app.py (_verify_claude_cli + CLAUDE_CLI_MISSING_ERROR + text/new handler registration + db_path stash + init_db in post_init)
    - runos/bot/__init__.py (re-export new handlers + reply constants + CLAUDE_CLI_MISSING_ERROR; sorted __all__)
    - tests/test_bot_handlers.py (2 existing tests updated + 13 new tests)
    - tests/test_bot_app.py (5 new tests for the claude CLI check + handler registration)
    - docs/TELEGRAM_BOT.md ("Phase 11: the agent loop" section after prerequisites)
    - README.md ("Claude Code agent loop (v1.1 / Phase 11)" subsection)
decisions:
  - "Path.cwd() is passed to run_turn at handler call time (NOT a cached
    application-level constant). Phase 12's launchd plist will set
    WorkingDirectory to the RunOS project root; until then `runos bot run`
    is launched from the repo root by convention. Documented inline."
  - "Per-call short-lived sqlite connections via runos.db.connect(db_path)
    inside a try/finally that closes the conn -- no pooling. Matches the
    rest of the project's convention (journal service, sync writes)."
  - "EMPTY_TRANSCRIPT_REPLY short-circuits before any agent call: Whisper
    detecting no speech is not worth a Claude Code turn. Preserves the
    Phase 10 '(no speech detected)' italics shape so users see the pipeline
    ran."
  - "Tool-call suppression (VOICE-10) is delegated to Plan 11-02's run_turn
    (already filters to AssistantMessage text blocks). Handler does NOT
    re-filter -- it trusts turn.text. Comment in voice_handler references
    the Plan 11-02 contract."
  - "init_db in _post_init (idempotent) so a fresh checkout without prior
    `runos init` still has migration 0005 (bot_session table) applied
    before the first handler runs. Run in asyncio.to_thread because
    init_db opens a sqlite connection that blocks."
  - "_verify_claude_cli runs BEFORE _require_telegram_config so the user
    fixes the CLI prerequisite first (no point asking for a Telegram
    token if claude isn't installed)."
  - "AgentInvocationError reply uses a backtick-free string
    ('Try claude login in a terminal.') so HTML quoting is trivial -- no
    need for &#96; entity escaping in the canonical reply."
  - "Existing Phase 10 escape test repurposed (not deleted) to assert the
    agent reply (now the untrusted source of HTML-special chars) is
    escaped via format_for_telegram. The transcript escape contract is
    GONE because the transcript no longer reaches Telegram."
metrics:
  duration_minutes: 35
  completed: 2026-05-27
  tasks_complete: 4
  files_modified: 7
  tests_added: 18  # 13 new in test_bot_handlers + 5 new in test_bot_app
  tests_updated: 2  # voice_handler happy path + html-escape repurposed
  baseline_tests: 450
  total_tests: 468
---

# Phase 11 Plan 03: Handler Integration -- Voice + Text + /new + Typing Keepalive + Startup CLI Check Summary

Wires Plan 11-01's session-id store and Plan 11-02's Claude Agent SDK
wrapper into the actual Telegram pipeline. After this plan, a voice memo
or non-command text message from the owner chat is transcribed (voice)
or forwarded directly (text), routed to Claude Code via `claude-agent-sdk`,
and the final assistant reply is sent back to Telegram as HTML
(possibly split across the 4096-char cap). The Phase 10 raw-transcript
echo is REMOVED. `/new` clears the per-chat session id on demand. While
the agent runs the chat shows the typing indicator. `runos bot run`
exits cleanly at startup if the `claude` CLI is missing.

## What changed

### 1. `runos/bot/handlers.py` -- voice_handler reworked + 3 new handlers + keepalive (Task 1)

- **`voice_handler`**: pre-agent path (defensive chat-id re-check, 20 MB
  guard, deterministic-filename download, warmed-singleton transcribe)
  is UNCHANGED byte-for-byte. After the existing
  `logger.info("transcribed %s -- ...")` line, the post-transcription
  flow is replaced:
  - Empty / whitespace-only transcript -> reply
    `EMPTY_TRANSCRIPT_REPLY` ("`<i>(no speech detected)</i>`") and
    return (NO agent call burned on silence).
  - Non-empty: log the raw transcript at INFO (developer-only; not sent
    to Telegram), then delegate to the shared `_run_agent_turn` helper.
- **`text_handler` (new)**: same post-transcription tail as voice but
  `update.message.text` is the prompt. Empty / whitespace-only text
  drops silently with DEBUG.
- **`new_command_handler` (new)**: owner-only; opens a connection,
  calls `reset_session(conn, chat_id)`, replies `NEW_SESSION_REPLY`.
- **`_run_agent_turn` shared helper**: the 8-step pipeline (connect ->
  get_or_create_session -> first TYPING ping -> spawn keepalive ->
  run_turn(cwd=Path.cwd()) -> cancel+gather keepalive -> save_session
  -> chunked HTML reply -> INFO log line).
- **`_keep_typing` helper**: infinite loop of `send_action(TYPING)` +
  `asyncio.sleep(4)`; caller wraps in `create_task` + `try/finally
  task.cancel + gather(return_exceptions=True)`.
- **`AgentInvocationError`** is the ONLY exception caught here -> maps
  to a single `MISSING_CLI_REPLY` reply + WARNING log. All other
  exceptions propagate per 11-CONTEXT.md `<decisions>` LIGHT-error-handling.
- New module-level constants: `MISSING_CLI_REPLY`, `NEW_SESSION_REPLY`,
  `EMPTY_TRANSCRIPT_REPLY`, `_TYPING_REFRESH_S=4.0`.

### 2. `runos/bot/app.py` -- startup CLI check + handler registration + db_path stash (Task 2)

- **`CLAUDE_CLI_MISSING_ERROR`**: canonical string -- "Set up the Claude
  Code CLI before starting the bot -- see docs/TELEGRAM_BOT.md Phase 11
  prerequisites (Node 18+ + `claude login`)."
- **`_verify_claude_cli()`** (new): runs `shutil.which("claude")`. Raises
  `RuntimeError(CLAUDE_CLI_MISSING_ERROR)` if None; logs the resolved
  path otherwise.
- **`build_application`**:
  - Calls `_verify_claude_cli()` BEFORE `_require_telegram_config` so the
    user is told to install/login the CLI before being asked for tokens.
  - Stashes `settings.db_path` in `app.bot_data["db_path"]` so handlers
    open per-call connections without re-importing `runos.config`.
  - Registers `CommandHandler("new", new_command_handler, owner_filter)`
    AFTER `/start` and BEFORE the generic TEXT handler.
  - Registers `MessageHandler(filters.TEXT & ~filters.COMMAND &
    owner_filter, text_handler)`.
  - Startup log line names `voice_handler=registered`,
    `text_handler=registered`, `new_command_handler=registered`.
- **`_post_init`**: now also calls `init_db(settings.db_path)` (in a
  thread, idempotent) BEFORE warming Whisper so migration 0005 is
  guaranteed applied before the first handler fires.

### 3. `runos/bot/__init__.py` (Tasks 1 + 2)

Re-exports `text_handler`, `new_command_handler`,
`CLAUDE_CLI_MISSING_ERROR`, `MISSING_CLI_REPLY`, `NEW_SESSION_REPLY`.
`__all__` is alphabetically sorted as before. Module docstring updated
to name the new exports and the Phase 11 wiring story.

### 4. Tests (Task 3)

**Updated** (existing Phase 10 tests that asserted the now-removed
italics echo):

- `test_voice_handler_happy_path_writes_file_transcribes_and_replies`
  -- now mocks `run_turn` + the session-store helpers via the
  handler-module-rebound patch targets; asserts the reply is the agent
  text (not `<i>{transcript}</i>`), `session_id=None` resolves to a
  fresh session (no prior bot_session row), `save_session` called with
  the agent's returned id, and the typing indicator was kicked at
  least once before `run_turn`.
- `test_voice_handler_escapes_html_in_agent_reply` (was
  `..._in_transcript`) -- the transcript no longer reaches Telegram, so
  the escape contract is now asserted against the AGENT reply
  (`format_for_telegram` HTML-escapes `<`/`>`/`&`).
- `test_voice_handler_creates_cache_dir_with_0700` -- wires the agent
  mocks because the handler now invokes `run_turn` after transcription.

**New** in `tests/test_bot_handlers.py` (13 tests):

1. `test_voice_handler_resumes_existing_session_and_saves_new_id`
2. `test_voice_handler_starts_fresh_session_when_window_expired`
3. `test_voice_handler_long_reply_is_split_into_chunks_with_prefix`
4. `test_voice_handler_missing_cli_replies_with_clear_message`
5. `test_voice_handler_does_not_echo_raw_transcript_anymore` (negative
   regression -- guards against accidental revival of the Phase 10
   `<i>{transcript}</i>` echo)
6. `test_voice_handler_empty_transcript_skips_agent`
7. `test_voice_handler_logs_token_usage_at_info` (caplog asserts the
   INFO line contains `chat=987654321`, `session=sess-LON` (first 8
   chars), `tokens_in=10`, `tokens_out=20`, `cost=$0.0010`, `wall=1.50s`)
8. `test_voice_handler_logs_subscription_when_cost_is_none`
   (`cost_usd=None` -> `cost=subscription`)
9. `test_text_handler_runs_agent_with_message_text_as_prompt`
10. `test_text_handler_empty_text_is_dropped_silently`
11. `test_text_handler_defensive_check_rejects_mismatched_chat`
12. `test_new_command_handler_resets_session_and_replies`
13. `test_new_command_handler_defensive_check_rejects_mismatched_chat`

Plus shared helpers: `_make_text_update`, `_make_new_command_update`,
`_make_bot_data` (creates a tmp_path SQLite with migrations applied
via `init_db`), `_make_agent_turn`, `_stub_typing`, `_setup_voice_mocks`.

**New** in `tests/test_bot_app.py` (5 tests):

1. `test_verify_claude_cli_passes_when_present`
2. `test_verify_claude_cli_raises_when_missing` (asserts the canonical
   `CLAUDE_CLI_MISSING_ERROR` string contains "Claude Code CLI" and
   the docs link)
3. `test_build_application_raises_when_claude_cli_missing`
4. `test_build_application_stashes_db_path`
5. `test_build_application_registers_text_and_new_handlers` (asserts
   filter conjunction: text accepts owner non-command, rejects owner
   `/start`, rejects non-owner; `/new` accepts owner `/new`, rejects
   non-owner)

### 5. Docs (Task 4)

- **`docs/TELEGRAM_BOT.md`**: new `## Phase 11: the agent loop` section
  after the existing Phase 11 prerequisites. Covers the new flow
  (voice + text -> agent; transcript echo gone), session memory (4-hour
  window, `/new`), observability (the canonical INFO log line; subscription
  users log `cost=subscription`), auth invariant (no
  `ANTHROPIC_API_KEY`), startup CLI check, multi-chunk replies with
  `[k/N] ` prefixes, typing indicator lifecycle, and what Phase 12 will
  add (error boundary, launchd, retention).
- **`README.md`**: new "Claude Code agent loop (v1.1 / Phase 11)"
  subsection under the existing "Telegram bot (v1.1)" -> "Voice intake
  (v1.1 / Phase 10)" sections. One paragraph linking back to
  `docs/TELEGRAM_BOT.md` for the full Phase 11 details.

## Acceptance against PLAN

| Must-have                                                                                                                          | Evidence                                                                                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Voice memo -> agent reply (NOT raw transcript echo)                                                                                | `test_voice_handler_does_not_echo_raw_transcript_anymore` + updated `test_voice_handler_happy_path_...` PASS                   |
| Text message -> agent reply via same flow                                                                                          | `test_text_handler_runs_agent_with_message_text_as_prompt` PASS                                                                |
| `/new` deletes session and replies confirmation                                                                                    | `test_new_command_handler_resets_session_and_replies` PASS                                                                     |
| Within 4-hour window, next message resumes prior session id                                                                        | `test_voice_handler_resumes_existing_session_and_saves_new_id` PASS                                                            |
| Tool-call activity NOT surfaced to Telegram                                                                                        | Delegated to Plan 11-02's `run_turn` (asserts AssistantMessage text-only filtering); handler trusts `turn.text`                |
| Per-turn INFO log contains chat id, short session id, tokens_in/out, cost (or `subscription`), wall                                | `test_voice_handler_logs_token_usage_at_info` + `test_voice_handler_logs_subscription_when_cost_is_none` PASS                  |
| Missing claude CLI -> clean startup error naming CLI + docs link                                                                   | `test_verify_claude_cli_raises_when_missing` + `test_build_application_raises_when_claude_cli_missing` PASS                    |
| Typing indicator shown while run_turn is in flight                                                                                 | Updated happy-path test asserts `send_action(TYPING)` was awaited >= 1 time                                                    |
| Phase 10 raw-transcript regression test                                                                                            | `test_voice_handler_does_not_echo_raw_transcript_anymore` PASS                                                                 |

Verification commands (all green):

- `uv run pytest -q` -> **468 passed** (was 450 baseline; +18 new tests, 2 updated)
- `uv run ruff check .` -> **All checks passed!**
- `uv run ruff format --check .` -> **89 files already formatted**
- `grep -n "MISSING_CLI_REPLY\|new_command_handler\|text_handler" runos/bot/handlers.py` -> all present
- `grep -n "_verify_claude_cli" runos/bot/app.py` -> present
- `grep -v '^#' runos/bot/handlers.py | grep -c "reply_text.*<i>"` -> **0** (Phase 10 italics echo gone; `EMPTY_TRANSCRIPT_REPLY` is a constant referenced via name, not inline)
- `grep -q "Phase 11: the agent loop" docs/TELEGRAM_BOT.md` -> hit
- `grep -qE "claude-agent-sdk|Claude Code agent loop" README.md` -> hit
- `grep -q "4-hour" docs/TELEGRAM_BOT.md` -> hit
- `uv run runos --help` -> renders fine (regression OK)

## Commits

1. `b8ab97e` -- `feat(11-03): rework voice_handler + add text_handler / new_command_handler / typing keepalive`
2. `2cf97d9` -- `feat(11-03): build_application wires text + /new handlers, stashes db_path, verifies claude CLI`
3. `0f34453` -- `test(11-03): voice→agent flow, text_handler, /new, multi-chunk, missing-CLI, startup check`
4. `3265553` -- `docs(11-03): document Phase 11 agent loop in TELEGRAM_BOT.md + README`

Task 1 was marked `tdd="true"` in the plan but the action block explicitly
deferred test writing to Task 3 ("Do NOT add new imports or new tests in
this task -- Task 2 wires the app, Task 3 owns the tests."). The plan's
verify gate for Task 1 was an import smoke-check, which passed before
Task 2 was committed.

## Deviations from Plan

**None of substance.** Mechanical adjustments only:

- **Ruff format / line-length fixes**: the canonical INFO log format
  string (`"agent turn · chat=%d · session=%s · ..."`) exceeded 100
  chars on one line; reflowed as a two-part C-style string literal so
  the message remains a single logical line. Also collapsed two short
  module-level string definitions (`MISSING_CLI_REPLY`, `NEW_SESSION_REPLY`)
  from multi-line `(...)` form to single-line per ruff format. The
  `runos/bot/__init__.py` docstring's first line was shortened to fit
  the 100-char limit. These were folded into the Task 3 commit alongside
  the test additions because they only surfaced once tests added new
  imports and triggered ruff's full scan.
- **Two extra tests beyond the plan's "~5"**: the plan called out ~5-7
  new tests; landed 18 (13 in test_bot_handlers + 5 in test_bot_app).
  The extras are the cost-subscription log assertion
  (`test_voice_handler_logs_subscription_when_cost_is_none`), the empty-
  transcript short-circuit (`test_voice_handler_empty_transcript_skips_agent`),
  and the two defensive-check tests for text/new handlers. All are within
  the spirit of the plan's verification list -- they cover behaviour the
  plan describes but the original test sketch did not explicitly enumerate.
- **Worktree fast-forward at startup**: the worktree branch was at the
  Phase 8 tip; the parent main repo had Phases 9/10/11-01/11-02
  unpushed. Fast-forwarded via `git fetch
  /Users/rossheadington/Projects/RunOS main` + `git merge FETCH_HEAD
  --ff-only` (the execution context explicitly anticipated this; safe
  fast-forward, no rebase, no conflicts).

## Threat Flags

None. This plan composes existing validated boundaries
(`runos.bot.sessions` for `bot_session` writes, `runos.bot.agent` for
Claude Agent SDK invocation) with PTB v22 handlers; it adds no new
network surface, no new auth path, and no schema change. Owner-only
allowlist is enforced by the existing `filters.Chat(chat_id=owner)` at
registration AND a defensive in-handler chat-id re-check for ALL three
new handler types (text, /new) and the reworked voice handler. The
startup `claude` CLI check is a safety addition, not a new attack
surface.

## What's next

- **Phase 12**: top-level error boundary around the handler pipeline
  (today, non-`AgentInvocationError` exceptions propagate to PTB's
  default handler); launchd `LaunchAgent` plist + `KeepAlive=true` for
  unattended runs across reboots and laptop sleep; `voice/` cache and
  bot-log retention policy. The `Path.cwd()` argument to `run_turn` will
  be replaced (or honoured) by the launchd plist's `WorkingDirectory`.

## Self-Check: PASSED

- All 4 task commits exist in `git log --oneline`: b8ab97e, 2cf97d9,
  0f34453, 3265553.
- All 7 modified files exist on disk and reflect the documented changes
  (`runos/bot/handlers.py`, `runos/bot/app.py`, `runos/bot/__init__.py`,
  `tests/test_bot_handlers.py`, `tests/test_bot_app.py`,
  `docs/TELEGRAM_BOT.md`, `README.md`).
- `uv run pytest -q` -> **468 passed**.
- `uv run ruff check .` -> **All checks passed!**
- `uv run ruff format --check .` -> **89 files already formatted**.
- All plan verification greps hit.
