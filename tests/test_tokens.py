"""Tests for the atomic rotating-token store (STRV-02; PITFALLS 4)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from runos.connectors import tokens as tokens_mod
from runos.connectors.tokens import TokenSet, TokenStore


def _store(tmp_path: Path) -> TokenStore:
    return TokenStore(tmp_path / "tokens", "strava")


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ts = TokenSet(access_token="a1", refresh_token="r1", expires_at=1000)
    store.save(ts)
    assert store.exists()
    loaded = store.load()
    assert loaded == ts


def test_token_file_is_owner_only_0600(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(TokenSet("a", "r", 1))
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_save_overwrites_previous_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(TokenSet("a1", "r1", 1000))
    store.save(TokenSet("a2", "r2", 2000))  # rotation: new refresh token
    loaded = store.load()
    assert loaded.refresh_token == "r2"
    assert loaded.access_token == "a2"
    assert loaded.expires_at == 2000


def test_no_temp_files_left_behind(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(TokenSet("a", "r", 1))
    store.save(TokenSet("a", "r2", 2))
    leftover = [p.name for p in (tmp_path / "tokens").iterdir() if p.suffix == ".tmp"]
    assert leftover == []


def test_write_is_atomic_old_file_survives_crash(tmp_path: Path, monkeypatch) -> None:
    """If os.replace fails mid-write, the previous complete token file survives.

    Simulates a crash between writing the temp file and the rename: the old file
    must still be loadable (never truncated to empty), which is the whole point
    of the atomic-rename strategy.
    """
    store = _store(tmp_path)
    store.save(TokenSet("a1", "r1", 1000))  # establish a good prior file

    def boom(_src, _dst):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save(TokenSet("a2", "r2", 2000))

    # The previous file is intact and complete; the new write never landed.
    loaded = store.load()
    assert loaded.refresh_token == "r1"
    # And no temp turd was left behind.
    leftover = [p for p in (tmp_path / "tokens").iterdir() if p.suffix == ".tmp"]
    assert leftover == []


def test_save_calls_fsync_for_durability(tmp_path: Path, monkeypatch) -> None:
    """The save path must fsync the file before the rename (durability)."""
    fsynced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (fsynced.append(fd), real_fsync(fd))[1])
    store = _store(tmp_path)
    store.save(TokenSet("a", "r", 1))
    assert fsynced, "expected at least one os.fsync call during save"


def test_load_missing_raises_filenotfound(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load()


def test_load_corrupt_file_raises_valueerror(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError):
        store.load()


def test_load_partial_json_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    # Missing refresh_token -> must be rejected loudly, not silently degraded.
    store.path.write_text(json.dumps({"access_token": "a", "expires_at": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load()


def test_file_mode_constant_is_0600() -> None:
    assert tokens_mod._FILE_MODE == 0o600
