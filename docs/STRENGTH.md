# Strength & conditioning log (`strength.md`)

**Status:** Authoritative for Phase 13 (SC-01 through SC-05).

RunOS has no strength-tracking UI by design. The owner maintains a hand-edited
`strength.md` markdown file in the content dir (default `<content_root>/strength.md`,
redirect with `RUNOS_CONTENT_DIR`), and RunOS reads it for S&C context in the
recovery report. The Strong app remains the source of truth during a session
(set logging, rest timer, PRs); the markdown is the **storage format** -- the
compact `WxR` syntax is friendlier for hand-editing than Strong's verbose
`Set 1: 40 kg × 8` exports, and it sits next to `races.md` / `heat.md` so all
three trackers share one shape.

The committed `strength.md.example` at the repo root is both the documentation
of the format and a parser fixture that gets smoke-tested in `tests/test_strength.py`.

---

## Format

One session per `## `-header block. The header is the only mandatory thing;
everything else is optional. The parser is lenient throughout: malformed lines
are skipped, never raise.

### Session header

```
## YYYY-MM-DD [HH:MM] [— Name]
```

- ISO date `YYYY-MM-DD` is **required**. Sessions whose header carries an
  unparseable date are dropped at parse time; parsing resumes at the next
  valid `## YYYY-MM-DD` header.
- `HH:MM` is optional (24-hour wall-clock start, stored verbatim on
  `StrengthSession.start_local` for downstream sorting only; not
  timezone-projected).
- ` — Name` is optional (free-form session name). Either an em-dash `—` or
  a hyphen ` - ` separator is accepted. Whitespace is trimmed.

### Metadata lines

Between the header and the first exercise bullet, any order:

| Key      | Meaning                                              | Stored as           |
|----------|------------------------------------------------------|---------------------|
| `rest`   | Rest between sets, `M:SS` (e.g. `1:30` → 90)         | `rest_s: int | None` |
| `notes`  | Free-form note for the session                       | `notes: str | None`  |

Anything else (`key: value` for some unrecognised key) is silently ignored.

### Exercise bullets

```
- <Exercise Name> [(Equipment)] [[GROUP]]: <set>, <set>, ...
```

- `<Exercise Name>` -- required, free-form text up to the first `(`, `[`,
  or `:` annotation.
- `(Equipment)` -- optional parenthesised annotation (`Barbell`, `Machine`,
  `Cable`, `Dumbbell`, `Leg Press`, ...). Stored verbatim. Bodyweight
  movements typically omit it.
- `[GROUP]` -- optional bracketed superset label, single letter or short
  token (`[A]`, `[B]`, `[C1]`). Stored verbatim on
  `StrengthExercise.superset_group`. See **Supersets** below.
- `: ` -- separator. Required to introduce sets.
- `<set>` -- one of three flavours (tried in this order):
  - **Weighted** `WxR` -- e.g. `55x8` → 55 kg × 8 reps. Lowercase `x`,
    uppercase `X`, and unicode `×` all accepted. Whitespace around the
    separator is fine (`55 x 8`).
  - **Timed hold** `M:SS` -- e.g. `1:00` → 60-second hold, `0:30` → 30 s.
    Single colon only.
  - **Bodyweight reps** bare integer -- e.g. `15` → 15 reps unloaded.
- Anything that doesn't match those three is silently skipped.

### The owner's Tuesday session as a worked example

```markdown
## 2026-05-26 18:19 — Lower body
rest: 1:30
notes: pogos + SLGB supersetted

- Romanian Deadlift (Barbell): 40x8, 50x8, 55x7, 55x8
- Hip Thrust (Barbell): 50x10, 55x10, 55x10, 55x10
- Seated Leg Curl (Machine): 25x12, 30x12, 30x12
- Calf Press (Leg Press): 80x16, 80x16, 80x16, 80x16
- Pogos [A]: 15, 15, 15
- Single Leg Glute Bridge [A]: 8, 8, 8
- Plank: 1:00, 1:00, 1:00, 0:30
```

Parses to one `StrengthSession`: `date=2026-05-26`, `start_local="18:19"`,
`name="Lower body"`, `rest_s=90`, `notes="pogos + SLGB supersetted"`, 7
exercises in source order, two of them (`Pogos` and `Single Leg Glute
Bridge`) sharing `superset_group="A"`, and `Plank`'s four sets parsing as
`duration_s=[60, 60, 60, 30]`. Total weighted-set tonnage: 9,835 kg.

---

## Supersets

Two exercises in the same session sharing the same group letter are
understood to be supersetted -- the user trains them back-to-back with one
rest period at the end of the pair, not after each individual exercise.

The parser stores the label verbatim on `StrengthExercise.superset_group`;
**it does not enforce that a label appears on ≥ 2 exercises** in the same
session. The renderer / downstream consumers are responsible for interpreting
pairings. Labels are free-form (single letters `[A]`, `[B]`, `[C]` are
conventional; `[C1]` and longer tokens are also accepted).

---

## Lenient parsing

The parser is built to **never break** on user-edited markdown. The
guarantees:

- **Missing file** → `StrengthContext(present=False)`. The recovery report
  omits the `## Strength & conditioning` section entirely; the rest of the
  recovery analysis runs unaffected.
- **Malformed set token inside a valid exercise bullet** → silently skipped.
  The exercise still parses with the sets that did match one of the three
  flavours.
- **Exercise bullet with zero parseable sets** → exercise is still kept
  (`sets=()`). The rollup contributes 0 to tonnage and 1 to count -- so the
  user sees the bullet they wrote, with no invented set data.
- **Session header with an unparseable date** → the entire session block is
  dropped (every line until the next valid `## YYYY-MM-DD` header). The
  parser enters skip-mode and the next valid header re-anchors it.
- **Unknown metadata keys** (`tempo: easy`, `mood: 7`, ...) → silently
  ignored.
- **Top-level `#` headings, blank lines, prose between bullets** → skipped.

**The parser NEVER raises** on malformed input. The only failure mode is
"this session/set/key didn't make it into the parsed result"; analyses
downstream see whatever did parse.

---

## Recovery report integration

Parsed sessions surface in the recovery report as a `## Strength &
conditioning` section, placed after the heat section so the two
load-context trackers cluster together. The renderer follows a **3-state
degradation rule**, mirroring how `heat.md` is rendered:

- **Absent** -- file missing, or `strength_present` is False, or no
  sessions have ever been logged → section omitted entirely.
- **Lapsed** -- sessions exist in history but none in the last 28 days →
  one-line nudge:
  `_S&C protocol lapsed — last session N days ago. (No sessions in the last 28 days.)_`
- **Active** -- at least one session in the last 28 days → full rollup
  line:
  `last 7 days: N sessions / X kg tonnage · last 14 days: N sessions / X kg tonnage · last 28 days: N sessions / X kg tonnage · last session: Name (M days ago)`

### Tonnage semantics

- **Tonnage** = sum of `weight_kg × reps` over every weighted set in the
  window. Bodyweight + timed-hold sets contribute **0** to tonnage but DO
  count toward `*_count` -- a Plank-and-conditioning day is still a logged
  session.
- Windows are inclusive closed intervals: 7-day = `[today - 6 .. today]`.
- Future-dated sessions (typo'd year) are defensively filtered out before
  rolling.
- Tonnage is formatted as `9,835 kg` under 10,000 kg, `12.4 t` at or above
  (one decimal). The renderer copy keeps it factual: tonnage is an
  imperfect proxy for S&C stress (a 4×8 squat at 100 kg has the same
  tonnage as a 4×8 RDL at 100 kg), useful as a "did I lift, and roughly
  how much" signal alongside count and last-session age.

---

## Relationship to Strong-app pastes

Strong is the live-session interface (set logging, rest timer, PRs); after
a session the user pastes the summary into `strength.md` in the compact
hand-editable format. The compact `WxR` syntax fits one bullet per
exercise, which is friendlier for the markdown layer than Strong's
verbose `Set 1: 40 kg × 8 reps` exports. There is no Strong-app importer
in this phase -- the markdown is hand-maintained. A future phase may add a
`runos strength import <csv>` connector if the manual mapping becomes
painful (see "What's not done in this layer" below).

---

## What's NOT done in this layer

Deferred to later phases (per Phase 13 CONTEXT):

- **Structured DB tables** (`strength_session` / `strength_exercise` /
  `strength_set`) -- the markdown layer must prove useful in real use
  first. If it does, Layer 2 adds tables plus a transform that rebuilds
  them from the parsed markdown (rederivable, same invariant as the rest
  of RunOS).
- **Strong-app CSV importer** (`runos strength import <csv>`) -- natural
  Layer 2 once the markdown is exercised in anger.
- **Validated `runos strength add` CLI** (mirror of `runos journal add`)
  -- lets the Telegram bot capture lifts via the agent loop.
- **Standalone S&C report** -- weekly tonnage trend, set-volume by
  movement pattern, RDL/squat/hinge stress balance. Only sensible after
  the structured tables land.
- **1RM estimation / PR tracking** -- Strong already does this; RunOS
  doesn't need to duplicate it.
- **Strava `WeightTraining` activity ↔ session auto-link** -- only useful
  if the owner starts logging lifts to Strava (currently they don't).
