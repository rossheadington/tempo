"""Test doubles and fixtures that mimic real Strava API payloads.

No network, no real credentials. :class:`FakeStravaClient` stands in for
``stravalib.Client`` with the exact method surface the connector uses:

* ``authorization_url`` / ``exchange_code_for_token`` / ``refresh_access_token``
  for the OAuth lifecycle, and
* ``protocol.get(url, **params)`` for verbatim data fetches.

It can be scripted to: rotate refresh tokens on refresh, raise
``RateLimitExceeded`` on a chosen call (to simulate a mid-backfill 429), and
serve paged activity lists + stream payloads shaped like the real API.
"""

from __future__ import annotations

from typing import Any

from stravalib.exc import RateLimitExceeded


# A realistic-but-fake activity summary (subset of Strava's SummaryActivity).
# Note the deliberate fake-Z on start_date_local (wall-clock local, not UTC).
def make_activity(activity_id: int, *, start_utc: str, start_local: str) -> dict[str, Any]:
    """Return a Strava-shaped activity summary dict."""
    return {
        "id": activity_id,
        "name": f"Run {activity_id}",
        "sport_type": "Run",
        "type": "Run",
        "distance": 10000.0 + activity_id,
        "moving_time": 3000,
        "elapsed_time": 3100,
        "total_elevation_gain": 120.0,
        "start_date": start_utc,  # true UTC instant, real Z
        "start_date_local": start_local,  # wall-clock local with a FAKE Z
        "timezone": "(GMT+00:00) Europe/London",
        "utc_offset": 0.0,
        "average_heartrate": 150.0,
        "max_heartrate": 175.0,
        "average_speed": 3.33,
        "average_watts": 240.0,
        "average_cadence": 86.0,
    }


def make_activity_tz(
    activity_id: int,
    *,
    start_utc: str,
    start_local: str,
    timezone: str,
    utc_offset: float,
) -> dict[str, Any]:
    """Like :func:`make_activity` but with explicit timezone / utc_offset.

    Used by Phase-3 date-bucketing edge cases (timezone travel, DST) where the
    local wall-clock date and the true-UTC date deliberately disagree.
    """
    activity = make_activity(activity_id, start_utc=start_utc, start_local=start_local)
    activity["timezone"] = timezone
    activity["utc_offset"] = utc_offset
    return activity


def make_run(
    activity_id: int,
    *,
    day: str,
    average_speed: float | None = 3.0,
    moving_time: int = 3600,
    average_heartrate: float | None = 150.0,
    sport_type: str = "Run",
) -> dict[str, Any]:
    """A Strava-shaped run on a given local ``day``, with tunable load inputs.

    Used by Phase-4 analysis tests to seed a date range of synthetic activities
    with known pace/HR so computed load can be asserted against hand calculations.
    The local day comes from ``start_date_local`` (wall-clock, fake ``Z``).
    """
    distance = (average_speed or 0.0) * moving_time
    return {
        "id": activity_id,
        "name": f"Run {activity_id}",
        "sport_type": sport_type,
        "type": sport_type,
        "distance": distance,
        "moving_time": moving_time,
        "elapsed_time": moving_time + 60,
        "total_elevation_gain": 50.0,
        "start_date": f"{day}T06:00:00Z",
        "start_date_local": f"{day}T07:00:00Z",
        "timezone": "(GMT+00:00) Europe/London",
        "utc_offset": 0.0,
        "average_heartrate": average_heartrate,
        "max_heartrate": 180.0,
        "average_speed": average_speed,
    }


def make_streams() -> dict[str, Any]:
    """Return a Strava-shaped key_by_type streams payload (all metric types)."""
    return {
        "time": {
            "data": [0, 1, 2, 3],
            "series_type": "distance",
            "original_size": 4,
            "resolution": "high",
        },
        "latlng": {
            "data": [[51.5, -0.1], [51.5, -0.1]],
            "series_type": "distance",
            "original_size": 4,
            "resolution": "high",
        },
        "distance": {
            "data": [0.0, 3.3, 6.6, 9.9],
            "series_type": "distance",
            "original_size": 4,
            "resolution": "high",
        },
        "altitude": {
            "data": [10.0, 11.0, 12.0, 11.5],
            "series_type": "distance",
            "original_size": 4,
            "resolution": "high",
        },
        "heartrate": {
            "data": [120, 140, 155, 160],
            "series_type": "distance",
            "original_size": 4,
            "resolution": "high",
        },
        "cadence": {
            "data": [80, 84, 86, 85],
            "series_type": "distance",
            "original_size": 4,
            "resolution": "high",
        },
        "watts": {
            "data": [200, 230, 250, 245],
            "series_type": "distance",
            "original_size": 4,
            "resolution": "high",
        },
        "velocity_smooth": {
            "data": [2.9, 3.1, 3.3, 3.2],
            "series_type": "distance",
            "original_size": 4,
            "resolution": "high",
        },
    }


class FakeStravaClient:
    """A scriptable stand-in for ``stravalib.Client``.

    Construct with the activity pages it should serve and (optionally) a
    refresh-token sequence to simulate rotation. ``fail_get_on_call`` raises a
    ``RateLimitExceeded`` on the Nth (1-based) ``protocol.get`` call to simulate
    a mid-run rate-limit interruption.
    """

    def __init__(
        self,
        *,
        pages: dict[int, list[dict[str, Any]]] | None = None,
        streams: dict[int, dict[str, Any]] | None = None,
        details: dict[int, dict[str, Any]] | None = None,
        refresh_sequence: list[dict[str, Any]] | None = None,
        exchange_result: dict[str, Any] | None = None,
        fail_get_on_call: int | None = None,
        rate_limit_from_page: int | None = None,
    ) -> None:
        self._pages = pages or {}
        self._streams = streams or {}
        self._details = details or {}
        self._refresh_sequence = list(refresh_sequence or [])
        self._exchange_result = exchange_result
        self._fail_get_on_call = fail_get_on_call
        # If set, EVERY activity-list GET for this page (and beyond) raises 429,
        # simulating a persistent rate-limit window that exhausts the retries.
        self._rate_limit_from_page = rate_limit_from_page

        # Mutable client state the connector sets after auth.
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.token_expires: int | None = None

        # Call counters for assertions.
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.refresh_calls = 0
        self.exchange_calls = 0

        self.protocol = _FakeProtocol(self)

    # ---- OAuth surface ----

    def authorization_url(
        self, client_id: int, redirect_uri: str, scope: Any = None, **_: Any
    ) -> str:
        scope_s = ",".join(scope) if isinstance(scope, list) else (scope or "")
        return (
            f"https://www.strava.com/oauth/authorize?client_id={client_id}"
            f"&redirect_uri={redirect_uri}&scope={scope_s}&response_type=code"
        )

    def exchange_code_for_token(
        self, client_id: int, client_secret: str, code: str, **_: Any
    ) -> dict[str, Any]:
        self.exchange_calls += 1
        if self._exchange_result is None:
            raise AssertionError("FakeStravaClient: no exchange_result scripted")
        return self._exchange_result

    def refresh_access_token(
        self, client_id: int, client_secret: str, refresh_token: str, **_: Any
    ) -> dict[str, Any]:
        self.refresh_calls += 1
        if not self._refresh_sequence:
            raise AssertionError("FakeStravaClient: no refresh_sequence scripted")
        return self._refresh_sequence.pop(0)

    # ---- Data surface (raw) ----

    def _do_get(self, url: str, **params: Any) -> Any:
        self.get_calls.append((url, params))
        if self._fail_get_on_call is not None and len(self.get_calls) == self._fail_get_on_call:
            raise RateLimitExceeded("simulated 429")

        if url == "/athlete/activities":
            page = int(params.get("page", 1))
            if self._rate_limit_from_page is not None and page >= self._rate_limit_from_page:
                raise RateLimitExceeded("simulated persistent 429")
            after = params.get("after")
            acts = self._pages.get(page, [])
            if after is not None:
                acts = [a for a in acts if a["start_date"] > _epoch_to_iso(after)]
            return acts
        if url.endswith("/streams"):
            aid = int(url.split("/")[2])
            return self._streams.get(aid, {})
        if url.startswith("/activities/"):
            aid = int(url.split("/")[2])
            return self._details.get(aid, {"id": aid})
        raise AssertionError(f"FakeStravaClient: unexpected GET {url}")


class _FakeProtocol:
    """The ``client.protocol`` shim exposing ``.get``."""

    def __init__(self, client: FakeStravaClient) -> None:
        self._client = client

    def get(self, url: str, **params: Any) -> Any:
        return self._client._do_get(url, **params)


def _epoch_to_iso(epoch: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(int(epoch), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
