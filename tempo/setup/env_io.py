"""Atomic, owner-only ``.env`` read/write for the setup wizard.

The wizard collects credentials and writes them to a project-root ``.env`` file
that ``pydantic-settings`` reads on next process start. That write must be:

- **atomic** — a crash or SIGINT mid-write must never leave a truncated/empty
  ``.env`` that would make Tempo unbootable;
- **owner-only** — the file holds OAuth client secrets and bot tokens, so it
  must end up at mode ``0600`` regardless of the system ``umask``;
- **non-destructive of human edits** — comments, blank lines, and the ordering
  of existing keys are preserved verbatim across updates.

The atomic-write template mirrors :class:`tempo.connectors.tokens.TokenStore`
exactly: write to a uniquely-named temp file in the destination directory,
``fchmod`` to ``0o600``, ``flush`` + ``fsync`` the temp file, ``os.replace``
over the destination (atomic on POSIX), ``fsync`` the parent directory, then a
belt-and-braces ``chmod`` on the final file.

This module never logs, echoes, or prints any value passed through it: the
``.env`` content is, by construction, secret-bearing.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Final ``.env`` permissions: owner read/write only. Identical to the rotating
# token store so the security posture is uniform across the project.
_FILE_MODE = 0o600

# A value containing any of these characters must be wrapped in double quotes
# on write so the line round-trips cleanly through dotenv parsers (theirs and
# ours). ``$`` would otherwise be subject to variable interpolation in stricter
# parsers; we don't interpolate, but quoting keeps the file portable.
_QUOTE_REQUIRED_CHARS = (" ", "$", "\t", "#")


def _quote_value(value: str) -> str:
    """Return ``value`` wrapped in double quotes only when necessary.

    Empty values keep the dotenv convention ``KEY=`` (no quotes). Values with
    spaces, ``$``, tab, or ``#`` are wrapped in double quotes. All other values
    pass through unchanged so a human-edited ``.env`` stays human-readable.
    """
    if value == "":
        return ""
    if any(ch in value for ch in _QUOTE_REQUIRED_CHARS):
        return f'"{value}"'
    return value


def read_env(path: Path) -> dict[str, str]:
    """Parse a ``.env``-format file leniently into a ``{key: value}`` dict.

    Contract:

    - missing file → ``{}`` (never raises :class:`FileNotFoundError`);
    - blank lines and lines whose first non-whitespace character is ``#`` are
      skipped;
    - the first ``=`` on a line splits key from value; lines without ``=`` are
      silently skipped;
    - a value that both starts and ends with ``"`` (length ≥ 2) has those
      outer quotes stripped; single quotes are NOT stripped;
    - on duplicate keys the **last** value wins (matches dotenv semantics and
      gives the wizard intuitive overwrite behaviour);
    - never raises; corrupt-looking lines simply don't contribute to the dict.
    """
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        eq = raw_line.find("=")
        if eq < 0:
            continue
        key = raw_line[:eq].strip()
        if not key:
            continue
        value = raw_line[eq + 1 :]
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        result[key] = value
    return result


def atomic_write_env(
    path: Path,
    updates: dict[str, str],
    delete_keys: frozenset[str] | set[str] = frozenset(),
) -> None:
    """Atomically rewrite ``path`` with ``updates`` applied and ``delete_keys`` removed.

    Existing comments, blank lines, and untouched ``KEY=value`` lines are
    preserved **verbatim and in place**. Keys present in ``updates`` that
    already exist in the file are rewritten in their original position. Keys
    present in ``delete_keys`` are dropped entirely. Keys in ``updates`` that
    do not appear in the existing file are appended at the end with a single
    leading blank line separating them from the existing content.

    The write follows the same all-or-nothing template as
    :meth:`tempo.connectors.tokens.TokenStore.save`: mkstemp in the destination
    directory → ``fchmod 0o600`` → write + flush + ``fsync`` → ``os.replace``
    over the destination → best-effort ``fsync`` on the parent dir → final
    ``chmod 0o600``. A crash at any point leaves either the prior complete
    ``.env`` or the new complete ``.env``; never a torn file. The temp file is
    unlinked on any exception so we never litter the destination directory.

    No value is ever logged or echoed by this function.
    """
    # ---- 1. Read existing content into preserve-verbatim lines ----
    existing_lines: list[str] = []
    if path.exists():
        # ``splitlines(keepends=True)`` preserves the original ``\n`` so a
        # no-op rewrite is byte-identical (modulo a trailing-newline tweak
        # we apply below before appending new keys).
        existing_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    # ---- 2. Walk existing lines: rewrite, delete, or preserve ----
    rewritten: list[str] = []
    consumed: set[str] = set()
    for line in existing_lines:
        stripped = line.lstrip()
        if not stripped.strip() or stripped.startswith("#"):
            # Blank or comment-only: preserve verbatim.
            rewritten.append(line)
            continue
        eq = line.find("=")
        if eq < 0:
            # No ``=`` — not a key=value line. Preserve verbatim.
            rewritten.append(line)
            continue
        key = line[:eq].strip()
        if not key:
            rewritten.append(line)
            continue
        if key in delete_keys:
            # Drop the line entirely.
            continue
        if key in updates:
            rewritten.append(f"{key}={_quote_value(updates[key])}\n")
            consumed.add(key)
            continue
        # Preserve the line exactly as it was.
        rewritten.append(line)

    # ---- 3. Append any updates keys we didn't consume above ----
    new_keys = [k for k in updates if k not in consumed]
    if new_keys:
        # Ensure the prior content ends with a newline, then exactly one blank
        # line separator before the appended block. Empty prior content gets
        # no leading blank line (avoids a stray blank first line in fresh files).
        if rewritten:
            if not rewritten[-1].endswith("\n"):
                rewritten[-1] = rewritten[-1] + "\n"
            if rewritten[-1].strip() != "":
                # Last line has content → insert a blank-line separator.
                rewritten.append("\n")
        for key in new_keys:
            rewritten.append(f"{key}={_quote_value(updates[key])}\n")

    payload = "".join(rewritten)

    # ---- 4. Atomic write (mirrors TokenStore.save exactly) ----
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, _FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Never leave a temp turd in the destination directory on failure.
        tmp_path.unlink(missing_ok=True)
        raise

    # ---- 5. Durable rename + belt-and-braces final-perm enforcement ----
    _fsync_dir(path.parent)
    os.chmod(path, _FILE_MODE)


def _fsync_dir(directory: Path) -> None:
    """Best-effort ``fsync`` of ``directory`` so the rename is durable.

    Directory fsync isn't supported on every platform; swallow ``OSError`` —
    the file contents are already durable via the earlier ``fsync`` on the
    temp file's fd.
    """
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)
