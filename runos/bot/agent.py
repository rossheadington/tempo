"""Claude Agent SDK wrapper for the RunOS Telegram bot (Phase 11 / VOICE-07/09/13).

This module is the **only** seam between RunOS and ``claude_agent_sdk``: Plan
11-03's handlers compose :func:`run_turn` with the per-chat session-id store
from Plan 11-01 and the warmed Whisper transcriber from Plan 10-01 to deliver
the voice-coach loop. Keeping the SDK boundary in ~150 lines means Plan 11-03's
handler tests can ``monkeypatch.setattr("runos.bot.agent.run_turn", ...)``
without ever importing the real SDK -- and any future SDK churn is contained.

What this module owns:

* :class:`AgentTurn` -- the immutable result of a single SDK round-trip:
  final assistant text (concatenated from ``AssistantMessage.content`` text
  blocks only, **never** tool-use blocks per VOICE-10), the resolved session
  id to persist, input/output token counts, an optional cost in USD, and the
  wall-clock duration of the call.

* :class:`AgentInvocationError` -- raised when the SDK's Node subprocess is
  missing or unauthenticated (``FileNotFoundError`` or
  ``subprocess.CalledProcessError``), so Plan 11-03's handlers can map a
  single exception type to the user-facing
  "Claude Code isn't running. Try ``claude login`` in a terminal." reply.

* :func:`run_turn` -- the async entry point. Builds
  :class:`ClaudeAgentOptions` with the RunOS project root as ``cwd`` (so the
  agent has access to RunOS's slash commands, skills, and gitignored data
  files via the user's existing Claude Code login) and forwards
  ``resume=session_id`` to either start fresh (``None``) or resume an
  existing on-disk session log. Note: we deliberately do **not** set
  ``setting_sources=[]`` -- the agent-sdk-research "loads ~/.claude" pitfall
  is intentionally accepted here (see ``.planning/phases/11-claude-code-agent-loop/11-CONTEXT.md``
  ``<decisions>``) because we WANT the user's Claude subscription auth plus
  their personal Claude Code configuration.

* :func:`format_for_telegram` -- HTML-escapes the assistant text (``&`` /
  ``<`` / ``>`` only, per VOICE-09 + the Telegram ``ParseMode.HTML``
  convention Plan 10-02 established) and splits the result into chunks no
  larger than :data:`TELEGRAM_MAX_BODY_CHARS` (4096), preferring ``\\n\\n``
  paragraph boundaries and hard-splitting only as a fallback. Multi-chunk
  output is prefixed ``[k/N] `` per chunk; the prefix counts against the
  4096-character budget.

Cost accounting: Claude-subscription users typically see
``ResultMessage.total_cost_usd`` either absent or ``None`` (the SDK only
populates it when the underlying provider returns a cost). We surface that
as ``AgentTurn.cost_usd is None`` rather than raising -- callers should treat
token counts (always present) as the primary usage proxy, with cost as
best-effort additional signal when available.
"""

from __future__ import annotations

import html
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query

logger = logging.getLogger(__name__)


# Telegram's hard cap on message body length (sendMessage `text` field).
# Plan 11-CONTEXT.md "Reply formatting" locks this at 4096; callers that need
# a tighter budget for testing pass it through via ``format_for_telegram``
# (no public arg yet -- the constant is the test seam).
TELEGRAM_MAX_BODY_CHARS: int = 4096

# Paragraph boundary used for "soft" chunking before falling back to a hard
# character split. The Telegram convention for assistant text is double-
# newline-separated paragraphs (VOICE-09 / telegram-bot-research.md).
_PARA_SEPARATOR: str = "\n\n"

# Worst-case prefix width: ``[NN/NN] `` is 8 chars (room for up to 99 chunks,
# which at >= ~400 KB of text is far beyond any realistic single reply). We
# reserve this so that the per-chunk budget is computed once up front and the
# final assertion that every chunk fits cannot fail.
_PREFIX_BUDGET: int = len("[99/99] ")


class AgentInvocationError(Exception):
    """The Claude Agent SDK could not be invoked.

    Raised by :func:`run_turn` when the SDK's Node subprocess is missing
    (the ``claude`` CLI is not installed or not on ``PATH``) or when the
    underlying ``subprocess`` call fails before any message is yielded.

    Plan 11-03's :func:`voice_handler` / :func:`text_handler` catch this
    by type and reply with a fixed, user-actionable message (VOICE-07).
    """


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """Result of a single :func:`run_turn` call.

    Attributes:
        text: Concatenated assistant text from all ``AssistantMessage``
            content blocks where ``block.type == "text"``. Tool-use,
            tool-result, thinking, and system messages are excluded by
            design (VOICE-10) -- the bot relays only the final, user-
            facing prose. Stripped of leading/trailing whitespace.
        session_id: Session id surfaced on the final ``ResultMessage``.
            When :func:`run_turn` is called with ``session_id=None`` the
            SDK creates a fresh session and this is the new id; when
            called with a prior id, the SDK either echoes it back
            (resumed) or surfaces a new one (if the on-disk log was
            deleted). Either way, this is what the caller should
            persist via :func:`runos.bot.sessions.save_session`.
        tokens_in: ``usage.input_tokens`` from the final ResultMessage.
        tokens_out: ``usage.output_tokens`` from the final ResultMessage.
        cost_usd: ``ResultMessage.total_cost_usd`` when present;
            ``None`` for Claude-subscription paths where the SDK does
            not surface a per-turn cost. Callers should treat tokens as
            the primary usage proxy.
        duration_s: Wall-clock seconds for the whole :func:`run_turn`
            call, measured with :func:`time.monotonic` so it is robust
            to system-clock changes.
    """

    text: str
    session_id: str
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    duration_s: float


def _looks_like_assistant_message(message: object) -> bool:
    """Duck-type check for an AssistantMessage.

    We deliberately do **not** ``isinstance``-check against
    ``claude_agent_sdk.AssistantMessage`` -- the SDK's public class names
    have changed across 0.1.x / 0.2.x and may again in 0.3.x. The SDK's
    AssistantMessage does NOT carry a ``role`` attribute, so we identify
    it by class name + a ``content`` iterable. Dict-shaped messages
    (used in tests) still match via the ``role="assistant"`` convention.
    """
    if isinstance(message, dict):
        return message.get("role") == "assistant" and "content" in message
    if getattr(message, "content", None) is None:
        return False
    # Real SDK 0.2.x: class name match (no ``role`` attribute exposed).
    # Test mocks: ``role="assistant"`` on a SimpleNamespace.
    return (
        type(message).__name__ == "AssistantMessage"
        or getattr(message, "role", None) == "assistant"
    )


def _looks_like_result_message(message: object) -> bool:
    """Duck-type check for a ResultMessage (has `usage` and `session_id`)."""
    return hasattr(message, "session_id") and hasattr(message, "usage")


def _extract_text_from_block(block: object) -> str | None:
    """Return the text payload of a content block, or None if it's not text.

    Tolerates both object-shaped blocks (``block.type``, ``block.text``,
    e.g. :class:`claude_agent_sdk.TextBlock`) and dict-shaped blocks
    (``block["type"]``, ``block["text"]``) -- the SDK exposes the former
    in 0.2.x but tests and adjacent SDK paths sometimes use dicts.
    """
    if isinstance(block, dict):
        if block.get("type") == "text":
            return str(block.get("text", ""))
        return None
    # SDK 0.2.x TextBlock has only ``.text``; no ``.type`` attribute.
    # Identify by class name + presence of ``text`` attr.
    if type(block).__name__ == "TextBlock" and hasattr(block, "text"):
        return str(block.text)
    if getattr(block, "type", None) == "text":
        return str(getattr(block, "text", ""))
    return None


async def run_turn(
    prompt: str,
    session_id: str | None,
    *,
    cwd: Path,
) -> AgentTurn:
    """Run a single Claude Agent turn and capture the final assistant reply.

    The SDK is an async iterator over :class:`Message` objects: system /
    assistant / user / tool-use / tool-result / result. We accumulate
    text content from assistant messages, ignore everything else, and
    read session id + token usage + optional cost from the final
    :class:`ResultMessage`.

    Args:
        prompt: User text fed to the agent (already transcribed for voice
            memos; raw text for ``/`` commands and free-form messages).
        session_id: Existing Claude Code session id to resume, or
            ``None`` to start a fresh session. Sourced from
            :func:`runos.bot.sessions.get_or_create_session`.
        cwd: Working directory the agent runs in. Plan 11-03 passes the
            RunOS repo root so the agent has access to RunOS's slash
            commands, skills, and gitignored data files via the user's
            existing Claude Code config.

    Returns:
        :class:`AgentTurn` with the concatenated assistant text, resolved
        session id, token usage, optional cost, and wall-clock duration.

    Raises:
        AgentInvocationError: The Node ``claude`` CLI is missing
            (``FileNotFoundError``) or its subprocess returned non-zero
            (``subprocess.CalledProcessError``) before any message was
            yielded. Plan 11-03 catches this to emit a user-facing
            'claude login' hint.
    """
    # 11-CONTEXT.md <decisions>: we DO want the user's ~/.claude config loaded
    # (subscription auth + slash commands + skills), so we leave
    # ``setting_sources`` at its default. See agent-sdk-research.md pitfalls.
    options = ClaudeAgentOptions(cwd=str(cwd), resume=session_id)

    start = time.monotonic()
    text_parts: list[str] = []
    final_session_id: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            if _looks_like_assistant_message(message):
                for block in message.content:
                    text = _extract_text_from_block(block)
                    if text is not None:
                        text_parts.append(text)
                continue
            if _looks_like_result_message(message):
                final_session_id = str(message.session_id)
                usage = message.usage or {}
                tokens_in = int(usage.get("input_tokens", 0))
                tokens_out = int(usage.get("output_tokens", 0))
                cost = getattr(message, "total_cost_usd", None)
                continue
            logger.debug("run_turn: ignoring message of type %s", type(message).__name__)
    except FileNotFoundError as exc:
        raise AgentInvocationError(
            "claude CLI not found -- is Node installed and `claude login` run?"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise AgentInvocationError(
            "claude CLI subprocess failed -- the Claude Code login may be stale "
            "(try `claude login` in a terminal)"
        ) from exc

    if final_session_id is None:
        # Defensive: every successful turn must produce a ResultMessage with
        # a session id. If it doesn't, we have no id to persist and the SDK
        # is in an unexpected state -- surface as AgentInvocationError so
        # Plan 11-03 maps it to a user-visible failure rather than a silent
        # "lost session" handoff.
        raise AgentInvocationError(
            "SDK did not surface a session id -- agent run completed without a ResultMessage"
        )

    duration_s = time.monotonic() - start
    return AgentTurn(
        text="".join(text_parts).strip(),
        session_id=final_session_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        duration_s=duration_s,
    )


def _escape_with_table_pre_blocks(text: str) -> str:
    """HTML-escape ``text`` while converting Markdown tables to ``<pre>`` blocks.

    Telegram has NO HTML table tag, but ``<pre>`` renders in a monospace font
    with horizontal scroll on overflow -- so a column-aligned plain-text table
    inside ``<pre>`` looks right on every client. Without this, raw Markdown
    pipes get HTML-escaped into a wall of unaligned bars.

    Algorithm: single line-by-line pass. Non-table text is HTML-escaped
    normally. When we hit a candidate table row (line starts+ends with
    ``|`` AND the next line is a separator like ``|---|---|``), we collect
    the contiguous block of table rows and replace it with a single
    pre-rendered ``<pre>`` block (cells stripped, padded to column width,
    contents escaped). The separator row is dropped from the rendered
    output. Lenient: a header row without a trailing separator falls back
    to plain escaped text.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _is_table_row(lines[i]) or i + 1 >= len(lines) or not _is_table_separator(
            lines[i + 1]
        ):
            out.append(html.escape(lines[i], quote=False))
            i += 1
            continue
        # Collect contiguous table rows: header, separator, body rows.
        table_lines: list[str] = [lines[i]]
        j = i + 2  # skip separator
        while j < len(lines) and _is_table_row(lines[j]):
            table_lines.append(lines[j])
            j += 1
        out.append(_format_table_as_pre(table_lines))
        i = j
    return "\n".join(out)


def _is_table_row(line: str) -> bool:
    """A table row starts AND ends with ``|`` (after stripping)."""
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and len(s) >= 2


def _is_table_separator(line: str) -> bool:
    """Separator rows contain only ``|`` / ``-`` / ``:`` / whitespace."""
    s = line.strip()
    return (
        s.startswith("|")
        and s.endswith("|")
        and all(c in "|-: \t" for c in s)
        and "-" in s
    )


def _split_table_row(line: str) -> list[str]:
    """Split a ``| a | b | c |`` row into ``["a", "b", "c"]`` (stripped)."""
    s = line.strip()
    # Strip the leading/trailing | and split. ``[1:-1]`` is safe because
    # _is_table_row guaranteed both ends are |.
    return [cell.strip() for cell in s[1:-1].split("|")]


def _format_table_as_pre(table_lines: list[str]) -> str:
    """Render a list of ``| ... |`` rows as a single ``<pre>`` block."""
    rows = [_split_table_row(line) for line in table_lines]
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    # Pad short rows so column-width calc is uniform.
    rows = [r + [""] * (cols - len(r)) for r in rows]
    widths = [max(len(r[c]) for r in rows) for c in range(cols)]
    # HTML-escape cell content inside the <pre> block: <pre> is a Telegram
    # HTML tag, so its body still needs &/</> escaping to stay valid.
    rendered_rows: list[str] = []
    for r in rows:
        padded = "  ".join(html.escape(r[c], quote=False).ljust(widths[c]) for c in range(cols))
        rendered_rows.append(padded.rstrip())
    body = "\n".join(rendered_rows)
    return f"<pre>{body}</pre>"


def format_for_telegram(text: str) -> list[str]:
    """HTML-escape ``text`` and split it into Telegram-sized chunks.

    Markdown tables (``| a | b |`` rows separated by a ``|---|---|`` line)
    are pre-rendered into aligned monospace ``<pre>`` blocks so they look
    correct in Telegram (which has no HTML table tag). Non-table text is
    HTML-escaped normally.

    Escapes only ``&`` / ``<`` / ``>`` (``quote=False``) because the bot's
    fixed reply mode is ``ParseMode.HTML`` and we don't need to escape
    quotes for plain text bodies.

    When the escaped text fits in :data:`TELEGRAM_MAX_BODY_CHARS`, returns
    a single un-prefixed chunk. Otherwise:

    1. Splits on ``\\n\\n`` paragraph boundaries; greedy-packs paragraphs
       into chunks of at most ``TELEGRAM_MAX_BODY_CHARS - 8`` characters
       (reserving worst-case ``[NN/NN] `` prefix width).
    2. If any single paragraph is itself larger than the per-chunk budget,
       hard-splits it on character boundaries.
    3. Prefixes each chunk ``[k/N] `` (1-indexed). The prefix width was
       already reserved, so every final chunk is <= 4096 chars.

    Args:
        text: Pre-escape assistant text (typically ``AgentTurn.text``).

    Returns:
        At least one chunk. An empty input becomes ``[""]`` -- callers
        decide whether to actually send an empty reply.
    """
    escaped = _escape_with_table_pre_blocks(text)

    if len(escaped) <= TELEGRAM_MAX_BODY_CHARS:
        return [escaped]

    # The per-chunk *body* budget reserves space for the worst-case prefix.
    body_budget = TELEGRAM_MAX_BODY_CHARS - _PREFIX_BUDGET

    paragraphs = escaped.split(_PARA_SEPARATOR)
    bodies: list[str] = []
    current = ""

    for para in paragraphs:
        if len(para) > body_budget:
            # Flush whatever we'd accumulated before the oversized para.
            if current:
                bodies.append(current)
                current = ""
            # Hard-split the oversized paragraph.
            for i in range(0, len(para), body_budget):
                bodies.append(para[i : i + body_budget])
            continue

        candidate = para if not current else current + _PARA_SEPARATOR + para
        if len(candidate) <= body_budget:
            current = candidate
        else:
            bodies.append(current)
            current = para

    if current:
        bodies.append(current)

    if not bodies:
        # Degenerate case (shouldn't happen given the >4096 guard above,
        # but defend against ``escaped`` being all-separators).
        bodies = [escaped[:body_budget]]

    total = len(bodies)
    chunks = [f"[{i}/{total}] {body}" for i, body in enumerate(bodies, start=1)]

    # Belt-and-braces: the prefix budget was the worst case, so this should
    # always hold. If it ever fails we want a loud crash, not a Telegram 400.
    for c in chunks:
        assert len(c) <= TELEGRAM_MAX_BODY_CHARS, (
            f"chunk exceeded budget: {len(c)} > {TELEGRAM_MAX_BODY_CHARS}"
        )

    return chunks
