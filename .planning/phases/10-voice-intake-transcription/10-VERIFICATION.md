---
phase: 10-voice-intake-transcription
verified: 2026-05-27T00:00:00Z
status: passed
score: 8/8 goal-backward checks verified
overrides_applied: 2
overrides:
  - must_have: "Roadmap SC #3 — singleton 'warmed by one dummy transcription'"
    reason: "10-CONTEXT.md explicitly chose model-load-only warmup ('Pre-loading at startup eliminates the cold-start latency on the first voice memo (~3-5s first-load)'). No dummy transcribe is performed; loading the model alone amortises the cost. REQUIREMENTS.md VOICE-06 wording ('first-run model download is warmed at startup, not on first request') is satisfied by the model-load step. Accepted at planning time."
    accepted_by: "Phase 10 planning (10-CONTEXT.md, LOCKED Singleton + warmup section)"
    accepted_at: "2026-05-27"
  - must_have: "Roadmap SC #4 — env-var names RUNOS_TRANSCRIBE_MODEL / RUNOS_TRANSCRIBE_COMPUTE_TYPE; models live under gitignored download_root inside data_dir, NOT ~/.cache/huggingface"
    reason: "10-CONTEXT.md explicitly chose bare WHISPER_* env-var names (mirrors the bare TELEGRAM_* convention from Phase 9 and matches faster-whisper's own WHISPER_* docs convention). VOICE-05 in REQUIREMENTS.md is the looser, authoritative ask ('configurable via pydantic-settings'). Model cache location is the faster-whisper default (~/.cache/huggingface/hub/), also explicit in 10-CONTEXT — defer download_root relocation if/when it ever matters."
    accepted_by: "Phase 10 planning (10-CONTEXT.md, LOCKED Settings additions + Library + model choice sections)"
    accepted_at: "2026-05-27"
---

# Phase 10: Voice Intake + Local Transcription — Verification Report

**Phase Goal:** The bot accepts a voice memo from the owner, downloads it into a gitignored local voice cache, transcribes it locally via `faster-whisper` (singleton, warmed at startup, `small.en` int8 default), and replies with the raw transcript in Telegram.

**Verified:** 2026-05-27
**Status:** passed
**Requirements:** VOICE-03, VOICE-04, VOICE-05, VOICE-06

## Goal-Backward Checks (Observable Truths)

| #  | Truth                                                                                                   | Status     | Evidence                                                                                                                                                                                                                                                                                  |
| -- | ------------------------------------------------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| G1 | `voice_handler` registers against `filters.VOICE & filters.Chat(owner)`                                 | ✓ VERIFIED | `runos/bot/app.py:116`: `app.add_handler(MessageHandler(filters.VOICE & owner_filter, voice_handler))`. `owner_filter = filters.Chat(chat_id=owner_chat_id)` defined at line 110. Pinned by `test_voice_handler_filter_drops_non_owner` and `test_build_application_registers_voice_handler`. |
| G2 | 20 MB guard is pre-download with a clear user reply                                                     | ✓ VERIFIED | `runos/bot/handlers.py:44`: `MAX_VOICE_BYTES = 20 * 1024 * 1024`. `handlers.py:111-114`: `if voice.file_size is not None and voice.file_size > MAX_VOICE_BYTES:` → reply `OVERSIZED_REPLY` → `return` (no `get_file()` call). Pinned by `test_voice_handler_rejects_oversized_with_no_download` (patches `Voice.get_file` to raise as hard proof). |
| G3 | Voice files downloaded to `<content_dir>/voice/<message_id>-<file_unique_id>.ogg` at 0700               | ✓ VERIFIED | `runos/bot/handlers.py:121`: `voice_cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)`. `handlers.py:122`: `target_path = voice_cache_dir / f"{update.message.message_id}-{voice.file_unique_id}.ogg"`. `runos/config.py:205-213`: `voice_cache_dir = content_root / "voice"`. Pinned by `test_voice_handler_creates_cache_dir_with_0700` (`stat().st_mode & 0o777 == 0o700`) and `test_voice_handler_happy_path_writes_file_transcribes_and_replies`. |
| G4 | `faster-whisper` loaded as a singleton, warmed at startup via `post_init`                               | ✓ VERIFIED | `runos/bot/transcribe.py:34`: `_MODEL: WhisperModel \| None = None` module-level singleton. `transcribe.py:38-76`: `warm_model(settings)` idempotent (early-returns if `_MODEL is not None`). `runos/bot/app.py:90-99`: `_post_init` hook calls `await asyncio.to_thread(warm_model, settings)` then logs `"Whisper model loaded and ready"`. Pinned by transcribe-module tests (`warm_model` is idempotent; `get_model` raises if unwarmed). |
| G5 | Default model is `small.en` int8 cpu, configurable via the 3 `WHISPER_*` env vars                       | ✓ VERIFIED | `runos/config.py:106-129`: `whisper_model_name="small.en"` / `whisper_compute_type="int8"` / `whisper_device="cpu"`, each with `validation_alias="WHISPER_MODEL_NAME"` / `"WHISPER_COMPUTE_TYPE"` / `"WHISPER_DEVICE"`. `transcribe.py:62-67`: `WhisperModel(name, device=..., compute_type=..., cpu_threads=4)`. `.env.example` documents all 3 with model trade-off comments (grep count = 3). Pinned by `test_warm_model_overridden_by_env` mock-driven env swap. |
| G6 | Transcript reply is HTML-escaped and italicised                                                         | ✓ VERIFIED | `runos/bot/handlers.py:146-150`: `reply_body = html.escape(transcript) if transcript else "(no speech detected)"` → `reply_text(f"<i>{reply_body}</i>", parse_mode=ParseMode.HTML)`. Pinned by `test_voice_handler_escapes_html_in_transcript` (`"3 < 4 & 5 > 4"` → `"<i>3 &lt; 4 &amp; 5 &gt; 4</i>"`). |
| G7 | 5-7 new handler tests pinning each behaviour                                                            | ✓ VERIFIED | `tests/test_bot_handlers.py` contains exactly 7 tests (`grep -c "^async def test_\|^def test_" = 7`): oversized-no-download, filter-drops-non-owner, happy-path, html-escape, defensive-chat-mismatch, 0700-mode, build-application-registers-voice-handler. `tests/test_bot_transcribe.py` adds 7 more tests covering the singleton + warmup + env override paths. |
| G8 | Full suite (424) green + ruff clean                                                                     | ✓ VERIFIED | `uv run pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` → `424 passed, 1 deselected, 6 warnings in 2.28s`. `uv run ruff check runos/ tests/` → `All checks passed!`. |

**Score:** 8/8 goal-backward checks verified

## Required Artifacts

| Artifact                                  | Expected                                                                                                                            | Status     | Details                                                                                                                                                                                |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runos/bot/transcribe.py`                 | Module-level WhisperModel singleton + `warm_model(settings)` + `get_model()` + `transcribe_file(path) -> str` (eager segments iter) | ✓ VERIFIED | 131 lines; all four exports present; `_reset_for_tests` helper for hygiene; eager `[seg.text for seg in segments]` consumption documented inline with the "most-reported foot-gun" note. |
| `runos/bot/handlers.py` (voice_handler)   | Async handler with chat-id re-check, 20 MB guard, deterministic filename, `asyncio.to_thread(transcribe_file)`, HTML-escaped reply  | ✓ VERIFIED | Lines 81-150. All seven flow steps present (defensive chat-id check; size guard; cache-dir 0700; filename pattern; `voice.get_file()` + `download_to_drive`; `to_thread`; reply).        |
| `runos/bot/app.py` (post_init + register) | `_post_init` calls `warm_model` via `asyncio.to_thread`; voice handler registered behind `filters.VOICE & owner_filter`             | ✓ VERIFIED | Lines 90-99 (warm_model + log line "Whisper model loaded and ready"); line 116 (MessageHandler registration); line 119 startup log includes `voice_handler=registered`.                  |
| `runos/bot/__init__.py`                   | Re-exports `warm_model` / `get_model` / `transcribe_file` / `voice_handler` / `MAX_VOICE_BYTES`                                     | ✓ VERIFIED | All five symbols listed in `__all__` and imported at lines 26-28.                                                                                                                       |
| `runos/config.py` (whisper fields)        | 3 whisper_* fields with bare `WHISPER_*` validation_alias + `voice_cache_dir` derived property                                      | ✓ VERIFIED | Lines 106-129 (3 Field declarations); lines 205-213 (`voice_cache_dir = content_root / "voice"` with deferred-creation docstring). NOT in `ensure_dirs()` — created lazily by handler.   |
| `tests/test_bot_transcribe.py`            | Singleton + warmup + env-override coverage; integration test with real fixture                                                      | ✓ VERIFIED | 7 tests including `test_warm_model_overridden_by_env` and real-fixture integration test (deselected from main run; runs when HF cache is warm).                                          |
| `tests/test_bot_handlers.py`              | 7 voice-handler tests covering size guard, owner filter, happy path, html-escape, defensive check, 0700, registration               | ✓ VERIFIED | 7 tests, all green. Notable: `test_voice_handler_rejects_oversized_with_no_download` patches `Voice.get_file` to raise — hard proof the guard runs first.                              |
| `tests/fixtures/voice/sample.ogg`         | Tiny pre-recorded OGG for the real-model integration test                                                                           | ✓ VERIFIED | Exists (6.4 KB); committed to repo for the optional `test_transcribe_file_real_fixture_returns_nonempty` integration test.                                                              |
| `.env.example`                            | Whisper section documenting all 3 `WHISPER_*` vars with model trade-off comments                                                    | ✓ VERIFIED | "Whisper transcription (v1.1 Phase 10)" block present; 3 commented assignments; model swap table covers tiny.en / base.en / small.en / medium.en / large-v3-turbo.                       |
| `README.md`                               | "Voice intake (v1.1 / Phase 10)" subsection covering user flow, env vars, 20 MB cap, out-of-scope notes                             | ✓ VERIFIED | Subsection present under "Telegram bot (v1.1)"; covers warmup, 20 MB rejection reply (exact `OVERSIZED_REPLY` string), Mac-CPU-only note, Phase 11/12 out-of-scope notes.                |

## Key Link Verification

| From                                   | To                                  | Via                                                                            | Status   | Details                                                                                                                                                         |
| -------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------ | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runos/bot/app.py::_post_init`         | `runos/bot/transcribe.warm_model`   | `await asyncio.to_thread(warm_model, settings)`                                | ✓ WIRED  | Line 98. Settings stashed in `app.bot_data["settings"]` at line 108 so the voice handler reads the same instance.                                              |
| `runos/bot/app.py::build_application`  | `runos/bot/handlers.voice_handler`  | `MessageHandler(filters.VOICE & owner_filter, voice_handler)`                  | ✓ WIRED  | Line 116. Imported at line 27. Owner filter built from `settings.telegram_owner_chat_id`.                                                                       |
| `runos/bot/handlers.voice_handler`     | `runos/bot/transcribe.transcribe_file` | `await asyncio.to_thread(transcribe_file, target_path)`                     | ✓ WIRED  | Line 131. Imported at handlers.py:28. Wraps blocking native call so PTB event loop stays responsive.                                                            |
| `runos/bot/handlers.voice_handler`     | `settings.voice_cache_dir`          | `settings: Settings = context.application.bot_data["settings"]`                | ✓ WIRED  | Line 119-120. Lazy-creates dir at line 121 with 0o700.                                                                                                          |
| `runos/bot/handlers.voice_handler`     | Telegram `voice.get_file()` + reply | `await voice.get_file()` → `download_to_drive(custom_path=target_path)`        | ✓ WIRED  | Lines 124-125. Followed by `reply_text(... ParseMode.HTML)` at line 147-150.                                                                                    |
| `runos/config.Settings`                | `WHISPER_*` env vars                | `validation_alias="WHISPER_MODEL_NAME"` etc.                                   | ✓ WIRED  | All 3 fields use the bare-env-var pattern (bypasses `RUNOS_` prefix), matching the locked design and faster-whisper convention.                                |

## Data-Flow Trace (Level 4)

| Artifact                       | Data Variable                | Source                                                                                   | Produces Real Data | Status    |
| ------------------------------ | ---------------------------- | ---------------------------------------------------------------------------------------- | ------------------ | --------- |
| `voice_handler` → reply        | `transcript: str`            | `await asyncio.to_thread(transcribe_file, target_path)` (warmed `WhisperModel.transcribe`) | Yes (real ASR; empty → "(no speech detected)" sentinel) | ✓ FLOWING |
| `transcribe_file`              | `parts: list[str]`           | Eager iteration of `model.transcribe(...).segments` generator                            | Yes (research-pitfall foot-gun explicitly handled in-code) | ✓ FLOWING |
| `_post_init` warmup log line   | `"Whisper model loaded and ready"` | `warm_model(settings)` populates the module-level singleton                          | Yes (the singleton is `WhisperModel(...)` with the live settings) | ✓ FLOWING |

## Behavioral Spot-Checks

| Behavior                                                                | Command                                                                                          | Result                                                | Status |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------- | ------ |
| Full pytest suite (minus 1 deselected real-model integration) is green | `uv run pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` | `424 passed, 1 deselected, 6 warnings in 2.28s`       | ✓ PASS |
| Lint is clean on `runos/` + `tests/`                                    | `uv run ruff check runos/ tests/`                                                                | `All checks passed!`                                  | ✓ PASS |
| `.env.example` documents all 3 WHISPER_* vars                           | `grep -c "WHISPER_MODEL_NAME\|WHISPER_COMPUTE_TYPE\|WHISPER_DEVICE" .env.example`                | `3`                                                   | ✓ PASS |
| `post_init` warms the model                                             | `grep "warm_model" runos/bot/app.py`                                                             | 2 matches (import + `asyncio.to_thread(warm_model, settings)`) | ✓ PASS |
| 20 MB guard constant + use site present                                 | `grep "MAX_VOICE_BYTES\|20 \* 1024 \* 1024" runos/bot/handlers.py`                               | 2 matches (definition at L44; use at L111)            | ✓ PASS |
| Voice handler registered behind `filters.VOICE`                         | `grep "filters.VOICE" runos/bot/app.py`                                                          | 2 matches (comment + actual `MessageHandler` registration) | ✓ PASS |
| Transcript reply HTML-escaped                                           | `grep "html.escape" runos/bot/handlers.py`                                                       | 2 matches (comment + actual call)                     | ✓ PASS |

## Requirements Coverage

| Requirement | Source Plan | Description                                                                                                                  | Status     | Evidence                                                                                                                                          |
| ----------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| VOICE-03    | 10-02       | Voice memos > 20 MB are gracefully rejected with a clear reply                                                              | ✓ SATISFIED | `MAX_VOICE_BYTES` + `OVERSIZED_REPLY` + pre-`get_file()` guard + `test_voice_handler_rejects_oversized_with_no_download` (with `get_file` raising). |
| VOICE-04    | 10-01/10-02 | Voice memos downloaded to gitignored local voice cache; transcribed locally — no audio bytes leave the laptop                | ✓ SATISFIED | `voice_cache_dir = content_root / "voice"` (content_root covered by `.gitignore` lines `.runos/` and `training/`); 0700 mkdir; faster-whisper runs in-process; no network call outside Telegram CDN download. |
| VOICE-05    | 10-01       | Default Whisper model is `small.en` int8 CPU; configurable via pydantic-settings (base.en / medium.en / large-v3-turbo)     | ✓ SATISFIED | 3 Settings fields with documented defaults; `test_warm_model_overridden_by_env` proves env override pipeline; `.env.example` lists swap options.  |
| VOICE-06    | 10-01       | Model loaded once at process start (singleton) and reused; first-run model download warmed at startup, not on first request | ✓ SATISFIED | Module-level `_MODEL` singleton; `warm_model` idempotent and called from `_post_init`; "Whisper model loaded and ready" log line emitted at startup. |

No orphaned requirements: phase frontmatter (VOICE-03, VOICE-04, VOICE-05, VOICE-06) exactly matches REQUIREMENTS.md's Phase 10 mapping.

## Anti-Patterns Found

| File                          | Line | Pattern | Severity | Impact                                                                                                                                       |
| ----------------------------- | ---- | ------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| _(none in modified files)_    | —    | —       | —        | No `TBD` / `FIXME` / `XXX` / `TODO` / `HACK` / `PLACEHOLDER` markers in `runos/bot/transcribe.py`, `runos/bot/handlers.py`, `runos/bot/app.py`. No empty-implementation returns or stub patterns. |

## Roadmap Deviations (Overridden at Planning Time)

Two roadmap success criteria for Phase 10 were intentionally narrowed during planning (see 10-CONTEXT.md "LOCKED" sections). Both are recorded as overrides in the frontmatter:

1. **Roadmap SC #3 — "warmed by one dummy transcription":** Implementation loads the model only; no dummy transcribe is performed. 10-CONTEXT.md explicitly chose this lighter approach ("Pre-loading at startup eliminates the cold-start latency on the first voice memo (~3-5s first-load)"). The REQUIREMENTS.md wording (VOICE-06 — "first-run model download is warmed at startup, not on first request") is satisfied.

2. **Roadmap SC #4 — `RUNOS_TRANSCRIBE_*` env-var names + `download_root` under data dir:** Implementation uses bare `WHISPER_*` names (matches bare `TELEGRAM_*` from Phase 9 and faster-whisper's own docs convention) and lets faster-whisper use the default `~/.cache/huggingface/hub/` (also explicit in 10-CONTEXT). VOICE-05 in REQUIREMENTS.md is satisfied (it only asks for configurability via pydantic-settings).

These are documented planning decisions, not implementation gaps.

## Human Verification Required

None for this verification pass. The phase is unit-test-pinned end-to-end with `transcribe_file` patched at handler boundaries. The single human-eyes-only smoke test (real `TELEGRAM_BOT_TOKEN` + real voice memo) is explicitly deferred in 10-02-SUMMARY.md ("Manual smoke test — Pending. This dev environment does not have a real `TELEGRAM_BOT_TOKEN` / `TELEGRAM_OWNER_CHAT_ID` configured"). The developer will run the smoke test on their own machine; the verifier records this as deferred-to-developer rather than a verification gap.

## Gaps Summary

None. All 8 goal-backward checks pass with codebase evidence. The phase delivers a complete vertical slice: owner voice memo → 20 MB pre-download guard → downloaded to gitignored `<content_dir>/voice/<msg>-<uid>.ogg` at 0700 → transcribed via warmed `faster-whisper` singleton on a worker thread → HTML-escaped italicised transcript reply. All 424 tests pass; ruff clean. The two roadmap-SC narrowings (dummy-transcribe; env-var naming + download_root) are documented planning decisions reflected in REQUIREMENTS.md's looser VOICE-05/06 wording and recorded as frontmatter overrides.

---

_Verified: 2026-05-27_
_Verifier: Claude (gsd-verifier)_
