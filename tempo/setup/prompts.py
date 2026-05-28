"""Thin ``typer.prompt`` / ``typer.confirm`` wrappers for the setup wizard.

The wizard's step functions stay focused on dispatch logic by delegating every
user-facing prompt and visual indicator to this module. The wrappers exist for
three reasons:

1. **Consistent visual language** — every step uses the same coloured indicator
   ``[set]`` / ``[done]`` / ``[fresh]`` / ``[skip]`` style and the same banner
   shape, so a re-run is visually identical to a fresh run modulo the per-step
   state markers.
2. **Hidden-input safety** — :func:`prompt_secret` always passes
   ``hide_input=True`` and never echoes the value after entry. The wizard MUST
   route every credential prompt through this helper.
3. **No business logic** — pure delegation to ``typer`` + colour constants
   keeps this surface trivially mockable in tests (a single
   ``monkeypatch.setattr(typer, "prompt", ...)`` covers every call site).
"""

from __future__ import annotations

import typer

# Coloured per-state indicator. ``[set]`` = key already present in ``.env``;
# ``[done]`` = step fully complete (creds + downstream artifact); ``[fresh]`` =
# running the step from scratch this invocation; ``[skip]`` = step opted out of
# (--skip flag or user declined a Y/N).
_INDICATOR_COLOURS = {
    "set": typer.colors.YELLOW,
    "done": typer.colors.GREEN,
    "fresh": typer.colors.CYAN,
    "skip": typer.colors.WHITE,
}


def print_step_banner(step_id: str, title: str) -> None:
    """Print a single-line bold cyan banner introducing a wizard step."""
    typer.secho(f"\n== {step_id}: {title} ==", fg=typer.colors.CYAN, bold=True)


def print_block(title: str, body: str) -> None:
    """Print an indented instruction block (Strava / BotFather / userinfobot copy).

    ``title`` is rendered bold; ``body`` is indented 4 spaces per line so the
    block is visually distinct from the surrounding step output without
    requiring a TUI library.
    """
    typer.secho(f"\n  {title}", bold=True)
    for line in body.splitlines():
        typer.echo(f"    {line}")
    typer.echo("")


def print_indicator(label: str, state: str) -> None:
    """Print a coloured per-state indicator (``[set]`` / ``[done]`` / ...)."""
    colour = _INDICATOR_COLOURS.get(state, typer.colors.WHITE)
    typer.secho(f"  [{state}] {label}", fg=colour)


def confirm_yn(question: str, *, default: bool = True) -> bool:
    """Yes/No confirmation with a sensible default. Returns the user's bool."""
    return typer.confirm(question, default=default)


def prompt_visible(label: str, *, default: str | None = None) -> str:
    """Plain ``typer.prompt`` for non-secret inputs (email, ids, paths)."""
    if default is None:
        return typer.prompt(label)
    return typer.prompt(label, default=default)


def prompt_secret(label: str) -> str:
    """Hidden-input prompt for credentials. NEVER echoes the value back."""
    return typer.prompt(label, hide_input=True, confirmation_prompt=False)


def prompt_int(label: str, *, default: int) -> int:
    """Integer prompt; renders the default as a string so typer is happy."""
    return int(typer.prompt(label, default=str(default)))
