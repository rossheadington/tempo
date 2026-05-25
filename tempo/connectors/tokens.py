"""Atomic, owner-only persistence of rotating OAuth tokens.

Strava issues **rotating refresh tokens**: every successful refresh returns a
*new* refresh token and immediately invalidates the old one, and access tokens
expire ~6 hours after creation (see ``.planning/research/PITFALLS.md`` Pitfall
4). If the new refresh token is ever lost -- not persisted at all, or persisted
non-atomically and a crash truncates the file -- the stored token becomes dead
and the user is forced back through the interactive OAuth browser flow.

This module guarantees a token write is **all-or-nothing** using the classic
temp-write -> ``fsync`` -> ``os.replace`` (atomic rename) sequence, so a crash
or power loss mid-write can never leave a torn/empty token file: either the old
complete file survives, or the new complete file does. Files are written with
mode ``0600`` (owner read/write only) so other local users can't read tokens.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

# Token file permissions: owner read/write only.
_FILE_MODE = 0o600


@dataclass(frozen=True, slots=True)
class TokenSet:
    """An OAuth token triple as Tempo persists it.

    ``expires_at`` is a Unix epoch integer (seconds) -- the instant the access
    token stops working. The ``refresh_token`` rotates on every refresh and is
    the value that *must* survive a crash.
    """

    access_token: str
    refresh_token: str
    expires_at: int

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> TokenSet:
        """Build a :class:`TokenSet` from a parsed JSON dict.

        Raises :class:`ValueError` if any required field is missing so a
        corrupt/partial file is rejected loudly rather than producing a token
        set with empty strings that would fail authentication confusingly later.
        """
        try:
            return cls(
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                expires_at=int(data["expires_at"]),  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid token data: {data!r}") from exc

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable dict of this token set."""
        return asdict(self)


class TokenStore:
    """Reads and atomically writes a single source's token file.

    One instance per source (e.g. ``strava``). The token file lives under the
    configured tokens directory (``~/.tempo/tokens/`` by default), outside the
    repository tree so it can never be committed.
    """

    def __init__(self, tokens_dir: Path, source: str) -> None:
        self._tokens_dir = tokens_dir
        self._source = source

    @property
    def path(self) -> Path:
        """Path to this source's token file."""
        return self._tokens_dir / f"{self._source}_tokens.json"

    def exists(self) -> bool:
        """Return ``True`` if a token file is present for this source."""
        return self.path.exists()

    def load(self) -> TokenSet:
        """Load and validate the persisted token set.

        Raises :class:`FileNotFoundError` if no token file exists (the user has
        not completed the one-time OAuth handshake) and :class:`ValueError` if
        the file is present but corrupt.
        """
        if not self.path.exists():
            raise FileNotFoundError(
                f"no {self._source} tokens at {self.path}; run the one-time auth first"
            )
        raw = self.path.read_text(encoding="utf-8")
        return TokenSet.from_dict(json.loads(raw))

    def save(self, tokens: TokenSet) -> None:
        """Persist ``tokens`` atomically with mode 0600.

        The write is durable and all-or-nothing:

        1. write to a uniquely-named temp file in the *same* directory (so the
           final ``os.replace`` is a same-filesystem atomic rename),
        2. ``flush`` + ``os.fsync`` the temp file so its bytes hit disk,
        3. ``os.replace`` over the destination (atomic on POSIX),
        4. best-effort ``fsync`` the directory so the rename itself is durable.

        A crash at any point leaves either the previous complete file or the new
        complete file -- never a truncated one.
        """
        self._tokens_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(tokens.to_dict(), indent=2, sort_keys=True)

        # NamedTemporaryFile in the destination directory guarantees same-FS
        # rename. delete=False because we hand the path to os.replace ourselves.
        fd, tmp_name = tempfile.mkstemp(
            dir=self._tokens_dir, prefix=f".{self._source}_tokens.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            os.fchmod(fd, _FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            # Clean up the temp file on any failure so we never litter the dir.
            tmp_path.unlink(missing_ok=True)
            raise

        # Ensure the directory entry (the rename) is itself durable.
        self._fsync_dir()
        # Belt-and-braces: enforce perms on the final file too.
        os.chmod(self.path, _FILE_MODE)

    def _fsync_dir(self) -> None:
        """fsync the tokens directory so the rename survives a crash.

        Directory fsync is not supported on every platform; failure here is
        non-fatal (the file content is already durable).
        """
        try:
            dir_fd = os.open(self._tokens_dir, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)
