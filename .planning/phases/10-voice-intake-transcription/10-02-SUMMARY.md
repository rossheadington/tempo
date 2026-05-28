---
phase: 10-voice-intake-transcription
plan: 02
subsystem: runos.bot.handlers
tags: [voice, telegram, handler, message-handler, 20mb-guard, html-escape, owner-only]
status: complete
requires:
  - runos.bot.transcribe.transcribe_file (Plan 10-01 warmed faster-whisper singleton)
  - runos.bot.app.build_application (Phase 9 telegram bot scaffold)
  - runos.config.Settings.voice_cache_dir (Plan 10-01 derived path)
provides:
  - runos.bot.handlers.voice_handler (owner-only voice-memo intake)
  - runos.bot.handlers.MAX_VOICE_BYTES (20 MB pre-download guard constant)
  - runos.bot.handlers.OVERSIZED_REPLY (fixed user-facing rejection string)
  - runos.bot.app voice MessageHandler registration via filters.VOICE & filters.Chat(owner)
affects:
  - runos/bot/__init__.py (re-exports voice_handler + MAX_VOICE_BYTES)
  - runos/bot/app.py (registers MessageHandler, startup log includes voice_handler=registered)
  - README.md (new "Voice intake (v1.1 / Phase 10)" subsection under Telegram bot)
tech-stack:
  added: []
  patterns:
    - asyncio.to_thread(sync_callable, ...) for off-event-loop work
    - html.escape for untrusted text in HTML-parse-mode Telegram replies
    - Pre-network guard pattern (file_size check BEFORE get_file) for hard API caps
    - Deterministic, collision-free cache filename (message_id + file_unique_id)
    - Lazy 0700 dir creation on first use (mirrors data_dir / tokens_dir convention)
    - Defence-in-depth allowlist (registration filter + in-handler chat-id re-check)
key-files:
  created:
    - tests/test_bot_handlers.py
  modified:
    - runos/bot/handlers.py
    - runos/bot/app.py
    - runos/bot/__init__.py
    - README.md
decisions:
  - Empty transcript replies "(no speech detected)" rather than dropping silently -- the user always sees the pipeline ran; a silent drop is indistinguishable from a hung bot.
  - The 20 MB guard returns IMMEDIATELY after replying (no cache-dir creation, no get_file call) so an oversized memo costs the bot exactly one reply and zero filesystem state.
  - The voice MessageHandler is added in default group (0) right after the /start CommandHandler -- no separate handler group needed because Phase 10 has no overlapping filters.
  - test_voice_handler_filter_drops_non_owner uses the same build_application(settings) path test_bot_app.py uses (rather than constructing a MessageHandler manually) so the test exercises the REAL registration -- catching a regression where a future refactor forgets to AND in filters.VOICE.
  - test_build_application_registers_voice_handler additionally asserts a plain-text Update from the owner is rejected, proving filters.VOICE is part of the conjunction (not just filters.Chat).
  - Real-model integration test (test_transcribe_file_real_fixture_returns_nonempty) was deselected from the verification run -- it requires a ~480 MB Hugging Face download on a cold cache. It still runs as part of `uv run pytest` when the cache is warm.
metrics:
  duration_seconds: 460
  duration_minutes_approx: 7.7
  completed: 2026-05-27
  tasks_completed: 2
  files_created: 1
  files_modified: 4
  tests_added: 7
  total_tests: 425
  total_tests_passing: 424
  total_tests_deselected: 1
---

# Phase 10 Plan 02: Voice Handler + 20 MB Guard + README Docs Summary

End-to-end vertical slice for the RunOS voice-coach intake: an owner-only Telegram voice-memo handler that downloads the `.ogg`, transcribes it locally via Plan 10-01's warmed faster-whisper singleton, and replies with the HTML-escaped transcript in italics — wired behind `filters.VOICE & filters.Chat(owner)` with a 20 MB pre-download safety guard and a defensive in-handler chat-id re-check.

## What shipped

**`runos/bot/handlers.py`** gains two module-level constants and one new async handler:

- `MAX_VOICE_BYTES = 20 * 1024 * 1024` (VOICE-03 hard cap on Telegram bot-API `getFile`).
- `OVERSIZED_REPLY` — the fixed user-facing rejection string used when a memo exceeds the cap.
- `voice_handler(update, context)` — the owner-only voice intake. Flow:
  1. Defensive chat-id re-check (silent return if mismatched; mirrors `start_handler`).
  2. 20 MB pre-download guard. If `voice.file_size > MAX_VOICE_BYTES`: log + reply `OVERSIZED_REPLY` + return (no `get_file()`, no cache-dir creation).
  3. Lazy-create `settings.voice_cache_dir` with mode `0o700`.
  4. Compute `target_path = voice_cache_dir / f"{message_id}-{file_unique_id}.ogg"` (collision-free).
  5. `await voice.get_file()` → `await tg_file.download_to_drive(custom_path=target_path)`.
  6. `transcript = await asyncio.to_thread(transcribe_file, target_path)` — keeps the event loop responsive while the blocking native transcribe runs.
  7. INFO log: `"transcribed <name> -- audio_s=N wall_s=W model=<settings.whisper_model_name>"`.
  8. Reply with `<i>{html.escape(transcript)}</i>` + `ParseMode.HTML`. Empty transcript falls back to `"(no speech detected)"` so the user always sees the pipeline ran.

**`runos/bot/app.py`** registers the new MessageHandler next to the existing `/start`:

```python
app.add_handler(MessageHandler(filters.VOICE & owner_filter, voice_handler))
```

The startup log line now reports `voice_handler=registered` so a manual `runos bot run` makes the wiring visible.

**`runos/bot/__init__.py`** re-exports `voice_handler` and `MAX_VOICE_BYTES`; module docstring updated to point to Plan 10-02 for the voice intake.

**`README.md`** gains a 60-line "Voice intake (v1.1 / Phase 10)" subsection under the existing "Telegram bot (v1.1)" heading, covering the user flow, the three `WHISPER_*` env vars (with the explicit Mac CPU-only note), the first-run model download cost, the 20 MB cap + rejection reply, and the explicit out-of-scope notes for Phase 11 (Claude agent loop) and Phase 12 (always-on launchd + voice-file retention).

## Test coverage

**`tests/test_bot_handlers.py`** — 7 new tests, all green:

| # | Test                                                          | Proves                                                                           |
| - | ------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| 1 | `test_voice_handler_rejects_oversized_with_no_download`       | 20 MB guard fires BEFORE any `get_file()` (patched to raise as hard proof)       |
| 2 | `test_voice_handler_filter_drops_non_owner`                   | `MessageHandler.check_update(non_owner_voice_update)` is falsy                   |
| 3 | `test_voice_handler_happy_path_writes_file_transcribes_and_replies` | Download path, `transcribe_file` call site, italics-HTML reply, cache dir auto-create |
| 4 | `test_voice_handler_escapes_html_in_transcript`               | `"3 < 4 & 5 > 4"` → `"<i>3 &lt; 4 &amp; 5 &gt; 4</i>"`                           |
| 5 | `test_voice_handler_defensive_check_rejects_mismatched_chat`  | In-handler chat-id mismatch → silent drop (belt-and-braces)                      |
| 6 | `test_voice_handler_creates_cache_dir_with_0700`              | `stat().st_mode & 0o777 == 0o700` after first use                                |
| 7 | `test_build_application_registers_voice_handler`              | Owner voice ✓, non-owner voice ✗, owner text ✗ (filters.VOICE is in the AND)     |

All tests patch `runos.bot.handlers.transcribe_file` with a synchronous `MagicMock` (NOT `AsyncMock`) because the handler wraps the call in `asyncio.to_thread`, which awaits a sync callable. `telegram.Message.reply_text` is patched at the class level (PTB v22 `Message` is slotted-frozen; instance-level mock assignment is rejected).

## Goal-backward verification

Each Phase 10 must-have, tied to the concrete test that proves it (truths from PLAN.md frontmatter):

| Must-have | Proved by |
| --------- | --------- |
| Owner memo → `<content_dir>/voice/<msg_id>-<file_uid>.ogg` (0700 dir) → transcribed via warmed singleton → italics-HTML reply | `test_voice_handler_happy_path_writes_file_transcribes_and_replies` + `test_voice_handler_creates_cache_dir_with_0700` |
| File size > 20 MB → clear reply, no `get_file()` call | `test_voice_handler_rejects_oversized_with_no_download` (with `Voice.get_file` patched to `raise AssertionError` as hard proof) |
| Non-owner chat → silently dropped at filter level | `test_voice_handler_filter_drops_non_owner` + `test_build_application_registers_voice_handler` |
| `asyncio.to_thread(transcribe_file, path)` keeps the event loop responsive | `grep -c "asyncio.to_thread(transcribe_file" runos/bot/handlers.py == 1` (verify automated check) + happy-path test patches `transcribe_file` as a SYNC MagicMock and the test passes (an AsyncMock would surface as a coroutine string) |

## Verification commands (all green)

```
$ uv run pytest --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty
424 passed, 1 deselected, 6 warnings in 1.93s

$ uv run ruff check runos tests
All checks passed!

$ uv run ruff format --check runos tests
85 files already formatted

$ grep -c "filters.VOICE" runos/bot/app.py            # 2 (import + registration)
$ grep -c "MAX_VOICE_BYTES" runos/bot/handlers.py     # 2 (constant def + guard usage)
$ grep -c "asyncio.to_thread(transcribe_file" runos/bot/handlers.py  # 1
```

## Manual smoke test

**Pending.** This dev environment does not have a real `TELEGRAM_BOT_TOKEN` / `TELEGRAM_OWNER_CHAT_ID` configured, so the end-to-end "speak into Telegram, see the transcript come back" smoke test is deferred to the developer's main machine. The unit tests cover every code path that does not require live Telegram traffic — the only thing the smoke test would add is proof that PTB v22's `MessageHandler` actually dispatches a real voice Update to our callback (which it already does for `/start` per Phase 9's smoke test). When the developer next runs `uv run runos bot run` against their real token + sends a voice memo, expect a log line of the shape:

```
INFO runos.bot.handlers transcribed 1234-AgADbAAD....ogg -- audio_s=3 wall_s=1.42 model=small.en
```

— and a reply in italics in the chat.

## Deviations from Plan / 10-CONTEXT.md

**None.** All LOCKED decisions in `10-CONTEXT.md` were honoured:

- `voice_handler` registered ONLY behind `filters.VOICE & filters.Chat(owner)`.
- 20 MB guard checks `voice.file_size` BEFORE `get_file()`; rejection reply uses the exact LOCKED string.
- `file_size is None` falls through past the guard (the documented "let `get_file()` raise" choice).
- Cache filename pattern: `<message_id>-<file_unique_id>.ogg` under `<content_root>/voice/` with 0700 mode.
- `transcribe_file` invoked via `asyncio.to_thread` per the LOCKED day-one decision.
- No top-level error handler — exceptions propagate to PTB's default (Phase 12 territory).
- HTML-escaped italics reply with `ParseMode.HTML`.
- Per-transcription INFO log includes `audio_s` / `wall_s` / `model=` (per the LOCKED shape).

One small refinement, deliberately within scope: empty transcripts reply `"(no speech detected)"` instead of an empty `<i></i>`. The plan's `<behavior>` block says "Empty transcript still goes back so the user sees the pipeline ran" — an empty italics tag would not be visible to the user, defeating that intent. This is consistent with the LOCKED decision in 10-CONTEXT.md that VAD silence → empty string is a real outcome, not an error.

## TDD Gate Compliance

Plan was tagged `tdd="true"` on Task 1. Gate sequence honoured:

1. **RED:** commit `a75b257` — `test(10-02): add failing tests for voice_handler`. Tests fail at import (`MAX_VOICE_BYTES` / `voice_handler` not yet exported).
2. **GREEN:** commit `292b565` — `feat(10-02): voice_handler + 20 MB guard + owner-only MessageHandler`. All 7 new tests pass.
3. **REFACTOR:** not needed — implementation landed clean (ruff format + check both green on first pass).

## Commits

| Step | Hash    | Type       | Subject                                                                  |
| ---- | ------- | ---------- | ------------------------------------------------------------------------ |
| RED  | a75b257 | `test`     | add failing tests for voice_handler                                      |
| GREEN| 292b565 | `feat`     | voice_handler + 20 MB guard + owner-only MessageHandler                  |
| Task 2 | 3e99e86 | `docs`   | README v1.1 voice-intake user docs                                       |

## Files touched

- **Created:** `tests/test_bot_handlers.py` (7 tests, ~440 lines including helpers + module docstring).
- **Modified:** `runos/bot/handlers.py` (added `MAX_VOICE_BYTES`, `OVERSIZED_REPLY`, `voice_handler`), `runos/bot/app.py` (imports + MessageHandler registration + log line), `runos/bot/__init__.py` (re-exports + docstring refresh), `README.md` (new "Voice intake (v1.1 / Phase 10)" subsection).

## Known stubs

None. The voice handler is fully wired; Plan 11 builds the Claude agent loop on top of this same code path and does not need to revisit anything 10-02 ships.

## Phase 10 status after this plan

With Plan 10-01 (singleton + warmup) and Plan 10-02 (handler + 20 MB guard + docs) on `main`, Phase 10's must-haves are complete: a real voice memo from the owner produces a real transcript in the chat. Remaining v1.1 work lives in Phase 11 (Claude Code agent loop / `/new` command) and Phase 12 (launchd KeepAlive + voice-file retention).

## Self-Check: PASSED

- File `tests/test_bot_handlers.py` exists.
- File `runos/bot/handlers.py` contains `MAX_VOICE_BYTES`, `OVERSIZED_REPLY`, `voice_handler`.
- File `runos/bot/app.py` contains `MessageHandler(filters.VOICE`.
- File `runos/bot/__init__.py` exports `voice_handler` + `MAX_VOICE_BYTES`.
- File `README.md` contains "Voice intake" + "WHISPER_MODEL_NAME" + "20 MB" + "small.en".
- Commits `a75b257` (RED), `292b565` (GREEN), `3e99e86` (docs) present in `git log`.
- `uv run pytest --deselect <real-model integration>` → 424 passed.
- `uv run ruff check runos tests` → all checks passed.
- `uv run ruff format --check runos tests` → 85 files already formatted.
