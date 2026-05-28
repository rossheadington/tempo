# Date Bucketing: the local-date attribution rule

**Status:** Authoritative. This rule is fixed *before any connector runs* and
governs every join, the `date_spine`, and the `daily_summary` view.

**Why this document exists.** RunOS joins activities, wellness, and journal
entries on a shared **date spine**. If "which day does this record belong to" is
computed inconsistently across sources, every downstream analysis — training
load vs recovery, trends, correlations — is subtly and confidently wrong (see
`.planning/research/PITFALLS.md`, Pitfall 6). So there is exactly one rule, and
everything references it.

---

## The rule

> **The `date_spine` is keyed by the athlete's _local calendar date_
> (`YYYY-MM-DD`). Every record is attributed to the local date on which the
> athlete experienced it.**

- **Activities** (Strava, later others): the local date the activity *started*.
- **Overnight wellness** (Garmin sleep / HRV / overnight recovery): the
  **wake-up day** — i.e. the source's own "calendar date" for that night,
  **not** the date the sleep started.
- **Same-day wellness** (resting HR, steps, stress, body battery): the calendar
  date the source assigns.
- **Journal entries**: the local date of the activity they describe (resolved by
  date + sport), or the entry's own local date for rest-day notes.

The spine stores **local dates only**. UTC timestamps and offsets are preserved
verbatim in the raw layer so the bucket can be recomputed (`runos rederive`)
without re-fetching if the rule ever changes.

---

## Source-specific derivation

### Strava — beware the fake `Z`

Strava returns two timestamps per activity:

| Field | What it actually is |
|-------|---------------------|
| `start_date` | True UTC instant, with a real trailing `Z`. |
| `start_date_local` | **Wall-clock local time with a _fake_ trailing `Z`.** It is **not** UTC. |

**Derivation:** take the local date from the first 10 characters of
`start_date_local` (`start_date_local[:10]`). **Never** parse `start_date_local`
as UTC, and **never** bucket on `start_date` (true UTC) — either choice can shove
a 10pm run into the wrong calendar day.

Also persist `utc_offset` and `timezone` from the raw payload so the rule stays
re-derivable.

```python
# Correct: wall-clock local date, ignore the lying Z.
day = activity["start_date_local"][:10]   # 'YYYY-MM-DD'

# WRONG: treats the fake-Z as UTC, can shift the day.
# day = datetime.fromisoformat(activity["start_date_local"]).astimezone(UTC).date()
```

### Garmin — use the source's `calendarDate`

Garmin's overnight metrics (sleep, HRV) span two calendar days. Garmin added a
`calendarDate` field specifically to remove this ambiguity: it is the day the
night's data is attributed to in Garmin Connect's "My Day" (the wake-up day).

**Derivation:** key sleep/HRV and daily-summary wellness rows by Garmin's
`calendarDate`. Do **not** bucket by the sleep *start* timestamp — that would
attach last night's sleep to yesterday and misalign "recovery today vs load
today."

---

## Canonical day key format

- Format: ISO `YYYY-MM-DD` (zero-padded), e.g. `2026-05-26`.
- Type in SQLite: `TEXT` (lexical sort == chronological sort).
- This string is the primary key of `date_spine.day` and the foreign key every
  structured table joins through.

---

## Worked edge cases (these become tests in Phase 3)

| Scenario | Input | Bucketed local day | Why |
|----------|-------|--------------------|-----|
| Late-night run | Strava `start_date_local = "2026-05-26T23:10:00Z"` (fake Z) | `2026-05-26` | Wall-clock local; ignore the fake Z. |
| Early-morning run | `start_date_local = "2026-05-26T05:30:00Z"` | `2026-05-26` | Same — local wall clock. |
| Timezone travel | Run started 11pm in a UTC+9 zone; `start_date_local` reflects local wall clock | day of `start_date_local[:10]` | Attribute to where/when the athlete actually ran. |
| DST transition | Activity on a spring-forward / fall-back night | day of `start_date_local[:10]` | Local wall-clock date is unaffected by the clock change. |
| Overnight sleep | Garmin sleep 23:30 → 07:00, `calendarDate = "2026-05-26"` | `2026-05-26` | Wake-up day per Garmin's `calendarDate`, not the start date. |

---

## Invariants

1. **One rule, one place.** Every join, view, and transform attributes records by
   the local date defined here. No source uses a different convention.
2. **Raw is preserved.** UTC timestamps + offsets stay verbatim in `raw_response`
   so the bucket is always re-derivable; the rule can be fixed and re-applied via
   `runos rederive` with zero network calls.
3. **Bucketing happens in the transform layer**, never in the connector — keeping
   it pure and re-runnable.

---

*Defined in Phase 1 (Foundation) before any connector runs. Edge-case tests are a
success criterion of Phase 3 (Strava Transforms + Date Spine).*
