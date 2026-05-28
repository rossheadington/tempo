"""Tests for ``runos.setup.env_io``: atomic ``.env`` read/write (SETUP-03).

The atomic-write contract mirrors :meth:`runos.connectors.tokens.TokenStore.save`
— mkstemp in the destination directory → fchmod → fsync → ``os.replace`` →
dir-fsync → chmod 0600. A crash mid-write must NEVER leave a partial ``.env``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from runos.setup.env_io import _quote_value, atomic_write_env, read_env

# ---- read_env ----


def test_read_env_missing_returns_empty(tmp_path: Path) -> None:
    assert read_env(tmp_path / "missing.env") == {}


def test_read_env_basic_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEY=value\nFOO=bar\n", encoding="utf-8")
    assert read_env(env) == {"KEY": "value", "FOO": "bar"}


def test_read_env_strips_surrounding_double_quotes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('KEY="value with spaces"\nFOO="x"\n', encoding="utf-8")
    assert read_env(env) == {"KEY": "value with spaces", "FOO": "x"}


def test_read_env_does_not_strip_single_quotes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEY='value'\n", encoding="utf-8")
    # Single quotes are preserved verbatim (we only strip double quotes).
    assert read_env(env) == {"KEY": "'value'"}


def test_read_env_skips_comments_and_blanks(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# header\n\nKEY=value\n  # indented comment\n", encoding="utf-8")
    assert read_env(env) == {"KEY": "value"}


def test_read_env_last_value_wins_on_duplicate_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEY=first\nKEY=second\n", encoding="utf-8")
    assert read_env(env) == {"KEY": "second"}


def test_read_env_skips_lines_without_equals(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEY=value\njust a line\nFOO=bar\n", encoding="utf-8")
    assert read_env(env) == {"KEY": "value", "FOO": "bar"}


def test_read_env_skips_empty_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("=novalue\nKEY=value\n", encoding="utf-8")
    assert read_env(env) == {"KEY": "value"}


# ---- _quote_value ----


def test_quote_value_simple_unquoted() -> None:
    assert _quote_value("simple_value-123") == "simple_value-123"


def test_quote_value_empty_stays_empty() -> None:
    assert _quote_value("") == ""


def test_quote_value_with_space_is_quoted() -> None:
    assert _quote_value("a b") == '"a b"'


def test_quote_value_with_dollar_is_quoted() -> None:
    assert _quote_value("abc$def") == '"abc$def"'


# ---- atomic_write_env: round-trip + preservation ----


def test_atomic_write_env_round_trip_fresh_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    atomic_write_env(env, {"KEY": "value"})
    assert read_env(env) == {"KEY": "value"}


def test_atomic_write_env_preserves_comments_and_unrelated_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# Header comment\nEXISTING1=keep\n\n# Section comment\nEXISTING2=also_keep\n",
        encoding="utf-8",
    )
    atomic_write_env(env, {"EXISTING1": "changed"})
    text = env.read_text(encoding="utf-8")
    # Header + section comments preserved.
    assert "# Header comment" in text
    assert "# Section comment" in text
    # Blank line between EXISTING1 and the section comment preserved.
    assert "\n\n# Section comment" in text
    # EXISTING2 untouched in its original position.
    assert "EXISTING2=also_keep" in text
    # EXISTING1 rewritten in place (still BEFORE the section comment).
    assert text.index("EXISTING1=changed") < text.index("# Section comment")


def test_atomic_write_env_preserves_line_ordering_of_untouched_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\nB=2\nC=3\nD=4\nE=5\n", encoding="utf-8")
    atomic_write_env(env, {"B": "two"}, delete_keys={"D"})
    text = env.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines == ["A=1", "B=two", "C=3", "E=5"]


def test_atomic_write_env_appends_new_keys_with_blank_separator(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("EXISTING=keep\n", encoding="utf-8")
    atomic_write_env(env, {"NEW_KEY": "v"})
    text = env.read_text(encoding="utf-8")
    assert text == "EXISTING=keep\n\nNEW_KEY=v\n"


def test_atomic_write_env_appends_multiple_new_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("EXISTING=keep\n", encoding="utf-8")
    atomic_write_env(env, {"NEW1": "a", "NEW2": "b"})
    text = env.read_text(encoding="utf-8")
    assert text == "EXISTING=keep\n\nNEW1=a\nNEW2=b\n"


def test_atomic_write_env_fresh_file_no_leading_blank_line(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    atomic_write_env(env, {"KEY": "value"})
    text = env.read_text(encoding="utf-8")
    assert text == "KEY=value\n"


def test_atomic_write_env_deletes_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEY1=a\nKEY2=b\nKEY3=c\n", encoding="utf-8")
    atomic_write_env(env, {}, delete_keys={"KEY2"})
    assert read_env(env) == {"KEY1": "a", "KEY3": "c"}


def test_atomic_write_env_quotes_values_with_spaces(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    atomic_write_env(env, {"KEY": "value with space"})
    text = env.read_text(encoding="utf-8")
    assert 'KEY="value with space"' in text
    # Round-trip strips the quotes back off.
    assert read_env(env) == {"KEY": "value with space"}


def test_atomic_write_env_quotes_values_with_dollar(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    atomic_write_env(env, {"KEY": "abc$def"})
    assert 'KEY="abc$def"' in env.read_text(encoding="utf-8")


def test_atomic_write_env_does_not_quote_simple_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    atomic_write_env(env, {"KEY": "simple_value-123"})
    text = env.read_text(encoding="utf-8")
    assert "KEY=simple_value-123\n" in text
    assert 'KEY="simple_value-123"' not in text


def test_atomic_write_env_byte_identical_for_unchanged_keys(tmp_path: Path) -> None:
    """If no updates target an existing key, that key's line bytes survive verbatim."""
    env = tmp_path / ".env"
    original = "# leading comment\nKEEP=  with leading spaces in value\nOTHER=plain\n"
    env.write_text(original, encoding="utf-8")
    atomic_write_env(env, {"NEW": "v"})
    text = env.read_text(encoding="utf-8")
    assert "# leading comment\nKEEP=  with leading spaces in value\nOTHER=plain\n" in text


# ---- atomic_write_env: permissions + atomicity ----


def test_atomic_write_env_sets_0600_perms(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    atomic_write_env(env, {"KEY": "v"})
    assert env.stat().st_mode & 0o777 == 0o600


def test_atomic_write_env_creates_parent_dir(tmp_path: Path) -> None:
    env = tmp_path / "deep" / "nested" / ".env"
    atomic_write_env(env, {"KEY": "v"})
    assert env.exists()
    assert read_env(env) == {"KEY": "v"}


def test_atomic_write_env_does_not_leave_partial_on_replace_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If os.replace fails mid-write, the original file survives and no .tmp lingers."""
    env = tmp_path / ".env"
    env.write_text("ORIGINAL=keep\n", encoding="utf-8")

    def boom(_src: str, _dst: str) -> None:
        raise OSError("simulated disk full")

    # Patch the bound reference inside env_io so internal os.replace is the one we hit.
    monkeypatch.setattr("runos.setup.env_io.os.replace", boom)
    with pytest.raises(OSError, match="simulated disk full"):
        atomic_write_env(env, {"NEW": "v"})

    # Original file unchanged.
    assert env.read_text(encoding="utf-8") == "ORIGINAL=keep\n"
    # No leftover temp file in destination directory.
    leftover = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftover == []


def test_atomic_write_env_does_not_leave_partial_on_fresh_file_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh write: crash leaves no destination AND no .tmp file behind."""
    env = tmp_path / ".env"

    def boom(_src: str, _dst: str) -> None:
        raise OSError("simulated crash")

    monkeypatch.setattr("runos.setup.env_io.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_env(env, {"KEY": "v"})

    assert not env.exists()
    leftover = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftover == []


def test_atomic_write_env_calls_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The save path must fsync at least once for durability."""
    fsynced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(
        "runos.setup.env_io.os.fsync",
        lambda fd: (fsynced.append(fd), real_fsync(fd))[1],
    )
    atomic_write_env(tmp_path / ".env", {"KEY": "v"})
    assert fsynced, "expected at least one os.fsync call during atomic_write_env"


def test_atomic_write_env_overwrite_round_trip(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    atomic_write_env(env, {"KEY": "first"})
    atomic_write_env(env, {"KEY": "second"})
    assert read_env(env) == {"KEY": "second"}
