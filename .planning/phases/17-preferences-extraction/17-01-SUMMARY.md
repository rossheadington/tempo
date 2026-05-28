# Plan 17-01 Summary -- Preferences analysis module + parser + example + docs

**Status:** Complete. Verification commands green.

## Files created

- `runos/analysis/preferences.py` -- new module. Exposes:
  - `Physiology`, `Units`, `Nutrition`, `PreferencesContext`
    `@dataclass(frozen=True, slots=True)` dataclasses (shapes per CONTEXT.md
    § Dataclasses).
  - `parse_preferences(path: Path) -> PreferencesContext` -- lenient,
    never-raises parser. Missing file → `present=False` with default
    instances of all typed dataclasses.
  - `_parse_threshold_pace(value: str) -> int | None` -- private helper
    handling the four accepted forms (`M:SS/mi`, `M:SS/km`, `NNN s/km`,
    bare `NNN`). Conversion constant `_KM_PER_MILE = 1.609344` (NIST).
- `tests/test_preferences.py` -- 17 tests covering the exact CONTEXT.md
  § Test scope list. Stdlib + `tmp_path` only.
- `preferences.md.example` -- repo-root committed template with all four
  section types (`## Physiology`, `## Units`, `## Nutrition`,
  `## Training week`, `## Goals / current focus`). Uses placeholder values
  per CONTEXT.md § Out-of-band safety (`threshold_pace: 4:00/km`, `max_hr:
  190`, `resting_hr: 50`, `target_kcal: 2200`).
- `docs/PREFERENCES.md` -- end-to-end documentation following
  `docs/NUTRITION.md` structure: phase boundary, env→preferences migration
  table, file grammar, threshold-pace formats, units enum + aliases,
  lenient-parsing contract, dataclass shapes, agent-edit-in-place guidance
  (distinct from append-only convention), `.env` relationship, deferred
  items.

## Dataclass shapes shipped

```python
@dataclass(frozen=True, slots=True)
class Physiology:
    threshold_pace_s_per_km: float | None = None
    max_hr: int | None = None
    resting_hr: int | None = None
    threshold_hr: int | None = None

@dataclass(frozen=True, slots=True)
class Units:
    distance: Literal["km", "miles"] = "km"
    pace: Literal["min_per_km", "min_per_mile"] = "min_per_km"

@dataclass(frozen=True, slots=True)
class Nutrition:
    target_kcal: int | None = None

@dataclass(frozen=True, slots=True)
class PreferencesContext:
    present: bool
    physiology: Physiology
    units: Units
    nutrition: Nutrition
    prose_sections: dict[str, str] = field(default_factory=dict)
    path: Path | None = None
    malformed_lines: tuple[int, ...] = ()
```

`prose_sections` is keyed by `header.lower().strip()` with the raw body
text preserved verbatim (trailing blank lines trimmed to keep
section-boundary slop out of the captured body).

## Test count

**17 tests in `tests/test_preferences.py`**, all green:

- Missing file / empty file → defaults (2)
- Physiology happy path + 4 threshold-pace format variants (mi, km, bare
  seconds, malformed) (5)
- Units happy path + alias normalisation + default when absent (3)
- Nutrition `target_kcal` (1)
- Lenient behaviours: unknown keys ignored, unknown sections captured as
  prose, inline comments stripped, case-insensitive headers, lines before
  first H2 discarded, prose section preserved verbatim (6)

## Verification (green)

```
$ uv run pytest tests/test_preferences.py -x
17 passed in 0.72s

$ uv run ruff check runos/analysis/preferences.py tests/test_preferences.py
All checks passed!
```

## Decisions made beyond CONTEXT/PLAN

A handful of implementation calls inside the scope locked by CONTEXT.md;
all consistent with the lenient-parsing contract:

1. **Inline-comment stripping rule**: I treat the first ` #` or `\t#`
   (whitespace-then-hash) as the start of an inline comment, not any bare
   `#`. This keeps the H2 header check (`stripped.startswith("## ")`) safe
   on the header line itself (we strip *after* the H2-detect branch
   anyway), and avoids eating `#` chars that might legitimately appear in
   a value (defensive -- the typed fields shouldn't contain `#` anyway).
2. **Empty key OR empty value → malformed**. A line like `: 190` or
   `max_hr:` (no value) lands in `malformed_lines`; the lenient contract
   only ignores *unknown* keys, not malformed `key: value` shapes.
3. **Line without colon inside typed section → silently ignored**, NOT
   recorded as malformed. This lets the user scatter a stray prose line
   inside a typed section (e.g. a section divider note) without polluting
   `malformed_lines`. Documented in `docs/PREFERENCES.md`.
4. **Repeated section headers**: latest-wins. The implementation flushes
   the current section state on every new H2, so a second `## Physiology`
   overrides the first. CONTEXT didn't specify but it falls out of the
   simple state-machine implementation; documented in
   `docs/PREFERENCES.md` § Section-header rules.
5. **Prose-section trailing blank lines** trimmed when captured (leading
   blanks preserved). This keeps section-boundary newlines out of the
   captured prose; matches the intent that `prose_sections["training
   week"]` returns just the meaningful body, not boundary slop.
6. **`_normalise_pace` accepts `"min per km"` / `"min per mile"`** (spaces
   collapsed to `_`) in addition to the canonical underscore form. Felt
   like a free win given the regex was already collapsing whitespace
   around `/`. CONTEXT listed `min_per_km` / `min/km` / `mpk` but not the
   spaced form; adding it costs nothing and matches how a human would
   write it.

## Anything weird

Nothing weird. The race-prose-section concept noted in the prompt
(`runos/analysis/races.py` as a tertiary mirror) didn't end up shaping
much -- the closer model was a state machine over H2 headers, with typed
sections going through a small key-value dispatch and everything else
buffered into `prose_sections`. The implementation is straightforward and
all 17 tests passed on the first run; no surprises.

The `Literal[...]` defaults on `Units` required the small `_normalise_*`
helpers to return `Literal[...] | None` so mypy / pyright would let me
assign back into the `Units(distance=..., pace=...)` constructor without
losing the literal type info -- standard Python pattern, just worth
noting if a future change needs the same shape.

## Out of scope (correctly not touched)

- `runos/config.py` -- Settings changes deferred to Plan 17-03.
- `runos/analysis/runner.py`, `cli.py`, `sync/daily.py`, `report.py`,
  `recovery.py`, `load.py` -- wire-up deferred to Plan 17-03.
- `.env.example` -- migration comment deferred to Plan 17-03.
- `tempo/units.py` and `tests/test_units.py` -- Plan 17-02 (runs in
  parallel).
- All existing tests other than the new `tests/test_preferences.py` --
  Plan 17-03 updates the ones that currently `monkeypatch.setenv` the
  migrated knobs.
