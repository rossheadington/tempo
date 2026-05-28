---
phase: 14-setup-wizard
verified: 2026-05-28T10:30:00Z
status: passed
score: 5/5 success criteria verified
re_verification:
  previous_status: null
  previous_score: null
  gaps_closed: []
  gaps_remaining: []
  regressions: []
---

# Phase 14: First-Run Setup Wizard — Verification Report

**Phase Goal:** A single interactive `runos setup` command walks a new user from zero (fresh clone, no DB, no `.env`, no tokens) to a fully working daily-sync pipeline (and optionally a running Telegram bot). Pure orchestration — every credentialed step delegates to the existing CLI surface. Stdlib-only prompts. Atomic `.env` writes with 0600 perms. Each step is skippable; `--only=<step>` re-runs one step.

**Verified:** 2026-05-28
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Success Criteria

| #   | Criterion                                                                                                                                                                                                                                                                                                              | Status     | Evidence                                                                                                                                                                                                                                                                                                                                       |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `runos setup` walks the 10 locked steps in order: welcome → db → content → strava → garmin → telegram → scheduler → bot-scheduler → smoke → finish                                                                                                                                                                     | ✓ VERIFIED | `runos/setup/wizard.py:59-70` `STEP_IDS` tuple matches CONTEXT verbatim; `dispatch` list (`wizard.py:611-622`) iterates them in order; `tests/test_setup_wizard.py:203-216` asserts the locked tuple; `test_wizard_runs_all_steps_in_order_fresh_install` asserts every delegated mock fires exactly once on a fresh install. |
| 2   | Wizard detects existing state and never blindly clobbers it; `[done]` / `[set] keep / change / fresh`; re-runnable                                                                                                                                                                                                     | ✓ VERIFIED | `runos/setup/state.py:94-113` `detect_install_state` is pure read-only over filesystem + DB schema version; each step in `wizard.py` checks `InstallState` and returns `StepResult("…", "skipped", "already …")` before any write (e.g. `step_db:167`, `step_strava:240`, `step_garmin:304`, `step_telegram:357`, `step_scheduler:419`, `step_bot_scheduler:473`). `tests/test_setup_wizard.py:338` `test_wizard_step_skipped_when_install_state_done` verifies no delegated mock fires when state is already "done". `--only` re-arms the step via `confirm_yn("Re-run …")`. |
| 3   | All `.env` writes go through `atomic_write_env` (temp → fsync → `os.replace` → fsync parent dir → chmod 0600); preserves comments + untouched keys; secrets never echoed                                                                                                                                               | ✓ VERIFIED | `runos/setup/env_io.py:90-187` mirrors `runos/connectors/tokens.py` template exactly (mkstemp → fchmod 0o600 → write + flush + fsync → `os.replace` → `_fsync_dir(parent)` → final chmod 0o600). `tests/test_setup_env_io.py` has 30 tests covering: 0600 perms (line 189), atomicity on replace failure (202), atomicity on fresh-file failure (224), fsync called (242), parent-dir creation (195), byte-identical preservation of untouched keys (176), comment preservation (95), delete + round-trip (146, 254), value-quoting (153, 162, 168). `prompts.py:71-73` `prompt_secret` always passes `hide_input=True`; the only secret-prompt sites in `wizard.py` (Strava client secret L252, Garmin password L321, Telegram bot token L376) route through it; the `env_io` module never logs/prints values. |
| 4   | Every credentialed step delegates to existing flows; no duplicated auth handshake, plist render, or MFA prompt                                                                                                                                                                                                         | ✓ VERIFIED | DB → `from runos.cli import _init` (`wizard.py:181`); Strava → `build_strava_connector` + `authorization_url` + `exchange_code` from `runos.connectors.factory` (`wizard.py:266-284`); Garmin → `garmin_login(settings, prompt_mfa=…)` from same factory (`wizard.py:328-334`); daily scheduler → `runos.scheduler.install_plist(...)` (`wizard.py:437-446`); bot scheduler → `runos.scheduler.install_telegram_bot_plist(...)` (`wizard.py:487-495`); smoke → `runos.sync.pipeline.run_full_sync(conn, settings)` (`wizard.py:520-527`). All six symbols exist exactly at the import sites (verified via `grep ^def`). No subprocess calls anywhere in the wizard. `tests/test_setup_wizard.py:219-252` asserts each mock fires exactly once on a fresh-install run.                  |
| 5   | CLI flags work (`--skip-garmin`, `--skip-telegram`, `--skip-scheduler`, `--skip-bot-scheduler`, `--skip-smoke`, `--only=<step>` stackable, `--non-interactive`); smoke reports per-source status; non-zero exit only if a non-skipped step failed terminally                                                            | ✓ VERIFIED | `runos/cli.py:944-1008` exposes every flag with help text; `--only` validated against `STEP_IDS - {welcome, finish}` with `exit code 2` on unknown step (`cli.py:986-995`). `--skip-telegram` implies `--skip-bot-scheduler` (`wizard.py:607-608`). `test_wizard_skip_telegram_implies_skip_bot_scheduler` (`tests/test_setup_wizard.py:311`) asserts both stay un-fired. Smoke step (`step_smoke`, `wizard.py:509-554`) iterates per-source results and prints OK/skipped; `tests/test_setup_wizard.py:471` (`test_wizard_smoke_reports_per_source_status`) and `:493` (`test_wizard_smoke_strava_terminal_failure_exits_1`) verify the per-source output and the non-zero-only-on-terminal-failure contract. `--non-interactive` raises `typer.Abort` → exit 2 (`_check_non_interactive`, `wizard.py:86-98`). |

**Score:** 5/5 criteria verified.

### Required Artifacts

| Artifact                                | Expected                                                                                                                                                | Status     | Details                                                                                                                                                                                                              |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runos/setup/__init__.py`               | Package entry; re-exports `run_wizard`                                                                                                                  | ✓ VERIFIED | 13-line module; imports `run_wizard` from `wizard.py` and exposes via `__all__`.                                                                                                                                     |
| `runos/setup/env_io.py`                 | `read_env(path)` + `atomic_write_env(path, updates, delete_keys)`; atomic template mirroring `runos/connectors/tokens.py`                              | ✓ VERIFIED | 207 lines; full atomic write template (mkstemp → fchmod 0o600 → fsync → replace → dir fsync → final chmod). `read_env` lenient (missing → `{}`).                                                                     |
| `runos/setup/state.py`                  | `InstallState` (frozen+slots, 7 bools); `detect_install_state(settings)`                                                                                | ✓ VERIFIED | 114 lines; `@dataclass(frozen=True, slots=True)`; 7 fields exactly; pure read-only (filesystem + single read-only SQLite connection closed in finally); no `launchctl`, no network.                                  |
| `runos/setup/prompts.py`                | Thin `typer.prompt` / `typer.secho` wrappers; `[set]` / `[done]` / `[fresh]` / `[skip]` indicators; hidden-input safety                                | ✓ VERIFIED | 79 lines; `prompt_secret` always uses `hide_input=True, confirmation_prompt=False`; `_INDICATOR_COLOURS` covers the 4 states; `print_step_banner`, `print_block`, `print_indicator`, `confirm_yn` all present.       |
| `runos/setup/wizard.py`                 | Orchestrator with all 10 step functions + `run_wizard`                                                                                                  | ✓ VERIFIED | 672 lines; one function per locked step; `STEP_IDS` tuple matches CONTEXT order; dispatch table at L611-622; `--skip-telegram` implies `--skip-bot-scheduler` (L607-608); `bot-scheduler` only offered if telegram configured (L469-471). |
| `runos/cli.py @app.command("setup")`    | One new typer command; thin wrapper; validates `--only` against `STEP_IDS`                                                                              | ✓ VERIFIED | `cli.py:943-1008`; all 6 skip/non-interactive flags exposed; unknown `--only` → exit 2; calls `run_wizard(settings, …)`; raises `typer.Exit(exit_code)` on non-zero.                                                  |
| `docs/SETUP.md`                         | End-to-end document with all 10 steps                                                                                                                   | ✓ VERIFIED | 19,238 bytes; 10 `### N.` step sections matching the locked order; each section has *What it does* / *Wizard prompts* / *Files written* / *Manual equivalent* / *Skip* / *Recover* subsections.                       |
| `README.md` rewrite                     | "Getting Started" leads with `runos setup`; links to `docs/SETUP.md`                                                                                    | ✓ VERIFIED | New "Getting Started" section (L29-89) leads with `uv run runos setup` (4-line one-command path); explains idempotent re-runs and `[set] keep/change/fresh`; links to `docs/SETUP.md`; preserves the manual fallback. |
| `tests/test_setup_env_io.py`            | round-trip, 0600, atomicity, comment-preservation, last-value-wins                                                                                     | ✓ VERIFIED | 30 tests; covers every guarantee in the CONTEXT `.env` I/O block.                                                                                                                                                    |
| `tests/test_setup_state.py`             | combinatoric state detection; `InstallState` is frozen+slots                                                                                            | ✓ VERIFIED | 17 tests; parametrised across DB / strava / garmin / telegram / both-plists; explicit frozen + `__slots__` assertions.                                                                                                |
| `tests/test_setup_wizard.py`            | step ordering, `--only`, `--skip-*`, atomic write order, OAuth failure path, smoke per-source, non-interactive abort, bot-scheduler gating, CLI runner | ✓ VERIFIED | 14 tests; mocks every delegated symbol; verifies dispatch counts; covers the LOCKED edge cases (CONTEXT § "Test scope").                                                                                              |

### Key Link Verification

| From                                | To                                                | Via                                                                  | Status   | Details                                                                                                                                                                                                                                  |
| ----------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runos setup` (CLI command)         | `run_wizard` (orchestrator)                       | `from runos.setup.wizard import STEP_IDS, run_wizard` (`cli.py:980`) | ✓ WIRED  | `setup_cmd` parses flags, validates `--only`, calls `run_wizard(settings, …)`, exits with the returned code.                                                                                                                             |
| `wizard.step_db`                    | `runos.cli._init`                                 | local import + call (`wizard.py:181-183`)                            | ✓ WIRED  | The same helper `runos init` calls today. No duplicated SQL.                                                                                                                                                                             |
| `wizard.step_strava`                | `runos.connectors.factory.build_strava_connector` | local import + call (`wizard.py:266-269`)                            | ✓ WIRED  | Then `connector.authorization_url(redirect_uri)` + `connector.exchange_code(code)` — same triple `runos strava auth` makes; no duplicated handshake.                                                                                     |
| `wizard.step_garmin`                | `runos.connectors.factory.garmin_login`           | local import + call with `prompt_mfa=_prompt_mfa` (`wizard.py:328-334`) | ✓ WIRED  | Same factory `runos garmin login` uses; no duplicated MFA prompt; library writes tokens under `~/.runos/tokens/garmin/`.                                                                                                                 |
| `wizard.step_scheduler`             | `runos.scheduler.install_plist`                   | local import + call (`wizard.py:437-446`)                            | ✓ WIRED  | Same arguments `runos install-scheduler` passes (`project_dir`, `data_dir`, `hour`, `minute`, `to_launch_agents=True`). No duplicated plist render.                                                                                       |
| `wizard.step_bot_scheduler`         | `runos.scheduler.install_telegram_bot_plist`      | local import + call (`wizard.py:487-495`)                            | ✓ WIRED  | Same function `runos bot install-scheduler` uses; gated on `state.telegram_configured or telegram_completed` (L469).                                                                                                                     |
| `wizard.step_smoke`                 | `runos.sync.pipeline.run_full_sync`               | local import + `conn = _db.init_db(db_path)` + call (`wizard.py:519-527`) | ✓ WIRED  | In-process call (not subprocess); conn closed in finally; per-source results iterated and reported with `r.ok` / `r.source` / `r.rows` / `r.detail`.                                                                                     |
| Every `.env` write in `wizard.py`   | `runos.setup.env_io.atomic_write_env`             | imported at module top (`wizard.py:46`); used at L225, L256, L322, L379, L397, L403 | ✓ WIRED  | Six call sites cover content dir, Strava client id/secret, Garmin email/password, Telegram token/chat-id, Whisper knobs, voice retention. No raw `open(.env, "w")` anywhere.                                                              |
| Every state check                   | `runos.setup.state.detect_install_state`          | imported (`wizard.py:56`); called at orchestrator start + after each step (L624, L669) | ✓ WIRED  | Same function, no duplicated detection logic. State is re-detected after every step that may have changed it.                                                                                                                            |

### Data-Flow Trace (Level 4)

The wizard renders prompts, indicator labels, and per-source smoke status — not dynamic data tables. Level 4 trace is not applicable (no DB-backed UI to verify is "flowing real data").

### Behavioral Spot-Checks

| Behavior                                              | Command                                                                                              | Result            | Status   |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ----------------- | -------- |
| Full pytest suite green (excluding slow Whisper test) | `uv run pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` | `593 passed, 1 deselected, 28 warnings in 2.70s` | ✓ PASS   |
| Ruff clean                                            | `uv run ruff check runos/ tests/`                                                                    | `All checks passed!` | ✓ PASS   |
| `setup` command registered                            | `grep "@app.command(\"setup\")" runos/cli.py`                                                        | one match at L943 | ✓ PASS   |
| `runos setup --help` lists every flag                 | `test_setup_cmd_via_clirunner_help_lists_all_flags` (`tests/test_setup_wizard.py:615`)               | passes in suite   | ✓ PASS   |
| Unknown `--only=<step>` exits 2                       | `test_setup_cmd_via_clirunner_unknown_only_step_exits_2` (`tests/test_setup_wizard.py:602`)          | passes in suite   | ✓ PASS   |

### Probe Execution

No phase-declared probes; `scripts/` has no `tests/probe-*.sh` for this phase. Not applicable.

### Requirements Coverage

| Requirement | Source Plan       | Description                                                                                | Status      | Evidence                                                                                                                                                              |
| ----------- | ----------------- | ------------------------------------------------------------------------------------------ | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SETUP-01    | 14-02 (and 14-03) | `runos setup` walks all 10 steps in order with welcome + summary                            | ✓ SATISFIED | `STEP_IDS` tuple + `step_welcome` + `step_finish` + 8 intermediate step functions; `test_wizard_runs_all_steps_in_order_fresh_install` confirms full ordering.        |
| SETUP-02    | 14-01, 14-02      | State detection + idempotent re-run; `[done]` / `[set] keep/change/fresh`                  | ✓ SATISFIED | `detect_install_state` + every step's `if state.X_configured: print_indicator(..., "done"); return skipped` short-circuit; `test_wizard_step_skipped_when_install_state_done`. |
| SETUP-03    | 14-01             | Atomic `.env` write with 0600; preserves untouched keys; secrets not echoed                | ✓ SATISFIED | `atomic_write_env` mirrors `tokens.py`; 30 env_io tests cover atomicity, perms, comment preservation; `prompt_secret` uses `hide_input=True`.                          |
| SETUP-04    | 14-02             | Every credentialed step delegates to existing flows; no duplicated handshake/plist/MFA     | ✓ SATISFIED | 6 in-process imports from `runos.cli`, `runos.connectors.factory`, `runos.scheduler`, `runos.sync.pipeline`; zero subprocess calls; tests assert one-call-each.       |
| SETUP-05    | 14-02             | All skip flags + `--only=<step>` (stackable) + smoke per-source + non-zero only on terminal failure | ✓ SATISFIED | All flags wired in `cli.py:944-972`; `--skip-telegram` implies `--skip-bot-scheduler`; `step_smoke` per-source loop; non-interactive aborts with exit 2.           |

No orphaned requirements; all 5 SETUP IDs are claimed by the executed plans.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| —    | —    | —       | —        | No `TODO`/`FIXME`/`XXX`/`TBD`/`HACK`/`placeholder` markers found in `runos/setup/*.py` or `tests/test_setup_*.py`. |

### Human Verification Required

The phase deliberately defers the *live* smoke test to a follow-up session (CONTEXT § "Out of scope"). The wizard is verified end-to-end against mocks for every delegated symbol; the only thing not yet exercised is a real `runos setup` against real Strava / Garmin / Telegram. This is intentional and matches the phase scope.

No items routed to human verification in this report (mocked tests are sufficient for "wizard orchestration works").

### Gaps Summary

None. All five Success Criteria pass; all 30 + 17 + 14 = **61 setup-suite tests** green within the 593-test full suite; ruff clean; no debt markers; all delegated symbols exist at the import paths the wizard names; `docs/SETUP.md` covers all 10 steps; README leads with `runos setup`.

---

*Verified: 2026-05-28*
*Verifier: Claude (gsd-verifier, Opus 4.7)*
