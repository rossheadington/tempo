"""Wire Tempo config into a ready-to-use Strava connector.

Keeps the credential-loading and "is the user set up?" checks in one place so
the CLI stays thin and tests can build connectors with explicit pieces instead.
"""

from __future__ import annotations

from tempo.config import Settings
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
