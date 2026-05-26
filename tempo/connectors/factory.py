"""Wire Tempo config into ready-to-use connectors.

Keeps the credential-loading and "is the user set up?" checks in one place so
the CLI stays thin and tests can build connectors with explicit pieces instead.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from tempo.config import Settings
from tempo.connectors.garmin import GarminConnector
from tempo.connectors.strava import StravaConnector
from tempo.connectors.tokens import TokenStore


def strava_token_store(settings: Settings) -> TokenStore:
    """Return the Strava :class:`TokenStore` for these settings."""
    return TokenStore(settings.tokens_dir, "strava")


def build_strava_connector(
    settings: Settings,
    *,
    backfill_page_budget: int | None = None,
) -> StravaConnector:
    """Construct a :class:`StravaConnector` from config.

    Raises :class:`ValueError` if the Strava client id/secret are not configured
    (the user must create a Strava API app and put the credentials in ``.env``).
    """
    if not settings.strava_client_id or not settings.strava_client_secret:
        raise ValueError(
            "Strava credentials missing. Create an API app at "
            "https://www.strava.com/settings/api and set TEMPO_STRAVA_CLIENT_ID "
            "and TEMPO_STRAVA_CLIENT_SECRET in your .env."
        )
    return StravaConnector(
        client_id=int(settings.strava_client_id),
        client_secret=settings.strava_client_secret,
        token_store=strava_token_store(settings),
        backfill_page_budget=backfill_page_budget,
    )


# ---------------------------------------------------------------------------
# Garmin
# ---------------------------------------------------------------------------


def garmin_token_dir(settings: Settings) -> Path:
    """Return the directory Garmin's DI tokens are persisted to.

    A dedicated subdirectory of the tokens dir, kept OUTSIDE the repo tree (under
    ``~/.tempo/tokens/garmin`` by default) so no Garmin session can be committed.
    The ``garminconnect`` library writes its token files here (mode 0600).
    """
    return settings.tokens_dir / "garmin"


def _real_garmin_login_client(token_dir: str) -> Any:
    """Build a *token-only* garminconnect client (no credentials).

    Used by the connector's no-fresh-login path: with no email/password the
    library can only load persisted tokens, never submit an SSO credential login
    (GRMN-02). Imported lazily so tests / non-Garmin commands don't require the
    fragile library to be importable.
    """
    from garminconnect import Garmin

    return Garmin()


def build_garmin_connector(
    settings: Settings,
    *,
    client_factory: Callable[..., Any] | None = None,
    backfill_days: int | None = None,
) -> GarminConnector:
    """Construct a token-reusing :class:`GarminConnector` from config (GRMN-01/02).

    This connector NEVER logs in with credentials -- the interactive
    :func:`garmin_login` is the only credential path. ``client_factory`` is
    injectable for tests; in production it defaults to a real token-only client.
    """
    token_dir = garmin_token_dir(settings)
    token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "client_factory": client_factory or _real_garmin_login_client,
    }
    if backfill_days is not None:
        kwargs["backfill_days"] = backfill_days
    return GarminConnector(str(token_dir), **kwargs)


def garmin_login(
    settings: Settings,
    *,
    prompt_mfa: Callable[[], str] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> Path:
    """Perform the ONE-TIME interactive Garmin login and persist tokens (GRMN-02).

    Submits the configured email/password (and an MFA code via ``prompt_mfa`` when
    Garmin asks for one), then lets the library dump DI tokens into the token dir
    so every later run reuses them and never logs in again. This is the *only*
    function that touches Garmin credentials. Returns the token directory.

    Raises :class:`ValueError` if credentials are not configured. The actual login
    network call is performed by the injected client; the ``client_factory``
    receives ``email``, ``password`` and ``prompt_mfa`` and must return a client
    exposing ``login(tokenstore)``.
    """
    if not settings.garmin_email or not settings.garmin_password:
        raise ValueError(
            "Garmin credentials missing. Set TEMPO_GARMIN_EMAIL and "
            "TEMPO_GARMIN_PASSWORD in your .env, then run `tempo garmin login`."
        )
    token_dir = garmin_token_dir(settings)
    token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    factory = client_factory or _real_garmin_credential_client
    client = factory(
        email=settings.garmin_email,
        password=settings.garmin_password,
        prompt_mfa=prompt_mfa,
    )
    # tokenstore=path makes the library load-or-create and dump tokens to disk.
    client.login(str(token_dir))
    return token_dir


def _real_garmin_credential_client(
    *, email: str, password: str, prompt_mfa: Callable[[], str] | None
) -> Any:
    """Build a credentialed garminconnect client for the interactive login only."""
    from garminconnect import Garmin

    return Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
