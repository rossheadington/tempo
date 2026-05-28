---
phase: 12-lifecycle-hardening-privacy
verified: 2026-05-28T00:00:00Z
status: gaps_found
score: 10/13 must-haves verified
overrides_applied: 0
gaps:
  - truth: ".env.example documents VOICE_RETENTION_DAYS"
    status: failed
    reason: "VOICE_RETENTION_DAYS is implemented in runos/config.py (line 135) but is not mentioned anywhere in .env.example. Proof step 8 explicitly requires this. The PRIVACY.md doc references the env var, but a new user copying .env.example will not discover this privacy knob."
    artifacts:
      - path: ".env.example"
        issue: "Missing VOICE_RETENTION_DAYS documentation block"
    missing:
      - "Add a VOICE_RETENTION_DAYS=0 commented stanza in .env.example near the Whisper / Telegram bot section, with a short note: '0 = delete on success (privacy-safe default); N>0 = keep N days then startup-sweep'"
  - truth: "REQUIREMENTS.md shows all 15 VOICE-* requirements ticked (v1.1 milestone closure)"
    status: failed
    reason: "Only 12 VOICE-* requirements are marked [x] in REQUIREMENTS.md. VOICE-01, VOICE-02, VOICE-03 are still [ ] unchecked even though Phase 9/10 shipped (these are tracked Complete in the traceability table at lines 204-218 but the upstream checkbox bullets at lines 90-92 were never ticked). Proof step 13 expects grep -c '[x] **VOICE-' = 15; actual = 12."
    artifacts:
      - path: ".planning/REQUIREMENTS.md"
        issue: "Lines 90-92 still have [ ] for VOICE-01/02/03; the per-row traceability table at lines 204-218 marks them Complete, but the milestone-status note at line 228 says 'Last updated: 2026-05-27 ... 15 VOICE-* requirements mapped to four new phases' and never updates the bullet checkboxes."
    missing:
      - "Flip VOICE-01, VOICE-02, VOICE-03 from [ ] to [x] in REQUIREMENTS.md lines 90-92"
      - "Update the trailing 'Last updated' / Coverage paragraph (line 228) so it reflects 60/60 = 100% with all 15 VOICE-* complete instead of '0 complete'"
  - truth: "ROADMAP.md shows Phases 9-12 complete and v1.1 milestone CLOSED"
    status: failed
    reason: "ROADMAP.md is severely out of sync with reality. The top-level phase checklist (lines 25-28) still has Phase 9/10/11/12 as [ ] unchecked. Phase 12's per-plan checklist (lines 192-193) still has 12-01-PLAN.md and 12-02-PLAN.md as [ ]. The Progress table (lines 211-214) shows Phase 9 'Not started', Phase 10/11 'In Progress', Phase 12 'Not started'. The Milestone status block (line 218) says 'v1.1 Telegram Voice Coach (Phases 9–12): Planning — 15 VOICE-* requirements mapped, 0 complete.' Plan 12-02 SUMMARY claims ROADMAP was updated to COMPLETE; it was not."
    artifacts:
      - path: ".planning/ROADMAP.md"
        issue: "Lines 25-28: Phase 9/10/11/12 all [ ]. Lines 192-193: 12-01/12-02 plans still [ ]. Lines 211-214: progress table stale. Line 218: milestone-status block says '0 complete' / 'Planning'."
    missing:
      - "Tick [x] for Phase 9, 10, 11, 12 in the top-level phase list (lines 25-28)"
      - "Tick [x] for 12-01-PLAN.md and 12-02-PLAN.md in the Phase 12 Plans section (lines 192-193)"
      - "Update Phase 9 in the Progress table to '2/2 Complete' (or equivalent — Phase 9's Plans row shows 0/0, fix that too), Phase 10 to 'Complete', Phase 11 to '3/3 Complete', Phase 12 to '2/2 Complete' (line 211-214)"
      - "Rewrite milestone-status bullet at line 218 to 'v1.1 Telegram Voice Coach (Phases 9–12): COMPLETE — 15/15 VOICE-* shipped (2026-05-28)'"
human_verification:
  - test: "Run the bot under launchd end-to-end and observe KeepAlive behaviour"
    expected: "After runos bot install-scheduler --to-launch-agents + launchctl bootstrap, the bot starts; kill -9 the process and confirm launchd restarts it within ThrottleInterval (10s); put the Mac to sleep, wake it, send a voice memo, confirm the bot replies. Logs land in <project>/logs/telegram-bot.{stdout,stderr}.log."
    why_human: "Requires a real launchctl bootstrap into the user's GUI session + sleep/wake cycle on the laptop; no automated test can exercise the launchd contract."
  - test: "Trigger the top-level error handler in production and verify the canonical reply lands in Telegram"
    expected: "Force an uncaught exception inside a handler (e.g. monkey-patch run_turn to raise mid-call, or send a malformed update), see the launchd stderr log gain a 'Bot handler crashed' ERROR record with full traceback, and confirm the chat receives the literal 'Sorry -- something went wrong on my end. Check the logs.' message. Worker keeps polling afterwards."
    why_human: "The 5 unit tests in tests/test_error_handler.py cover the contract under mocks; only a live Telegram round-trip proves the end-to-end behaviour the user actually experiences."
  - test: "Verify voice-cache retention=0 default in production"
    expected: "Send a voice memo, confirm the .ogg appears in <voice_cache_dir>/ briefly, then confirm it is gone from disk after the agent reply lands. With VOICE_RETENTION_DAYS=7 set, confirm the .ogg is retained and the next bot restart logs 'voice cache startup sweep: retention=7 days, deleted=N'."
    why_human: "End-to-end privacy invariant requires real Telegram voice upload + filesystem inspection while the bot is running; the unit tests cover the helper functions but not the live integration."
  - test: "Confirm runos bot purge-voice --yes works on a real populated cache"
    expected: "After accumulating a few .ogg files (e.g. with retention=7), run runos bot purge-voice --yes and confirm every file is removed, the directory remains, and the next memo creates files normally."
    why_human: "Tested in CLI unit tests but the user should run it once against a real cache to confirm the prompt UX, the listed sizes match disk usage, and exit codes are sane."
---

# Phase 12: Lifecycle + Hardening + Privacy Verification Report

**Phase Goal:** launchd LaunchAgent (KeepAlive=true) so bot survives sleep/wake/crash; top-level error boundary that replies "something went wrong" instead of crashing; voice-file retention policy (default delete-on-success); cwd scoping confirmed.

**Verified:** 2026-05-28
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth                                                                                              | Status     | Evidence       |
| --- | -------------------------------------------------------------------------------------------------- | ---------- | -------------- |
| 1   | launchd plist exists at `launchd/com.runos.telegram-bot.plist` + lints clean                       | VERIFIED   | `plutil -lint launchd/com.runos.telegram-bot.plist` -> OK; 6 grep hits for `KeepAlive\|ThrottleInterval\|WorkingDirectory` (proof: ≥3) |
| 2   | Plist has KeepAlive=true + ThrottleInterval=10 + WorkingDirectory + log paths                      | VERIFIED   | Lines 52-66, 82-85 of plist: `<key>KeepAlive</key><true/>`, `<key>ThrottleInterval</key><integer>10</integer>`, WorkingDirectory={{PROJECT_ROOT}}, StandardOut/ErrorPath under logs/ |
| 3   | `runos bot install-scheduler` resolves placeholders + writes a plist + prints launchctl commands    | VERIFIED   | `runos bot --help` shows install-scheduler; runos/scheduler.py:268 render_telegram_bot_plist + 310 install_telegram_bot_plist; runos/cli.py:364 wires the command |
| 4   | `runos bot purge-voice [--yes]` exists and works as documented                                     | VERIFIED   | `runos bot --help` shows purge-voice; runos/cli.py:436 implements it; 6 unit tests cover yes/no/interactive/empty paths |
| 5   | `voice_handler` calls `_cleanup_voice_file` after the agent turn; retention=0 deletes immediately   | VERIFIED   | runos/bot/handlers.py:326 (empty-transcript path) + line 342 (try/finally after `_run_agent_turn`); `_cleanup_voice_file` at line 90 unlinks when retention_days == 0 |
| 6   | Startup sweep runs in `_post_init` after `warm_model` and deletes files older than retention_days   | VERIFIED   | runos/bot/app.py:204-212; `_sweep_voice_cache` at line 92 iterates voice_cache_dir, computes cutoff = time.time() - retention*86400, unlinks older files |
| 7   | Top-level error handler is registered + replies the canonical message + swallows reply failures     | VERIFIED   | runos/bot/app.py:248 `app.add_error_handler(telegram_error_handler)`; runos/bot/error_handler.py:46 ERROR_REPLY = "Sorry -- something went wrong on my end. Check the logs."; lines 99-117 broad-except + swallow on send_message failure |
| 8   | cwd log line emitted at startup so the user can verify scoping                                     | VERIFIED   | runos/bot/app.py:197 `logger.info("agent cwd = %s", Path.cwd().resolve())`; line 198 also logs data_dir |
| 9   | `docs/PRIVACY.md` exists + covers what stays local / what flows to Claude+Telegram / privacy knobs   | VERIFIED   | docs/PRIVACY.md present; sections "What stays on the laptop, always" (line 13), "What leaves the laptop, and to whom" (line 36), "Voice retention policy" (line 65), purge-voice usage (lines 85-89); README links to it (3 occurrences) |
| 10  | Full test suite passes (497) + ruff clean                                                          | VERIFIED   | `uv run pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` -> 497 passed, 1 deselected; `uv run ruff check runos/ tests/` -> All checks passed |
| 11  | `VOICE_RETENTION_DAYS` documented in `.env.example`                                                | FAILED     | grep "VOICE_RETENTION" .env.example -> no matches. The setting is implemented in runos/config.py:135 but never surfaced in the env template. |
| 12  | All 15 VOICE-* requirements ticked in REQUIREMENTS.md                                              | FAILED     | grep -c "[x] **VOICE-" .planning/REQUIREMENTS.md -> 12 (proof expected 15). VOICE-01, VOICE-02, VOICE-03 are still [ ] at lines 90-92. The traceability table at lines 204-218 marks them Complete, but the bullet checkboxes were never updated. The "Last updated" paragraph at line 228 still says "15 VOICE-* requirements mapped ... 0 complete." |
| 13  | ROADMAP.md Phase 9-12 marked complete + v1.1 milestone marked COMPLETE                             | FAILED     | Top-level phase list (lines 25-28): all of Phase 9/10/11/12 still [ ]. Plan checkboxes for 12-01 and 12-02 (lines 192-193) still [ ]. Progress table (lines 211-214) shows "Not started" / "In Progress". Milestone-status block (line 218) says "v1.1 Telegram Voice Coach (Phases 9–12): Planning — 15 VOICE-* requirements mapped, 0 complete." 12-02 SUMMARY explicitly claims this was updated to COMPLETE; it was not. |

**Score:** 10/13 truths verified

### Required Artifacts

| Artifact                                                  | Expected                                                                | Status     | Details                                                                                                                                                                                |
| --------------------------------------------------------- | ----------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `launchd/com.runos.telegram-bot.plist`                    | LaunchAgent template, KeepAlive=true, ThrottleInterval=10, WorkingDirectory, log paths | VERIFIED   | plutil -lint OK; KeepAlive true; ThrottleInterval 10; RunAtLoad true; WorkingDirectory {{PROJECT_ROOT}}; log paths under logs/                                                          |
| `runos/bot/error_handler.py`                              | telegram_error_handler + ERROR_REPLY                                    | VERIFIED   | ERROR_REPLY at line 46; handler at line 49; logs full traceback (line 81); isinstance/effective_chat gating (lines 89-92); broad-except swallow on reply failure (lines 108-117)         |
| `runos/bot/app.py` (handler registration + cwd log + sweep) | add_error_handler + agent cwd log + voice sweep call                    | VERIFIED   | `from runos.bot.error_handler import telegram_error_handler` (line 43); `app.add_error_handler(...)` (line 248); cwd log (line 197); `_sweep_voice_cache` invocation (lines 204-212)    |
| `runos/bot/handlers.py` (voice cleanup integration)       | _cleanup_voice_file called after voice turn                             | VERIFIED   | Helper at line 90; called at line 326 (empty-transcript) and 342 (try/finally after agent turn)                                                                                         |
| `runos/scheduler.py` (telegram-bot plist render + install) | render_telegram_bot_plist + install_telegram_bot_plist                  | VERIFIED   | Lines 268 and 310                                                                                                                                                                       |
| `runos/cli.py` (`bot install-scheduler`, `bot purge-voice`) | New typer subcommands                                                  | VERIFIED   | Lines 364 (install-scheduler) + 436 (purge-voice); both visible in `runos bot --help`                                                                                                   |
| `runos/config.py` (`voice_retention_days` field)          | Settings field with VOICE_RETENTION_DAYS env alias                      | VERIFIED   | Line 135: `voice_retention_days: int = Field(default=..., validation_alias="VOICE_RETENTION_DAYS")`                                                                                     |
| `docs/PRIVACY.md` (NEW)                                   | Single-source privacy contract, linked from README                      | VERIFIED   | File present; README has 3 PRIVACY.md links                                                                                                                                             |
| `docs/TELEGRAM_BOT.md` (updated)                          | launchd section + retention section + error-handler section             | VERIFIED   | Per Plan 12-02 SUMMARY; not deeply re-inspected but file unchanged-but-extended pattern matches docs                                                                                    |
| `README.md` (updated)                                     | v1.1 surface + always-on bot section + PRIVACY.md link                  | VERIFIED   | 3 PRIVACY.md links; v1.1 section described in 12-02 SUMMARY                                                                                                                             |
| `tests/test_error_handler.py` (NEW)                       | 5 tests covering handler contract                                       | VERIFIED   | File present; 5 tests included in the 497-test suite which passes cleanly                                                                                                               |
| `.env.example` (VOICE_RETENTION_DAYS)                     | Documentation for the new env var                                       | MISSING    | grep returns nothing for VOICE_RETENTION in .env.example                                                                                                                                |
| `.planning/REQUIREMENTS.md` (VOICE-11/12/14/15 ticked)    | All 15 VOICE-* requirements [x]                                          | PARTIAL    | VOICE-11/12/14/15 are correctly [x] in lines 109-116; but VOICE-01/02/03 (Phase 9/10) are still [ ] from earlier phases AND the trailing "0 complete" paragraph at line 228 is stale    |
| `.planning/ROADMAP.md` (Phase 9-12 + v1.1 milestone status) | [x] markers + milestone "COMPLETE"                                     | FAILED     | All Phase 9-12 [ ]; both 12-* plan checkboxes [ ]; progress table stale; milestone-status line still "Planning — 0 complete"                                                            |
| `.planning/STATE.md`                                      | status=shipped, progress=100, completed_phases includes 12              | VERIFIED   | Lines 5-15: status: shipped, completed_phases: 4, completed: [12], percent: 100                                                                                                         |

### Key Link Verification

| From                       | To                              | Via                                              | Status   | Details                                                                                                                              |
| -------------------------- | ------------------------------- | ------------------------------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `runos/bot/app.py`         | `runos/bot/error_handler.py`    | `app.add_error_handler(telegram_error_handler)`  | WIRED    | Import at line 43; registration at line 248                                                                                          |
| `runos/bot/app.py`         | voice sweep helper               | `_sweep_voice_cache(settings.voice_cache_dir, settings.voice_retention_days)` in `_post_init` | WIRED    | Lines 204-212; awaited via asyncio.to_thread                                                                                         |
| `runos/bot/handlers.py`    | `_cleanup_voice_file`           | call after empty-transcript reply AND after agent turn in `finally` | WIRED    | Lines 326 and 342                                                                                                                    |
| `runos/cli.py`             | `runos/scheduler.py`             | `scheduler.install_telegram_bot_plist(...)`      | WIRED    | runos/cli.py:403 inside the `bot install-scheduler` command                                                                          |
| `runos/config.py`          | env loader                       | `validation_alias="VOICE_RETENTION_DAYS"`        | PARTIAL  | Field exists in config; **NOT documented in .env.example** -- a user copying the template will never set or learn about this knob    |
| `README.md`                | `docs/PRIVACY.md`               | markdown links                                   | WIRED    | 3 hits for PRIVACY.md in README                                                                                                      |

### Data-Flow Trace (Level 4)

| Artifact                          | Data Variable                     | Source                                                                   | Produces Real Data | Status                                              |
| --------------------------------- | --------------------------------- | ------------------------------------------------------------------------ | ------------------ | --------------------------------------------------- |
| `_cleanup_voice_file`             | `path` (Path), `retention_days`   | `voice_handler` passes the actual downloaded `.ogg` path + `settings.voice_retention_days` | Yes                | FLOWING -- path comes from PTB voice download, retention from real Settings |
| `_sweep_voice_cache`              | `voice_cache_dir`, `retention_days` | Real Settings; runs at every `_post_init`                              | Yes                | FLOWING -- iterates real dir; counts deletions; result logged             |
| `telegram_error_handler`          | `update`, `context.error`         | PTB framework dispatches whenever a handler raises                       | Yes                | FLOWING -- handler is registered and PTB will route real errors here       |
| `cwd log line`                    | `Path.cwd().resolve()`            | Runtime; reflects launchd `WorkingDirectory` when run as a service       | Yes                | FLOWING                                              |

### Behavioral Spot-Checks

| Behavior                                | Command                                                                | Result                                                                       | Status |
| --------------------------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ------ |
| plist lints                              | `plutil -lint launchd/com.runos.telegram-bot.plist`                    | `launchd/com.runos.telegram-bot.plist: OK`                                    | PASS   |
| critical plist keys present              | `grep -c "KeepAlive\|ThrottleInterval\|WorkingDirectory" launchd/com.runos.telegram-bot.plist` | `6`                                                                           | PASS   |
| bot CLI subcommands registered           | `uv run runos bot --help`                                              | Lists `run`, `install-scheduler`, `purge-voice`                              | PASS   |
| error handler registered in app          | `grep "telegram_error_handler\|add_error_handler" runos/bot/app.py`    | Import + registration both present                                            | PASS   |
| canonical error reply text               | `grep "something went wrong" runos/bot/error_handler.py`               | line 46 ERROR_REPLY                                                            | PASS   |
| voice cleanup wired in handlers          | `grep "_cleanup_voice_file\|_sweep_voice_cache" runos/bot/handlers.py runos/bot/app.py` | Both files contain both helpers / calls                                       | PASS   |
| agent cwd logged at startup              | `grep "agent cwd" runos/bot/app.py`                                    | line 197                                                                       | PASS   |
| PRIVACY.md exists                        | `ls docs/PRIVACY.md`                                                    | present                                                                        | PASS   |
| README links to PRIVACY.md               | `grep -c "PRIVACY.md" README.md`                                       | 3 (≥1 required)                                                                | PASS   |
| **VOICE_RETENTION_DAYS in .env.example** | `grep "VOICE_RETENTION_DAYS" .env.example`                              | **no match**                                                                   | **FAIL** |
| **REQUIREMENTS.md VOICE-* completion**   | `grep -c "[x] **VOICE-" .planning/REQUIREMENTS.md`                     | **12** (expected 15)                                                           | **FAIL** |
| pytest suite green                       | `uv run pytest tests/ --deselect ...`                                  | 497 passed, 1 deselected                                                       | PASS   |
| ruff clean                               | `uv run ruff check runos/ tests/`                                      | All checks passed                                                              | PASS   |

### Requirements Coverage

| Requirement | Source Plan        | Description                                                              | Status  | Evidence                                                                                                                                          |
| ----------- | ------------------ | ------------------------------------------------------------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| VOICE-11    | 12-01, 12-02 docs  | launchd LaunchAgent with KeepAlive lifecycle                              | SATISFIED | Plist committed + lints; runos bot install-scheduler ships; KeepAlive=true + ThrottleInterval=10 in template; checkbox in REQUIREMENTS.md is [x]   |
| VOICE-12    | 12-02              | Top-level error boundary so worker survives single-message failures      | SATISFIED | error_handler.py registered in build_application; 5 unit tests; canonical reply asserted in tests; checkbox is [x]                                |
| VOICE-14    | 12-01              | Voice files deleted after successful transcription (configurable retention) | SATISFIED | _cleanup_voice_file + _sweep_voice_cache wired; default 0 = delete immediately; checkbox is [x]                                                  |
| VOICE-15    | 12-01              | Agent runs in RunOS project dir; no access outside the tree              | SATISFIED | plist WorkingDirectory={{PROJECT_ROOT}}; cwd log line at startup; PRIVACY.md documents this; checkbox is [x]                                       |

(VOICE-01/02/03 are not Phase 12 requirements, so they do NOT block this phase's verdict — but they DO block the v1.1 milestone closure claim. See Gap 2.)

### Anti-Patterns Found

None of the modified files contain `TODO`, `FIXME`, `XXX`, `placeholder`, `coming soon`, or `return null`/`return {}` stub patterns inside the Phase-12-touched code paths. The error handler's `except Exception` is intentional (documented and required by the VOICE-12 contract; commented `# noqa: BLE001 - intentional broad swallow`).

### Human Verification Required

See `human_verification:` block in the frontmatter. Four items: launchd live behaviour, error-handler live behaviour, voice retention live behaviour, purge-voice live UX. All are end-to-end checks that cannot be exercised under unit-test mocks.

### Gaps Summary

The Phase 12 *code* is fully wired and the *test suite* is green. Three documentation/state-tracking gaps remain:

1. **`.env.example` is missing the `VOICE_RETENTION_DAYS` knob.** The setting works, PRIVACY.md describes it, but a fresh user who copies `.env.example` won't see it. Small, single-line fix.

2. **REQUIREMENTS.md is internally inconsistent.** VOICE-11/12/14/15 (this phase's) are ticked, but VOICE-01/02/03 (Phase 9/10 work that shipped weeks earlier) were never ticked. The traceability table claims them Complete but the bullet checklist at the top still says `[ ]`, and the trailing milestone-status paragraph still reads "0 complete." Three checkbox flips + one paragraph rewrite.

3. **ROADMAP.md is severely out of sync.** Phases 9, 10, 11, 12 are all still `[ ]` in the top-level list; both 12-01 and 12-02 plan checkboxes are still `[ ]`; the Progress table shows Phase 9 "Not started" and Phase 12 "Not started"; the milestone-status block at line 218 still says "v1.1 Telegram Voice Coach (Phases 9–12): Planning — 15 VOICE-* requirements mapped, 0 complete." Plan 12-02 SUMMARY explicitly claimed this was updated to COMPLETE — it was not.

The *engineering* delivery is done. The *milestone closure ceremony* (REQUIREMENTS.md ticks + ROADMAP.md milestone status + .env.example documentation) is incomplete. v1.1 cannot truthfully be called "shipped" while ROADMAP.md says "0 complete" and the canonical phase checklist shows everything unchecked.

---

*Verified: 2026-05-28*
*Verifier: Claude (gsd-verifier)*
