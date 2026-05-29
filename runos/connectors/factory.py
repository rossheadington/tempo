"""Wire RunOS config into ready-to-use connectors.

Keeps the credential-loading and "is the user set up?" checks in one place so
the CLI stays thin and tests can build connectors with explicit pieces instead.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from runos.config import Settings
from runos.connectors.coros import CorosConnector, CorosTokenStore
from runos.connectors.garmin import GarminConnector
from runos.connectors.strava import StravaConnector
from runos.connectors.tokens import TokenStore


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
            "https://www.strava.com/settings/api and set RUNOS_STRAVA_CLIENT_ID "
            "and RUNOS_STRAVA_CLIENT_SECRET in your .env."
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
    ``~/.runos/tokens/garmin`` by default) so no Garmin session can be committed.
    The ``garminconnect`` library writes its token files here (mode 0600).
    """
    return settings.tokens_dir / "garmin"


def _real_garmin_login_client() -> Any:
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
            "Garmin credentials missing. Set RUNOS_GARMIN_EMAIL and "
            "RUNOS_GARMIN_PASSWORD in your .env, then run `runos garmin login`."
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


# ---------------------------------------------------------------------------
# Coros
# ---------------------------------------------------------------------------


def coros_token_dir(settings: Settings) -> Path:
    """Return the directory the Coros bearer-token bundle is persisted in.

    A dedicated subdirectory of the tokens dir, kept OUTSIDE the repo tree
    (under ``~/.runos/tokens/coros`` by default) so no Coros session can be
    committed. The connector writes ``token`` (mode 0600) here.
    """
    return settings.tokens_dir / "coros"


def coros_token_store(settings: Settings) -> CorosTokenStore:
    """Return the :class:`CorosTokenStore` for these settings."""
    return CorosTokenStore(coros_token_dir(settings))


def _coros_credentials(settings: Settings) -> tuple[str | None, str | None]:
    """Pull the Coros email/password out of settings, tolerant of older configs.

    Uses ``getattr`` with ``None`` defaults so this factory keeps importing
    cleanly even before the Settings fields are wired up in wave 18-05. The
    password field is a ``SecretStr`` once added; we unwrap it via
    ``get_secret_value`` when present so the connector never sees a wrapped
    object.
    """
    email = getattr(settings, "coros_email", None)
    raw_password = getattr(settings, "coros_password", None)
    if raw_password is None:
        password = None
    else:
        # SecretStr exposes the raw via .get_secret_value(); plain strings stay
        # as-is for forward compatibility.
        getter = getattr(raw_password, "get_secret_value", None)
        password = getter() if callable(getter) else str(raw_password)
    return email, password


def build_coros_connector(
    settings: Settings,
    *,
    http_client_factory: Callable[[], Any] | None = None,
    backfill_days: int | None = None,
) -> CorosConnector:
    """Construct a :class:`CorosConnector` from config.

    Returns a connector wired with the persisted token directory and the
    configured (email, password) so a scheduled sync can perform the one-shot
    re-login on a 401 without prompting. Credentials are optional at
    construction time -- if they're absent the connector still works for any
    call covered by the persisted token, but a 401 will surface as
    :class:`runos.connectors.coros.CorosAuthError` immediately.
    """
    token_dir = coros_token_dir(settings)
    token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    email, password = _coros_credentials(settings)
    kwargs: dict[str, Any] = {
        "email": email,
        "password": password,
    }
    if http_client_factory is not None:
        kwargs["http_client_factory"] = http_client_factory
    if backfill_days is not None:
        kwargs["backfill_days"] = backfill_days
    return CorosConnector(token_dir, **kwargs)


def coros_login(
    settings: Settings,
    *,
    prompt_password: Callable[[], str] | None = None,
    http_client_factory: Callable[[], Any] | None = None,
) -> Path:
    """Perform the ONE-TIME interactive Coros login and persist the token bundle.

    Submits the configured email + MD5(password) (prompting via
    ``prompt_password`` if no password is configured), then lets the connector
    persist ``{access_token, user_id}`` atomically into the token directory.
    This is the *only* function the CLI uses that touches the raw password
    interactively. Returns the token directory path so the CLI can show the
    user where the token lives.

    Raises :class:`ValueError` if the email is missing entirely (the password
    can be supplied via the prompt).
    """
    email, password = _coros_credentials(settings)
    if not email:
        raise ValueError(
            "Coros email missing. Set RUNOS_COROS_EMAIL in your .env, then "
            "run `runos coros login` (the password can come from the prompt)."
        )
    if not password:
        if prompt_password is None:
            raise ValueError(
                "Coros password missing and no prompt provided. Set "
                "RUNOS_COROS_PASSWORD in your .env or pass a prompt_password."
            )
        password = prompt_password()

    token_dir = coros_token_dir(settings)
    token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    # Build a connector seeded with the live credentials and let its _login
    # path do the handshake + persistence. The connector's _login method
    # already handles every failure mode + the redacted logging, so the CLI
    # gets a single consistent error surface.
    kwargs: dict[str, Any] = {
        "email": email,
        "password": password,
    }
    if http_client_factory is not None:
        kwargs["http_client_factory"] = http_client_factory
    connector = CorosConnector(token_dir, **kwargs)
    connector._login()
    return token_dir
