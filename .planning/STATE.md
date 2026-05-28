---
gsd_state_version: 1.0
milestone: v1.4
milestone_name: weight-tracker
status: shipped
stopped_at: v1.4 (Phase 15) complete; weight tracker live in recovery report.
last_updated: "2026-05-28T11:15:00.000Z"
last_activity: 2026-05-28 — Phase 15 (Weight Tracker, v1.4) shipped end-to-end. New `tempo/analysis/weight.py` (lenient parser + kg/lb normalisation + 7d/28d windows + EWMA alpha=0.1 trend + unit_mixed flag); `Settings.weight_path` derived property; `RecoveryAssessment` gains `weight`/`weight_present` fields; `_render_weight_section` enforces the 3-state degradation rule (absent / stale >14d / current); `runner.generate_recovery` + `generate_all` thread `weight_path`; CLI passes `settings.weight_path` at both call sites. `weight.md.example` (14 mixed-unit entries) + `docs/WEIGHT.md` (199 lines) + README mention. 619 tests green (+26 from Phase 14), ruff clean. Verifier PASS 5/5.
progress:
  total_phases: 1
  completed_phases: 1
  completed: [15]
  total_plans: 3
  completed_plans: 3
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-26)

**Core value:** Turn scattered training and health data into trustworthy, structured signal that tells the user when to push, when to back off, and whether they're on track — combining objective data (Strava/Garmin) with their own plan and reflections.
**Current focus:** v1.4 complete (weight tracker). v1.1 + v1.2 + v1.3 already shipped. Pi port deferred. Nutrition tracker (v1.5 / Phase 16) is the next planned phase.

## Current Position

Phase: Phase 15 (Weight Tracker, v1.4) — COMPLETE
Plans: 15-01 + 15-02 + 15-03 — all COMPLETE
Status: v1.4 SHIPPED. New `weight.md` markdown tracker with a lenient parser (kg / lb / lbs accepted; latest-wins on duplicate dates; out-of-range guard catches typos), a 7d/28d rolling rollup with kg-normalisation and an EWMA trend (alpha=0.1, ~7-entry half-life), surfaced in the recovery report as a `## Weight` section with the same 3-state degradation rule (absent → omit / stale >14d → one-line nudge / current → full rollup line with Unicode-minus / plus-minus delta and an optional `_(mixed kg/lb in log — normalised to kg)_` caveat) that heat + strength already use. Plan 15-01 added the parser + rollup + `Settings.weight_path` + 19 unit tests. Plan 15-02 wired the rollup into `RecoveryAssessment` + `_render_weight_section` + `runner.generate_recovery`/`generate_all` + CLI; added 7 recovery-integration tests. Plan 15-03 shipped `weight.md.example` (14 mixed-unit entries, 4 with notes, 1 with embedded `|`), `docs/WEIGHT.md` (199 lines), and a README mention. 619 tests green (+26 from Phase 14), ruff clean. All 5 WEIGHT-* requirements satisfied. Verifier PASS 5/5.
Last activity: 2026-05-28 — Phase 15 verified. v1.4 milestone closed. The recovery report now carries Heat → Strength → Weight as its non-running-context cluster.

## What's Done (Phase 15: Weight Tracker — v1.4 milestone)

- `tempo/analysis/weight.py` (303 LoC) — three `@dataclass(frozen=True, slots=True)` types (`WeightEntry`, `WeightContext`, `WeightRollup`), the `_to_kg` + `_parse_entry_line` helpers, the lenient `parse_weight(path)` reader, and the `weight_rollup(entries, today)` function. Single regex grammar `- YYYY-MM-DD: <weight> [kg|lb|lbs] [| notes: ...]`; `lbs` normalises to `lb`; missing unit defaults to `kg`. Out-of-range guard: kg-equivalent must satisfy `20 < kg < 500` (catches `7.24 kg`, `724 kg`, `1600 lb`). Latest-wins on duplicate dates via `dict[date, WeightEntry]`. Lenient throughout: missing file → `present=False`, malformed lines recorded in `malformed_lines`, never raises. (WEIGHT-01, WEIGHT-02)

- `weight_rollup` math: windows `(today - N, today]` left-open right-closed (same-day weigh-in always counts; `today - N` itself excluded); every numeric output kg-normalised via `_to_kg` (lb × 0.453592); EWMA `alpha=0.1` seeded from the FIRST entry's kg-converted weight, iterated forward; `latest_entry` preserves original unit; `unit_mixed=True` iff both `kg` and `lb` appear. Hand-computed EWMA `[70, 80, 90] → 72.9` proven by test. (WEIGHT-03)

- `Settings.weight_path` derived property in `tempo/config.py` (mirrors `strength_path` exactly): returns `content_root / "weight.md"`. No new env var. (WEIGHT-01)

- `RecoveryAssessment` gains `weight: WeightRollup | None = None` and `weight_present: bool = False` (defaults preserve back-compat). `assess_recovery_from_db` accepts `weight_path: Path | None = None` and threads it through the same single-reconstruction pattern that already carries heat + strength. `_render_weight_section` enforces the 3-state rule: absent OR `latest_entry is None` → omit; `days_since_last > 14` → `_Last weigh-in N days ago — log a current reading to keep the rollup live._`; current → `{latest} kg today · 7d avg {a7} kg · 28d avg {a28} kg · trend {ewma} kg · {±X.X} kg vs 28d baseline`. `_fmt_weight_delta` uses Unicode minus (U+2212) for negatives, plus-minus (U+00B1) for near-zero, plain `+` for positives, one decimal throughout. When `unit_mixed=True`, trailing ` _(mixed kg/lb in log — normalised to kg)_` caveat is appended. Section is placed AFTER `## Strength & conditioning` so the recovery report's non-running-context cluster reads Heat → Strength → Weight. (WEIGHT-04)

- `runner.generate_recovery` + `runner.generate_all` accept `weight_path: Path | None = None` and thread it through `assess_recovery_from_db` alongside heat + strength. CLI: `tempo analyze` (L613) and `tempo analyze recovery` (L684) both pass `weight_path=settings.weight_path` next to `settings.strength_path`. (WEIGHT-04)

- `weight.md.example` (71 lines) — committed worked example with 14 entries spanning 2026-05-15→2026-05-28 (2 weeks). 12 × `kg`, 1 × `lb`, 1 × `lbs` (demonstrating the normalisation contract); 4 entries carry `| notes: ...` annotations; one entry's notes contain an embedded `|` pipe (proving only the FIRST `| notes:` is the split point). Numbers are synthetic. Parses cleanly through `tempo.analysis.weight.parse_weight`: `present=True`, 14 entries, `malformed_lines=()`. Doubles as a parser regression fixture. (WEIGHT-05)

- `docs/WEIGHT.md` (199 lines) — end-to-end format documentation mirroring `docs/STRENGTH.md`: `## Format` (entry grammar + 4-entry CONTEXT example as fenced code), `## Lenient parsing` (missing-file degradation, malformed-line recording, out-of-range guard, never-logs-values privacy guarantee), `## Rollup semantics` (windows, kg-normalisation, EWMA alpha=0.1 + ~7-entry half-life), `## Recovery report integration` (3-state rule, placement, mixed-unit caveat), `## Agent-append guidance` (single-line append at EOF, `cat >> weight.md` is safe, latest-wins enables append-only corrections), `## What's NOT in this layer` (deferred features). (WEIGHT-05)

- `README.md` — Tracker-files paragraph (L424-428) updated to include `weight.md.example` / `weight.md` alongside `races.md` / `heat.md` / `strength.md` with a link to `docs/WEIGHT.md`. (WEIGHT-05)

- `tests/test_weight.py` (315 LoC, 19 tests) — covers `_to_kg`, `_parse_entry_line`, the full `parse_weight` lenient contract (missing file, happy path with mixed kg/lb/lbs, malformed lines, latest-wins on duplicates, optional notes with embedded `|` pipes, out-of-range rejection, header/blank-line/prose ignoring), and the rollup (empty, single-entry-today, left-open-right-closed window math, EWMA hand-computed expectation, unit-mixed kg-normalisation, days-since-last).

- `tests/test_recovery.py` — 7 new tests under `# ---- Weight section ----` divider: `test_recovery_renderer_omits_weight_section_when_absent`, `test_recovery_renderer_omits_weight_when_present_but_empty`, `test_recovery_renderer_emits_stale_nudge_when_last_weigh_in_over_14d`, `test_recovery_renderer_emits_full_rollup_when_current`, `test_recovery_renderer_appends_mixed_unit_caveat`, `test_recovery_renderer_weight_section_follows_strength`, `test_fmt_weight_delta_signs`.

- **Test totals:** 619 tests green (was 593 after Phase 14; +26 from this phase). `ruff check tempo/ tests/` clean. Zero `TODO` / `FIXME` / `XXX` / `TBD` / `HACK` / `placeholder` markers in any `tempo/analysis/weight.py` or `tests/test_weight.py`. The one slow Whisper test stays deselected per project convention.

- **Verifier outcome:** PASS 5/5 success criteria. See `.planning/phases/15-weight-tracker/15-VERIFICATION.md`.

### Conventions established this phase

- **Markdown trackers stay markdown until they prove themselves.** Weight is the fourth tracker (races / heat / strength / weight) to ship as a lenient markdown-only Layer-1 surface before any structured DB table. Pattern stays consistent: frozen+slots dataclasses, lenient parser, never-raises contract, `Settings.{name}_path` derived property, recovery-report integration with the 3-state degradation rule, committed `.example` file that doubles as a parser fixture, `docs/{NAME}.md` end-to-end. Next tracker (nutrition, Phase 16) will follow the same shape.

- **kg-normalisation at rollup time, unit preserved on the entry.** The rollup carries kg numerics; the latest entry preserves its original unit so the user-facing renderer can still report `72.4 kg today` faithfully when the source was kg, OR `72.6 kg today` after auto-converting from `160.0 lb`. Mixed-unit logs get a footnote caveat rather than a broken rollup — the user who logs lb on a hotel scale and kg at home shouldn't see a discontinuity.

## What's Done (Phase 14: First-Run Setup Wizard — v1.3 milestone)

- `tempo/setup/env_io.py` — `read_env(path) -> dict[str, str]` (lenient: missing → `{}`, blank/comment lines skipped, duplicate keys last-wins, surrounding double-quotes stripped) and `atomic_write_env(path, updates, delete_keys)`. The write template mirrors `tempo/connectors/tokens.py` exactly: `tempfile.mkstemp` in destination dir → `os.fchmod(fd, 0o600)` → write + `flush` + `fsync` → `os.replace(tmp, path)` → best-effort `fsync` on the parent dir → final `chmod 0o600`. Crash mid-write leaves either the prior complete `.env` or the new one, never a torn file. Comments + untouched key ordering preserved byte-identically; values with spaces / `$` / `#` / tab are double-quoted on write. Module never logs / echoes a value. (SETUP-03)

- `tempo/setup/state.py` — `@dataclass(frozen=True, slots=True) class InstallState` with 7 bool fields (`db_initialised`, `content_dir_set`, `strava_configured`, `garmin_configured`, `telegram_configured`, `daily_scheduler_installed`, `bot_scheduler_installed`); `detect_install_state(settings)` is pure read-only over filesystem + a single read-only SQLite connection (closed in `finally`) for the schema-version check. No network, no `launchctl`. Plist presence at `~/Library/LaunchAgents/com.tempo.{daily,telegram-bot}.plist` is the contract. (SETUP-02)

- `tempo/setup/prompts.py` — thin `typer.prompt` / `typer.confirm` / `typer.secho` wrappers with `[set]` / `[done]` / `[fresh]` / `[skip]` coloured indicators. `prompt_secret(label)` always passes `hide_input=True, confirmation_prompt=False`. Single mockable surface for tests. (SETUP-03)

- `tempo/setup/wizard.py` (~670 LOC) — the 10-step orchestrator. `STEP_IDS = ("welcome", "db", "content", "strava", "garmin", "telegram", "scheduler", "bot-scheduler", "smoke", "finish")`. One function per step; each starts with a state check and returns `[done]`+skipped when the corresponding `InstallState` bool is True. `run_wizard(settings, *, only, skip_garmin, skip_telegram, skip_scheduler, skip_bot_scheduler, skip_smoke, non_interactive)` iterates the dispatch list, re-detects state after every step (cheap), and returns the exit code (0 = ok / 1 = a non-skipped step failed terminally / 2 = `typer.Abort` from Ctrl-C or `--non-interactive` hitting a required prompt). `--skip-telegram` implies `--skip-bot-scheduler`. The bot-scheduler step is only offered if Telegram is configured (either already-state or completed-this-run). Credentials are always written to `.env` BEFORE the downstream delegated call, so a partial failure leaves creds in place for retry. (SETUP-01, SETUP-02, SETUP-05)

- **Delegation (SETUP-04, LOCKED)** — every credentialed step calls into the existing helper directly: DB → `tempo.cli._init`; Strava → `tempo.connectors.factory.build_strava_connector` + `connector.authorization_url` + `connector.exchange_code` (same triple `tempo strava auth` makes); Garmin → `tempo.connectors.factory.garmin_login(settings, prompt_mfa=…)`; daily scheduler → `tempo.scheduler.install_plist(...)`; bot scheduler → `tempo.scheduler.install_telegram_bot_plist(...)`; smoke → `tempo.sync.pipeline.run_full_sync(conn, settings)`. Zero subprocess calls; zero duplicated handshake / plist render / MFA prompt code.

- `tempo/cli.py` — `@app.command("setup")` thin wrapper that parses `--only` / `--skip-*` / `--non-interactive`, validates `--only` against `STEP_IDS - {welcome, finish}` (unknown → `typer.Exit(2)`), calls `run_wizard(settings, …)`, raises `typer.Exit(exit_code)` on non-zero return.

- `docs/SETUP.md` — end-to-end walkthrough. Two paths (one-command + manual). All 10 steps documented in the locked order with *What it does* / *Wizard prompts* / *Files written* / *Manual equivalent* / *Skip* / *Recover* subsections.

- `README.md` — "Getting Started" rewritten to lead with the 4-line `git clone / cd / uv sync / uv run tempo setup` path.

- 593 tests green (+63 from Phase 13). Verifier PASS 5/5.

## What's Done (Phase 13: Strength & Conditioning Tracker — v1.2 milestone)

- `tempo/analysis/strength.py` — frozen+slots `StrengthSet` / `StrengthExercise` / `StrengthSession` / `StrengthContext` / `StrengthRollup` dataclasses + `parse_strength(path)` + `strength_rollup(sessions, today)`. Lenient parser modelled directly on `tempo/analysis/heat.py`: missing file → `present=False`, malformed lines skipped, unknown keys ignored, never raises. Handles weighted sets (`55x8`), bare-rep sets (`15`), timed holds (`1:00`), supersets (`[A]`/`[B]`), equipment / notes / rest metadata. (SC-01, SC-02)

- `tempo/config.py` — `Settings.strength_path` returns `<content_root>/strength.md` (mirrors `heat_path`). (SC-03)

- `tempo/analysis/recovery.py` + `tempo/analysis/runner.py` + `tempo/analysis/report.py` — recovery report gains a `## Strength & conditioning` section with the same 3-state degradation as the heat section (absent → omit / lapsed → one-line nudge / active → rollup with session count, total tonnage, last-session age). (SC-04, SC-05)

- `strength.md.example` + `docs/STRENGTH.md` — committed format reference + operational doc.

- `tests/test_strength.py` + recovery-report integration tests — 32 new tests; 530 total tests green.

## What's Done (Phase 12: lifecycle / hardening / privacy — v1.1 closing milestone)

- Plan 12-01: `tempo bot install-scheduler` + launchd `com.tempo.telegram-bot.plist` with `KeepAlive=true` so a crash / sleep / network blip auto-restarts the bot. `VOICE_RETENTION_DAYS` startup sweep + per-handler immediate-delete + `tempo bot purge-voice` manual hatch. Agent cwd + data_dir logged at startup.

- Plan 12-02: top-level `telegram_error_handler` (logs traceback, sends a fixed "something went wrong" reply, never re-raises). `docs/PRIVACY.md` is the single-source user-facing privacy contract. README + `docs/TELEGRAM_BOT.md` updated with launchd lifecycle, voice retention, and error-handler sections.

- 498 tests green; v1.1 closed.

## What's Done (Phase 11: Claude Code agent via SDK)

- `tempo/bot/agent.py` — wraps `claude-agent-sdk` (uses the user's Claude Code subscription, no `ANTHROPIC_API_KEY`). Per-chat `--resume` over a 4hr rolling window. Final assistant text → HTML reply, split at 4096 chars. Detects `AssistantMessage` by class name (the SDK 0.2.x message shapes have no `.role` / `.type` attrs). Empty assistant text → `"(agent finished without a reply)"` so Telegram doesn't reject an empty message.

- `tempo/bot/sessions.py` — per-chat session-id store with a 4-hour idle window; `/new` resets.

## What's Done (Phase 10: Telegram bot worker)

- `tempo/bot/app.py` — Telegram Application builder + handler registration + Whisper warmup + cwd log + voice sweep. Defensive `delete_webhook` in `post_init` to avoid 409 Conflicts.

- `tempo/bot/handlers.py` — `start`, `voice`, `text`, `/new` handlers. Owner-chat-id allowlist; the bot ignores everything else silently.

- `tempo/bot/transcribe.py` — `faster-whisper` singleton on CPU (no Metal/GPU on Mac). `small.en` int8 default. Eager `list(segments)` because the iterator is lazy.

## What's Done (Phase 9: Telegram + Whisper foundations)

- `pyproject.toml` deps: `python-telegram-bot`, `faster-whisper`, `claude-agent-sdk`. `WHISPER_MODEL_NAME` / `WHISPER_COMPUTE_TYPE` / `WHISPER_DEVICE` / `VOICE_RETENTION_DAYS` settings (with `validation_alias` for bare-name env keys).

- Voice cache under `<content_dir>/voice/`, gitignored. faster-whisper warmup on startup.

## What's Done (Phase 8: Modular Trackers + Heat Adaptation)

- `races.md` gains a `result:` field + auto-link from race → matching Strava activity (`tempo/analysis/race_link.py`).

- New `heat.md` tracker — appendable session log; `tempo/analysis/heat.py` lenient parser + 3-state rollup surfaced in recovery report.

- `plan.md` retired (training plan moved to whichever format the owner prefers; no more parser).

- `tempo/analysis/context.py` deleted; per-tracker modules now own their own parse + render shape.

## What's Done (Phases 1-7: v1.0 — Strava + Garmin → SQLite → analyses → daily launchd job)

- See `.planning/phases/01-foundation/` through `.planning/phases/07-recovery-correlation/` for the full per-phase shipped list. Summary: Strava OAuth + paged resumable backfill → raw store; Garmin (isolated failure domain, no-retry-on-429) → raw store; pure-stdlib transforms → structured layer + `daily_summary` view; `tempo/analysis/{load,fitness,race,recovery,correlation,noteworthy}.py` produce dated markdown reports; `tempo run-daily` launchd job runs the lot at 05:30 local time. 235 → 288 → 339 → 497 tests across phases.

## Performance Metrics

**Velocity:**

- Total plans completed (this milestone): 3
- Average duration: ~ unknown (parallel waves; not tracked per-plan in this phase)
- Total execution time (this milestone): single-day session

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 15. Weight Tracker (v1.4) | 3 | — | — |
| 14. First-Run Setup Wizard (v1.3) | 3 | — | — |
| 13. Strength & Conditioning Tracker (v1.2) | 3 | — | — |
| 12. Lifecycle / hardening / privacy (v1.1 closing) | 2 | — | — |

**Recent Trend:**

- Last 3 plans: 15-01 (weight parser + rollup), 15-02 (recovery integration), 15-03 (docs + example)
- Trend: shipped same-day; 619 tests green; ruff clean.

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Strava-first milestone: prove pull → store → analyse end-to-end on the clean source before the fragile Garmin connector
- Two-layer raw → structured storage: connectors write only to `raw_response`; transforms read raw and write structured, enabling `tempo rederive` with no network
- Date spine in Phase 3 (not later): CTL/ATL EWMAs and ACWR windows are silently wrong without a zero-filled spine
- Journaling early (Phase 5): correlation analysis is data-hungry, so paired subjective history must start accumulating before Garmin
- **(Phase 14, 2026-05-28)** First-run setup is orchestration-only — every credentialed step delegates in-process to the existing helper. No subprocess; no duplicated OAuth handshake, MFA prompt, or plist render. `.env` writes go through a single atomic helper modelled on `tokens.py`.
- **(Phase 15, 2026-05-28)** Markdown trackers stay markdown until they prove themselves. Weight is the fourth tracker (races / heat / strength / weight) to ship as a lenient markdown-only Layer-1 surface before any structured DB table. kg-normalisation happens at rollup time; the entry preserves its original unit. Mixed-unit logs get a footnote caveat rather than a broken rollup.

### Roadmap Evolution

- Phase 8 added: Modular Trackers + Heat Adaptation — split plan.md into focused tracker files (`races.md` w/ result + auto-link, new `heat.md`); retire `plan.md`. (2026-05-27)
- Phase 14 added + shipped: First-Run Setup Wizard (v1.3) — `tempo setup` reduces clone-to-working-daily-sync from a multi-step README walkthrough to a single idempotent command. (2026-05-28)
- Phase 15 added + shipped: Weight Tracker (v1.4) — `weight.md` markdown tracker with kg/lb normalisation + EWMA trend; surfaced in the recovery report as the third tracker section (Heat → Strength → Weight). (2026-05-28)

### Pending Todos

- **Live `tempo setup` smoke** against real Strava + Garmin + Telegram (the wizard is verified against mocked delegated symbols; this is a follow-up session task carried over from Phase 14).
- **Phase 16 (Nutrition Tracker, v1.5)** is the next planned phase: `food.md` markdown tracker (two interchangeable formats — inline single-line and block-per-meal), daily P/C/F/cal rollup, new `tempo analyze nutrition` standalone report, recovery-report 7-day-trailing nutrition mini-section.

### Blockers/Concerns

- [Phase 2 — RESOLVED] Strava API Agreement conflict documented as accepted (README + REQUIREMENTS Known Accepted Conflicts); private self-data, never shared.
- [Phase 2 — pending user] Live Strava pull needs the user's own API app: create at https://www.strava.com/settings/api, set TEMPO_STRAVA_CLIENT_ID/SECRET in .env, run `tempo strava auth`, then `tempo strava backfill`. All machinery (incl. Phase-4 analysis) proven against mocks/seeded data; this is the only remaining step before live reports.
- [Phase 4 — RESOLVED] rTSS uses `avg_pace_s_km` directly (no grade-adjusted/normalised pace in v1; NGP/GAP is a documented future refinement). hrTSS fallback uses HR-reserve anchored on threshold HR. Threshold pace is a configurable pydantic setting. Insufficient days are flagged, not invented.
- [Phase 6] `garminconnect` is the single fragile dependency (garth deprecated 2026-03-27); pin version, monitor upstream, budget for a version bump
- [Phase 7] HRV baseline cold-start and multi-signal recovery weighting may need a brief planning-time research pass; first weeks of Garmin data will be low-quality and must be flagged honestly

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Setup | `tempo doctor` (diagnose-only health check; separable from setup) | Deferred to follow-up phase | 2026-05-28 (Phase 14 CONTEXT) |
| Setup | `tempo setup --uninstall` reverse path (3-line manual `rm` documented in `docs/SETUP.md`) | Deferred; manual is fine | 2026-05-28 (Phase 14 CONTEXT) |
| Setup | Pi / Linux systemd-equivalent of the launchd steps | Deferred until Pi-port milestone | 2026-05-28 (Phase 14 CONTEXT) |
| Setup | Auto-detect optimal Whisper model / threshold pace / max HR / resting HR | Deferred (cross-cuts Phase 4) | 2026-05-28 (Phase 14 CONTEXT) |
| Weight | Structured `weight_entry` DB table (markdown layer proves itself first) | Deferred | 2026-05-28 (Phase 15 CONTEXT) |
| Weight | `tempo weight add --kg 72.4` CLI (symmetric with `tempo journal add`) | Deferred to Layer 2 | 2026-05-28 (Phase 15 CONTEXT) |
| Weight | Body composition (body-fat %, lean mass) | Deferred / out of scope | 2026-05-28 (Phase 15 CONTEXT) |
| Weight | Withings / Fitbit / Garmin weight auto-import | Deferred (separate phase if ever) | 2026-05-28 (Phase 15 CONTEXT) |
| Weight | Standalone `tempo analyze weight` trend report | Deferred (recovery section is enough) | 2026-05-28 (Phase 15 CONTEXT) |
| Weight | Goal tracking (target weight + ETA from trend) | Deferred | 2026-05-28 (Phase 15 CONTEXT) |

## Session Continuity

Last session: 2026-05-28T11:15:00.000Z
Stopped at: v1.4 SHIPPED. Phase 15 (Weight Tracker) verified PASS 5/5. New
`tempo/analysis/weight.py` (lenient parser + kg/lb normalisation +
7d/28d windows + EWMA alpha=0.1 trend + unit_mixed flag);
`Settings.weight_path`; `RecoveryAssessment` gains `weight` / `weight_present`;
`_render_weight_section` enforces the 3-state degradation rule (absent /
stale >14d / current with Unicode-minus / plus-minus delta and optional
mixed-unit caveat); `runner.generate_recovery` + `generate_all` thread
`weight_path`; CLI passes `settings.weight_path` at both call sites.
`weight.md.example` (14 mixed-unit entries) + `docs/WEIGHT.md` (199 lines) +
README mention. 619 tests green (+26 from Phase 14), ruff clean. All 5
WEIGHT-* requirements satisfied. Next planned: Phase 16 (Nutrition
Tracker, v1.5).

Previous session: 2026-05-28T10:30:00.000Z. Stopped at: v1.3 SHIPPED. Phase 14
(First-Run Setup Wizard) verified PASS 5/5. New `tempo setup` command walks 10
locked steps in order (welcome → db → content → strava → garmin → telegram →
scheduler → bot-scheduler → smoke → finish); every credentialed step delegates
in-process to the existing `tempo` helper. Atomic `.env` writes at 0600 perms
mirror `tempo/connectors/tokens.py`. 593 tests green (+63), ruff clean.

Resume file: None
