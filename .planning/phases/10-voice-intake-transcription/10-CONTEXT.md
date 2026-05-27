# Phase 10: Voice Intake + Local Transcription — Context

**Gathered:** 2026-05-27
**Status:** Ready for planning
**Source:** Inline orchestrator (derived from research + roadmap success criteria; user's architecture already endorsed)

<domain>
## Phase Boundary

**What this phase delivers:**

- New `faster-whisper` dep (`uv add faster-whisper`).
- Voice-message handler in `tempo/bot/handlers.py` that downloads `update.message.voice` into a gitignored local voice cache (`<content_dir>/voice/<message_id>-<file_unique_id>.ogg`), runs faster-whisper transcription, replies with the raw transcript in Telegram.
- New module `tempo/bot/transcribe.py` — singleton WhisperModel loaded at process start (warm), `transcribe_file(path) -> str` function. Default model `small.en` int8, configurable.
- New pydantic-settings fields: `whisper_model_name: str = "small.en"`, `whisper_compute_type: str = "int8"`, `whisper_device: str = "cpu"`. All `validation_alias`-d to bare names (`WHISPER_MODEL_NAME` etc.).
- 20 MB pre-download guard — if `update.message.voice.file_size > 20 * 1024 * 1024`, reply with a clear "file too large for Telegram bot API" message and DROP without downloading.
- Voice cache dir auto-created with 0700 permissions (mirrors existing `data_dir` pattern from `tempo/config.py`).
- Singleton model loaded at `Application` startup via `post_init` hook (warms first-run model download so the first voice memo doesn't time out).
- Transcript reply uses `ParseMode.HTML` with `html.escape()` applied (transcripts are untrusted input → escape).
- Logs per transcription: `transcribed <ogg-filename> · <duration>s audio · <transcribe-time>s wall · model=<name>`.
- `.env.example` documents the 3 new whisper vars (with explanatory comments — Mac CPU is the only path that works, `small.en` is the sane default).
- README mention in the "Telegram bot (v1.1)" section: "Send a voice memo — get the transcript back."

**What this phase does NOT deliver (out of scope):**

- Claude Code agent integration (Phase 11).
- `/new` command for session reset (Phase 11).
- launchd plist / KeepAlive (Phase 12).
- Voice-file retention / deletion policy (Phase 12).
- Top-level error handler / "something went wrong" reply (Phase 12 — Phase 10 errors crash the handler; that's fine for this vertical).
- Multi-language transcription (default `small.en` is English-only; user can swap to `small` if needed but multilingual UX is not in scope).
- Streaming transcription. Voice memos arrive as complete files.
- Voice-message metadata persistence (no SQLite table for messages yet; Phase 11 introduces session storage if needed).

</domain>

<decisions>
## Implementation Decisions (LOCKED)

### Library + model choice
- `faster-whisper` v1.2.x (`uv add faster-whisper`). Pure-Python install via uv; wheels exist for macOS arm64 and linux aarch64 across cp310-cp314.
- Default model **`small.en`** with `compute_type="int8"` and `device="cpu"`. This was confirmed in `.planning/research/transcription-research.md`: no Metal/GPU acceleration in CTranslate2 on Mac, so `large-v3-turbo` would take 20-30s for a 60s memo. `small.en` int8 = ~8-12s for 60s audio, ~500 MB RAM, accurate enough for runner jargon.
- Model is configurable via the 3 settings fields. User can swap to `base.en` / `medium.en` / `large-v3-turbo` if they want to wait.
- `WhisperModel(model_size_or_path=name, device=device, compute_type=compute_type)` — first-run downloads model to `~/.cache/huggingface/hub/` (Hugging Face default).

### Singleton + warmup
- One `WhisperModel` instance per process. Loaded ONCE at `Application` startup via `post_init` hook (`tempo/bot/app.py` extends the existing post-init that already runs `delete_webhook`).
- Singleton lives in module-level state inside `tempo/bot/transcribe.py` (`_MODEL: WhisperModel | None = None`, exposed via `get_model() -> WhisperModel` and `warm_model() -> None`).
- Pre-loading at startup eliminates the cold-start latency on the first voice memo (~3-5s first-load).
- Singleton is **not** thread-safe — faster-whisper's `transcribe()` blocks the event loop. For now, that's fine: voice memos are sequential per-user, and `concurrent_updates=True` only affects unrelated handlers. If concurrency becomes an issue in Phase 12 or later, wrap `transcribe()` in `asyncio.to_thread()`.
- Decision: **DO** wrap `transcribe()` in `asyncio.to_thread()` from day one — it's one line, prevents future foot-gun, and makes the bot responsive if other handlers are added.

### Voice cache directory
- New derived path on Settings: `voice_cache_dir = content_root / "voice"`.
- Created with 0700 permissions on first use (mirror existing `_ensure_dir` pattern in `tempo/config.py`).
- Gitignored — extend the existing `.tempo/` / `training/` / `~/.tempo/` gitignore lines to cover the cache. Since `voice_cache_dir` is derived from `content_root` (which is already gitignored via `~/.tempo/` and `training/`), no new gitignore line is strictly needed. Verify.
- Filename pattern: `<message_id>-<file_unique_id>.ogg`. `message_id` is per-chat unique; `file_unique_id` is Telegram's stable file id across all servers. Combo is collision-free.

### 20 MB guard
- Check `update.message.voice.file_size` BEFORE calling `get_file()` — Telegram's bot API caps downloadable file size at 20 MB. A larger file raises an error from `get_file()` itself; we want a clean, defensive guard with a clear user-facing reply.
- Reply: `"Sorry — that voice memo is over Telegram's 20 MB bot API limit. Try a shorter recording or split it."`
- If `file_size` is `None` (rare, but Telegram returns it as optional), proceed and let `get_file()` raise — that's a Telegram bug, not our problem.

### Handler shape
- New handler `voice_handler` in `tempo/bot/handlers.py`. Registered in `tempo/bot/app.py::build_application` with filter `filters.VOICE & filters.Chat(chat_id=settings.telegram_owner_chat_id)`.
- Handler flow:
  1. Defensive chat-id re-check (belt-and-braces, mirrors `start_handler` pattern).
  2. Size guard (above).
  3. `voice_file = await update.message.voice.get_file()` → `await voice_file.download_to_drive(custom_path=str(target_path))`.
  4. `transcript = await asyncio.to_thread(transcribe_file, target_path)`.
  5. Reply: `await update.message.reply_text(f"<i>{html.escape(transcript)}</i>", parse_mode=ParseMode.HTML)`. The italics give the user a quick visual confirmation that this is the transcript, not the agent reply.
  6. Log: `transcribed <filename> · <audio-duration>s audio · <wall>s · model=<name>`.
- No error handling at handler level in Phase 10. Errors propagate up to PTB's default error handler. Phase 12 wraps everything in a top-level "something went wrong" boundary.

### Transcription function shape
- `tempo/bot/transcribe.py`:
  - `_MODEL: WhisperModel | None = None` (module-level singleton).
  - `warm_model(settings: Settings) -> None`: idempotent. If `_MODEL is None`, load it; otherwise no-op. Called once at app startup.
  - `get_model() -> WhisperModel`: returns the loaded model or raises `RuntimeError("model not warmed")` if `warm_model` wasn't called first.
  - `transcribe_file(path: Path) -> str`: opens the file, calls `model.transcribe(str(path), language="en", beam_size=5)`. Joins all segment texts into one string. Returns the joined transcript (stripped).
- **Trap from research:** `segments` is a generator — iterate it eagerly (`list(segments)`) or transcription silently doesn't run. Spell this out in code + a comment.
- Performance budget: 60s of audio → ~8-12s transcription on M-series CPU at `small.en` int8. Log the wall-clock time per transcription to make this visible.

### Thread oversubscription guard
- Set `OMP_NUM_THREADS=4` in the launchd plist (Phase 12) and document in `docs/TELEGRAM_BOT.md`. For Phase 10 development on Mac, the default is acceptable, but the research notes thread oversubscription as a real pitfall.
- Add `cpu_threads=4` argument when creating `WhisperModel` to keep the model from spawning unbounded threads.

### Testing strategy
- **Unit tests** for `transcribe_file` using a tiny pre-recorded `.ogg` fixture committed to `tests/fixtures/voice/sample.ogg` (~2-3 seconds of clear speech, e.g. "hello world this is a test"). Asserts the transcript contains the expected words (substring match, not exact — Whisper output isn't 100% deterministic).
- **Handler test** using PTB update builders + monkey-patched transcribe to bypass the model load: prove the 20 MB guard rejects + the small file flows through.
- **Settings test**: 3 new whisper fields default to the documented values.
- **Singleton test**: `warm_model` is idempotent; `get_model` raises if not warmed.
- Expect ~5-7 new tests. Total target: ~412 passing.
- Skip the actual model load in CI by mocking `WhisperModel(...)` at the test level — committing a model file is impractical (244 MB) and slow.

### Settings additions
- `tempo/config.py::Settings`:
  - `whisper_model_name: str = Field(default="small.en", validation_alias="WHISPER_MODEL_NAME", description="faster-whisper model name")`
  - `whisper_compute_type: str = Field(default="int8", validation_alias="WHISPER_COMPUTE_TYPE", description="int8/int8_float16/float16/float32")`
  - `whisper_device: str = Field(default="cpu", validation_alias="WHISPER_DEVICE", description="cpu (default; CTranslate2 has no Metal support — cuda only on Linux+NVIDIA)")`
  - `voice_cache_dir: Path` derived property (`content_root / "voice"`).
- `.env.example` block:
  ```
  # Whisper transcription (Phase 10)
  # Model: small.en (default, ~500MB) / base.en / medium.en / large-v3-turbo (~800MB, slower on CPU)
  # WHISPER_MODEL_NAME=small.en
  # WHISPER_COMPUTE_TYPE=int8
  # WHISPER_DEVICE=cpu
  ```
  (All three commented because the defaults are sane.)

### Performance + UX
- User experience target: voice memo arrives → transcript appears in chat within 15s for a typical 30-60s memo. Acceptable; comparable to other voice-assistant latencies.
- If real-world latency is bad, the model knob lets the user step down to `base.en` (smaller, faster, slightly less accurate).
- Bot does NOT acknowledge receipt before transcribing (no "transcribing..." typing indicator in Phase 10). That's a nice-to-have for Phase 11+ when wait times grow with the agent loop.

</decisions>

<canonical_refs>
- `.planning/research/transcription-research.md` — faster-whisper specifics, model trade-offs, traps (generator iteration, model singleton, thread oversubscription, OGG handling via bundled PyAV).
- `.planning/research/telegram-bot-research.md` — Voice download API + 20 MB cap + concrete code.
- `tempo/bot/app.py` — `post_init` hook to extend with `warm_model` call.
- `tempo/bot/handlers.py` — pattern to mirror for `voice_handler`.
- `tempo/config.py` — Settings field pattern, `_ensure_dir` helper, derived-path properties.
- `tempo/connectors/garmin.py` — for the singleton pattern (`_authenticated_client` style, though simpler).
- `tests/test_bot_app.py` — test pattern for PTB handlers.
- `tests/test_config.py` — Settings test pattern.

</canonical_refs>

<specifics>
- faster-whisper bundles PyAV which natively decodes Opus-in-OGG. No ffmpeg dependency. Just pass the `.ogg` path to `model.transcribe()`.
- `model.transcribe()` returns `(segments, info)`. `info` has `info.duration` (audio length in seconds). Useful for the log line.
- Telegram's voice format is consistently Opus mono ~16 kbps — small files, fast to download.
- The 20 MB limit is a hard Telegram cap. For voice memos this is ~10+ minutes of audio — well beyond any reasonable user use case. Guard is for safety, not common-case.
- Whisper's `beam_size=5` is the conventional default that trades a small amount of speed for noticeably better accuracy on hesitant or noisy speech (matters for outdoor/post-run memos).

</specifics>

<deferred>
- `/transcribe-only` slash command (text reply only, no agent). Probably never needed — current handler shape produces this as the de-facto behavior.
- Diarization, speaker separation — not applicable (single user, single mic).
- Multilingual transcription — `small.en` is English-only by design. User can swap to `small` (multilingual) via env var if they ever record in another language.
- Background warming of next-larger model. Premature.
- Voice file deletion policy (Phase 12).
- "Transcribing..." typing indicator. Add when latency grows in Phase 11.
</deferred>

---

*Phase: 10-voice-intake-transcription*
*Context gathered: 2026-05-27 via inline orchestrator (research + roadmap success criteria + endorsed architecture)*
