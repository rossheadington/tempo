# Transcription Research — faster-whisper

**Researched:** 2026-05-27
**Scope:** Local voice-memo transcription for RunOS's Telegram bot milestone (Mac-first, Pi 5 portability).
**Overall confidence:** HIGH on library choice and pipeline shape; MEDIUM on exact perf numbers (workload-dependent).

## Recommendation

**Confirm `faster-whisper` (latest 1.2.1, Oct 2025). Push back on `large-v3-turbo` for the Mac milestone — use `small.en` or `base.en` instead, and keep `turbo` as an opt-in.** faster-whisper has no Metal/GPU acceleration on Apple Silicon (CTranslate2's GPU backend is CUDA-only); everything falls back to CPU using Apple Accelerate BLAS. `large-v3-turbo` on M-series CPU runs ~3–5× realtime in practice for short clips — fine for batch but borderline for a "send memo, get reply" Telegram UX where a 90-second memo would take 20–30s of model time on top of model load. For a single-user voice-memo flow where every memo is English running monologue, `small.en` at int8 gives sub-2× realtime on M-series CPU, fits in <500 MB RAM, and the WER is more than acceptable for journaling. If quality turns out to matter (rare proper nouns, training jargon), bump to `medium.en` or finally `large-v3-turbo` — but make it a `pydantic-settings` knob, not the default. The pipeline is `Telegram .ogg → faster-whisper.transcribe(path, vad_filter=True) → str` — no separate ffmpeg step needed because faster-whisper bundles PyAV which handles Opus-in-OGG natively. For the Pi 5 future port, `large-v3-turbo` is a non-starter; `tiny.en`/`base.en` are the only realistic options.

## faster-whisper install + runtime

**Install (Mac, uv):** `uv add faster-whisper` — just works. CTranslate2 4.7.2 publishes prebuilt `macosx_11_0_arm64` wheels for cp310–cp314, including `cp314-cp314t` (free-threaded), so Python 3.14 is covered. PyAV is pulled in transitively and bundles its own FFmpeg shared libs; **you do not need a system `ffmpeg`**. No OpenMP/cuBLAS gymnastics on Mac — those are CUDA-only concerns. Total install footprint is around 80 MB before any models download. Confidence: HIGH (PyPI wheel index + faster-whisper README).

**Install (Pi 5, ARM64 Linux):** CTranslate2 publishes `linux_aarch64` wheels — also "just works" with `uv add`. The Pi 5 caveat is performance, not packaging: ARM CPU performance was historically weak (issue #38) but improved with later CTranslate2 releases that wired Ruy/oneDNN for ARM. Plan for `tiny.en`/`base.en` only on Pi 5; `large-v3-turbo` on a Pi is hours-per-minute territory and not viable.

**Metal/GPU acceleration:** **There is none for faster-whisper.** CTranslate2 ships only CUDA + CPU compute backends; the Metal/MPS feature request has sat open since 2023 (issue #515) with no roadmap commitment. On Mac, faster-whisper unambiguously runs on CPU, accelerated by Apple Accelerate BLAS (the CTranslate2 macOS wheel links against Accelerate). This is the single most important constraint for the milestone: any "faster-whisper + Apple GPU" content you see online is either confused or referring to a different library (`insanely-fast-whisper`, `mlx-whisper`, `whisper.cpp`). Confidence: HIGH.

**Realistic CPU latency on M-series (no Metal):** Order-of-magnitude figures, single thread of audio:

| Model | Disk | RAM at inference (int8) | Realtime factor (CPU, M2/M3) | 60s clip → wall time |
|-------|------|--------------------------|-------------------------------|----------------------|
| `tiny.en` | ~75 MB | ~200 MB | ~15–25× | ~2–4 s |
| `base.en` | ~145 MB | ~300 MB | ~10–15× | ~4–6 s |
| `small.en` | ~480 MB | ~500–700 MB | ~5–8× | ~8–12 s |
| `medium.en` | ~1.5 GB | ~1.5 GB | ~2–3× | ~20–30 s |
| `large-v3-turbo` | ~1.6 GB | ~2 GB | ~3–5× | ~12–20 s |
| `large-v3` | ~3 GB | ~3 GB | ~1× | ~60 s |

Numbers above are CPU-only, int8 quantization, beam_size=5, VAD on. The turbo's encoder-equal-to-v3 + 4-layer decoder means it's faster than `large-v3` on CPU but still much slower than `small`. Cold start: first run downloads the model from HF Hub (one-time, can be minutes on `large-v3-turbo`) and then loading the int8 model into RAM is ~1–3s on subsequent runs. Confidence: MEDIUM on the table (numbers vary by chip generation, audio content, beam settings).

**UX implication for Telegram:** A typical voice memo is 15–90s. With `small.en`, even a 90s memo finishes in ~10–15s — fine for a chatbot. With `large-v3-turbo` you're at 18–30s, which crosses into "feels slow" territory and risks Telegram bot timeout patterns. Recommend `small.en` as default, expose `RUNOS_TRANSCRIBE_MODEL` as a config knob.

## Model selection

**`large-v3-turbo` IS available in faster-whisper** — load it as `WhisperModel("turbo", ...)` or `WhisperModel("large-v3-turbo", ...)`. The README explicitly lists `turbo` as a supported size, and there are well-maintained CT2-converted weights on HF (`deepdml/faster-whisper-large-v3-turbo-ct2`, `mobiuslabsgmbh/faster-whisper-large-v3-turbo`). So the choice is valid — the question is whether you *want* it.

**Turbo architecture:** Same encoder as `large-v3`, decoder pruned 32→4 layers, then fine-tuned to recover accuracy. WER within ~0.4 pp of `large-v3` on most benchmarks. **One trap: turbo dropped translation training data — it transcribes in source language only.** Not relevant for English voice memos, but worth knowing.

**Why I'm pushing back on turbo for v1:**
1. **No Metal means turbo is still slow on Mac.** The well-known "2× realtime" figures for turbo are all GPU benchmarks. On CPU, it's ~3–5× realtime, which is faster than `large-v3` but ~3× slower than `small.en`.
2. **Voice memos are short, monolingual, casual speech.** `small.en` handles this fine. The accuracy delta vs. turbo on casual English speech is small enough to be invisible in journaling output. Where turbo wins big — long-form, accented, multilingual, technical content — doesn't match the use case.
3. **RAM headroom matters on a personal Mac.** Turbo int8 at ~2 GB is fine on a 16 GB Mac, but the model stays resident if you keep the connector hot. `small.en` at ~500 MB is friendlier alongside everything else (Claude Code, browsers, Strava sync running).
4. **Pi 5 portability.** Turbo is unusable on Pi 5; `small.en` is borderline; `base.en`/`tiny.en` are realistic. If the architecture targets Pi 5 eventually, settling on `small.en` (with a fallback config to `base.en`) keeps one code path.

**Recommended config knob:**
```python
# pydantic-settings field
transcribe_model: str = "small.en"        # "tiny.en" | "base.en" | "small.en" | "medium.en" | "turbo"
transcribe_compute_type: str = "int8"     # int8 on CPU, float16 only matters on GPU
transcribe_language: str = "en"
```

`distil-large-v3` is *not* recommended as a fallback — it predates turbo, has measurably worse WER, and turbo replaces its niche.

Confidence: HIGH on availability, MEDIUM on the small.en-vs-turbo perceived quality call (depends on memo content).

## Alternatives considered

**`whisper.cpp`** — Pure C++ with native Metal acceleration. On M2 Pro with Metal + `large-v3-turbo`, 60s of audio in ~2.8s — genuinely 5–10× faster than faster-whisper on the same chip. The catch: it's not a Python lib. You'd ship a binary, call it via subprocess, parse stdout/JSON. Adds a build step (or a vendored binary), and the .gguf model format is separate from HF's CT2 weights. **Verdict:** Best raw perf on Mac, worst DX for a uv-based Python project. Worth revisiting only if `faster-whisper` CPU latency becomes a real UX problem.

**`mlx-whisper`** — Apple's MLX framework, fastest on Apple Silicon by ~30–50% over whisper.cpp. Pure Python install (`uv add mlx-whisper` works), uses unified-memory GPU acceleration natively. **Catch: Mac-only.** MLX doesn't run on Linux/ARM, so the Pi 5 port would need a second code path. **Verdict:** Would be the right answer if Mac were the only target; the Pi 5 portability constraint kills it. Reconsider if Pi 5 is dropped from the roadmap.

**OpenAI `whisper` (official PyPI)** — PyTorch-based, supports MPS via `--device mps` since PR #382. Slower than faster-whisper on every platform, heavier deps (Torch is ~700 MB), and on Mac MPS support has historically been buggy for Whisper specifically. **Verdict:** No reason to use this.

**`insanely-fast-whisper`** — Built on HF Transformers with MPS support. Lower accuracy than faster-whisper for the same nominal model, less battle-tested. **Verdict:** Skip.

**Recommendation stands: faster-whisper.** It's the best Python-native, cross-platform (Mac CPU + Linux ARM + Linux/Windows CUDA) option, and the perf gap vs. whisper.cpp/MLX only matters if you're transcribing hours per day, not a handful of voice memos.

## OGG/Opus handling

**faster-whisper accepts the .ogg path directly. No ffmpeg step needed.** The `WhisperModel.transcribe()` method takes a file path string and decodes via bundled PyAV (which links FFmpeg's libraries into the wheel). Opus-in-OGG is handled out of the box.

**Recommended pipeline:**

```python
from faster_whisper import WhisperModel

# Load once at process start; reuse across transcriptions
model = WhisperModel(
    "small.en",
    device="cpu",
    compute_type="int8",
    cpu_threads=4,           # tune; see Pitfalls
    download_root=settings.models_dir,  # keep models inside the project, not ~/.cache
)

def transcribe(ogg_path: pathlib.Path) -> str:
    segments, info = model.transcribe(
        str(ogg_path),
        language="en",
        vad_filter=True,                      # default; Silero VAD strips silence
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
    )
    # segments is a generator — must consume to actually run inference
    return " ".join(seg.text.strip() for seg in segments).strip()
```

**Telegram bot wiring:** `python-telegram-bot` gives you `Voice.get_file().download_to_drive(path)` which yields an `.ogg`. Hand the path straight to `transcribe()`. No format conversion needed.

**If PyAV ever chokes** on a specific file (issue #988 reports rare PyAV decode aborts where ffmpeg-CLI would have succeeded): keep a fallback path that shells out to `ffmpeg -i in.ogg -ar 16000 -ac 1 -c:a pcm_s16le out.wav` and feed the WAV. Don't build this on day one; only add if you see decode failures in practice.

## Streaming + chunking

**File-based is correct for this use case.** Telegram delivers a complete `.ogg` blob — there's no live audio to stream. Streaming/incremental APIs (`mlx-streaming-whisper` and similar) exist for mic-input agents and add complexity (windowed VAD, partial-hypothesis stitching) that you don't need.

**Chunking for long memos:** faster-whisper internally segments via Silero VAD and decodes in 30s windows, so a single `.transcribe()` call on a 5-minute memo just works — no manual chunking required. The library handles context carry-over between windows. The only practical ceiling is wall time: with `small.en` a 5-min memo is ~30–50s of inference, which you might want to acknowledge to the user ("Got it — transcribing...") rather than block silently. For v1, voice memos will be short (<2 min); revisit only if usage shows otherwise.

**`vad_filter=True` is on by default** and worth keeping — strips silences >2s, broadens windows by 100ms on each side to avoid clipping consonants. Reduces wall time 30–60% on memos with pauses. Confidence: HIGH.

## Pitfalls

- **`segments` is a generator.** Inference does not run until you iterate. `list(segments)` or a `for` loop is mandatory; calling `transcribe()` and immediately checking `info` will look like it ran but did nothing. Most-reported faster-whisper foot-gun.
- **Model download on first run is silent and slow.** `small.en` is ~480 MB, `large-v3-turbo` is ~1.6 GB, pulled from HF Hub on first `WhisperModel(...)` call with no progress bar by default. For a Telegram bot, do a warm-up call at startup so the user-visible request isn't paying download cost. Set `download_root` to keep models inside the RunOS project tree (gitignored), not buried in `~/.cache/huggingface`.
- **Memory growth across many transcriptions.** Documented issues (#249, #390, #1055, #660): RAM creeps up over hundreds of sequential transcriptions, worse with parallel calls. For a single-user voice-memo flow (<50/day) this won't bite. If the bot runs for weeks without restart, plan to either (a) recycle the process daily via launchd, or (b) keep the model in a subprocess that can be torn down. Don't share one `WhisperModel` across threads in parallel.
- **Thread oversubscription.** Default `cpu_threads=0` lets CTranslate2 auto-detect, which can mean "use all 10 perf cores" and starve the rest of the system. Set explicitly: `cpu_threads=4` on an M-series Mac is a good default. Also set `OMP_NUM_THREADS=4` in the launchd plist env to prevent the BLAS layer from spawning its own pool on top.
- **Don't load the model per request.** Model init is 1–3s of overhead. Load once at process start, hold a module-level singleton, reuse across all incoming voice memos.
- **Beam size silently changes accuracy.** faster-whisper defaults `beam_size=5`; openai-whisper defaults to 1. If you benchmark against another implementation, match the setting. For voice memos, beam_size=5 is fine.
- **CT2 weights are not interchangeable across model variants.** `large-v3-turbo` weights are distinct from `large-v3`; downloading "turbo" pulls the right CT2 model from HF. Don't try to convert PyTorch turbo weights manually — use the published CT2 conversions.
- **No translation in `turbo`.** If you ever want non-English memos translated to English, switch model to `large-v3`. For RunOS (English only), irrelevant.
- **Telegram voice memo edge case:** Some Telegram clients send memos as `.oga` extension instead of `.ogg`. Same Opus-in-Ogg payload; PyAV decodes either, but if you key off the extension, normalize first.

## Sources

- [SYSTRAN/faster-whisper (GitHub, README)](https://github.com/SYSTRAN/faster-whisper) — install, model names, PyAV bundling, beam-size default. v1.2.1 (Oct 2025). Confidence: HIGH.
- [faster-whisper on PyPI](https://pypi.org/project/faster-whisper/) — current version + supported model identifiers (`large-v3`, `turbo`, `distil-large-v3`). Confidence: HIGH.
- [ctranslate2 on PyPI](https://pypi.org/project/ctranslate2/) — wheel availability incl. `macosx_11_0_arm64` for cp310–cp314 and `linux_aarch64`. Confidence: HIGH.
- [Issue #515: AMD and/or Apple Metal/MPS acceleration](https://github.com/SYSTRAN/faster-whisper/issues/515) — confirms no Metal/MPS in CTranslate2; CPU-only on Mac. Confidence: HIGH.
- [Issue #1030: Benchmark faster-whisper turbo v3](https://github.com/SYSTRAN/faster-whisper/issues/1030) — turbo perf vs. v3 in faster-whisper. Confidence: MEDIUM.
- [Issue #38: Slower than original Whisper on ARM 64-bit](https://github.com/SYSTRAN/faster-whisper/issues/38) — Pi/ARM perf history. Confidence: HIGH.
- [Issue #249 / #390 / #1055 / #660: Memory growth & leaks](https://github.com/SYSTRAN/faster-whisper/issues/1055) — long-running memory behavior. Confidence: HIGH.
- [Issue #988: PyAV decode stops where ffmpeg continues](https://github.com/SYSTRAN/faster-whisper/issues/988) — fallback rationale. Confidence: HIGH.
- [Issue #207: FFMPEG vs PyAV](https://github.com/SYSTRAN/faster-whisper/issues/207) — bundled PyAV handles standard formats incl. OGG/Opus. Confidence: HIGH.
- [Voice Activity Detection (DeepWiki)](https://deepwiki.com/SYSTRAN/faster-whisper/5.2-voice-activity-detection) — Silero VAD defaults, silence handling. Confidence: HIGH.
- [openai/whisper Discussion #2363: Turbo model release](https://github.com/openai/whisper/discussions/2363) — turbo architecture (4-layer decoder, no translation training). Confidence: HIGH.
- [deepdml/faster-whisper-large-v3-turbo-ct2 (HF)](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2) — CT2-converted turbo weights, speed benchmarks. Confidence: HIGH.
- [Whisper Performance on Apple Silicon — voicci.com](https://www.voicci.com/blog/apple-silicon-whisper-performance.html) — M1–M4 benchmark context. Confidence: MEDIUM.
- [whisper.cpp Metal on Apple Silicon — fazm.ai](https://fazm.ai/blog/whisper-cpp-metal-apple-silicon) — whisper.cpp Metal numbers, 60s/2.8s figure for turbo on M2 Pro. Confidence: MEDIUM.
- [Streaming with Whisper in MLX vs Faster-Whisper vs IFW (Medium)](https://medium.com/@GenerationAI/streaming-with-whisper-in-mlx-vs-faster-whisper-vs-insanely-fast-whisper-37cebcfc4d27) — alternatives comparison. Confidence: MEDIUM.
- [Modal blog: Choosing between Whisper variants](https://modal.com/blog/choosing-whisper-variants) — practical decision matrix. Confidence: MEDIUM.
- [Performance Evaluation of Whisper on Raspberry Pi (ACM, Dec 2025)](https://dl.acm.org/doi/10.1145/3769102.3774244) — Pi 5 model-suitability findings. Confidence: HIGH.
