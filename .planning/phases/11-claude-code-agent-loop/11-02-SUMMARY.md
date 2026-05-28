---
phase: 11-claude-code-agent-loop
plan: 02
subsystem: bot
tags: [bot, claude-agent-sdk, telegram, voice-coach]
requires:
  - "claude-agent-sdk python wrapper (>=0.2.87)"
  - "Plan 10-01 / 11-01 prerequisites (runos/bot package + sessions module already in tree)"
provides:
  - "runos.bot.agent.AgentTurn — frozen dataclass returned by run_turn"
  - "runos.bot.agent.AgentInvocationError — typed error for missing Node CLI / auth"
  - "runos.bot.agent.run_turn — async fn wrapping claude_agent_sdk.query"
  - "runos.bot.agent.format_for_telegram — HTML-escape + 4096-char chunking helper"
affects:
  - "Plan 11-03 will import all four names via `from runos.bot import ...`"
tech-stack:
  added:
    - "claude-agent-sdk>=0.2.87 (runtime dep)"
  patterns:
    - "Async iterator consumption with try/except mapping FileNotFoundError + CalledProcessError to a typed AgentInvocationError"
    - "Duck-typed message identification (role + content / usage + session_id) to absorb SDK class-name churn"
    - "Worst-case prefix-budget reservation so post-chunk assertion cannot trip"
key-files:
  created:
    - runos/bot/agent.py
    - tests/test_bot_agent.py
    - .planning/phases/11-claude-code-agent-loop/11-02-SUMMARY.md
  modified:
    - pyproject.toml
    - uv.lock
    - runos/bot/__init__.py
decisions:
  - "Duck-type messages (role+content for AssistantMessage; usage+session_id for ResultMessage) instead of isinstance against SDK private types — survives SDK class-name renames"
  - "Reserve worst-case `[NN/NN] ` (8-char) prefix width up front so the post-chunk size assertion always holds"
  - "Treat missing session_id on the final ResultMessage as AgentInvocationError (no id to persist = lost session = surface, don't paper over)"
  - "Leave `setting_sources` at SDK default — we WANT the user's ~/.claude config (subscription auth + skills + slash commands), per 11-CONTEXT <decisions>"
metrics:
  duration_minutes: 8
  completed_at: 2026-05-27T22:40:49Z
  tasks_completed: 3
  files_created: 3
  files_modified: 3
  tests_added: 12
---

# Phase 11 Plan 02: Claude Agent SDK Wrapper Summary

**One-liner:** Wraps `claude_agent_sdk.query` behind a fully-mockable `run_turn(prompt, session_id, *, cwd) -> AgentTurn` plus a `format_for_telegram(text)` chunker so Plan 11-03's handlers can compose the SDK with the session store without ever importing the real SDK.

## What Was Built

### `runos/bot/agent.py` (new module)

The single seam between RunOS and `claude_agent_sdk`. Public surface:

- **`AgentTurn`** — `@dataclass(frozen=True, slots=True)` with the exact six fields specified in the plan's `<interfaces>`: `text`, `session_id`, `tokens_in`, `tokens_out`, `cost_usd: float | None`, `duration_s`.
- **`AgentInvocationError(Exception)`** — typed error Plan 11-03 will catch to surface a user-facing "claude login" hint. Raised on:
  - `FileNotFoundError` from `query()` (Node CLI missing / not on PATH).
  - `subprocess.CalledProcessError` from `query()` (CLI subprocess failed).
  - SDK yielding no `ResultMessage` at all (no session id to persist — defensive).
- **`run_turn(prompt, session_id, *, cwd) -> AgentTurn`** — async; builds `ClaudeAgentOptions(cwd=str(cwd), resume=session_id)`, iterates `query(prompt=..., options=...)`, and:
  - Collects text from `AssistantMessage.content` blocks where `block.type == "text"`. Tool-use, tool-result, thinking, system, user blocks are filtered out per VOICE-10.
  - Reads `session_id`, `usage.input_tokens`, `usage.output_tokens`, and `total_cost_usd` (via `getattr(..., None)`) from the final `ResultMessage`.
  - Times the whole call with `time.monotonic()`.
- **`format_for_telegram(text) -> list[str]`** — `html.escape(text, quote=False)` then:
  - Single chunk (no prefix) when escaped length ≤ 4096.
  - Otherwise greedy-packs paragraphs (split on `\n\n`) into chunks of ≤ `4096 - 8` chars (reserving worst-case `[NN/NN] ` prefix), hard-splits oversized single paragraphs, and prefixes each final chunk `[k/N] `.

The module is **import-safe**: importing `runos.bot.agent` (or `runos.bot`) does NOT spawn the `claude` CLI, touch the network, or read any state. The SDK subprocess only starts when `run_turn` is awaited.

### `runos/bot/__init__.py` (modified)

Re-exports `AgentTurn`, `AgentInvocationError`, `run_turn`, `format_for_telegram` via the package-level `__all__` (kept sorted). Module docstring extended with the new `runos.bot.agent` bullet.

### `tests/test_bot_agent.py` (new — 12 tests)

| # | Test                                                                      | Proves                                                       |
| - | ------------------------------------------------------------------------- | ------------------------------------------------------------ |
| 1 | `test_run_turn_concatenates_assistant_text_blocks_only`                   | tool_use blocks filtered (VOICE-10)                          |
| 2 | `test_run_turn_captures_session_id_and_usage_from_result_message`         | session id + token counts + cost propagated                  |
| 3 | `test_run_turn_returns_new_session_id_when_resume_is_none`                | `resume=None` forwarded; SDK-issued id propagated            |
| 4 | `test_run_turn_passes_resume_to_options`                                  | `resume=session_id` + `cwd=str(path)` forwarded              |
| 5 | `test_run_turn_cost_is_none_when_result_message_omits_total_cost_usd`     | Claude-subscription path: `cost_usd is None`, no exception   |
| 6 | `test_run_turn_raises_agent_invocation_error_on_missing_cli`              | `FileNotFoundError` → `AgentInvocationError` with `__cause__`|
| 7 | `test_run_turn_raises_when_no_result_message`                             | missing ResultMessage → `AgentInvocationError`               |
| 8 | `test_format_for_telegram_short_text_html_escaped_single_chunk`           | `&` `<` `>` escaped; no prefix when N==1                     |
| 9 | `test_format_for_telegram_empty_string_returns_single_empty_chunk`        | `""` → `[""]`                                                |
| 10| `test_format_for_telegram_long_text_splits_on_paragraph_boundaries_with_prefix` | clean paragraph splits; `[k/N] ` prefixes; reconstructable |
| 11| `test_format_for_telegram_hard_split_when_no_paragraph_break`             | hard-split fallback for paragraph > budget                   |
| 12| `test_format_for_telegram_escapes_then_splits`                            | escape happens BEFORE length math (post-escape size drives split) |

All tests monkeypatch `runos.bot.agent.query` with a fake async iterator that yields `SimpleNamespace` stand-ins. No Node subprocess, no `claude` CLI, no network is involved. Async tests use `asyncio.run(...)` inline — matches `tests/test_bot_handlers.py` / `tests/test_bot_app.py`.

### `pyproject.toml` (modified)

`claude-agent-sdk>=0.2.87` added to `[project] dependencies`. `uv.lock` updated by `uv add`.

## Verification

| Check                                                                         | Result |
| ----------------------------------------------------------------------------- | ------ |
| `uv run python -c "import claude_agent_sdk"`                                  | OK     |
| `grep -n "claude-agent-sdk" pyproject.toml`                                   | hit at line 15 |
| `uv run pytest tests/test_bot_agent.py -x -v`                                 | 12 passed |
| `uv run pytest`                                                               | 450 passed |
| `uv run ruff check runos/bot/agent.py tests/test_bot_agent.py runos/bot/__init__.py` | clean |
| `uv run ruff format --check ...`                                              | clean |
| `grep -c "def test_" tests/test_bot_agent.py >= 6`                            | 12 (≥ 6) |
| `grep -c "claude_agent_sdk" tests/test_bot_agent.py` (real import count)     | 0 source-level imports (1 docstring mention) |
| `runos/bot/agent.py` imports `telegram` or `faster_whisper`                   | NO (success criterion #6) |

## Success Criteria

1. **claude-agent-sdk runtime dependency + importable** — `uv add claude-agent-sdk` installed 0.2.87; `import claude_agent_sdk` succeeds in `uv run python`.
2. **Public surface matches `<interfaces>`** — `AgentTurn`, `AgentInvocationError`, `run_turn`, `format_for_telegram` with the exact signatures from the plan.
3. **`run_turn` filters non-text blocks; surfaces typed error on missing CLI** — Tests 1, 6, 7 cover.
4. **`format_for_telegram` HTML-escapes + 4096 cap + `[k/N] ` prefixes** — Tests 8–12 cover.
5. **Tests are fully offline** — No real `claude_agent_sdk.query`, no Node, no network. 12 tests pass; full suite remains green at 450/450.
6. **No `telegram` / `faster_whisper` imports in `runos/bot/agent.py`** — verified by grep; the module imports only stdlib + `claude_agent_sdk`.

## Deviations from Plan

None. Plan executed exactly as written. Minor stylistic note:

- The plan suggested catching `subprocess.CalledProcessError` to map to `AgentInvocationError`; I implemented both that catch and a defensive "no ResultMessage was yielded" branch (also mapping to `AgentInvocationError`). The plan called this out under `<action>` ("if `final_session_id is None`, raise `AgentInvocationError("SDK did not surface a session id")` (defensive — every successful turn should produce one)") so it is implementation of the plan rather than a deviation.

## Worktree Setup Note

Worktree was forked from a commit predating Phases 9, 10, and 11-01. Per the executor preamble, I fast-forward-merged `main` into the worktree branch (`git merge main --ff-only`) before starting any plan work. This brought in `runos/bot/__init__.py`, `runos/bot/app.py`, `runos/bot/handlers.py`, `runos/bot/sessions.py`, `runos/bot/transcribe.py`, migration `0005_bot_sessions.sql`, and 11-01-SUMMARY.md. No conflicts.

## Commits

| Hash      | Type    | Message                                                       |
| --------- | ------- | ------------------------------------------------------------- |
| 2619c76   | chore   | chore(11-02): add claude-agent-sdk runtime dependency         |
| a5d0a14   | test    | test(11-02): add failing tests for runos.bot.agent (RED)      |
| dd2de53   | feat    | feat(11-02): implement runos.bot.agent Claude Agent SDK wrapper (GREEN) |

## TDD Gate Compliance

- RED gate (test commit): `a5d0a14 test(11-02): add failing tests for runos.bot.agent (RED)` — `ModuleNotFoundError: No module named 'runos.bot.agent'` confirmed before GREEN.
- GREEN gate (feat commit): `dd2de53 feat(11-02): implement runos.bot.agent Claude Agent SDK wrapper (GREEN)` — all 12 tests pass.
- REFACTOR gate: not required (implementation was clean; ruff auto-format applied within the same GREEN commit).

## Known Stubs

None. Every public name in `runos/bot/agent.py` is fully implemented with tests proving behaviour. The module does not stub UI surfaces — it is a pure library used by Plan 11-03's handlers.

## Self-Check: PASSED

- `runos/bot/agent.py` exists: FOUND
- `tests/test_bot_agent.py` exists: FOUND
- Commit `2619c76` (chore): FOUND in git log
- Commit `a5d0a14` (test/RED): FOUND in git log
- Commit `dd2de53` (feat/GREEN): FOUND in git log
- `claude-agent-sdk` in pyproject.toml: FOUND
- All 12 new tests pass: VERIFIED
- Full suite 450/450 green: VERIFIED
- Ruff lint + format clean: VERIFIED
