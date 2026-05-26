"""Test doubles and fixtures that mimic real garminconnect payloads.

No network, no real credentials. :class:`FakeGarminClient` stands in for
``garminconnect.Garmin`` with the exact method surface the connector uses:

* ``login(tokenstore)`` -- token-reuse vs credential login, MFA, and a 429.
* ``get_sleep_data`` / ``get_hrv_data`` / ``get_stats`` -- per-day wellness, shaped
  like the real API (sleep nested under ``dailySleepDTO``, HRV under
  ``hrvSummary``, stats as a flat user-summary dict).

It can be scripted to: simulate a 429 on login or on a chosen data call (to prove
fail-log-skip + isolation), record whether a *credential* login happened (to prove
the scheduled path reuses tokens and never logs in), and serve per-day payloads.

A ``FakeTooManyRequests`` exception named like the real
``GarminConnectTooManyRequestsError`` lets the connector's string/name-based 429
detection fire without importing the fragile library.
"""

from __future__ import annotations

from typing import Any


class FakeTooManyRequests(Exception):
    """Stand-in for garminconnect.GarminConnectTooManyRequestsError (a 429).

    The connector detects a 429 by exception class *name* (and a string fallback),
    so naming this class identically exercises the real no-retry path without the
    library.
    """


# Rename so the connector's ``type(exc).__name__ == "GarminConnectTooManyRequestsError"``
# check matches exactly.
FakeTooManyRequests.__name__ = "GarminConnectTooManyRequestsError"
FakeTooManyRequests.__qualname__ = "GarminConnectTooManyRequestsError"


class FakeAuthError(Exception):
    """Stand-in for an auth failure (expired/invalid session)."""


def make_sleep(cdate: str, *, score: int = 82) -> dict[str, Any]:
    """A garminconnect-shaped ``get_sleep_data`` payload for one calendar date."""
    return {
        "dailySleepDTO": {
            "calendarDate": cdate,
            "sleepTimeSeconds": 27000,  # 7.5 h
            "deepSleepSeconds": 5400,
            "lightSleepSeconds": 16200,
            "remSleepSeconds": 5400,
            "awakeSleepSeconds": 600,
            "sleepScores": {"overall": {"value": score, "qualifierKey": "GOOD"}},
        },
        "remSleepData": True,
    }


def make_hrv(
    cdate: str, *, last_night_avg: float = 65.0, status: str = "BALANCED"
) -> dict[str, Any]:
    """A garminconnect-shaped ``get_hrv_data`` payload for one calendar date."""
    return {
        "hrvSummary": {
            "calendarDate": cdate,
            "lastNightAvg": last_night_avg,
            "lastNight5MinHigh": last_night_avg + 20,
            "status": status,
            "baseline": {"balancedLow": 55, "balancedUpper": 78},
        },
        "hrvReadings": [],
    }


def make_stats(
    cdate: str,
    *,
    resting_hr: int = 48,
    steps: int = 9000,
    stress_avg: int = 30,
    bb_high: int = 90,
    bb_low: int = 20,
) -> dict[str, Any]:
    """A garminconnect-shaped ``get_stats`` (user-summary) payload for one date."""
    return {
        "calendarDate": cdate,
        "restingHeartRate": resting_hr,
        "totalSteps": steps,
        "averageStressLevel": stress_avg,
        "bodyBatteryHighestValue": bb_high,
        "bodyBatteryLowestValue": bb_low,
        "totalKilocalories": 2600,
    }


def make_day(
    cdate: str,
    *,
    resting_hr: int = 48,
    hrv: float = 65.0,
    sleep_score: int = 82,
    steps: int = 9000,
) -> dict[str, dict[str, Any]]:
    """Bundle one day's sleep/hrv/stats payloads keyed by endpoint."""
    return {
        "sleep": make_sleep(cdate, score=sleep_score),
        "hrv": make_hrv(cdate, last_night_avg=hrv),
        "stats": make_stats(cdate, resting_hr=resting_hr, steps=steps),
    }


class FakeGarminClient:
    """A scriptable stand-in for ``garminconnect.Garmin``.

    Construct with per-day payloads (``days[cdate][endpoint]``). Scripting knobs:

    * ``tokens_present``  -- if True, ``login(tokenstore)`` succeeds via token reuse
      WITHOUT counting a credential login; if False it raises ``FileNotFoundError``
      (no persisted tokens) unless ``credentialed`` is set.
    * ``credentialed``    -- marks this client as built from email/password (the
      interactive login path). A credential ``login`` counts as a real login.
    * ``raise_429_on_login`` / ``raise_auth_on_login`` -- login failure modes.
    * ``raise_429_on``    -- endpoint name to raise a 429 on (data-call 429).
    * ``raise_error_on``  -- endpoint name to raise a generic error on.
    """

    def __init__(
        self,
        *,
        days: dict[str, dict[str, Any]] | None = None,
        tokens_present: bool = True,
        credentialed: bool = False,
        raise_429_on_login: bool = False,
        raise_auth_on_login: bool = False,
        raise_429_on: str | None = None,
        raise_error_on: str | None = None,
    ) -> None:
        self._days = days or {}
        self._tokens_present = tokens_present
        self._credentialed = credentialed
        self._raise_429_on_login = raise_429_on_login
        self._raise_auth_on_login = raise_auth_on_login
        self._raise_429_on = raise_429_on
        self._raise_error_on = raise_error_on

        # Assertion counters.
        self.login_calls = 0
        self.credential_login_calls = 0  # real SSO logins (the dangerous ones)
        self.token_login_calls = 0
        self.data_calls: list[tuple[str, str]] = []
        self.dumped_to: str | None = None

    # ---- Auth surface ----

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        self.login_calls += 1
        if self._raise_429_on_login:
            raise FakeTooManyRequests("429 too many requests")
        if self._raise_auth_on_login:
            raise FakeAuthError("401 unauthorized")
        if self._credentialed:
            # The interactive credential path: a genuine SSO login, then dump.
            self.credential_login_calls += 1
            if tokenstore:
                self.dumped_to = str(tokenstore)
            return (None, None)
        # Token-reuse path (the scheduled run): only succeeds if tokens exist.
        if not self._tokens_present:
            raise FileNotFoundError(f"no tokens at {tokenstore}")
        self.token_login_calls += 1
        return (None, None)

    # ---- Data surface ----

    def _maybe_raise(self, endpoint: str) -> None:
        if self._raise_429_on == endpoint:
            raise FakeTooManyRequests(f"429 on {endpoint}")
        if self._raise_error_on == endpoint:
            raise RuntimeError(f"garmin library blew up on {endpoint}")

    def get_sleep_data(self, cdate: str) -> Any:
        self.data_calls.append(("sleep", cdate))
        self._maybe_raise("sleep")
        return self._days.get(cdate, {}).get("sleep")

    def get_hrv_data(self, cdate: str) -> Any:
        self.data_calls.append(("hrv", cdate))
        self._maybe_raise("hrv")
        return self._days.get(cdate, {}).get("hrv")

    def get_stats(self, cdate: str) -> Any:
        self.data_calls.append(("stats", cdate))
        self._maybe_raise("stats")
        return self._days.get(cdate, {}).get("stats")
