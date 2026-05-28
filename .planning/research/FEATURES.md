# Feature Research

**Domain:** Personal (single-user, local-first) runner's training & health analytics + lightweight training log, built on Strava + Garmin ingest with scheduled Claude analyses writing markdown reports
**Researched:** 2026-05-26
**Confidence:** HIGH (metric definitions and formulas verified against TrainingPeaks help docs, Intervals.icu, Runalyze docs, and peer-reviewed sources; product feature sets verified across multiple sources)

## Context Reminder

RunOS is NOT a product. It is one runner's local tool. The bar for a feature is
not "do competitors have it" but "does it make the four target analyses
(recovery/overtraining, load & trends, race readiness, correlations)
**trustworthy**, and is it cheap enough to be worth it for an audience of one."
That reframing is what separates table stakes from anti-features here: a lot of
what TrainingPeaks/Intervals.icu/Runalyze build is multi-user UI, social, and
device-integration surface area that is pure waste for a single local user.

---

## The Metrics That Matter (foundation for every analysis)

These are the computed quantities the analyses rest on. They are documented here
first because "features" downstream are mostly *analyses over these metrics*. Each
includes the actual computation so requirements can be specified precisely.

### Training load per activity — the unit everything aggregates

You need a single per-activity load number. Three viable sources, in priority order:

1. **rTSS (running Training Stress Score)** — pace-based, the standard for running.
   Requires an estimate of threshold pace (functional threshold pace, FTP-pace).
   `rTSS = (duration_sec × NGP × IF) / (FTP_pace × 3600) × 100`, where NGP =
   Normalized Graded Pace (pace adjusted for hills), IF = NGP / threshold pace.
   Conceptually: 100 = one hour at threshold. Pace is "a nearly perfect proxy for
   power" for running, so rTSS is accurate. **Complexity: MEDIUM** (needs NGP from
   grade-adjusted pace + a threshold-pace value that drifts over time).
2. **hrTSS / TRIMP (HR-based)** — Bannister TRIMP or Strava's "Relative Effort"
   approach. Uses time-in-HR-zones weighted by intensity. Good fallback when pace
   is unreliable (trail, treadmill, GPS dropout) but available HR. **Complexity: LOW–MEDIUM.**
3. **sRPE (session RPE)** — `RPE (0–10) × duration_minutes` = a unitless load.
   Validated in sports science, and critically: **this is the load metric the
   journaling feature can supply for runs with no good HR/pace data, or for
   cross-training (strength, bike).** **Complexity: LOW.**

**Recommendation:** Compute rTSS as primary, hrTSS as fallback, store sRPE from
journaling as a parallel/sanity track. Persist which method produced each day's
load so analyses can flag low-confidence days.

### CTL / ATL / TSB — fitness, fatigue, form (the PMC model)

The single most important trend model; every competitor implements it (Intervals.icu
literally renames it Fitness/Fatigue/Form). All are exponentially weighted moving
averages of daily load:

- **CTL (Chronic Training Load = "Fitness")**: EWMA of daily TSS over **42 days**.
  `CTL_today = CTL_yesterday + (TSS_today − CTL_yesterday) × (1/42)`
- **ATL (Acute Training Load = "Fatigue")**: EWMA over **7 days**.
  `ATL_today = ATL_yesterday + (TSS_today − ATL_yesterday) × (1/7)`
- **TSB (Training Stress Balance = "Form/Freshness")**: `TSB = CTL − ATL` (often
  uses *yesterday's* values). Positive TSB ≈ fresh/tapered; deeply negative ≈
  fatigued; mild negative is the normal "productive training" zone.

**Complexity: LOW** once daily load exists (it's a simple recurrence over the date
spine). This is the backbone of both the load-trend analysis AND race-readiness
(you want CTL high and TSB positive on race day).

### ACWR — acute:chronic workload ratio (injury/overreaching guardrail)

Ratio of short-term to long-term load. Two computation methods:

- **Coupled rolling average**: `(7-day total load) / (28-day total load / 4)`.
- **EWMA method**: ratio of a 7-day-constant EWMA to a 28-day-constant EWMA. More
  sensitive and the better-regarded method in recent literature.

"Sweet spot" widely cited as **0.8–1.3**; >1.5 is the elevated-risk "danger zone."
**Complexity: LOW** (reuses the daily-load series). Caveat to surface in reports:
ACWR is correlated with injury risk but is contested in the literature — treat as a
*flag for a conversation*, not gospel. Note it overlaps conceptually with TSB/ramp
rate; presenting both is fine but the analysis should reconcile them, not double-count.

### Ramp rate — how fast fitness is rising

`CTL_today − CTL_7_days_ago` (CTL change per week). >5–8/week is aggressive ramp =
overreaching risk. **Complexity: LOW.** A cleaner, less-contested companion to ACWR.

### HRV, resting HR, sleep — the recovery inputs (from Garmin)

Not computed by RunOS (Garmin provides them) but they must be **baselined**, not
read raw:

- **HRV status**: compare today vs a rolling personal baseline (Garmin uses a 7-day
  vs longer-term comparison; "balanced/unbalanced/low"). A drop of ~20–30% below
  baseline is the commonly cited rest-day trigger. **Subtlety to encode:** in deep
  overtraining HRV can paradoxically *rise* (parasympathetic saturation) — so
  "abnormal in either direction" matters, not just "low."
- **Resting HR**: elevated multi-day RHR vs baseline = classic overreaching signal.
- **Sleep**: duration + quality/score trend.

**Complexity: LOW to ingest, MEDIUM to baseline well** (need enough history for
stable personal baselines; cold-start is real).

### Effective VO2max / running performance estimate (race-readiness input)

From the HR↔pace relationship per run (Runalyze's "Effective VO2max" = scientific
VO2max blended with running economy). Trend it over time to see fitness direction
independent of load. **Complexity: MEDIUM** (estimation model; sensitive to bad HR
data). For a personal tool, a simpler proxy — trend of pace-at-fixed-HR, or best
recent efforts — may be enough and far cheaper. **Flag for a build-vs-borrow call.**

### Race prediction (race-readiness output)

- **Riegel**: `T2 = T1 × (D2/D1)^1.06`. Accurate within ~1–3% when distance ratio
  <4:1; over-optimistic for big jumps (5K→marathon). **Complexity: LOW.**
- **VDOT (Daniels)**: maps a race performance to a fitness number + equivalent
  times + training paces. **Complexity: LOW–MEDIUM** (table/formula lookup).
- **Critical Speed / D′**: needs two max efforts; **probably overkill** for v1.

**Recommendation:** Riegel + VDOT off recent best efforts, adjusted by "is CTL high
and trending up?" + "marathon-shape"-style long-run sufficiency check for long races.

---

## Feature Landscape

### Table Stakes (required for the analyses to be trustworthy)

These aren't "nice"; if any are missing, the four analyses produce confidently-wrong
output, which is worse than nothing for a tool whose whole value is *trustworthy signal*.

| Feature | Why Expected (trust requirement) | Complexity | Notes |
|---------|----------------------------------|------------|-------|
| Per-activity load metric (rTSS primary, hrTSS fallback) | Every aggregate metric is a function of daily load; no load = no analysis | MEDIUM | Store which method computed each value; need threshold-pace estimate |
| CTL / ATL / TSB time series over a date spine | The universal fitness/fatigue/form model; load-trend + race-readiness depend on it | LOW | Simple EWMA recurrence; needs continuous daily spine incl. zero-load days |
| Date spine with zero-fill | EWMAs and rolling windows are wrong if rest days are missing rows | LOW | Already in PROJECT scope ("shared date spine") — this is why it matters |
| Personal baselines for HRV / RHR / sleep | Raw wellness numbers are meaningless without a personal baseline; recovery analysis is baseline-relative | MEDIUM | Rolling baseline + handle cold-start / sparse history gracefully |
| Recovery / overtraining signal (multi-signal) | Explicit user goal; single-metric is unreliable, must combine load↑ vs HRV/RHR/sleep | MEDIUM | Combine ramp/ACWR + HRV-vs-baseline + RHR trend + sleep + sRPE; encode the "HRV can rise in OTS" subtlety |
| Training load & trend analysis (volume, intensity, fitness/fatigue) | Explicit user goal | LOW–MEDIUM | Weekly volume, time-in-zone distribution, CTL trend, ramp rate |
| ACWR and/or ramp-rate guardrail | Standard overreaching/injury flag; cheap given daily load exists | LOW | Present as a flag with caveats, reconcile with TSB |
| Race-readiness analysis vs goal race | Explicit user goal; needs predicted time vs target + form on race day | MEDIUM | Riegel/VDOT + CTL trend + taper/TSB + long-run sufficiency; reads races.md |
| Reads races markdown for context (date/distance/goal) | Race readiness is meaningless without knowing the target | LOW | Already in scope; parse into structured race objects |
| Reads training-plan markdown for context | Lets analysis say "you're behind/ahead of plan" qualitatively | LOW | Already in scope; read-for-context, not a diff engine (see anti-features) |
| Structured post-workout journal (RPE, feel, notes) linked to activity | Subjective data is half of correlation insight + supplies sRPE load | LOW–MEDIUM | Captured via Claude; must write structured rows (RPE numeric, mood, soreness, free text) keyed to activity_id |
| Unified daily-summary join (activity + wellness + journal) | Correlation analysis and reports need one row per day | LOW | Already in scope; this is the analysis substrate |
| Correlation insight (sleep/HRV/feel vs performance) | Explicit user goal and a core differentiator | MEDIUM | Needs enough paired days; report correlations honestly incl. "not enough data / weak signal" |
| Markdown reports into reports/ folder | The delivery surface (no UI by design) | LOW | Already in scope; reports must cite the numbers they're built on |
| Graceful handling of missing/low-quality data | A personal dataset is full of gaps (no HR, GPS dropout, Garmin sync miss) | MEDIUM | Every analysis must degrade to "insufficient data" rather than fabricate |

### Differentiators (where RunOS wins vs the incumbents)

The incumbents are strong on metrics and weak on *synthesis + reflection*. RunOS's
edge is that Claude can read objective data + plan + journal + races together and
write a narrative that a dashboard can't.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Journaling-via-Claude (no input UI) | Zero-friction capture; conversational extraction of RPE/feel/soreness/context into structured rows | MEDIUM | The capture interface is the chat; Claude must map free speech → schema reliably and link to the right activity (disambiguate "today's run") |
| Narrative synthesis across all sources | "Your HRV dipped, ramp is hot, and you said the tempo felt awful — back off this week" — a coach-like read no dashboard gives | MEDIUM | Claude over the daily-summary + journal + plan + races; the actual product magic |
| Correlation insight with subjective inputs | Most tools correlate device metrics only; RunOS can fold in "how it felt"/RPE/mood | MEDIUM | Depends on journaling volume; report effect sizes + confidence, avoid spurious correlations on tiny n |
| Plan/race-aware advice | Analysis grounded in *this runner's* goal race and plan, not generic | LOW–MEDIUM | Reads the markdown; cheap because plan is just context, not a structured engine |
| Two-layer raw→structured storage | Re-derive new metrics later (e.g., add ACWR-EWMA, new VO2max model) without re-fetching | MEDIUM | Already a PROJECT decision; genuinely future-proofing for a metrics tool |
| Scheduled "daily check" that only surfaces when noteworthy | Signal over noise: don't generate a report every day, alert when load/recovery/readiness crosses a threshold | MEDIUM | Avoids report fatigue; needs thresholds + change detection |
| Honest uncertainty in reports | Reports that say "weak signal / 9 days of data" build trust the incumbents' confident dashboards don't | LOW | Cultural/prompt discipline more than code; high payoff for a trust-first tool |

### Anti-Features (deliberately do NOT build)

For a single-user local tool, most of the incumbents' surface area is pure cost.

| Feature | Why Requested (surface appeal) | Why Problematic Here | Alternative |
|---------|--------------------------------|----------------------|-------------|
| Structured planned-vs-actual plan engine (workout library, prescribed paces, compliance %) | TrainingPeaks' core; feels rigorous | Huge build; plan changes constantly; PROJECT explicitly chose markdown-for-context | Markdown plan read for context; Claude does qualitative "ahead/behind" |
| Web/mobile dashboard & charts | Every competitor has one; "I want to see graphs" | Violates no-UI constraint; massive effort for audience of one; markdown + Claude is the chosen surface | Markdown reports; ad-hoc SQLite queries; let Claude render a table when asked |
| Real-time / live workout tracking | Wearable apps do it | Out of scope by design; batch daily is sufficient; no value local | Daily batch sync |
| Social / sharing / segments / leaderboards | Strava's whole identity | Single-user, private-by-design, code-only public repo | None — explicitly not a goal |
| Multi-sport device feature parity (cycling power curves, swim, etc.) | Incumbents support all sports | User is a runner; cross-training only matters as load input via sRPE | Capture non-run sessions as sRPE load only |
| Nutrition / food logging | TrainingPeaks/MFP do it | No official MFP API; scraping fragile; explicitly deferred in PROJECT | Deferred; possible CSV-drop ingest later |
| Re-deriving Garmin's proprietary scores exactly (Body Battery, Training Readiness 0–100, Firebeat VO2max) | Tempt to "match Garmin" | Closed algorithms; chasing parity wastes effort and will never match | Ingest Garmin's scores as inputs; compute RunOS's own transparent signals instead |
| Auto-adjusting/AI-prescribed daily workouts | "HRV-guided training" hype | Prescribing training has real injury/health stakes; out of scope; advice yes, prescriptions no | Surface signals + suggestions ("consider easy day"), human decides |
| Generic LLM "chat with all my data" as the primary interface | Feels modern | Unbounded scope, hard to make trustworthy/reproducible | Scheduled, scoped analyses with defined inputs/outputs; chat is for journaling capture |
| Multi-user / accounts / auth / hosting | Sounds scalable | Single-user by definition; pure overhead | Local files + personal tokens |

---

## Feature Dependencies

```
Ingestion (Strava activities + streams, Garmin wellness)
    └──requires──> Two-layer raw→structured storage
                       └──requires──> Date spine (zero-filled)
                                          ├──requires──> Per-activity load (rTSS/hrTSS/sRPE)
                                          │                  └──requires──> CTL/ATL/TSB
                                          │                  └──requires──> ACWR / ramp rate
                                          │                  └──requires──> Load & trend analysis
                                          ├──requires──> HRV/RHR/sleep baselines
                                          │                  └──requires──> Recovery/overtraining analysis
                                          │                                     ^
                                          │                                     │ enhances
                                          │                       (CTL/ATL ramp ┘)
                                          └──requires──> Unified daily-summary join
                                                             └──requires──> Correlation insight

Journaling-via-Claude ──writes──> structured journal rows (RPE, feel, mood)
    ├──enhances──> Unified daily-summary join ──> Correlation insight (subjective inputs)
    └──supplies──> sRPE load (fallback / cross-training) ──> Per-activity load

races.md  ──context──> Race-readiness analysis  <──requires── CTL/ATL/TSB + race prediction (Riegel/VDOT)
plan.md   ──context──> Load & trend + race-readiness (qualitative ahead/behind)

All analyses ──write──> Markdown reports (reports/)
Scheduled daily sync ──triggers──> daily analysis check ──> reports
```

### Dependency Notes

- **Everything requires the date spine with zero-fill.** EWMA (CTL/ATL) and rolling
  windows (ACWR) are silently wrong if rest days are absent rows. This is the
  single most load-bearing piece of plumbing — get it in an early phase.
- **All trend/recovery metrics require per-activity load first.** Load is the atom;
  CTL/ATL/TSB, ACWR, and ramp are all just transforms of the daily-load series. Build
  load computation before any trend analysis.
- **Recovery analysis requires baselines, not raw wellness.** It also *enhances* with
  CTL/ATL ramp (rising load + falling HRV is the high-confidence overtraining
  pattern), so recovery analysis is best built after load exists, even though it can
  start with wellness alone.
- **Journaling enhances correlation AND supplies load.** It has a dual role: subjective
  inputs for correlation, and an sRPE fallback when device load is unavailable. This
  makes it higher-leverage than a "diary" framing suggests.
- **Race readiness requires both the load model and the race calendar.** Without
  races.md it has no target; without CTL/TSB it can't assess form/taper.
- **Correlation insight requires the unified daily join + enough paired days.** It is
  data-hungry; it will be weak until there's history (cold-start). Build the join early
  so data accumulates, but expect the *analysis* to be honest about low n for a while.

---

## MVP Definition

### Launch With (v1) — Strava-first milestone (matches PROJECT's "Strava-first" decision)

Goal: prove pull → store → analyse end-to-end on the clean source, with the load
model and at least two of the four target analyses trustworthy.

- [ ] Strava ingest (activities + streams) into raw→structured store — the foundation
- [ ] Zero-filled date spine — required by every metric
- [ ] Per-activity load: rTSS (with a configurable threshold pace) + hrTSS fallback — the atom
- [ ] CTL / ATL / TSB series — backbone trend model
- [ ] Load & trend analysis report (weekly volume, intensity mix, CTL/ramp) — target goal #2, cheapest given the above
- [ ] ACWR / ramp-rate guardrail in the load report — near-free injury flag
- [ ] races.md + plan.md reading for context — cheap, unlocks race-readiness framing
- [ ] Race-readiness analysis (Riegel/VDOT off best efforts + CTL/TSB) — target goal #3
- [ ] Journaling-via-Claude → structured rows linked to activity — the differentiator; start collecting subjective data ASAP so correlation has fuel later
- [ ] Markdown reports into reports/ + CLI entrypoint — the delivery surface

### Add After Validation (v1.x) — Garmin milestone

Trigger: Strava loop is trustworthy and the load model is validated against feel.

- [ ] Garmin wellness ingest (HRV, sleep, RHR, body battery, stress) — adds the recovery dimension
- [ ] Personal HRV/RHR/sleep baselines — required before recovery analysis is trustworthy
- [ ] Recovery / overtraining analysis (load↑ vs HRV/RHR/sleep, multi-signal) — target goal #1; gated on Garmin + baselines
- [ ] Correlation insight (sleep/HRV/feel vs performance) — target goal #4; gated on having paired history accumulated since v1
- [ ] Scheduled daily sync + "only surface when noteworthy" check — automation + signal-over-noise
- [ ] sRPE as parallel load track / cross-training load from journaling — robustness for gappy data

### Future Consideration (v2+)

- [ ] Effective VO2max / pace-at-HR fitness trend — richer race readiness; defer until base loop proven, decide build-vs-borrow
- [ ] Marathon-shape-style long-run sufficiency model for long-race readiness — refinement
- [ ] EWMA-method ACWR (vs rolling) — accuracy upgrade once the simple version is trusted
- [ ] CSV-drop nutrition ingest — only if the user actually wants it; explicitly deferred
- [ ] Structured plan-vs-actual diffing — only if markdown-for-context proves insufficient

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Strava ingest + raw→structured store | HIGH | MEDIUM | P1 |
| Zero-filled date spine | HIGH | LOW | P1 |
| Per-activity load (rTSS + hrTSS) | HIGH | MEDIUM | P1 |
| CTL/ATL/TSB series | HIGH | LOW | P1 |
| Load & trend analysis report | HIGH | LOW | P1 |
| ACWR / ramp-rate guardrail | MEDIUM | LOW | P1 |
| Journaling-via-Claude → structured rows | HIGH | MEDIUM | P1 |
| races.md / plan.md context reading | MEDIUM | LOW | P1 |
| Race-readiness analysis (Riegel/VDOT + form) | HIGH | MEDIUM | P1 |
| Markdown reports + CLI | HIGH | LOW | P1 |
| Garmin wellness ingest | HIGH | MEDIUM | P2 |
| HRV/RHR/sleep baselines | HIGH | MEDIUM | P2 |
| Recovery/overtraining analysis | HIGH | MEDIUM | P2 |
| Correlation insight (with subjective) | HIGH | MEDIUM | P2 |
| Scheduled daily sync + noteworthy-only check | MEDIUM | MEDIUM | P2 |
| sRPE parallel/cross-training load | MEDIUM | LOW | P2 |
| Effective VO2max / pace-at-HR trend | MEDIUM | MEDIUM | P3 |
| EWMA-method ACWR | LOW | LOW | P3 |
| CSV nutrition ingest | LOW | MEDIUM | P3 |
| Structured plan-vs-actual engine | LOW | HIGH | P3 |

**Priority key:** P1 = must have for launch (Strava milestone). P2 = add when possible
(Garmin milestone). P3 = future / only if validated need.

---

## Competitor Feature Analysis

| Feature | TrainingPeaks | Intervals.icu | Runalyze | Garmin Connect | Strava | RunOS's Approach |
|---------|---------------|---------------|----------|----------------|--------|------------------|
| Fitness/Fatigue/Form (PMC) | Yes (CTL/ATL/TSB origin) | Yes (renamed Fitness/Fatigue/Form) | Yes | Training Status (proxy) | Fitness & Freshness | Compute CTL/ATL/TSB transparently from daily load |
| Per-activity load | TSS/rTSS | Load (TSS-equiv) | ATL/CTL via HR | Training Load (Firstbeat) | Relative Effort (TRIMP) | rTSS primary, hrTSS fallback, sRPE parallel |
| ACWR / ramp | Ramp rate | Yes | Yes | Acute:Chronic load | — | Ramp + ACWR as a flag with caveats |
| Recovery / HRV-guided | Limited | HRV in wellness | Yes | Strong (Training Readiness, HRV Status) | — | Multi-signal recovery report grounded in personal baselines |
| Race prediction | Yes | Yes | Marathon Shape + Effective VO2max (strong) | Race predictor | — | Riegel/VDOT + CTL/TSB form check (v1); VO2max trend (v2+) |
| Subjective journaling / RPE | Yes (forms) | Wellness + notes | Yes | Limited | Perceived exertion | Conversational capture via Claude → structured rows (the differentiator) |
| Correlation / synthesis | Dashboards | Charts/custom | Charts | Insights | — | Claude narrative across objective + subjective + plan + races |
| Plan engine | Strong (core) | Calendar/workouts | Plans | Coach plans | — | Markdown-for-context only (deliberate anti-feature) |
| UI | Web/mobile | Web | Web | App | App/web | None — markdown + Claude (deliberate) |
| Multi-user/social | Coach-athlete | Public/groups | Community | Connections | Social core | Single-user local (deliberate) |

**Takeaway:** The incumbents collectively cover the metrics extremely well — RunOS
should *borrow their formulas* (they are public and standard) rather than reinvent.
RunOS's whitespace is the synthesis + frictionless subjective capture + plan/goal
grounding, delivered as narrative markdown rather than a dashboard. Garmin already
does recovery best, so RunOS should *consume Garmin's signals as inputs* and add
value by combining them with load, plan, journal, and goal — not by re-deriving
closed scores.

---

## Sources

Metric definitions & formulas (HIGH confidence):
- [Training Stress Scores (TSS) Explained — TrainingPeaks Help Center](https://help.trainingpeaks.com/hc/en-us/articles/204071944-Training-Stress-Scores-TSS-Explained)
- [Fitness (CTL) — TrainingPeaks Help Center](https://help.trainingpeaks.com/hc/en-us/articles/204071884-Fitness-CTL)
- [Fitness, Fatigue & Form Chart — Intervals.icu](https://www.intervals.icu/features/fitness-chart/)
- [RUNALYZE — Understanding the calculations](https://blog.runalyze.com/tutorial/runalyze-understanding-the-calculations/)
- [RUNALYZE — Marathon Shape](https://runalyze.com/help/article/marathon-shape)
- [RUNALYZE — VO2max / Effective VO2max](https://runalyze.com/help/article/vo2max?_locale=en)
- [Acute:Chronic Workload Ratio — Science for Sport](https://www.scienceforsport.com/acutechronic-workload-ratio/)
- [ACWR and injury risk: systematic review — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC7047972/)
- [Weekly load vs ACWR in running — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC11537214/)
- [Race Time Prediction: VDOT, Critical Speed & Riegel](https://sport-calculator.com/blog/how-to-predict-race-times-vdot-critical-speed)
- [How Accurate Are Race Calculators? A Riegel Formula Guide — RunnersConnect](https://runnersconnect.net/race-calculators/)

Recovery / overtraining (MEDIUM–HIGH):
- [Garmin — Training Readiness](https://www.garmin.com/en-US/garmin-technology/running-science/physiological-measurements/training-readiness/)
- [Garmin — Training Status](https://www.garmin.com/en-US/garmin-technology/running-science/physiological-measurements/training-status/)
- [Garmin Training Load Explained — Should I Train](https://www.shoulditrain.com/blog/garmin-training-load-explained)
- [Overtraining vs Overreaching: How Wearables Detect the Difference — Sensai](https://www.sensai.fit/blog/overtraining-vs-overreaching-wearable-biomarker-detection)
- [HRV Training Guide — WHOOP](https://www.whoop.com/us/en/thelocker/heart-rate-variability-training/)

Subjective monitoring / sRPE / wellness correlation (MEDIUM, peer-reviewed):
- [Session-RPE Method for Training Load Monitoring — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC5673663/)
- [Subjective wellness predicting RPE and training load — SAGE](https://journals.sagepub.com/doi/abs/10.1177/17543371211049066)
- [Sleep quality/quantity and perceived training quality — Frontiers](https://www.frontiersin.org/journals/sports-and-active-living/articles/10.3389/fspor.2021.705650/full)

Platform comparison (MEDIUM):
- [Strava Relative Effort Guide — the5krunner](https://the5krunner.com/2025/11/17/strava-relative-effort-guide-tss-2025/)
- [Comparing Garmin Training Status and Strava Fitness & Freshness — Stationary Waves](https://www.stationarywaves.com/2019/03/comparing-garmins-training-status-and.html)

---
*Feature research for: personal runner's training & health analytics (RunOS)*
*Researched: 2026-05-26*
