"""Personal rolling baselines for wellness metrics (HRV / resting HR / sleep).

A raw HRV of 62 ms or a resting HR of 50 bpm means nothing on its own -- only
*relative to the individual's own normal range* (GRMN-05). This module computes
**personal rolling baselines** so a day's reading can be read against the user's
own recent norm: is today's HRV suppressed vs the last 60 days? Is resting HR
elevated vs baseline?

Everything here is **pure**: functions over an ordered series of ``(day, value)``
points (read from ``wellness_day`` / ``daily_summary`` by :mod:`tempo.analysis.data`
helpers), with no DB or network. That keeps the math unit-testable against
hand-calculated numbers and lets Phase 7's recovery analysis consume these
baselines directly.

Two complementary baselines per metric:

* **Trailing window** -- mean + sample standard deviation over the prior ``window``
  days (default 60 for a stable personal norm, with shorter 7/30 also useful).
  A *z-score* (``(value - mean) / sd``) expresses how unusual today is in the
  user's own units. The window is **trailing and exclusive of today** so a reading
  is compared to its history, not to itself.
* **EWMA** -- an exponentially-weighted moving average that reacts faster to a
  genuine shift (e.g. a training-camp drop in HRV) while still smoothing noise.

Gaps are handled honestly: missing days are simply absent from the series (Garmin
didn't sync, or the watch wasn't worn), and a baseline with too few prior points
returns ``None`` rather than a falsely-confident number ("insufficient data" is a
first-class outcome, matching the project's honesty principle).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

# Metrics this module baselines, mapped to their ``wellness_day`` columns. Sleep
# uses total measured duration (seconds); HRV uses the overnight average; resting
# HR is the daily value. (Sleep *score* could be baselined too, but duration is
# the more comparable cross-person-agnostic signal for a personal norm.)
METRIC_COLUMNS: dict[str, str] = {
    "hrv": "hrv_last_night",
    "resting_hr": "resting_hr",
    "sleep": "sleep_seconds",
}

# Default trailing window (days) for a stable personal norm. A reading is compared
# to up to this many *prior* days that actually have data.
DEFAULT_WINDOW = 60

# Minimum prior data points required before a baseline is trustworthy. Below this
# we return None ("insufficient data") rather than a noisy mean of one or two days.
MIN_POINTS = 7

# EWMA smoothing: span (in days) -> alpha. A 14-day span reacts within ~2 weeks.
DEFAULT_EWMA_SPAN = 14


@dataclass(frozen=True, slots=True)
class BaselinePoint:
    """A single day's value alongside its trailing baseline and deviation.

    ``mean`` / ``sd`` / ``ewma`` are computed from PRIOR days only (exclusive of
    this day), so ``z`` answers "how unusual is today vs my recent normal?". Any of
    ``mean``/``sd``/``z``/``ewma`` may be ``None`` when there is insufficient prior
    history (fewer than :data:`MIN_POINTS` points, or zero variance for ``z``).
    """

    day: str
    value: float
    mean: float | None
    sd: float | None
    z: float | None
    ewma: float | None
    n: int  # number of prior points the baseline was computed from


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _sample_sd(values: list[float], mean: float) -> float | None:
    """Sample standard deviation (n-1). ``None`` if fewer than 2 points."""
    if len(values) < 2:
        return None
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def rolling_baseline(
    series: list[tuple[str, float]],
    *,
    window: int = DEFAULT_WINDOW,
    min_points: int = MIN_POINTS,
    ewma_span: int = DEFAULT_EWMA_SPAN,
) -> list[BaselinePoint]:
    """Compute a trailing personal baseline for each point in an ordered series.

    ``series`` is ``[(day, value), ...]`` sorted ascending by day, already filtered
    to days that have a value (no ``None`` gaps). For each point, the baseline mean
    /sd are taken over up to ``window`` *prior* points; the z-score expresses the
    point in the user's own SD units. The EWMA is maintained across the whole
    series (also using prior points only for each row's reported value).

    Returns one :class:`BaselinePoint` per input point, in order. Points with fewer
    than ``min_points`` prior values get ``None`` baselines (insufficient data).
    """
    out: list[BaselinePoint] = []
    prior_values: list[float] = []
    ewma_prev: float | None = None
    alpha = 2.0 / (ewma_span + 1.0)

    for day, value in series:
        window_vals = prior_values[-window:]
        n = len(window_vals)
        mean: float | None = None
        sd: float | None = None
        z: float | None = None
        if n >= min_points:
            mean = _mean(window_vals)
            sd = _sample_sd(window_vals, mean)
            if sd is not None and sd > 0:
                z = (value - mean) / sd

        out.append(
            BaselinePoint(
                day=day,
                value=value,
                mean=mean,
                sd=sd,
                z=z,
                ewma=ewma_prev,  # EWMA of prior days (exclusive of today)
                n=n,
            )
        )

        # Advance the EWMA and prior history AFTER recording this row, so every
        # baseline value is strictly a function of earlier days.
        ewma_prev = value if ewma_prev is None else alpha * value + (1 - alpha) * ewma_prev
        prior_values.append(value)

    return out


def latest_baseline(
    series: list[tuple[str, float]],
    *,
    window: int = DEFAULT_WINDOW,
    min_points: int = MIN_POINTS,
    ewma_span: int = DEFAULT_EWMA_SPAN,
) -> BaselinePoint | None:
    """Return the baseline for the most recent point, or ``None`` if the series is empty."""
    points = rolling_baseline(series, window=window, min_points=min_points, ewma_span=ewma_span)
    return points[-1] if points else None


# ---------------------------------------------------------------------------
# DB readers (read-only; the only DB touch in this module)
# ---------------------------------------------------------------------------


def read_metric_series(conn: sqlite3.Connection, metric: str) -> list[tuple[str, float]]:
    """Read an ordered ``[(day, value)]`` series for ``metric`` from ``wellness_day``.

    Skips days where the metric is ``NULL`` (no reading), so the baseline math sees
    only real data points. ``metric`` must be one of :data:`METRIC_COLUMNS`.
    Returns an empty list if the ``wellness_day`` table does not yet exist.
    """
    if metric not in METRIC_COLUMNS:
        raise ValueError(f"unknown wellness metric {metric!r}; expected {list(METRIC_COLUMNS)}")
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='wellness_day'"
    ).fetchone()
    if has_table is None:
        return []
    column = METRIC_COLUMNS[metric]
    rows = conn.execute(
        f"SELECT day, {column} AS v FROM wellness_day WHERE {column} IS NOT NULL ORDER BY day"  # noqa: S608 - column from fixed allowlist
    ).fetchall()
    return [(str(r["day"]), float(r["v"])) for r in rows]


def compute_baselines(
    conn: sqlite3.Connection,
    *,
    window: int = DEFAULT_WINDOW,
    min_points: int = MIN_POINTS,
    ewma_span: int = DEFAULT_EWMA_SPAN,
) -> dict[str, list[BaselinePoint]]:
    """Compute trailing baselines for every wellness metric from ``wellness_day``.

    Returns ``{metric: [BaselinePoint, ...]}`` for HRV, resting HR, and sleep --
    the inputs Phase 7's recovery analysis reads against personal norms (GRMN-05).
    Read-only, no network.
    """
    return {
        metric: rolling_baseline(
            read_metric_series(conn, metric),
            window=window,
            min_points=min_points,
            ewma_span=ewma_span,
        )
        for metric in METRIC_COLUMNS
    }


def latest_baselines(
    conn: sqlite3.Connection,
    *,
    window: int = DEFAULT_WINDOW,
    min_points: int = MIN_POINTS,
    ewma_span: int = DEFAULT_EWMA_SPAN,
) -> dict[str, BaselinePoint | None]:
    """Return the most recent baseline point per metric (``None`` where no data)."""
    return {
        metric: latest_baseline(
            read_metric_series(conn, metric),
            window=window,
            min_points=min_points,
            ewma_span=ewma_span,
        )
        for metric in METRIC_COLUMNS
    }
