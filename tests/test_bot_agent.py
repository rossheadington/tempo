"""Tests for ``tempo.bot.agent`` (Phase 11 Plan 11-02 / VOICE-07/09/13).

Covers the Claude Agent SDK wrapper that Plan 11-03's handlers will compose with
the session store from Plan 11-01. The wrapper is intentionally tiny and fully
mockable -- these tests prove that the wrapper:

* concatenates **only** ``AssistantMessage`` text-block content into
  :class:`AgentTurn.text` (tool-use blocks must NOT leak into the Telegram reply,
  per VOICE-10 / Plan 11-CONTEXT "agent invocation pattern"),
* captures the resolved session id + token usage + optional cost from the final
  :class:`ResultMessage`,
* forwards ``session_id`` (None or a prior id) to the SDK as ``resume=...`` so
  the SDK either starts fresh or resumes an existing on-disk session log,
* tolerates a missing ``total_cost_usd`` (Claude subscription users) by setting
  ``AgentTurn.cost_usd = None`` with no exception,
* raises :class:`AgentInvocationError` when the SDK's Node subprocess is missing
  (FileNotFoundError) so Plan 11-03 can surface a user-facing message,
* HTML-escapes ``&`` / ``<`` / ``>`` in :func:`format_for_telegram` output,
* splits long replies on ``\\n\\n`` paragraph boundaries when possible and hard-
  splits as a last resort, with ``[k/N] `` prefixes when N > 1, every chunk
  <= 4096 chars (Telegram bot API body cap).

**No real SDK calls.** ``claude_agent_sdk.query`` is monkey-patched at the
``tempo.bot.agent.query`` import site; tests never touch Node, the ``claude``
CLI, or the network. Async tests use ``asyncio.run(...)`` inline -- matches
``tests/test_bot_handlers.py`` / ``tests/test_bot_app.py``; Tempo does not
use ``pytest-asyncio``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tempo.bot.agent import (
    TELEGRAM_MAX_BODY_CHARS,
    AgentInvocationError,
    AgentTurn,
    format_for_telegram,
    run_turn,
)


# ---------------------------------------------------------------------------
# Fake-iterator helpers
# ---------------------------------------------------------------------------


def _assistant_message(*text_parts: str, with_tool_use: bool = False) -> SimpleNamespace:
    """Build a duck-typed AssistantMessage stand-in.

    The agent module filters by ``getattr(message, "role", None) == "assistant"``
    plus ``hasattr(message, "content")``. Content blocks are filtered by
    ``getattr(block, "type", None) == "text"``.
    """
    content: list[SimpleNamespace] = [
        SimpleNamespace(type="text", text=part) for part in text_parts
    ]
    if with_tool_use:
        content.append(
            SimpleNamespace(
                type="tool_use",
                name="Read",
                input={"file_path": "/tmp/x"},
                # text attr deliberately missing -- the agent must not touch it
            )
        )
    return SimpleNamespace(role="assistant", content=content)


def _result_message(
    *,
    session_id: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_cost_usd: float | None = 0.0,
    include_cost: bool = True,
) -> SimpleNamespace:
    """Build a duck-typed ResultMessage stand-in.

    The agent module identifies these by having ``usage`` AND ``session_id``;
    ``total_cost_usd`` is read via ``getattr(..., None)`` so omitting the
    attribute (Claude-subscription path) is valid.
    """
    attrs: dict[str, object] = {
        "session_id": session_id,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    if include_cost:
        attrs["total_cost_usd"] = total_cost_usd
    return SimpleNamespace(**attrs)


def _make_fake_query(messages: list[SimpleNamespace], captured: list[dict] | None = None):
    """Return an async-iterator factory that yields ``messages`` and records call args."""

    async def fake_query(*, prompt: str, options):  # SDK uses kwargs-only
        if captured is not None:
            captured.append({"prompt": prompt, "options": options})
        for m in messages:
            yield m

    return fake_query


# ---------------------------------------------------------------------------
# run_turn behavioural tests
# ---------------------------------------------------------------------------


def test_run_turn_concatenates_assistant_text_blocks_only(monkeypatch, tmp_path: Path) -> None:
    """Only ``type="text"`` blocks land in ``AgentTurn.text``; tool_use is filtered."""
    messages = [
        SimpleNamespace(role="system", content=[]),  # ignored
        _assistant_message("part one ", "part two", with_tool_use=True),
        _result_message(session_id="sess-XYZ", input_tokens=10, output_tokens=20),
    ]
    monkeypatch.setattr("tempo.bot.agent.query", _make_fake_query(messages))

    turn = asyncio.run(run_turn("hi", None, cwd=tmp_path))

    assert isinstance(turn, AgentTurn)
    assert turn.text == "part one part two"  # tool_use block absent
    assert "tool_use" not in turn.text
    assert "Read" not in turn.text


def test_run_turn_captures_session_id_and_usage_from_result_message(
    monkeypatch, tmp_path: Path
) -> None:
    messages = [
        _assistant_message("hello"),
        _result_message(
            session_id="sess-XYZ",
            input_tokens=1234,
            output_tokens=567,
            total_cost_usd=0.0123,
        ),
    ]
    monkeypatch.setattr("tempo.bot.agent.query", _make_fake_query(messages))

    turn = asyncio.run(run_turn("hi", None, cwd=tmp_path))

    assert turn.session_id == "sess-XYZ"
    assert turn.tokens_in == 1234
    assert turn.tokens_out == 567
    assert turn.cost_usd == 0.0123
    assert turn.duration_s >= 0.0


def test_run_turn_returns_new_session_id_when_resume_is_none(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[dict] = []
    messages = [
        _assistant_message("fresh"),
        _result_message(session_id="sess-FRESH", input_tokens=1, output_tokens=2),
    ]
    monkeypatch.setattr("tempo.bot.agent.query", _make_fake_query(messages, captured))

    turn = asyncio.run(run_turn("hi", None, cwd=tmp_path))

    assert turn.session_id == "sess-FRESH"
    assert len(captured) == 1
    # ClaudeAgentOptions exposes the kwargs as attributes.
    assert captured[0]["options"].resume is None


def test_run_turn_passes_resume_to_options(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict] = []
    messages = [
        _assistant_message("resumed"),
        _result_message(session_id="sess-OLD", input_tokens=0, output_tokens=0),
    ]
    monkeypatch.setattr("tempo.bot.agent.query", _make_fake_query(messages, captured))

    turn = asyncio.run(run_turn("hi again", "sess-OLD", cwd=tmp_path))

    assert turn.session_id == "sess-OLD"
    assert len(captured) == 1
    assert captured[0]["options"].resume == "sess-OLD"
    # cwd is stringified and forwarded.
    assert captured[0]["options"].cwd == str(tmp_path)


def test_run_turn_cost_is_none_when_result_message_omits_total_cost_usd(
    monkeypatch, tmp_path: Path
) -> None:
    """Claude-subscription users don't get a per-turn cost; that's not an error."""
    messages = [
        _assistant_message("ok"),
        _result_message(
            session_id="sess-X",
            input_tokens=5,
            output_tokens=7,
            include_cost=False,
        ),
    ]
    monkeypatch.setattr("tempo.bot.agent.query", _make_fake_query(messages))

    turn = asyncio.run(run_turn("hi", None, cwd=tmp_path))

    assert turn.cost_usd is None
    assert turn.tokens_in == 5
    assert turn.tokens_out == 7


def test_run_turn_raises_agent_invocation_error_on_missing_cli(
    monkeypatch, tmp_path: Path
) -> None:
    """FileNotFoundError from the SDK (Node CLI missing) becomes AgentInvocationError."""

    async def boom(*, prompt: str, options):  # noqa: ARG001
        raise FileNotFoundError("claude")
        yield  # pragma: no cover -- make this an async generator

    monkeypatch.setattr("tempo.bot.agent.query", boom)

    with pytest.raises(AgentInvocationError) as excinfo:
        asyncio.run(run_turn("hi", None, cwd=tmp_path))

    assert "claude" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, FileNotFoundError)


def test_run_turn_raises_when_no_result_message(monkeypatch, tmp_path: Path) -> None:
    """If the SDK never yields a ResultMessage we have no session id to persist -- raise."""
    messages = [_assistant_message("only text, no result")]
    monkeypatch.setattr("tempo.bot.agent.query", _make_fake_query(messages))

    with pytest.raises(AgentInvocationError):
        asyncio.run(run_turn("hi", None, cwd=tmp_path))


# ---------------------------------------------------------------------------
# format_for_telegram tests
# ---------------------------------------------------------------------------


def test_format_for_telegram_short_text_html_escaped_single_chunk() -> None:
    chunks = format_for_telegram("3 < 4 & 5 > 4")
    assert chunks == ["3 &lt; 4 &amp; 5 &gt; 4"]
    # No prefix when N == 1
    assert not chunks[0].startswith("[1/")


def test_format_for_telegram_empty_string_returns_single_empty_chunk() -> None:
    assert format_for_telegram("") == [""]


def test_format_for_telegram_long_text_splits_on_paragraph_boundaries_with_prefix() -> None:
    # Three 2000-char paragraphs separated by \n\n -- total > 4096, splits cleanly.
    paragraphs = ["a" * 2000, "b" * 2000, "c" * 2000]
    text = "\n\n".join(paragraphs)
    chunks = format_for_telegram(text)

    assert len(chunks) >= 2
    for i, c in enumerate(chunks, start=1):
        assert len(c) <= TELEGRAM_MAX_BODY_CHARS
        assert c.startswith(f"[{i}/{len(chunks)}] ")
    # Re-joining the de-prefixed chunks reconstructs the escaped input.
    stripped = [c.split("] ", 1)[1] for c in chunks]
    assert "\n\n".join(stripped) == text  # no characters lost


def test_format_for_telegram_hard_split_when_no_paragraph_break() -> None:
    text = "x" * 6000
    chunks = format_for_telegram(text)

    assert len(chunks) >= 2
    for i, c in enumerate(chunks, start=1):
        assert len(c) <= TELEGRAM_MAX_BODY_CHARS
        assert c.startswith(f"[{i}/{len(chunks)}] ")
    # All 6000 x's are present (sum of un-prefixed bodies).
    body = "".join(c.split("] ", 1)[1] for c in chunks)
    assert body == text


def test_format_for_telegram_escapes_then_splits() -> None:
    """Escape happens BEFORE length math, so a paragraph of `<` (1 char) becomes
    `&lt;` (4 chars) and the post-escape length is what drives splitting."""
    # 2000 ``<`` chars escape to 8000 chars -- forces a split even without \n\n.
    chunks = format_for_telegram("<" * 2000)

    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= TELEGRAM_MAX_BODY_CHARS
        # No raw `<` should remain anywhere in the output.
        assert "<" not in c
    body = "".join(c.split("] ", 1)[1] for c in chunks)
    assert body == "&lt;" * 2000
