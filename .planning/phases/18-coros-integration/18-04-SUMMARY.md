# Wave 18-04: Recovery report Coros (EvoLab) section — Summary

**Status:** Complete
**Files modified:** 3
**Tests added:** 8 (5 mandated + 3 bonus)
**ruff:** clean

## Delivered

| File | Lines | Role |
|------|-------|------|
| `runos/analysis/recovery.py` | +138 / -3 | `RecoveryAssessment` gains `evolab`/`evolab_present`/`evolab_stamina_7d_ago`; new `_render_evolab_section`; `render_recovery` takes `units` |
| `runos/analysis/runner.py` | +12 | `generate_recovery` reads `read_evolab(conn)`, threads `evolab_ctx` + `units` |
| `tests/test_recovery.py` | +184 | 8 new tests; `_ok_assessment` / `_render` helpers extended |
| `runos/cli.py` | +1 (post-wave) | `runos analyze recovery` standalone CLI passes `units=prefs.units` (orchestrator one-line fix the agent flagged) |

## Rendered block format (current state)

```
## Coros (EvoLab)

- VO2max: 56.4 ml/kg/min
- Stamina: 62 (7d Δ +3)
- Training load (today): 412
- Threshold HR (Coros): 172 bpm — _cross-check vs preferences.md threshold_hr_
- Threshold pace (Coros): 3:58/km — _cross-check vs preferences.md threshold_pace_
```

Each line omits if its field is None. If ALL fields are None, falls through to absent. Threshold pace uses `format_pace(latest.ltsp_s_per_km, units)` so user-preferred units honoured.

## 3-state degradation

| State | Trigger | Output |
|---|---|---|
| Absent | `evolab_ctx.present is False` OR `latest is None` OR all fields None | Section omitted |
| Stale | `latest.day < today - timedelta(days=3)` | `## Coros (EvoLab)\n\n_Last EvoLab reading N days ago — wear the watch to refresh the dashboard._` |
| Current | Otherwise | Block above |

## Tests added

- `test_recovery_omits_evolab_section_when_absent`
- `test_recovery_emits_stale_evolab_nudge`
- `test_recovery_renders_evolab_block_with_vo2max_stamina_load_lthr_ltsp`
- `test_recovery_evolab_renders_pace_in_user_units` (Units(distance="miles", pace="min_per_mile") → `M:SS /mi`)
- `test_recovery_evolab_omits_missing_lines`
- `test_recovery_evolab_omits_stamina_delta_when_no_7d_ago_value`
- `test_recovery_evolab_falls_through_to_absent_when_all_metrics_none`
- `test_recovery_evolab_section_follows_nutrition`

## Decisions worth knowing

1. **Stamina delta lookup keyed off `evolab.day`**, not "today". A stale-but-shown reading still gets a delta if a real 7d-prior row exists.
2. **Delta glyph uses ASCII `+`/`-`** (`(7d Δ +3)`) — matches CONTEXT.md's example.
3. **`units` kwarg defaults to `None`** for back-compat. The standalone `runos analyze recovery` CLI was wired up post-wave by the orchestrator (one-line fix) to pass `units=prefs.units`.

## Verification

```
uv run python -m pytest tests/test_recovery.py -x → 51 passed (was 43)
uv run python -m pytest tests/ -x --deselect …whisper… → 760 passed (was 752)
uv run ruff check runos/ tests/ → clean
```
