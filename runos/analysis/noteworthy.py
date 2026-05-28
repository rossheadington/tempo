"""Noteworthy-only surfacing for the scheduled daily run (SCHED-03).

A daily report every single day is noise; the scheduled job should only *surface*
(flag / highlight / notify) when something crosses a threshold. Reports are always
written to disk -- "noteworthy" controls what gets pushed to attention (printed to
the launchd log as a NOTEWORTHY block, and a marker file written next to the
reports so a later notifier or the user can see at a glance that today mattered).

The thresholds are **configurable and documented** here in one place:

* ``ACWR`` outside the safe range (danger / elevated / detraining).
* An aggressive CTL ramp rate.
* A recovery verdict of ``monitor`` or ``elevated`` (multi-signal, incl. the HRV
  either-direction concern).
* A strong baseline z-score on any single wellness marker.
* A target race within ``race_within_days``.
* Source staleness (a source not synced for ``stale_after_days``) -- a silent data
  gap is itself noteworthy (PITFALLS 7: surface staleness, never trust silently).

Pure: it takes already-computed findings and returns a verdict, so the threshold
logic is unit-testable in isolation from the orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from runos.analysis.data import SourceFreshness
from runos.analysis.fitness import Guardrail
from runos.analysis.recovery import RecoveryAssessment


@dataclass(frozen=True, slots=True)
class NoteworthyThresholds:
    """Tunable thresholds deciding when the daily run surfaces output (SCHED-03)."""

    # Recovery statuses that are worth surfacing (ok / insufficient are not).
    recovery_surface_statuses: tuple[str, ...] = ("monitor", "elevated")
    # |z| on a single wellness marker beyond this is independently noteworthy.
    baseline_z: float = 2.0
    # A race within this many days is noteworthy (taper / readiness window).
    race_within_days: int = 14
    # A source unsynced for more than this many days is a noteworthy data gap.
    stale_after_days: int = 2
    # ACWR flags that surface (the guardrail already classifies these).
    acwr_surface_flags: tuple[str, ...] = ("high", "danger", "low")
    # Ramp flags that surface.
    ramp_surface_flags: tuple[str, ...] = ("aggressive",)


@dataclass(frozen=True, slots=True)
class NoteworthyResult:
    """The verdict: is today noteworthy, and the specific reasons why."""

    noteworthy: bool
    reasons: list[str] = field(default_factory=list)

    def as_marker_text(self, generated_on: date) -> str:
        """A short marker-file / log body listing the reasons (SCHED-03 surfacing)."""
        head = f"NOTEWORTHY {generated_on.isoformat()}"
        if not self.noteworthy:
            return f"{head}: nothing noteworthy today."
        lines = [f"{head}:"]
        lines.extend(f"  - {r}" for r in self.reasons)
        return "\n".join(lines)


def evaluate_noteworthy(
    *,
    as_of: date,
    guardrail: Guardrail,
    recovery: RecoveryAssessment,
    freshness: list[SourceFreshness],
    next_race_days: int | None,
    thresholds: NoteworthyThresholds | None = None,
) -> NoteworthyResult:
    """Decide whether the daily run should surface output, and why (SCHED-03).

    Returns ``noteworthy=False`` with no reasons when everything is nominal -- the
    scheduled job then writes its reports quietly without flagging the user. Any one
    threshold crossing makes the day noteworthy and is recorded as a reason.
    """
    t = thresholds or NoteworthyThresholds()
    reasons: list[str] = []

    # --- Load guardrail (ACWR + ramp) ---
    if guardrail.acwr_flag in t.acwr_surface_flags and guardrail.acwr is not None:
        reasons.append(f"ACWR {guardrail.acwr:.2f} ({guardrail.acwr_flag}).")
    if guardrail.ramp_flag in t.ramp_surface_flags and guardrail.ramp_rate is not None:
        reasons.append(f"Aggressive CTL ramp +{guardrail.ramp_rate:.1f}/week.")

    # --- Recovery verdict (multi-signal) ---
    if recovery.status in t.recovery_surface_statuses:
        reasons.append(f"Recovery status: {recovery.status}.")

    # --- A single strong baseline deviation (incl. HRV either-direction) ---
    for s in recovery.signals:
        if s.z is not None and abs(s.z) >= t.baseline_z:
            reasons.append(f"{s.metric} {abs(s.z):.1f} SD {s.direction} vs personal baseline.")

    # --- Race proximity ---
    if next_race_days is not None and 0 <= next_race_days <= t.race_within_days:
        reasons.append(f"Target race in {next_race_days} day(s).")

    # --- Source staleness (a data gap is itself noteworthy) ---
    for f in freshness:
        if f.last_sync_at is None:
            continue  # never-synced sources aren't a new gap to surface daily
        if f.days_stale is not None and f.days_stale > t.stale_after_days:
            reasons.append(f"{f.source} data is stale ({f.days_stale} days old).")

    return NoteworthyResult(noteworthy=bool(reasons), reasons=reasons)


def next_race_within_days(races_ctx, as_of: date) -> int | None:  # type: ignore[no-untyped-def]
    """Days until the soonest upcoming dated race, or ``None`` if none / no races."""
    if not getattr(races_ctx, "present", False):
        return None
    upcoming = [r for r in races_ctx.upcoming(as_of) if r.race_date is not None]
    if not upcoming:
        return None
    soonest = min(r.race_date for r in upcoming)
    return (soonest - as_of).days
