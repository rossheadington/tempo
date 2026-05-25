# Pitfalls Research

**Domain:** Personal local-first training/health data pipeline (Strava + Garmin → SQLite, scheduled Claude analysis, public code-only repo)
**Researched:** 2026-05-26
**Confidence:** HIGH for Strava/Garmin/secrets/scheduling (Context-rich official docs + active GitHub issues + recent maintainer announcements); MEDIUM for correctness-of-metrics (synthesized from domain docs, fewer direct post-mortems)

## Critical Pitfalls

### Pitfall 1: Secret or health-data leak into the public repo

**What goes wrong:**
A Strava `client_secret`, a refresh token, `garmin_tokens.json`, the SQLite DB, or a raw API-response dump gets committed and pushed to the public GitHub repo. Even if deleted in a later commit, it stays in git history forever and may already be cloned, scraped, or forked. Strava tokens grant access to all the owner's activity data; Garmin tokens grant access to the entire Garmin Connect account (which is the owner's real login session, valid ~1 year).

**Why it happens:**
- A new file path (e.g., `reports/`, `data/exports/`, a `.env.local`, a debug `dump.json`) isn't covered by `.gitignore`.
- `git add -A` / `git add .` sweeps up an untracked secret.
- A token is hardcoded in a script "just to test" and committed.
- The SQLite file is placed inside the repo tree and only excluded by a broad pattern that a later file path slips past.
- Markdown reports written by Claude analyses embed raw health data and land in a tracked `reports/` folder.

**How to avoid:**
- `.gitignore` from the very first commit (already done) — but treat it as necessary, not sufficient.
- Keep the SQLite DB, tokens, and `.env` **outside** the repo working tree entirely (e.g., `~/.tempo/`), so an accidental `git add .` cannot reach them.
- Add a pre-commit hook (`gitleaks` or `git-secrets`) that blocks commits containing high-entropy strings / known token patterns. This is the real safety net.
- Decide explicitly whether `reports/` is committed. If analyses contain personal health data, gitignore them or keep them outside the tree. The PROJECT.md says reports go "into a reports/ folder in the repo" — flag this: either commit only redacted/sanitized reports or keep the folder gitignored.
- Use placeholder/example config files (`.env.example`) that are committed; real config never is.
- Never hardcode secrets, even temporarily — load from env or a local file from day one.

**Warning signs:**
- `git status` shows an untracked file inside the repo tree that contains tokens or data.
- A report file containing HR/sleep numbers appears in `git status` as staged.
- A pre-commit hook is not installed (you have no automated backstop).

**Phase to address:**
Foundation / earliest phase (credential + storage scaffolding). The directory layout decision (data and tokens outside the repo tree) and the pre-commit hook must exist **before** the first connector writes anything.

---

### Pitfall 2: Garmin account lockout / 48h+ 429 block from repeated logins

**What goes wrong:**
The Garmin connector logs in with username/password on every run instead of reusing a saved session. Garmin's Cloudflare-fronted SSO aggressively rate-limits login attempts; repeated logins from the same IP trigger `429 Too Many Requests` at the **account level**, blocking login for 48+ hours. A daily scheduled job that re-authenticates every run, plus any retry-on-failure loop, will reliably trip this. Because it's the owner's real Garmin account, the lockout also affects the Garmin Connect app/website, not just the script.

**Why it happens:**
- Treating Garmin like a normal OAuth API where you authenticate per request.
- Naive retry logic: login fails → immediately retry → retry → each attempt counts against the limit and deepens the block.
- Not persisting the OAuth1/OAuth2 token pair that `garminconnect` writes to `~/.garminconnect/garmin_tokens.json`.
- Running the connector in a fresh container/env each time with no token volume mounted.

**How to avoid:**
- **Authenticate once, persist tokens, reuse them.** `garminconnect` (v0.3.4, May 2026) stores tokens at `~/.garminconnect/garmin_tokens.json` (mode 0600) and auto-refreshes them indefinitely as long as the refresh token is valid. The daily job should load saved tokens and only fall back to full credential login when the token is genuinely expired/revoked.
- Keep the token file in a stable location outside the repo (Pitfall 1) and make sure the scheduled job uses the same path the interactive login used.
- **No tight retry loop on auth failure.** On a 429 or auth error, fail the run, log it, and back off for hours — do not retry within the same run. Treat 429 as "stop, do not pass go."
- Implement exponential backoff with a long floor (hours) for any login retry, and a hard cap on attempts per day (e.g., 1).
- Add a manual `tempo garmin login` command separate from the scheduled sync, so re-auth (and MFA entry) is an explicit human action, never an automated loop.

**Warning signs:**
- Logs show a login network call on every scheduled run (should be rare — only on token expiry).
- First `429` appears; if you keep running, the block compounds.
- Garmin Connect app suddenly asks you to re-login or shows "too many attempts."

**Phase to address:**
Garmin ingestion phase. Token persistence + reuse and the no-retry-on-429 policy are core to the first working Garmin sync, not a later hardening step. (Strava-first milestone ordering in PROJECT.md is correct partly because it defers this risk.)

---

### Pitfall 3: The Garmin library breaks because the auth flow is unofficial and Garmin changes it

**What goes wrong:**
The entire Garmin ingestion path depends on reverse-engineered, undocumented auth. This is a moving target. Concretely: in **late March 2026, Garmin changed their auth flow and broke the `garth` library** (the SSO foundation many Garmin clients used); `garth` was **deprecated by its maintainer on 2026-03-27**. Scripts depending on it stopped working overnight. `garminconnect` has since moved to a mobile-SSO flow (v0.3.4, May 2026) and uses `curl_cffi` TLS-impersonation to get past Cloudflare — but this is explicitly a "cat-and-mouse" situation that can break again with no warning. A daily pipeline silently produces a gap in Garmin data when this happens.

**Why it happens:**
- There is no official individual Garmin Health API; the only path is logging in as the Connect app.
- Garmin actively adds Cloudflare WAF / TLS-fingerprinting countermeasures against programmatic access.
- Pinning an old library version freezes you on a flow Garmin may invalidate; not pinning means a `uv` upgrade can swap in a behavior change.

**How to avoid:**
- **Isolate Garmin behind a connector interface** (as PROJECT.md already mandates: "design connectors to fail gracefully and isolate that risk"). The rest of the system (storage, analysis) must not care which library or flow produced the data.
- Make Garmin sync failures **non-fatal**: a broken Garmin pull should not abort the Strava sync or the analysis run. Record the failure, continue, and surface it.
- Pin `garminconnect` to a known-working version in `uv`, but **monitor the upstream repo** for auth-breakage issues; budget time to bump it when Garmin changes things.
- Keep raw-response storage (two-layer raw→structured) so that when the library returns a slightly different shape, you can re-derive structured tables without re-fetching, and you can detect schema drift.
- Have a fallback plan documented: if the library dies, the owner can still export Garmin data manually (Garmin's official export / FIT files) and CSV-drop it — the same deferred mechanism mentioned for MyFitnessPal.

**Warning signs:**
- Garmin connector starts returning auth errors or empty/changed payloads after working fine.
- Upstream GitHub issues spike with "login broken" / "auth changed" titles.
- A `uv` dependency update coincides with Garmin sync failures.

**Phase to address:**
Garmin ingestion phase (connector isolation + graceful failure). Architecture decision (connector boundary) should be set in the foundation/architecture phase so Garmin can be added behind a stable seam.

---

### Pitfall 4: Strava refresh-token rotation handled wrong → re-auth every time or lost access

**What goes wrong:**
Strava access tokens expire **6 hours** after creation. On refresh, Strava returns a **new refresh token AND immediately invalidates the old one** (rotating refresh tokens). If the pipeline doesn't persist the *new* refresh token after each refresh — or persists it non-atomically and a crash loses it — the stored refresh token becomes dead, and the next run can't authenticate. The owner then has to manually re-do the OAuth browser flow. A subtler version: two processes (e.g., a manual run and the scheduled run) both refresh, each invalidating the other's token, causing intermittent auth failures.

**Why it happens:**
- Treating the refresh token as static (it isn't — it rotates).
- Writing the new token to memory but forgetting to persist it, or persisting only the access token.
- Non-atomic writes: process reads token, refreshes, crashes before writing the new one back.
- Concurrent refreshes from two entry points racing.

**How to avoid:**
- After **every** token refresh, persist the returned `refresh_token` and `expires_at` atomically (write to temp file, `fsync`, rename) before making API calls.
- Check `expires_at` and only refresh when within ~1 hour of expiry, not on every call.
- Serialize token access — don't let a manual `tempo sync` and the scheduled job refresh simultaneously (a simple file lock around the token-refresh-and-persist step).
- Store `client_id`, `client_secret`, and the rotating `refresh_token` together in the local secrets store (outside the repo).
- Consider using a maintained wrapper (e.g., `stravalib`) that handles refresh, but still own the persistence of the rotated token.

**Warning signs:**
- The OAuth browser flow is needed more than once (it should be a true one-time setup).
- Intermittent `401 Unauthorized` on the first call of a run.
- Logs show a refresh succeeding but the next run fails to authenticate (token wasn't persisted).

**Phase to address:**
Strava ingestion / foundation phase (Strava-first milestone). Token persistence is part of the very first end-to-end Strava pull.

---

### Pitfall 5: All-time backfill blows the Strava rate limit and isn't resumable

**What goes wrong:**
The all-time history pull fetches the activity list (paged) **and** detailed streams per activity (HR, pace, GPS, power, cadence, elevation). Fetching streams is **≥2 API calls per activity** (detailed activity + streams), and there is **no bulk export endpoint**. Default limits are 100 requests / 15 min and 1,000 / day (non-upload), resetting at natural quarter-hours and midnight UTC. A runner with years of history (hundreds–thousands of activities) needs hundreds–thousands of calls. A naive loop hits `429`, and if the backfill isn't checkpointed, a crash or rate-limit at activity #380 means starting over — burning the daily budget again and possibly never finishing.

**Why it happens:**
- Assuming a single run can pull everything.
- Not reading the rate-limit headers Strava returns on every response.
- No persistence of "which activities are already fully fetched," so restarts re-fetch.
- Treating a `429` as a fatal error instead of a "pause until window resets" signal.

**How to avoid:**
- **Make the backfill resumable and idempotent.** Persist per-activity fetch state (list-fetched, detail-fetched, streams-fetched) so the job can stop and resume across days. Store raw responses immediately (two-layer model) so a re-run never re-fetches already-stored activities.
- Read the `X-RateLimit-Limit` / `X-RateLimit-Usage` headers on every response; when near the 15-min or daily cap, sleep until the next reset rather than charging into a `429`.
- Spread the backfill across multiple days deliberately — budget calls per day and stop cleanly.
- Fetch streams **lazily / on demand** for older activities if not all are needed immediately, prioritizing recent + race-relevant ones first.
- Separate "backfill" (one-time, resumable, slow) from "incremental sync" (daily, small) as distinct code paths.

**Warning signs:**
- A `429` mid-backfill with no checkpoint to resume from.
- Daily budget exhausted before the backfill completes and no plan to continue tomorrow.
- Re-running the backfill re-downloads activities already stored.

**Phase to address:**
Strava ingestion phase — backfill design. The resumable/checkpointed/idempotent design and rate-limit-header awareness are first-class requirements of the all-time pull, not optimizations.

---

### Pitfall 6: Wrong day attribution (timezone bucketing) corrupts the date spine and analyses

**What goes wrong:**
The whole product joins activities + wellness + journal on a shared **date spine** and a daily-summary view. If "which day" is computed inconsistently, every downstream analysis (load vs recovery, trends, correlations) is subtly wrong. Two concrete traps:
1. **Strava:** `start_date` and `start_date_local` are *both* serialized with a trailing `Z` (looks like UTC), but `start_date_local` is actually the wall-clock local time with a fake `Z`. Parsing `start_date_local` as UTC, or bucketing on `start_date` (true UTC), can shove a 10pm run into the next/previous calendar day. A late-evening or early-morning run lands on the wrong date.
2. **Garmin:** Overnight **sleep/HRV spans two calendar days**. Garmin attributes a night's data to a single `calendarDate` (the "My Day" it shows in Connect). If Tempo instead buckets by the sleep *start* timestamp, last night's sleep gets attached to yesterday, misaligning "recovery today vs load today."

If both sources are bucketed by different conventions, you get systematic misalignment — e.g., correlating today's HRV with yesterday's run.

**Why it happens:**
- Assuming the trailing `Z` always means UTC (Strava's `start_date_local` lies).
- Bucketing all sources by UTC date uniformly, ignoring that "training day" is a local concept and that sleep crosses midnight.
- Not capturing/using the activity's UTC offset (Strava provides `utc_offset` and `timezone`).
- Re-deriving daily summaries without a single, documented "what day does this belong to" rule.

**How to avoid:**
- Define **one explicit rule**: the date spine is keyed by the athlete's **local calendar date**. For Strava, derive the local date from `start_date_local`'s wall-clock value (do NOT treat its `Z` as UTC); store the activity's `utc_offset`/`timezone` raw so it's re-derivable. For Garmin wellness, key by Garmin's provided `calendarDate` (sleep/HRV/daily summaries) rather than raw start timestamps — Garmin literally added `calendarDate` to remove this ambiguity.
- Store raw timestamps verbatim (two-layer model) and compute the local-date bucket in the structured layer, so the bucketing rule can be fixed and re-derived without re-fetching.
- Write unit tests with edge cases: a 11pm run, a 5am run, a run while traveling across time zones, a night of sleep spanning a DST change.
- Document the convention in one place; every join and view references it.

**Warning signs:**
- A late-evening run appears on the wrong day in the daily summary.
- "Recovery today" seems to lag/lead training by exactly one day.
- Activities done while traveling land on odd dates.
- Sleep counts appear on two days or zero days.

**Phase to address:**
Storage & modelling phase (date spine + daily-summary view). This is the spine's defining decision; correctness here gates every analysis phase. Add timezone edge-case tests as a success criterion.

---

### Pitfall 7: Scheduled job fails silently (asleep machine, cron, no alerting)

**What goes wrong:**
The daily sync + analysis is the heartbeat of the product, but it runs unattended on a personal Mac. Common failures: (a) the Mac is **asleep** at the scheduled time and the job never fires; (b) it's scheduled with **cron**, which on macOS simply **skips** missed runs while asleep and runs in a minimal environment that often lacks the right `PATH`/`uv`/Python; (c) the job errors (Garmin auth broke, Strava 429, disk full) and nobody notices for weeks because nothing alerts. Result: silent data gaps and stale analyses that the owner trusts as current.

**Why it happens:**
- Using cron out of habit; cron doesn't catch up missed jobs and runs with a stripped environment.
- `launchd` runs missed `StartCalendarInterval` jobs on wake — but only if configured, and not if the machine was fully off.
- No success/failure notification, no "last successful sync" timestamp surfaced anywhere.
- The CLI assumes an interactive shell environment that the scheduler doesn't provide.

**How to avoid:**
- Prefer **`launchd`** (a LaunchAgent) over cron on macOS: it runs missed `StartCalendarInterval` jobs when the machine wakes. Optionally pair with `pmset` to schedule a wake, and wrap long pulls in `caffeinate` so the Mac doesn't sleep mid-sync.
- Make the job **idempotent and catch-up-aware**: on each run, sync everything since the last successful watermark, so a missed day is filled automatically next run rather than lost.
- **Surface health, don't fail silently.** Record `last_successful_sync` per source and have the daily analysis explicitly flag stale/missing data ("Garmin last synced 4 days ago") in its markdown report. Optionally a local notification on failure.
- Use absolute paths and an explicit environment in the launchd plist (full path to `uv`/the venv); never rely on the login shell's `PATH`.
- Capture stdout/stderr to a log file (launchd `StandardOutPath`/`StandardErrorPath`) so failures are diagnosable after the fact.

**Warning signs:**
- Analyses look unchanged day to day (data isn't actually updating).
- `last_successful_sync` is days old but no alert fired.
- The job works when run by hand in the terminal but never when scheduled (environment mismatch — classic cron symptom).

**Phase to address:**
Analysis & delivery / scheduling phase. The watermark + catch-up sync design belongs with the incremental-sync work; the launchd setup and staleness-surfacing belong with the scheduled-delivery work.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Skip the raw-response layer, write straight to structured tables | Less code, fewer tables | Can't add new metrics or fix bucketing without re-fetching from rate-limited APIs; lose ability to debug schema drift | Never — two-layer raw→structured is a core PROJECT.md decision and the main insurance against API changes |
| Re-login to Garmin every run instead of persisting tokens | Simpler auth code | Account lockout / 48h+ 429 block (Pitfall 2) | Never |
| Backfill in one non-resumable script | Fast to write | Crash or 429 wastes the day's budget and may never finish (Pitfall 5) | Acceptable only for a tiny test dataset, never for the real all-time pull |
| Store timestamps as parsed local datetimes only | Easy joins | Loses UTC offset / original value; bucketing bugs become unfixable without re-fetch | Never — keep raw timestamps; derive buckets in structured layer |
| Use cron because it's familiar | 1-line setup | Silent skips on sleep, stripped env, no catch-up (Pitfall 7) | Acceptable only if paired with catch-up sync + staleness alerting; launchd is strictly better on macOS |
| Commit reports with real health data to the public repo | Free hosting/history of insights | Permanent leak of personal health data in git history | Only if reports are sanitized; otherwise gitignore or keep outside tree |
| Pin garminconnect and never update | Stable today | Breaks silently when Garmin changes auth and you're on a dead version | Acceptable short-term IF you monitor upstream and budget bumps |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Strava OAuth | Treating refresh token as static | Persist the rotated refresh token after every refresh (atomic write); old token is invalidated immediately |
| Strava streams | Assuming a bulk export exists | None exists; ≥2 calls per activity; checkpoint per activity and respect rate-limit headers |
| Strava `start_date_local` | Parsing the trailing `Z` as UTC | It's wall-clock local with a fake `Z`; use it for the local-date bucket, keep `utc_offset`/`timezone` |
| Garmin auth | Logging in every run | Persist tokens (`~/.garminconnect/garmin_tokens.json`), reuse, re-login only on true expiry |
| Garmin 429 | Retrying login immediately | Stop and back off for hours; a 429 means account-level throttling that compounds |
| Garmin sleep/HRV | Bucketing by sleep start timestamp | Use Garmin's `calendarDate` for sleep/daily summaries (added specifically to kill this ambiguity) |
| Garmin library | Hard-coupling the whole pipeline to it | Isolate behind a connector interface; make failures non-fatal; keep a manual-export fallback |
| Public GitHub repo | Relying on `.gitignore` alone | Keep data/tokens outside the repo tree + a gitleaks pre-commit hook |

## Performance Traps

This is a single-user, batch-daily, local tool — "scale" means data volume over years, not concurrent users.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Re-fetching full history on each sync | Slow runs, rate-limit pressure | Incremental sync with a per-source watermark; backfill is a separate one-time path | Immediately once history > a few hundred activities |
| Concurrent SQLite writers (manual run + scheduled run) | `database is locked` errors, partial writes | Enable WAL mode; serialize writes / single-writer; file lock around sync | When a manual `tempo sync` overlaps the scheduled job |
| Recomputing all-time derived metrics (CTL/ATL trends) from scratch every analysis | Analysis gets slow as years accumulate | Materialize daily summaries; compute rolling metrics incrementally | After a few years of daily data (thousands of rows) — still small, but wasteful |
| Storing huge stream blobs unindexed and querying across all | Slow analysis queries | Keep raw streams as blobs/JSON but index the structured per-day aggregates that analyses actually read | When stream data spans years and analyses scan raw |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Committing tokens/secrets/DB/raw dumps to the public repo | Full access to owner's Strava data and Garmin account; permanent in git history | Data + tokens outside repo tree; gitleaks/git-secrets pre-commit hook; `.env.example` only |
| Token files with world-readable permissions | Other local users/processes read tokens | Store with 0600 (garminconnect already does); same for Strava token file |
| Logging full API responses (with PII/health data) to a log file in the repo | Health data leak via logs | Log metadata/counts, not bodies; keep logs outside the tree |
| Committing Claude-written reports containing health data | Personal health data published | Decide reports policy: sanitize, gitignore, or store outside repo |
| Not deleting data on revocation | Strava API Agreement requires deleting a user's data if access is revoked | Personal single-user use makes this low-risk, but be aware the Agreement also prohibits feeding Strava data to AI models — relevant since Claude analyzes it (review terms; personal/non-shared use is the safer reading) |

## UX Pitfalls

Single-user, but "the user" still gets burned by these.

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Analysis reports don't flag stale/missing data | Owner trusts a "race readiness" report built on data that stopped updating a week ago | Every report header states per-source last-sync date and flags staleness |
| MFA / re-auth happens inside the scheduled job | Job hangs waiting for a code nobody enters, or fails the whole run | Separate interactive `tempo garmin login` command for re-auth; scheduled job never prompts |
| Silent partial sync (Garmin failed, Strava ok) reported as success | Owner thinks data is complete | Per-source status surfaced; partial success clearly labeled |
| Unit confusion in reports (meters vs km, m/s vs min/km) | Misleading paces/distances | Convert units once in the structured layer with documented canonical units; test conversions |

## "Looks Done But Isn't" Checklist

- [ ] **Strava token refresh:** Works once — but does it persist the *rotated* refresh token and survive a second-day run without re-auth? Verify by running twice across a 6h+ gap.
- [ ] **Garmin auth:** Logs in interactively — but does the scheduled run *reuse saved tokens* without a fresh login? Verify the daily run makes no login network call.
- [ ] **Backfill:** Pulls activities — but is it resumable after a `429`/crash and idempotent on re-run? Verify by killing it mid-run and restarting.
- [ ] **Date spine:** Joins data — but does a 11pm run and an overnight sleep land on the correct local day? Verify with explicit edge-case rows.
- [ ] **Scheduled job:** Runs in terminal — but does it fire via launchd with the right PATH/env, and catch up a missed day after sleep? Verify by sleeping the Mac through a scheduled time.
- [ ] **Reports:** Generate markdown — but do they state data freshness and flag a failed source? Verify by breaking one connector and reading the report.
- [ ] **Secrets:** `.gitignore` set — but is there a pre-commit hook, and are data/tokens *outside* the repo tree? Verify with `git add -A` from repo root and confirm nothing sensitive stages.
- [ ] **Re-derivation:** Structured tables exist — but can they be rebuilt from stored raw data without hitting the APIs? Verify by dropping structured tables and re-deriving.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Secret committed to public repo | HIGH | (1) Immediately rotate/revoke the secret at the source (Strava app settings; Garmin password change for token compromise). (2) Rewrite history with `git filter-repo` / BFG and force-push. (3) Accept it may already be cloned/forked — rotation is the only real fix; history rewrite is cleanup. |
| Garmin account 429-locked | MEDIUM (time) | Stop all login attempts; wait out the 48h+ block (do NOT retry — retries extend it). Then switch to token-persistence so it can't recur. |
| Garmin library broken by auth change | MEDIUM | Bump to a patched library version once upstream fixes it; in the meantime mark Garmin sync degraded and fall back to manual FIT/CSV export. Raw-layer design means no data is lost, just delayed. |
| Lost/dead Strava refresh token | LOW | Re-run the one-time OAuth browser flow; fix persistence so it doesn't recur. No data lost. |
| Backfill burned the daily budget | LOW | Resume next day from the checkpoint; ensure checkpointing exists so no re-fetch. |
| Wrong-day bucketing discovered late | MEDIUM | Because raw timestamps are stored, fix the bucketing rule and re-derive the structured layer — no re-fetch. This is exactly why the two-layer model matters. |
| Missed scheduled runs (sleep/cron) | LOW | Catch-up sync fills the gap on next run from the watermark; switch cron→launchd to prevent recurrence. |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Secret/data leak in public repo | Foundation (repo + credential scaffolding) | `git add -A` stages nothing sensitive; gitleaks hook installed; DB/tokens outside tree |
| Strava refresh-token rotation | Foundation / Strava ingestion | Two runs >6h apart succeed without re-auth |
| Strava backfill rate limit / resumability | Strava ingestion (backfill design) | Kill mid-backfill, restart, no re-fetch, completes across days |
| Date-spine timezone bucketing | Storage & modelling (date spine + daily view) | Edge-case tests: 11pm run, overnight sleep, DST, travel all bucket correctly |
| Garmin account lockout (login reuse) | Garmin ingestion | Scheduled run makes no login call; 429 triggers backoff not retry |
| Garmin library fragility / isolation | Architecture (connector seam) + Garmin ingestion | Garmin failure doesn't abort Strava sync or analysis; manual-export fallback documented |
| Scheduled job silent failure | Scheduling / delivery | launchd catches up after sleep; reports show last-sync + staleness flags |
| Unit/correctness in metrics | Analysis phases | Conversion tests; canonical units documented and asserted |

## Sources

- Strava Rate Limits (official): https://developers.strava.com/docs/rate-limits/ — HIGH
- Strava Authentication (official, token expiry/rotation): https://developers.strava.com/docs/authentication/ — HIGH
- Strava API Agreement (data deletion, AI-use restriction): https://www.strava.com/legal/api and https://press.strava.com/articles/updates-to-stravas-api-agreement — HIGH
- Strava API developer guide (streams ≥2 calls/activity, no bulk export, backfill cost): https://openwearables.io/blog/strava-api-developer-guide-activities-heart-rate-gps-data — MEDIUM
- Strava `start_date_local` fake-`Z` timezone gotcha: https://groups.google.com/g/strava-api/c/vmJUsW-srdM — MEDIUM
- python-garminconnect repo (v0.3.4, mobile SSO, token storage 0600, MFA callback): https://github.com/cyberjunky/python-garminconnect — HIGH
- garminconnect 429 / account-level lockout / login-rate-limit issues: https://github.com/cyberjunky/python-garminconnect/issues/213 , /issues/344 , /issues/127 ; https://forums.garmin.com/developer/fit-sdk/f/discussion/435087 — HIGH
- garth DEPRECATED (2026-03-27, Garmin auth change broke mobile flow): https://github.com/matin/garth/discussions/222 ; https://github.com/matin/garth — HIGH
- garmin-health-data (curl_cffi TLS impersonation, Cloudflare WAF, deliberate 30–45s delays): https://github.com/diegoscarabelli/garmin-health-data — MEDIUM
- Garmin `calendarDate` for sleep/daily summaries (overnight day-attribution): https://developerportal.garmin.com/blog/new-field-calendardate-sleep-and-daily-summaries — HIGH
- macOS launchd vs cron, sleep behavior, WakeSystem/pmset/caffeinate: https://www.josephspurrier.com/macos-sleep-cron ; https://deniapps.com/blog/scheduling-a-cron-job-on-macos-with-wake-support ; Apple Scheduling Timed Jobs (developer.apple.com archive) — MEDIUM/HIGH
- SQLite migrations (limited ALTER TABLE, transactional rebuilds) + timezone handling (store UTC, app-layer date logic): https://www.schemalens.tech/blog/sqlite-schema-migration-best-practices.html ; https://blog.sqlite.ai/handling-timestamps-in-sqlite — MEDIUM
- Removing secrets from git history / rotate-first (BFG, git filter-repo): https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository — HIGH

---
*Pitfalls research for: personal Strava + Garmin → SQLite training-data pipeline with scheduled Claude analysis (public code-only repo)*
*Researched: 2026-05-26*
