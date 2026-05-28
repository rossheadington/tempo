# Preferences profile (`preferences.md`)

**Status:** Authoritative for Phase 17 (preferences extraction).

Tempo separates two kinds of owner-maintained data: **append-only logs**
(food.md, weight.md, races.md, heat.md, strength.md) and a single
**edit-in-place profile** -- `preferences.md`. The profile carries the
owner's physiology numbers (threshold pace, max HR, resting HR, optional
threshold HR), display-unit preferences (km vs miles, pace formatting), an
optional nutrition kcal target, and free-form prose sections the coach reads
when planning (weekly training shape, current focus, recurring niggles).

The owner hand-edits the file in their content dir (default
`<content_root>/preferences.md`, redirect with `TEMPO_CONTENT_DIR`); Tempo
reads it once per analysis run via `parse_preferences()` and threads the
typed sections into the existing analysis modules. The prose sections are
captured but uninterpreted: the coach reads them via
`Read training/preferences.md` reactively in conversation.

The committed `preferences.md.example` at the repo root is both
documentation of the format and a parser reference exercised by
`tests/test_preferences.py`. The numbers in the example are textbook
placeholders, NOT the owner's real values -- physiology numbers MUST live
only in the gitignored content dir.

---

## Why this file exists (Phase 17 context)

Before Phase 17 the four physiology knobs lived in `.env` as
`TEMPO_THRESHOLD_PACE_S_PER_KM`, `TEMPO_MAX_HR`, `TEMPO_RESTING_HR`,
`TEMPO_THRESHOLD_HR`, and the kcal target lived as `TEMPO_TARGET_KCAL`.
This made them awkward to edit (open `.env`, find the line, edit, restart
nothing because settings re-read on each call), and it forced the coach to
infer the owner's training shape from the data alone.

Phase 17 moves the five typed knobs out of `.env` into a single
human-readable markdown file, and adds two new affordances:

- **`Units` section** -- `distance: miles` / `pace: min_per_mile` switches
  the user-facing rendering layer to imperial units (storage stays SI
  everywhere; conversion only at the render boundary).
- **Free-form prose sections** -- `## Training week`, `## Goals / current
  focus`, anything else -- captured verbatim into the parsed context's
  `prose_sections` dict so the coach can read them as-is in a conversation
  without having to re-explain his weekly shape every time.

This is an **architectural refactor**, not a new user-facing feature. The
old `TEMPO_*` env vars are GONE from `Settings`; the example `.env`
documents the move with a brief comment.

---

## File format

A single Markdown file. The H1 (`# Preferences`) is purely cosmetic --
discarded by the parser. Sections are delimited by H2 (`## <Name>`) headers
and are **independently optional**: a missing section means the typed
fields default to `None` (or the canonical default unit). Free-form prose
between sections is tolerated and silently discarded.

Worked example:

```markdown
# Preferences

## Physiology
threshold_pace: 4:00/mi          # M:SS/mi, M:SS/km, NNN s/km, or bare NNN
max_hr: 188
resting_hr: 40
threshold_hr: 172                # optional; if absent load.py's ~0.92*max_hr kicks in

## Units
distance: miles                  # km | miles
pace: min_per_mile               # min_per_km | min_per_mile

## Nutrition
target_kcal: 2800                # optional; enables 7d delta in nutrition + recovery reports

## Training week
Monday: easy doubles, ~12 mi
Tuesday: threshold day
Wednesday: medium-long, 14-16 mi steady
Thursday: single easy
Friday: marathon-pace work OR easy (inverse of Sunday)
Saturday: easy
Sunday: long with marathon-pace work OR pure easy (inverse of Friday)

## Goals / current focus
- Serpent 100k 4 Jul (A-race)
- Berlin Marathon 27 Sep (A-race)
```

---

## Section grammar

### H2 headers

- `## <name>` delimits a section. The header text is **case-insensitive**
  and stripped: `## physiology`, `## Physiology`, `##   PHYSIOLOGY   ` all
  map to the same canonical section name.
- Typed sections are `Physiology`, `Units`, `Nutrition`. **Any other H2
  header opens a prose section**, keyed in `PreferencesContext.prose_sections`
  by `header.lower().strip()` (e.g. `"training week"`, `"goals / current
  focus"`).
- The H1 (`# Preferences`) and any preamble lines before the first H2 are
  discarded -- NOT captured as a prose section, NOT recorded as malformed.

### Typed-section lines

Inside a typed section, each non-blank, non-comment line is parsed as
`key: value`:

- **Keys** are lowercased + stripped. Inside `Physiology`: `threshold_pace`,
  `max_hr`, `resting_hr`, `threshold_hr`. Inside `Units`: `distance`,
  `pace`. Inside `Nutrition`: `target_kcal`.
- **Values** are stripped; an inline trailing ` # comment` is stripped from
  the value (the space-then-hash rule); a line that is just a `# comment`
  is silently ignored.
- **Unknown keys are silently ignored** (NOT recorded as malformed) --
  forward-compat by design. Add `vo2max: 62` today and the parser drops it
  cleanly; a future Phase can recognise the key without breaking older files.
- **Unparseable values** leave the field at its default and the 1-indexed
  line number lands in `PreferencesContext.malformed_lines`.

### Prose sections

Anything that isn't a typed section is captured verbatim:

```python
ctx.prose_sections["training week"]
# 'Monday: easy doubles, ~12 mi\nTuesday: threshold day\n...'
```

The body preserves every line of the section (including indentation and
blank lines) with trailing newlines stripped. The parser does **NOT** parse
prose sections -- there is no "Monday: " key extraction, no day-of-week
lookup table, nothing. They exist for the coach to read.

---

## Threshold-pace formats

`threshold_pace` accepts four shapes; the parser normalises every form to
an `int` seconds-per-km internally so downstream analysis code only ever
sees one type. The `mi` form converts via the NIST constant
`1 mile = 1.609344 km`.

| Input         | Parsed (s/km) | Notes                                       |
|---------------|---------------|---------------------------------------------|
| `4:00/mi`     | 149           | 240 s/mi ÷ 1.609344 ≈ 149.13 -> round 149   |
| `6:26/mi`     | 240           | 386 s/mi ÷ 1.609344 ≈ 239.85 -> round 240   |
| `4:00/km`     | 240           | M:SS/km direct                              |
| `240 s/km`    | 240           | bare seconds with unit                      |
| `240`         | 240           | bare integer assumed s/km                   |
| `4 min/mi`    | malformed     | missing seconds component (needs M:SS/unit) |
| `garbage`     | malformed     | caught, field stays `None`                  |

Malformed input leaves `Physiology.threshold_pace_s_per_km` at `None` and
the line number lands in `malformed_lines`. Valid sibling keys still apply.

The helper is `tempo.analysis.preferences._parse_threshold_pace`; it's
private but the conversion rules above are stable across Phase 17.

---

## Units enum

`distance` accepts the following aliases (case-insensitive), normalised to
the canonical `"km"` or `"miles"`:

- `km` / `kilometre` / `kilometres` / `kilometer` / `kilometers` -> `"km"`
- `mi` / `mile` / `miles` -> `"miles"`

`pace` accepts:

- `min_per_km` / `min/km` / `mpk` -> `"min_per_km"`
- `min_per_mile` / `min/mi` / `mpm` -> `"min_per_mile"`

When the `## Units` section is absent OR a key is missing, the canonical
default applies (`distance="km"`, `pace="min_per_km"`) -- matching the
pre-Phase-17 rendering behaviour exactly, so a user who never adds the
section sees no display change.

Unrecognised values (`distance: stones`, `pace: shrugs`) leave the field at
the canonical default and the line number lands in `malformed_lines`.

---

## Lenient-parsing contract

The parser is built to **never break** on a hand-edited file:

- **Missing file** -> `PreferencesContext(present=False, physiology=Physiology(),
  units=Units(), nutrition=Nutrition(), prose_sections={}, path=None,
  malformed_lines=())`. Every downstream analysis sees defaults and runs
  normally; the recovery report's nutrition goal line silently omits when
  `target_kcal is None`, etc.
- **Empty file** -> `present=True` with default-everything dataclasses.
- **Malformed value** in a typed section -> field stays at its default; the
  1-indexed line number lands in `PreferencesContext.malformed_lines`.
  Parsing continues.
- **Unknown key** in a typed section -> silently ignored (forward-compat).
- **Unrecognised section header** -> captured as a prose section keyed by
  lowercased header text.
- **Empty value after comment strip** (`max_hr:   # TBD`) -> recorded as
  malformed so the user notices on next file edit.
- **Lines before the first H2** -> discarded (H1 + preamble both fall here).
- **BOM, trailing whitespace, mixed line endings, CRLF** -> tolerated
  transparently (`utf-8-sig` read).
- **The parser NEVER raises.**

For privacy, the parser **never logs the parsed VALUES** on malformed-line
warnings. Only the line number is surfaced in `malformed_lines`. Personal
physiology numbers stay out of stderr.

---

## Latest-wins on duplicate keys (within one section)

If the same `key:` appears twice in a single typed section, the **LAST
occurrence wins** -- there is no "first non-empty" rule and no warning. To
correct an earlier line, you can either edit it in place (preferred for an
edit-in-place profile) or append a fresh `key: value` below the old one.
The implementation falls out of using a single `field_state` dict per
section that successive assignments overwrite.

If the same H2 section header appears twice (e.g. two `## Physiology`
blocks separated by prose), the typed-field state from BOTH blocks accumulates
into the same field-state dict (so later keys overwrite earlier per the
above), and a duplicate prose-section header silently overwrites the
earlier capture. In practice this never matters -- the file is short and
edit-in-place -- but the rule is "latest-wins everywhere".

---

## Agent-edit-in-place guidance

Unlike the append-only trackers (`food.md`, `weight.md`, `races.md`,
`heat.md`, `strength.md`) where the agent's job is to `cat >> file`, the
preferences file is meant to be **hand-edited**. The agent SHOULD NOT
write to this file in normal coaching flow -- if the owner says "update my
max HR to 190", the agent's job is to use the `Edit` tool against
`<content_root>/preferences.md`, replacing the existing `max_hr: NNN` line
in place. Don't append a second `max_hr:` line; while latest-wins resolves
the ambiguity at parse time, it leaves the file untidy for the next
hand-edit.

When the owner asks the coach to "read my profile" or "what's my current
focus", the coach reads the whole file (`Read training/preferences.md`)
rather than relying on `parse_preferences` -- the prose sections are the
point, and the parser deliberately doesn't surface them in a structured
way.

There is intentionally **no `tempo preferences add` / `edit` CLI** in
Phase 17. The file is short, hand-editable, and the bot's `Edit` tool is
enough.

---

## What moved from `.env` in Phase 17

| `.env` var (pre-Phase-17)         | New home (post-Phase-17)                       |
|-----------------------------------|------------------------------------------------|
| `TEMPO_THRESHOLD_PACE_S_PER_KM`   | `Physiology.threshold_pace_s_per_km` (via `## Physiology` `threshold_pace:`) |
| `TEMPO_MAX_HR`                    | `Physiology.max_hr`                            |
| `TEMPO_RESTING_HR`                | `Physiology.resting_hr`                        |
| `TEMPO_THRESHOLD_HR`              | `Physiology.threshold_hr`                      |
| `TEMPO_TARGET_KCAL`               | `Nutrition.target_kcal` (via `## Nutrition` `target_kcal:`) |

The example `.env` (`tempo/.env.example`) keeps a brief comment trail at
the load-and-analysis-settings section documenting the move and pointing
at `preferences.md.example`. There is intentionally NO backward-compat
fallback that reads the old env vars when the section is missing -- this
is a single-cut-over, solo-user project, and the comment trail in
`.env.example` is the migration path.

DB columns and analysis-layer math all stay SI (metres, m/s, s/km). The
`Units` preference flows ONLY through the rendering layer; nothing
upstream of `tempo.units.format_distance` / `format_pace` ever sees the
imperial choice.

---

## Storage location & privacy

- `preferences.md` lives in the **gitignored content dir** (default
  `<content_root>/preferences.md`, configurable via `TEMPO_CONTENT_DIR`).
  It MUST NOT be committed -- physiology numbers are personal.
- The committed `preferences.md.example` at the repo root uses placeholder
  textbook values (`threshold_pace: 4:00/km`, `max_hr: 190`, `resting_hr:
  50`, `target_kcal: 2200`). When copying the example to the live file,
  REPLACE these with your real numbers.
- The parser never logs values; only line numbers appear in
  `malformed_lines`. The coach reads the file via the `Read` tool when
  needed; the agent SDK's outbound text is the only path personal numbers
  can leave the laptop, and that's the same boundary as every other
  tracker file.

---

## What's NOT in this layer

Deferred (per Phase 17 CONTEXT):

- **Auto-loading `preferences.md` into the bot's session bootstrap** so
  the coach always has it in context without an explicit `Read`. Phase 18
  polish; the coach can `Read training/preferences.md` reactively in any
  planning conversation today.
- **`tempo preferences edit` / `tempo preferences show` CLI** -- the file
  is meant to be hand-edited; a structured pretty-printer is nice debug
  surface but out of scope for now.
- **Backward-compat fallback** that reads the old `TEMPO_*` env vars when
  the section is missing. Single cut-over; the old vars are gone from
  `Settings`.
- **Units beyond miles/km and min/km / min/mile.** No nautical miles, no
  metric/imperial weight mixing (the weight tracker handles kg/lb
  normalisation at parse time -- orthogonal to the display-units
  preference here).
- **Structured prose-section parsing** (weekly template as YAML / TOML
  inside the markdown). The coach reads, the parser ignores -- speculative
  to add structure until a concrete reader needs it.
- **A structured DB table** for preferences. The markdown is the source of
  truth; rederivable from the file alone.
