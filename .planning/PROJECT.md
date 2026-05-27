# Tempo

## What This Is

Tempo is a personal, local-first training and health system for a runner. It
pulls running and wellness data from Strava and Garmin into a structured,
queryable SQLite database, lets the user plan (races + training plan) and reflect
(post-workout journaling via Claude), and runs scheduled Claude analyses that
write markdown reports on recovery, training load, race readiness, and
correlations. It's a single-user tool for the project owner, not a product.

## Core Value

Turn scattered training and health data into trustworthy, structured signal that
tells the user when to push, when to back off, and whether they're on track for
their goals — combining objective data (Strava/Garmin) with their own plan and
reflections.

## Current Milestone: v1.1 Telegram Voice Coach (Mac)

**Goal:** Voice memos sent to a personal Telegram chat become structured journal / heat / race entries — transcribed locally on the user's Mac, fed to a Claude Code session running on the laptop (driven via the Claude Agent SDK using the user's Claude subscription), replies delivered back via Telegram.

**Target features:**
- Telegram bot intake (`python-telegram-bot`, long-polling, single-chat allowlist)
- Local voice transcription via `faster-whisper` (default `small.en` int8 — CPU-only on Apple Silicon; configurable to bigger models if latency budget allows; `large-v3-turbo` rejected because faster-whisper has no Metal acceleration and turbo runs ~3-5× realtime on Mac CPU)
- **Brain = Claude Code itself, driven via the Claude Agent SDK** — spawns the `claude` CLI as a subprocess so the user's existing Claude subscription is the auth + billing path (no separate Anthropic API key, no per-token cost beyond their subscription). Claude Code's built-in tools (Bash, Read, Write, Edit, plus all of the project's GSD slash commands and the `tempo` CLI it can call via Bash) are all available — no hand-rolled tool definitions needed.
- **4-hour rolling conversation window** via Claude Code's native session resume (`--resume <session-id>`) per chat. New session after 4hrs of silence or on explicit "new session" command.
- Reply routing — Claude Code stdout (final assistant message) flows back as a Telegram message, formatted as HTML (NOT MarkdownV2 — too many escape gotchas; HTML needs only `& < >` escaped).
- Runs as a launchd background process on the user's Mac; KeepAlive=true, restart-on-crash.

**Privacy stance:** Voice files NEVER leave the local machine — `faster-whisper` runs entirely on CPU on the laptop. Transcribed text + Claude Code's tool calls + replies flow through the user's existing Claude subscription (which already has access to chat history, code, etc. via Claude Code today — this is not a new disclosure). Telegram carries the voice memo and the reply text. No additional cloud surface vs. what the user already accepts by using Claude Code.

**Out of scope** (deferred to a later milestone — "v1.2 Pi port"):
- Raspberry Pi deployment + systemd unit
- ARM-specific `curl_cffi` / `garminconnect` wheel work
- Remote / public-URL / TLS considerations

## Requirements

### Validated

**v1.0 milestone (shipped 2026-05-27):** all 39 v1 requirements (FND, STRV, STORE, LOAD, ANL, PLAN, JRNL, GRMN, SCHED, DELIV) plus the 6 Phase-8 v1.1 requirements (TRACK-01..06: races result + auto-link, heat.md tracker + recovery surfacing, plan.md retired).

### Active

<!-- Current scope. Building toward these. -->

**Ingestion**
- [ ] Pull all-time Strava activity history (activities + detailed streams: HR, pace, GPS, power, cadence, elevation)
- [ ] Incrementally sync new Strava activities on a daily schedule
- [ ] Pull Garmin wellness data (sleep, HRV, body battery, resting HR, stress, steps) via the unofficial garminconnect library
- [ ] Store every raw API response verbatim, then normalise into structured tables (two-layer raw → structured)

**Storage & modelling**
- [ ] Structured SQLite schema with a shared date spine joining all sources
- [ ] A unified daily-summary view/table that joins activities, wellness, and journal per day
- [ ] Re-derive structured tables from stored raw data without re-fetching

**Plan & reflect**
- [ ] Maintain upcoming races (date, distance, goal) in a simple markdown file Tempo reads for context
- [ ] Maintain a training plan in a simple markdown file Tempo reads for context
- [ ] Capture structured post-workout journal entries (RPE, how it felt, notes) by telling Claude, written into the DB and linked to the activity

**Analysis & delivery**
- [ ] Daily scheduled sync followed by a daily analysis check
- [ ] Analyses written as markdown reports into a reports/ folder in the repo
- [ ] Recovery / overtraining analysis (rising load vs HRV / sleep / resting HR)
- [ ] Training load & trend analysis (volume, intensity, fitness/fatigue over time)
- [ ] Race-readiness analysis (progress toward goal race / target pace)
- [ ] Correlation insight (sleep / HRV / how runs felt vs performance)

**Foundation**
- [ ] Secure local credential/token handling (Strava OAuth tokens, Garmin login) — never committed
- [ ] CLI entrypoint to run pulls and analyses (`tempo ...`)

### Out of Scope

- **MyFitnessPal / food & nutrition** — no official API; deferred. May return later via CSV-drop ingest.
- **Multi-user / accounts / hosting** — single-user local tool; no server, no auth beyond personal API tokens.
- **Mobile or web UI** — interaction is via CLI, markdown files, and Claude; no front end.
- **Real-time / live tracking** — batch daily sync is sufficient.
- **Selling or sharing data externally** — personal use only; code-only public repo, data stays local.

## Context

- **Owner/user:** a runner who currently has training data scattered across
  Strava (activities) and Garmin (wellness), with no unified store and no
  structured way to capture subjective post-workout reflection or compare against
  a plan.
- **Goals span four areas:** recovery/overtraining, training load & trends, race
  readiness, and correlations — plus planning (races, training plan) and
  journaling.
- **Repo:** https://github.com/rossheadington/tempo (public, code-only; all
  secrets and health data gitignored from the first commit).
- **Source realities:**
  - *Strava* — official OAuth2 REST API; clean. One-time auth, refresh token,
    paged history pulls within rate limits.
  - *Garmin* — no individual official Health API; the community `garminconnect`
    library logs in with Connect credentials. Robust enough for personal use but
    can break on site changes / MFA.
  - *MyFitnessPal* — API removed in 2020; deferred deliberately.
- **Journaling model:** the user prefers to "tell Claude," so Claude is the
  capture interface and writes structured journal rows — no separate input UI.
- **Plan model:** training plan and race calendar are simple markdown files the
  user maintains; Tempo reads them for analysis context rather than diffing
  planned-vs-actual (a structured plan engine can come later).

## Constraints

- **Tech stack**: Python 3.14, `uv` for packaging/deps, SQLite for storage — chosen for best health-data library support and a zero-infrastructure local tool.
- **Privacy**: Public repo holds code only. Credentials, tokens, and all health data must stay local and gitignored — non-negotiable.
- **Dependencies**: Garmin access relies on an unofficial library that may break; design connectors to fail gracefully and isolate that risk.
- **Rate limits**: Strava API limits require paged, resumable history backfill for the all-time pull.
- **Local-first**: No servers, no hosted database; everything runs on the user's machine, analyses run on a schedule via Claude.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Name: Tempo | Running term (tempo runs) + the steady rhythm of scheduled data collection | — Pending |
| Python + uv + SQLite | Best library support (garminconnect, strava), zero-infra local store | — Pending |
| Strava-first milestone | Easiest, cleanest source; proves pull → store → analyse end-to-end before Garmin | — Pending |
| Two-layer raw → structured storage | Keep raw verbatim so new metrics can be derived later without re-fetching | — Pending |
| Defer MyFitnessPal / food | No official API; scraping is fragile — not worth blocking on | — Pending |
| Journaling via Claude | User prefers telling Claude; avoids building an input UI | — Pending |
| Plan/races as simple markdown | Low friction; read for context now, structured plan-vs-actual later if needed | — Pending |
| Public, code-only repo | Share code; keep all health data and secrets gitignored and local | — Pending |
| Telegram as v1.1 interaction shell | Voice-friendly, mobile-first, near-zero infra; lets the user journal while walking the cool-down | v1.1 |
| Local Whisper transcription (faster-whisper, `small.en` default) | Keeps voice audio on-device; never uploads raw audio to any cloud service. `small.en` chosen over `large-v3-turbo` because faster-whisper has no Metal/GPU on Apple Silicon — CPU-only inference makes the big model too slow (20-30s for 60s of audio). `small.en` is ~8-12s and accurate enough for runner jargon. Model is a config knob if user wants to upgrade later. | v1.1 |
| Brain = Claude Code via the Claude Agent SDK (not the raw `anthropic` API, not a hand-rolled agent) | Uses the user's existing Claude subscription as the auth + billing path — no separate API key, no per-token cost on top of the subscription. All of Claude Code's built-in tools (Bash, Read, Write, Edit, plus the project's GSD slash commands) come for free; no need to define each Tempo function as a tool. Node 18+ is the one new system dependency. | v1.1 |
| Claude Code `--resume` for session continuity, not hand-rolled message log | Claude Code has native multi-turn session resume; reusing it is cheaper and more correct than building our own conversation memory. Per-chat session-id mapped + reset after 4hrs of silence (or on explicit "new session" command). | v1.1 |
| Telegram as accepted privacy surface | v1 was strictly local-first. v1.1 sends voice memo + reply text through Telegram, and Claude Code calls touch Anthropic infra (but the user already accepts that today by using Claude Code). Voice audio never leaves the laptop. Conscious tradeoff for voice ergonomics. | v1.1 |
| 4-hour rolling conversation window per chat | Lets `yeah do it` / `RPE 6 not 7` follow-ups feel natural; resets organically between training sessions | v1.1 |
| Mac-first, Pi-later milestone split | Iterate on familiar hardware; isolate ARM / curl_cffi risk into its own milestone | v1.1 |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-27 — v1.0 (Phases 1-8) shipped; v1.1 Telegram Voice Coach milestone begun*
