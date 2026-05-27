---
phase: 10-voice-intake-transcription
plan: 01
subsystem: tempo.bot.transcribe
tags: [voice, transcription, faster-whisper, settings, warmup]
status: complete
requires:
  - tempo.bot.app (Phase 9 telegram bot scaffold)
  - tempo.config.Settings (existing pydantic-settings shape)
provides:
  - tempo.bot.transcribe.warm_model (idempotent WhisperModel singleton load)
  - tempo.bot.transcribe.get_model (raises RuntimeError if unwarmed)
  - tempo.bot.transcribe.transcribe_file (eager segments-generator consumer)
  - Settings.whisper_model_name / whisper_compute_type / whisper_device (BARE env-var aliases)
  - Settings.voice_cache_dir (derived under content_root)
  - tempo/bot/app.py::_post_init startup warmup (logs "Whisper model loaded and ready")
affects:
  - .env.example (new Whisper transcription section)
  - tempo/bot/__init__.py (re-exports warm_model / get_model / transcribe_file)
tech-stack:
  added:
    - faster-whisper 1.2.1
    - ctranslate2 4.7.2 (transitive)
    - PyAV 17.0.1 (transitive, bundles ffmpeg shared libs)
    - huggingface-hub 1.16.4 (transitive)
    - tokenizers 0.23.1 (transitive)
    - onnxruntime 1.26.0 (transitive)
    - numpy 2.4.6 (transitive)
  patterns:
    - Module-level singleton (mirrors connectors/garmin.py style)
    - validation_alias bypass of TEMPO_ env_prefix (mirrors TELEGRAM_* pattern)
    - asyncio.to_thread(...) wrap for blocking native init in async startup hook
    - _reset_for_tests() helper for module-level state hygiene in pytest
key-files:
  created:
    - tempo/bot/transcribe.py
    - tests/test_bot_transcribe.py
    - tests/fixtures/voice/sample.ogg
  modified:
    - pyproject.toml
    - uv.lock
    - tempo/config.py
    - tempo/bot/app.py
    - tempo/bot/__init__.py
    - .env.example
    - tests/test_config.py
decisions:
  - Integration test left UNMARKED (no slow marker convention defined in pyproject.toml yet) -- wall time ~34s on M-series with model download, <10s on warm cache.
  - voice_cache_dir is INTENTIONALLY kept out of ensure_dirs() so `tempo init` does not surface a voice/ dir for users who never run the bot. Plan 10-02 will create it lazily 0700 in the voice handler.
  - WHISPER_MODEL_NAME=base.en confirmed via mock (test_warm_model_overridden_by_env) -- not exercised end-to-end with a real model swap (would have doubled the test wall time).
  - Single commit per task (Task 1 = settings/.env/test commit, Task 2 = module + warmup + tests + fixture commit) rather than TDD-style RED/GREEN split, matching the phase frontmatter (type: execute, not type: tdd) and Task 1's prior style.
metrics:
  duration_seconds: 339
  duration_minutes_approx: 5.6
  completed: 2026-05-27
  tasks_completed: 2
  files_created: 3
  files_modified: 7
  tests_added: 12
  total_tests: 418
---

# Phase 10 Plan 01: Faster-Whisper Substrate Summary

Local-transcription substrate landed: `faster-whisper 1.2.1` is now a runtime dep, three new bare-env-var `WHISPER_*` Settings fields configure it (defaults: `small.en` / `int8` / `cpu`), and a module-level `WhisperModel` singleton in `tempo/bot/transcribe.py` is warmed once via `asyncio.to_thread(warm_model, settings)` from the existing `post_init` hook so the first voice memo does not pay the multi-second model load.

## Faster-whisper version installed

`faster-whisper==1.2.1` (range pin in `pyproject.toml`: `faster-whisper>=1.2.1`). Brings in:

| Package | Version | Role |
| ------- | ------- | ---- |
| ctranslate2 | 4.7.2 | Whisper inference engine; CPU on Mac (no Metal). |
| PyAV | 17.0.1 | Opus-in-OGG decode; bundles ffmpeg shared libs (no system ffmpeg needed). |
| huggingface-hub | 1.16.4 | Model download from HF Hub on first `WhisperModel(...)` call. |
| tokenizers | 0.23.1 | Whisper tokenizer. |
| onnxruntime | 1.26.0 | VAD (Silero) ONNX runtime. |
| numpy | 2.4.6 | Tensor math. |

## Integration test wall time + gate

`test_transcribe_file_real_fixture_returns_nonempty` is **unmarked** — `pyproject.toml` does not yet define a `slow` marker convention so adding one in this plan would be scope creep. Wall time on M-series in the local CI run was **34.25 s** (cold, first-run included `small.en` ~480 MB download + load). Re-running with the model cached completes in well under 10 s. The test asserts the transcript contains at least one of `hello / world / test / tempo / transcription` so it tolerates Whisper's small run-to-run variation.

If the suite ever needs to be sub-second for fast CI feedback, the next plan or a follow-up can:

1. Add `[tool.pytest.ini_options].markers = ["slow: real-model integration tests"]`.
2. Decorate this test with `@pytest.mark.slow`.
3. Default `pytest` to `-m "not slow"` while keeping a `tempo test --slow` recipe.

## `WHISPER_MODEL_NAME=base.en` swap

Tested **via mock only** (`test_warm_model_overridden_by_env`): with `WHISPER_MODEL_NAME=base.en`, `WHISPER_COMPUTE_TYPE=int8_float16`, `WHISPER_DEVICE=cuda` set via `monkeypatch.setenv`, the `WhisperModel` constructor receives `("base.en", device="cuda", compute_type="int8_float16", cpu_threads=4)`. A real end-to-end swap (loading `base.en` against the fixture) was skipped to keep the suite wall time bounded; the mock proves the env-override pipeline.

## Final test count

**418 passing** (411 after Task 1's `+5` config tests, then `+7` transcribe tests in Task 2). Baseline was 406 from Phase 9. Ruff check + format clean across `tempo/` and `tests/`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Setup - Worktree behind main]** The worktree branch `worktree-agent-a3fd8d04605b045c7` was created from commit `15c7103` (the v1.1 milestone start) which predates Phase 09's Telegram bot scaffold merging into `main`. This plan strictly depends on `tempo/bot/app.py::_post_init`, `tempo/bot/__init__.py`, and `tempo/bot/handlers.py` (none of which existed at the worktree's HEAD). Fast-forward merged `main` (a clean fast-forward, no conflicts) into the worktree branch to bring the Phase 09 deliverables in.

**2. [Setup - Plan files not yet committed]** The Phase 10 plan, context, and research files exist in the main repo's working tree but had not been committed before this executor started. Copied them into the worktree's `.planning/phases/10-voice-intake-transcription/` and `.planning/research/` so they are tracked alongside this plan's commits.

**3. [Rule 2 - critical correctness]** Added an extra test (`test_ensure_dirs_does_not_create_voice_cache_dir`) on top of the plan's four config tests. The plan's Step 3 explicitly notes "Do NOT add a `mkdir` call inside `ensure_dirs`" — pinning that behaviour with a regression test costs nothing and prevents a future "just create everything in ensure_dirs" refactor from silently surfacing `voice/` in `tempo init`.

**4. [Rule 2 - critical correctness]** Added an extra test (`test_transcribe_file_empty_segments_returns_empty_string`) covering the documented contract that VAD-filtered silence returns `""` rather than raising. Plan 10-02's voice handler will need to distinguish "transcribed nothing" from "errored" — pinning this now makes that branch safe to build on.

No issues required `Rule 4` (architectural decision). No auth gates were hit. Both tasks proceeded autonomously.

## Auto-fix Attempt Summary

| Task | Fix attempts | Outcome |
| ---- | ------------ | ------- |
| Task 1 | 1 (ruff format auto-reflow of long lines in `tempo/config.py`) | Applied via `ruff format`, all checks pass. |
| Task 2 | 1 (ruff format reflowed `tempo/bot/transcribe.py` get_model error and `tests/test_bot_transcribe.py` signatures) | Applied via `ruff format`, all checks pass. |

## Threat Flags

None. This plan adds no new network endpoints, no new auth paths, and no new file-access surface beyond what the plan's `<threat_model>` (implicit via `voice_cache_dir`) already covers — the directory is gitignored via the existing `content_dir`/`~/.tempo/` exclusions and creation is deferred to Plan 10-02 with explicit 0700 mode.

## Known Stubs

None. All implementations are wired end-to-end; no placeholder data or "coming soon" surfaces.

## Self-Check: PASSED

- [x] `tempo/bot/transcribe.py` exists.
- [x] `tests/test_bot_transcribe.py` exists.
- [x] `tests/fixtures/voice/sample.ogg` exists (6.4 KB).
- [x] `tempo/config.py` modified (whisper fields + voice_cache_dir property).
- [x] `tempo/bot/app.py` modified (warm_model call in _post_init + settings stashed).
- [x] `tempo/bot/__init__.py` re-exports warm_model / get_model / transcribe_file.
- [x] `.env.example` documents `WHISPER_*` vars.
- [x] `pyproject.toml` lists `faster-whisper>=1.2.1`.
- [x] Commit `d5f1117` exists (Task 1 — settings/config/.env/tests).
- [x] Commit `aa04fab` exists (Task 2 — transcribe module/warmup/tests/fixture).
- [x] `uv run pytest tests/` reports 418 passed.
- [x] `uv run ruff check tempo tests` + `ruff format --check` exit 0.
- [x] `grep "asyncio.to_thread(warm_model, settings)" tempo/bot/app.py` matches.
- [x] `from tempo.bot import warm_model, get_model, transcribe_file` succeeds.
