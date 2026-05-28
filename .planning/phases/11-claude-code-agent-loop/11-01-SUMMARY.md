---
phase: 11-claude-code-agent-loop
plan: 01
subsystem: bot
tags: [bot, sessions, sqlite, migrations, schema, docs]
requires:
  - migration runner (runos.db.migrate, SCHEMA_VERSION constant)
  - existing runos.bot package (re-export pattern from Phase 9/10)
  - conftest.py `conn` fixture (in-memory migrated SQLite)
provides:
  - runos.bot.sessions module (per-chat Claude Code session-id store)
  - bot_session table at schema v5
  - SESSION_WINDOW_HOURS (locked 4-hour resume window)
  - get_or_create_session / save_session / reset_session API
  - docs/TELEGRAM_BOT.md "Phase 11 prerequisites" section
affects:
  - Plan 11-02 (agent.py) -- will consume save_session + the session-id return
    from run_turn
  - Plan 11-03 (handlers wiring) -- will call get_or_create_session before each
    turn, save_session after, and reset_session from /new
tech-stack:
  added: []
  patterns:
    - "Validated boundary (mirrors runos.journal.service): a thin module that
      owns all writes to a single SQLite table via parameterised SQL inside
      `with conn:` transactions. No ORM. No free-form SQL outside this module."
    - "ISO 8601 UTC timestamps stored as TEXT, parsed back via
      datetime.fromisoformat for tz-aware comparison."
    - "SQLite UPSERT via INSERT ... ON CONFLICT(<pk>) DO UPDATE with a CASE
      expression on the conflicting row's value (preserves started_at when
      session_id is unchanged, rotates it when session_id flips)."
key-files:
  created:
    - runos/migrations/0005_bot_sessions.sql
    - runos/bot/sessions.py
    - tests/test_bot_sessions.py
  modified:
    - runos/db.py (SCHEMA_VERSION 4 -> 5, new BOT_TABLES constant)
    - runos/bot/__init__.py (re-export SESSION_WINDOW_HOURS + 3 functions)
    - tests/test_db.py (3 new tests for migration 0005)
    - docs/TELEGRAM_BOT.md (Phase 11 prerequisites section)
decisions:
  - "Locked 4-hour resume window (SESSION_WINDOW_HOURS = 4); future config
    knob if needed (11-CONTEXT.md Decisions)."
  - "Read-only get_or_create_session: returns None outside window but does NOT
    delete the stale row -- the next save_session UPSERT will overwrite. Keeps
    the function side-effect-free and the data-loss surface small."
  - "started_at semantics implemented in SQL via CASE on the conflict row, not
    in Python, so the rotation is atomic with the row write."
  - "No FK constraint on chat_id (Telegram-supplied external value)."
  - "Explicit ix_bot_session_chat_id index even though PK already covers it,
    for symmetry with ix_wellness_day / ix_journal_day from prior migrations."
  - "Re-export the public surface from runos.bot to match the existing
    handlers/transcribe pattern (`from runos.bot import get_or_create_session`)."
metrics:
  duration_minutes: 12
  completed: 2026-05-27
  tasks_complete: 3
  files_created: 3
  files_modified: 4
  tests_added: 13
  tests_total: 437
  lines_added: ~270
---

# Phase 11 Plan 01: Session-id store + migration 0005 Summary

## One-liner

Lays the deterministic data foundation for the Phase 11 agent loop: a per-chat
Claude Code session-id store backed by a new `bot_session` table at schema v5,
with a locked 4-hour resume window and a parameterised-SQL boundary module
ready for the agent (11-02) and handler-wiring (11-03) plans to consume in
parallel.

## What got built

### 1. Migration `0005_bot_sessions.sql` + `runos.db` constants (Task 1)

- New migration file `runos/migrations/0005_bot_sessions.sql` creates the
  `bot_session` table:
  ```sql
  CREATE TABLE bot_session (
      chat_id          INTEGER PRIMARY KEY,
      session_id       TEXT NOT NULL,
      last_message_at  TEXT NOT NULL,   -- ISO 8601 UTC
      started_at       TEXT NOT NULL    -- ISO 8601 UTC
  );
  CREATE INDEX ix_bot_session_chat_id ON bot_session(chat_id);
  ```
  Header comment follows the `0003_journal.sql` / `0004_wellness.sql` style:
  references VOICE-08, references 11-CONTEXT.md, names the trust boundary
  (`runos.bot.sessions`), and documents why there is no FK on `chat_id` (it
  is a Telegram-supplied external value).
- `runos.db.SCHEMA_VERSION` bumped from 4 to 5.
- New module constant `runos.db.BOT_TABLES = ("bot_session",)` placed
  alongside `FOUNDATION_TABLES` / `STRUCTURED_TABLES` / `JOURNAL_TABLES` /
  `WELLNESS_TABLES`, with a one-line "Phase 11 (v1.1)" comment.
- Three new tests in `tests/test_db.py`:
  - `test_migrate_creates_bot_session_table` -- fresh `init_db` yields a DB
    at `user_version=5` with `bot_session` in `table_names`; asserts both
    `db.SCHEMA_VERSION == 5` and `db.BOT_TABLES == ("bot_session",)`.
  - `test_bot_session_table_has_expected_columns` -- exact column set
    `{chat_id, session_id, last_message_at, started_at}`, PK is
    `[chat_id]` only, the three TEXT columns are NOT NULL, types match.
  - `test_migrate_is_idempotent_at_v5` -- calling `db.migrate(conn)` on a
    just-initialised DB is a no-op (returns 5, no error), and every prior
    table (foundation + structured + journal + wellness + bot) still exists.

### 2. `runos/bot/sessions.py` + re-exports + ~10 tests (Task 2)

- New module `runos/bot/sessions.py` (~140 lines incl. docstrings):
  - `SESSION_WINDOW_HOURS: int = 4` -- the locked default per 11-CONTEXT.md.
  - `get_or_create_session(conn, chat_id, *, now=None, window_hours=4) -> str | None`
    -- read-only lookup; parses `last_message_at` via `datetime.fromisoformat`
    and compares against `(now or datetime.now(UTC))`. Returns the stored
    session id only if `now - last_at < window`. Never inserts/updates/deletes.
  - `save_session(conn, chat_id, session_id, now=None) -> None`
    -- UPSERT via `INSERT INTO bot_session ... ON CONFLICT(chat_id) DO UPDATE`
    with a `CASE` expression that preserves `started_at` when the conflicting
    row's `session_id` matches the incoming value and resets it otherwise.
    Wrapped in `with conn:` for transactional commit.
  - `reset_session(conn, chat_id) -> None`
    -- `DELETE FROM bot_session WHERE chat_id = ?`; idempotent on absent rows
    by virtue of SQL semantics.
  - Internal helpers `_now()` and `_load_session()`.
  - Imports: stdlib only (`sqlite3`, `datetime`). No Telegram, no
    `claude_agent_sdk`, no `faster_whisper` (verified via grep --
    success-criteria check 5).
- `runos/bot/__init__.py` extended:
  - Adds the four names (`SESSION_WINDOW_HOURS`, `get_or_create_session`,
    `save_session`, `reset_session`) to the import block and `__all__`
    (kept sorted).
  - Module docstring "Modules" bullet list now includes a
    `:mod:`runos.bot.sessions`` entry naming the three exports and the
    window constant.
- New test file `tests/test_bot_sessions.py` -- 10 tests using the existing
  `conn` fixture from `conftest.py` (real on-disk migrated SQLite, no mocks):
  - `test_session_window_hours_constant_is_four`
  - `test_get_or_create_session_returns_none_on_empty_table` (asserts
    no side-effect row inserted)
  - `test_save_then_get_within_window_returns_same_session_id` (T0+3h)
  - `test_get_just_inside_window_returns_session_id` (T0 + 4h - 1s)
  - `test_get_after_window_returns_none` (T0 + 4h + 1s; row remains)
  - `test_save_session_preserves_started_at_on_same_id`
  - `test_save_session_resets_started_at_on_new_id`
  - `test_reset_session_deletes_row_and_subsequent_get_returns_none`
    (incl. idempotency on second reset)
  - `test_two_chat_ids_do_not_interfere`
  - `test_session_re_exports_from_tempo_bot_package`

  All assertions use the fixed anchor `T0 = datetime(2026, 5, 27, 12, 0,
  tzinfo=UTC)` so window-boundary tests are deterministic.

### 3. `docs/TELEGRAM_BOT.md` -- "Phase 11 prerequisites" section (Task 3)

New `## Phase 11 prerequisites (Claude Code agent loop)` section inserted
after `## Stop the bot` and before `## Troubleshooting` (does not disturb
the existing Phase 9/10 onboarding flow). Content:

- Lead sentence: explains that Phase 11 routes every message through Claude
  Code via `claude-agent-sdk` which spawns the `claude` Node CLI as a
  subprocess and uses the user's existing Claude Code login. Explicit: the
  bot does NOT use `ANTHROPIC_API_KEY`.
- Three numbered prerequisites: (1) Node 18+ on PATH + macOS install line
  + launchd PATH note for Phase 12, (2) `claude login` once via OAuth, (3)
  `claude-agent-sdk` Python package added by `uv sync` in Plan 11-02.
- One-line shell check: `command -v claude || echo "claude CLI missing"`.
- Auth-precedence callout: if `ANTHROPIC_API_KEY` is set the SDK may prefer
  it; RunOS's invocation does not pass an API key; leave it unset for v1.1.
- Session-memory callout: the bot remembers the last 4 hours of chat;
  `/new` resets the session early; the id is stored in `bot_session`.

## Acceptance against PLAN

| Must-have                                                                          | Evidence                                                              |
| ---------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `bot_session` table exists after `init_db()`, SCHEMA_VERSION reports 5             | `test_migrate_creates_bot_session_table` PASS; `grep SCHEMA_VERSION` -> 5 |
| `get_or_create_session(chat_id)` returns None on first call                        | `test_get_or_create_session_returns_none_on_empty_table` PASS         |
| Returns same session id within the 4-hour window after `save_session`              | `test_save_then_get_within_window_returns_same_session_id` PASS; `test_get_just_inside_window_returns_session_id` PASS |
| Returns None when `last_message_at` is older than the window                       | `test_get_after_window_returns_none` PASS                             |
| `save_session(chat_id, session_id, now)` updates `last_message_at` (window resets) | `test_save_session_preserves_started_at_on_same_id` PASS              |
| `reset_session(chat_id)` deletes the row                                           | `test_reset_session_deletes_row_and_subsequent_get_returns_none` PASS |
| Node 18+ + `claude` CLI prerequisite documented in docs/TELEGRAM_BOT.md            | `grep -c "Phase 11 prerequisites"` -> 1; `grep` for Node 18 / claude login / bot_session -> all hit |

Verification commands (all green):

- `uv run pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` -> **437 passed, 1 deselected**.
- `uv run ruff check .` -> **All checks passed**.
- `uv run ruff format --check .` -> **87 files already formatted**.
- `python -c "from runos.bot import get_or_create_session, save_session, reset_session, SESSION_WINDOW_HOURS; print(SESSION_WINDOW_HOURS)"` -> **4**.
- `grep -n "SCHEMA_VERSION = 5" runos/db.py` -> hit.
- `grep -n "CREATE TABLE bot_session" runos/migrations/0005_bot_sessions.sql` -> hit.
- Forbidden-imports grep on `runos/bot/sessions.py` (`claude_agent_sdk` | `telegram` | `faster_whisper`) -> 0 matches (success-criterion 5).

## Commits

1. `5127a6c` -- `test(11-01): add failing tests for bot_session migration + SCHEMA_VERSION=5`
2. `a7c70ae` -- `feat(11-01): add 0005_bot_sessions migration + SCHEMA_VERSION=5 + BOT_TABLES`
3. `af9d7c0` -- `test(11-01): add failing tests for runos.bot.sessions store`
4. `d181225` -- `feat(11-01): runos.bot.sessions per-chat Claude Code session-id store`
5. `c752d8c` -- `docs(11-01): add Phase 11 prerequisites section to docs/TELEGRAM_BOT.md`

Tasks 1 and 2 used a strict RED/GREEN cycle (test commit -> implementation
commit); Task 3 is a docs-only change with no test gate.

## Deviations from Plan

None of substance. The plan called for "~5" tests in `tests/test_bot_sessions.py`
and "~3" in `tests/test_db.py`; landed 10 and 3 respectively, both within
the stated `~5-7` and `~3` ranges in the plan's task descriptions.

Two minor mechanical adjustments by the ruff auto-fixer after the initial
write (import ordering and a small line-break reflow inside the
`_load_session` helper signature); the behaviour is unchanged. These are
formatter-only diffs and were folded into the same Task 2 GREEN commit.

The plan's verify gate enumerated 437 tests as the expected total (424
baseline + ~13 new = ~437); the suite reports **437 passed** exactly.

## Threat Flags

None. This plan adds a new SQLite table and a Python module that owns its
writes; no new network surface, no new auth path, no schema change at any
existing trust boundary. The `bot_session` schema and the `_save_session` /
`_load_session` helpers form the validated boundary that future plans
(11-02, 11-03) must go through to write the table -- consistent with the
existing `runos.journal.service` pattern.

## What's next

- **Plan 11-02**: implement `runos/bot/agent.py` with `AgentTurn` dataclass
  and `run_turn(prompt, session_id) -> AgentTurn` wrapping
  `claude_agent_sdk.query`. This plan can now consume `save_session(chat_id,
  turn.session_id, now)` against the settled interface.
- **Plan 11-03**: wire `voice_handler` / `text_handler` / `/new` to call
  `get_or_create_session` -> `run_turn` -> `save_session` (or
  `reset_session` for `/new`). Typing indicator + 4096-char split land here.

## Self-Check: PASSED

All 5 commits exist in `git log --all`; all 3 created files exist on disk;
all 4 modified files exist on disk and reflect the documented changes;
`uv run pytest` reports 437 passed with the documented deselect; ruff
check + format both clean; the verification grep checks from the plan all
hit.
