---
phase: 12-lifecycle-hardening-privacy
plan: 12-02
subsystem: bot-lifecycle / docs
tags: [telegram-bot, error-handler, privacy, docs, v1.1-closeout]
requires:
  - runos.bot.app build_application (Phase 9)
  - runos.bot.handlers (Phase 10/11)
  - runos.bot.agent + sessions (Phase 11)
  - Plan 12-01 launchd plist + voice retention + cwd logging
provides:
  - runos.bot.error_handler.telegram_error_handler (top-level PTB error boundary)
  - runos.bot.error_handler.ERROR_REPLY (fixed user-facing reply text)
  - docs/PRIVACY.md (single-source user-facing privacy contract)
  - docs/TELEGRAM_BOT.md "Always-on under launchd", "Voice cache retention",
    "Error handler behaviour" sections
  - README "Always-on bot via launchd (v1.1 / Phase 12)" section
affects:
  - runos/bot/app.py (registers app.add_error_handler(...))
  - runos/bot/__init__.py (re-exports ERROR_REPLY + telegram_error_handler)
  - .planning/REQUIREMENTS.md (VOICE-11/12/14/15 marked complete)
  - .planning/STATE.md (status=shipped, percent=100, completed_phases includes 12)
  - .planning/ROADMAP.md (Phase 9/10/11/12 ticked + v1.1 milestone status COMPLETE)
key-files:
  created:
    - runos/bot/error_handler.py
    - tests/test_error_handler.py
    - docs/PRIVACY.md
    - .planning/phases/12-lifecycle-hardening-privacy/12-02-SUMMARY.md
  modified:
    - runos/bot/app.py
    - runos/bot/__init__.py
    - docs/TELEGRAM_BOT.md
    - README.md
    - .planning/REQUIREMENTS.md
    - .planning/STATE.md
    - .planning/ROADMAP.md
decisions:
  - "Top-level error handler logs via logger.error(msg, exc_info=err) -- not logger.exception() -- because PTB calls the handler outside an `except` block. exc_info=err is the explicit form that works either way."
  - "ERROR_REPLY is plain text (no HTML special chars) sent without parse_mode. Avoids any escape-mode subtle bugs in the worst-case path."
  - "isinstance(update, Update) AND update.effective_chat is not None gating BEFORE attempting a reply: PTB fires the error handler for non-Update failures (jobqueue / internal updater errors) where there is no chat. Replying to those would raise inside our last-line-of-defence."
  - "Reply failures (Telegram unreachable, chat blocked the bot) are caught with a broad `except Exception` + logged + swallowed. We are the bottom of the stack; re-raising defeats VOICE-12."
  - "docs/PRIVACY.md is the SINGLE-SOURCE privacy contract. README + TELEGRAM_BOT.md link to it rather than restate it, so updates stay in sync."
  - "TELEGRAM_BOT.md's old `What's next` Phase 10/11/12 bullet list is replaced with a `v1.1 is feature-complete` note pointing at ROADMAP for v1.2."
metrics:
  duration_seconds: 1800
  duration_human: "~30m"
  tasks_completed: 3
  files_created: 4
  files_modified: 7
  tests_added: 5
  tests_passing: 498
  completed: 2026-05-28
---

# Phase 12 Plan 12-02: Top-level Error Handler + Privacy Docs Summary

The v1.1 closeout plan. Adds the top-level Telegram error boundary
(`telegram_error_handler`) so a single bad message can never crash the
worker (VOICE-12), publishes a one-page user-facing privacy contract
(`docs/PRIVACY.md`) covering everything RunOS touches, what leaves the
laptop, and the per-credential leak-response steps, and updates
`docs/TELEGRAM_BOT.md` + `README.md` with launchd lifecycle, voice
retention, and error-handler-behaviour sections. Marks VOICE-11/12/14/15
complete and flips the milestone state to `shipped`.

## What's wired

### New module: `runos/bot/error_handler.py`

* `ERROR_REPLY: str` -- fixed canonical reply: `"Sorry -- something went
  wrong on my end. Check the logs."`
* `async telegram_error_handler(update, context)`:
  1. `logger.error("Bot handler crashed: %r", err, exc_info=err)` --
     full traceback in launchd log.
  2. If `isinstance(update, Update)` and `update.effective_chat is not None`,
     `await context.bot.send_message(chat_id, ERROR_REPLY)` -- plain text,
     no parse_mode (the reply is safe to send raw).
  3. Reply failures swallowed via broad `except Exception` -> log
     `"error reply failed: chat=... original=... reply_error=..."`.
  4. Never re-raises.

### Registration in `build_application`

Added `from runos.bot.error_handler import telegram_error_handler` and a
single `app.add_error_handler(telegram_error_handler)` call AFTER every
existing handler is registered. The startup INFO log now ends with
`error_handler=registered` for sanity.

### `tests/test_error_handler.py` (5 new tests)

1. **Handler exception -> log + canonical reply.** Asserts an ERROR-level
   `"Bot handler crashed"` log record AND a single `send_message` with
   `chat_id=987654321` and `text=ERROR_REPLY`.
2. **Reply failure swallowed.** `send_message.side_effect = RuntimeError`;
   asserts the handler completes (no `pytest.raises`), the original
   crash IS logged, AND an additional `"error reply failed"` log record
   exists.
3. **Non-Update object -> log only, no reply.** Passes a plain string as
   `update`; asserts zero `send_message` calls and the crash log line.
4. **Update with no `effective_chat` -> log only, no reply.** Passes
   `Update(update_id=42)` (chat/message stripped); asserts zero
   `send_message` calls and the crash log line.
5. **`build_application` registers the handler.** Builds an Application
   with test env vars, asserts `telegram_error_handler in app.error_handlers`.

### `docs/PRIVACY.md` (new)

Single-source user-facing privacy contract, structured as:

* **What stays on the laptop, always** -- per-artifact table (db, tokens,
  journal, races/plan/heat md, reports, .env, owner_chat_id, voice
  cache, transcripts, session ids, launchd logs).
* **What leaves the laptop, and to whom** -- Strava, Garmin, Anthropic
  (via Claude Code) with the explicit note that raw `.ogg` audio is
  NEVER uploaded -- Whisper runs locally.
* **Voice retention policy** -- the `VOICE_RETENTION_DAYS` knob,
  privacy-safe default of 0, startup sweep, manual `runos bot purge-voice`.
* **The repository is public; the data is not** -- the privacy invariants
  (no creds in git, no personal fixtures, 0600 tokens, no hard-coded
  user paths) that keep that safe.
* **If a credential leaks** -- per-source revocation steps (Strava,
  Garmin, Telegram bot token, Claude Code subscription).
* **What's NOT private** -- aggregate metrics in reports, the bot's
  existence, the public code/prompts.
* **See also** -- pointers to README, TELEGRAM_BOT.md, JOURNALING.md,
  REQUIREMENTS Known Accepted Conflicts.

### `docs/TELEGRAM_BOT.md` updates

* New "Always-on under launchd (Phase 12)" section: `runos bot
  install-scheduler` flow, `launchctl bootstrap` / `kickstart` /
  `bootout`, plist properties (`KeepAlive=true`, `ThrottleInterval=10`,
  `RunAtLoad=true`, `WorkingDirectory`, log paths), pointer to the
  plist template, and the `plutil -lint`-before-handoff guard.
* New "Voice cache retention" section: `VOICE_RETENTION_DAYS` semantics
  (0 = immediate delete; N>0 = N-day retention with startup sweep),
  `runos bot purge-voice [--yes]` hatch.
* New "Error handler behaviour" section: the three-step contract
  (log/reply/no-re-raise), the relationship to the Phase 11
  `AgentInvocationError` catch (which fires first for the
  Claude-CLI-missing case), and the combined launchd-KeepAlive +
  error-handler bullet ("a single bad message can never take the worker
  down").
* Phase 11 "What this phase does NOT yet do" paragraph rewritten to
  reflect that Phase 12 has shipped.
* `What's next` rewritten from a list of in-flight phases to a
  "v1.1 is feature-complete" note pointing at ROADMAP for v1.2.
* Top-of-file summary line extended with "top-level error boundary
  and a configurable voice-cache retention policy" + a link to
  `docs/PRIVACY.md`.

### `README.md` updates

* Telegram bot section opener rewritten to describe the full v1.1
  surface (Whisper, agent loop, error boundary, launchd) instead of
  Phase 9's narrow scope.
* "What this phase does NOT yet do" paragraph replaced with a
  "Voice retention" paragraph documenting `VOICE_RETENTION_DAYS=0` +
  `runos bot purge-voice` and pointing at `docs/PRIVACY.md`.
* New "Always-on bot via launchd (v1.1 / Phase 12)" subsection after the
  Claude Code agent loop section: install commands + the error-handler
  reply text + log paths + cross-link to TELEGRAM_BOT.md / PRIVACY.md.

## Requirements satisfied

| Req | Description | Where |
| --- | --- | --- |
| VOICE-11 | launchd `KeepAlive` lifecycle | Plan 12-01 (plist) + 12-02 docs |
| VOICE-12 | Top-level error boundary -- worker survives single-message failures | `runos/bot/error_handler.py` + `build_application` registration + 5 tests |
| VOICE-14 | Voice files deleted after successful transcription (configurable retention) | Plan 12-01 (handler + sweep) + 12-02 docs (`docs/PRIVACY.md` + TELEGRAM_BOT.md "Voice cache retention") |
| VOICE-15 | Agent runs in RunOS project dir; no access outside the project tree | Plan 12-01 (cwd log + launchd `WorkingDirectory`) + 12-02 docs (`docs/PRIVACY.md` "What leaves the laptop") |

All 15 VOICE-* requirements now complete; 60/60 v1.1 requirements
satisfied.

## Deviations from Plan

None. The plan was executed exactly as written, with one minor tactical
choice (`logger.error(msg, exc_info=err)` instead of
`logger.exception(msg)`) because PTB calls the error handler outside an
`except` block -- `logger.exception` only auto-attaches the traceback
when called inside one. The explicit `exc_info=err` form preserves the
same effective behaviour (full traceback in the log record) and matches
the docstring contract; tests assert against the message + ERROR level,
not the call shape, so no test had to change.

The execution context noted "492 + new tests" baseline; the actual
baseline on this worktree was 493 (one extra test landed in 12-01).
Final count after 12-02 is **498** (493 + 5 new error-handler tests).

## Authentication gates

None. No CLI logins or third-party auth flows touched by this plan.

## Test results

* `uv run pytest`: **498 passed, 0 failed, 28 PTB deprecation warnings**
  (the `voice.duration -> timedelta` deprecation comes from inside
  python-telegram-bot itself and is not actionable on the RunOS side).
* `uv run ruff check runos/ tests/`: All checks passed.

## Commits

| Hash | Message |
| --- | --- |
| 16787e5 | `test(12-02): add failing tests for runos.bot.error_handler (RED)` |
| 08f514f | `feat(12-02): add top-level Telegram error handler (VOICE-12)` |
| 306b658 | `docs(12-02): add docs/PRIVACY.md — single-source privacy contract` |
| (this commit) | `docs(12-02): launchd + privacy doc updates; close v1.1 milestone` |

## Self-Check: PASSED

* `runos/bot/error_handler.py` -- FOUND
* `tests/test_error_handler.py` -- FOUND
* `docs/PRIVACY.md` -- FOUND
* `.planning/phases/12-lifecycle-hardening-privacy/12-02-SUMMARY.md` -- FOUND
* Commit `16787e5` -- FOUND in `git log`
* Commit `08f514f` -- FOUND in `git log`
* Commit `306b658` -- FOUND in `git log`

## Known Stubs

None. Every code path in `error_handler.py` is wired to a real PTB
callback registered on the live `Application`, the docs link to real
files at real paths, and every state/requirements/roadmap update points
at the work that landed in this plan or 12-01.

## v1.1 milestone closure

Status flipped to `shipped` in STATE.md. ROADMAP.md milestone status
updated to **v1.1 Telegram Voice Coach (Phases 9–12): COMPLETE**.
REQUIREMENTS.md VOICE-11/12/14/15 ticked + traceability rows marked
Complete. Next milestone (v1.2 -- Pi port) is deferred.
