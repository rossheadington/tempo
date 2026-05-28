# RunOS First-Run Setup

Single user. Local-first. Mac (for now). This doc walks you from a fresh
clone to a working `runos run-daily` (and optionally a running Telegram
voice/text bot) end-to-end.

There are two paths:

- **One command (recommended):** `runos setup` — interactive wizard, ~5
  minutes, picks up where it left off if you Ctrl-C.
- **Manual (advanced):** the same 10 steps run one at a time. Useful if
  you want to understand each layer, or if you're recovering from a
  partial install.

Both paths produce the same final state: a SQLite DB with the latest
schema, Strava OAuth complete, optional Garmin login complete, optional
Telegram bot creds in `.env`, optional launchd jobs in
`~/Library/LaunchAgents/`, and a successful `runos sync` confirming
the pipeline works.

## One command

Prerequisites:

- Python ≥ 3.14 (`python --version`)
- `uv` on PATH (`brew install uv` if missing)
- A Strava account and the API page at <https://www.strava.com/settings/api>
  ready to receive a new app
- **Optional:** a Garmin Connect account (for HRV / sleep / resting-HR data)
- **Optional:** a Telegram account (for the voice/text bot)

Then:

```bash
git clone https://github.com/rossheadington/RunOS
cd RunOS
uv sync                        # install deps into .venv
uv run runos setup             # run the wizard
```

The wizard walks you through 10 steps. Each step is skippable; each step
is idempotent (re-running is safe). Press Ctrl-C at any prompt to abort
— your `.env` and DB are left in a consistent state and a re-run picks
up where you left off.

## Manual setup

Below is the same flow, step-by-step, for power users who prefer to drive
each layer by hand (or who are debugging a stuck install).

### 1. Welcome / tooling check

**What it does:** prints a banner, checks Python ≥ 3.14, checks `uv` is
on PATH, prints a one-line snapshot of what's already installed (e.g.
`Detected: DB ✓ · Strava ✓ · Garmin ✗ · Telegram ✗ · daily-scheduler ✗ · bot-scheduler ✗`).

**Wizard prompts:** none.

**Files written:** none.

**Manual equivalent:**

```bash
python --version       # ≥ 3.14
which uv               # any abs path
```

**Skip:** can't be skipped (always runs; warns rather than fails if `uv`
is missing — `python -m runos` works fine as a fallback).

**Recover:** install Python 3.14+ from <https://www.python.org/downloads/>
or via `uv python install 3.14`. Install `uv` with `brew install uv`.

### 2. DB init

**What it does:** creates `~/.runos/` (or wherever `RUNOS_DATA_DIR`
points), opens / creates the SQLite DB in WAL mode, applies all
migrations up to the current schema version.

**Wizard prompts:** none in a fresh-install run (skipped with `[done]` if
the DB is already at the latest schema; offers a confirm re-run only
under `--only=db`).

**Files written:** `~/.runos/runos.db` (WAL on, FK on).

**Manual equivalent:**

```bash
uv run runos init
```

**Skip:** not a sensible skip (the rest of RunOS can't work without the
DB). The wizard's `[done]` short-circuit handles the "already done"
case.

**Recover:** delete `~/.runos/runos.db` and re-run
`runos setup --only=db` (or `runos init` manually). Migrations are
idempotent so a re-run on a partially-migrated DB also recovers.

### 3. Content directory

**What it does:** picks where your markdown trackers live (`races.md`,
`heat.md`, `strength.md`). Default is `~/.runos/`; the wizard suggests
`<project>/training/` if it detects you're inside the RunOS source tree.

**Wizard prompts:**

1. `Content dir path` (default = `~/.runos`)

**Files written:** `RUNOS_CONTENT_DIR=<chosen>` is appended to `.env`
via the atomic-write helper (0600 perms). The directory is created with
0700 perms if it doesn't already exist.

**Manual equivalent:** edit `.env` and add
`RUNOS_CONTENT_DIR=/path/to/dir`; then
`mkdir -p "$RUNOS_CONTENT_DIR" && chmod 700 "$RUNOS_CONTENT_DIR"`.

**Skip:** decline the prompt (accept the default) to leave
`RUNOS_CONTENT_DIR` unset — the default `~/.runos/` is used.

**Recover:** re-run `runos setup --only=content` to change the choice.
Existing tracker files stay where they were; move them manually if you
change the dir.

### 4. Strava credentials + OAuth

**What it does:** the only step that requires you to leave the terminal:
you open <https://www.strava.com/settings/api> in a browser, create an
app (callback domain = `localhost`), then paste the Client ID and Client
Secret back into the wizard. The wizard writes them to `.env` BEFORE
starting the OAuth handshake (so a partial-OAuth failure leaves the
creds for retry), then walks you through `authorization_url` →
"paste the `code` parameter" → `exchange_code`.

**Wizard prompts:**

1. `Strava Client ID` (visible)
2. `Strava Client Secret` (hidden — `getpass`-style; not echoed back)
3. Browser opens the auth URL automatically (macOS) or prints the URL
   (headless)
4. `Paste the 'code' parameter from the redirect URL` (visible)

**Files written:** `RUNOS_STRAVA_CLIENT_ID` +
`RUNOS_STRAVA_CLIENT_SECRET` appended to `.env` (0600). On successful
OAuth, `~/.runos/tokens/strava_tokens.json` is created atomically
(rotating refresh token persisted).

**Manual equivalent:**

```bash
# Edit .env to add RUNOS_STRAVA_CLIENT_ID and RUNOS_STRAVA_CLIENT_SECRET, then:
uv run runos strava auth                       # prints the URL
uv run runos strava auth --code <CODE>         # completes the handshake
```

**Skip:** you can't sensibly skip Strava — it's the load-bearing data
source. Decline-and-revisit means re-running
`runos setup --only=strava` later.

**Recover:** wizard prints the `runos setup --only=strava` remediation
on any OAuth failure. The creds you typed are preserved in `.env`, so a
retry only needs the auth URL → code paste. If the code expired (Strava
codes are single-use and short-lived), just re-open the auth URL and
grab a fresh `code`.

### 5. Garmin login (optional)

**What it does:** captures your Garmin email + password (in `.env`,
0600), then runs the one-time `garmin_login` flow which prompts for an
MFA code if Garmin asks. On success, Garmin session tokens are persisted
under `~/.runos/tokens/garmin/`. Every later `runos sync` reuses these
tokens — no fresh login (this is what keeps you clear of Garmin's
per-account 429 lockout).

**Wizard prompts:**

1. `Do you also use Garmin?` (Y/n, default Y)
2. `Garmin email` (visible)
3. `Garmin password` (hidden)
4. `Garmin MFA code` (visible — prompted only if Garmin requires it)

**Files written:** `RUNOS_GARMIN_EMAIL` + `RUNOS_GARMIN_PASSWORD`
appended to `.env`; `~/.runos/tokens/garmin/` directory created with
session tokens.

**Manual equivalent:**

```bash
# Edit .env to add RUNOS_GARMIN_EMAIL and RUNOS_GARMIN_PASSWORD, then:
uv run runos garmin login
```

**Skip:** `runos setup --skip-garmin`, or decline the wizard's first
Garmin prompt. RunOS runs fine on Strava-only data; some analyses (HRV
z-score, resting-HR trend, sleep correlation) degrade to "insufficient
data" without Garmin.

**Recover:** if `garmin_login` fails repeatedly with 429s, **STOP**.
Wait at least a few hours. Garmin's per-account lockout compounds with
every login attempt. Re-run `runos setup --only=garmin` only after the
wait. RunOS itself never retries a Garmin 429 internally for exactly
this reason.

### 6. Telegram bot credentials (optional)

**What it does:** captures the Telegram bot token (from @BotFather) and
your numeric chat id (from @userinfobot), writes them to `.env`.
Optionally lets you tweak the Whisper model defaults and the voice-file
retention window.

**Wizard prompts:**

1. `Do you want to run the Telegram voice/text bot?` (y/N, default N)
2. Instruction block: @BotFather + @userinfobot steps printed verbatim
3. `Telegram bot token` (hidden)
4. `Your Telegram chat id (numeric)` (visible)
5. `Change Whisper model defaults?` (y/N, default N) — if Y, prompts
   for `WHISPER_MODEL_NAME` / `WHISPER_COMPUTE_TYPE` / `WHISPER_DEVICE`
6. `Change voice retention?` (y/N, default N) — if Y, prompts for
   `VOICE_RETENTION_DAYS`

**Files written:** `TELEGRAM_BOT_TOKEN` + `TELEGRAM_OWNER_CHAT_ID`
appended to `.env` (note: these are **bare** names, not
`RUNOS_`-prefixed — see `.env.example`). Optional Whisper /
voice-retention keys are appended only if you explicitly opted to change
them.

**Manual equivalent:** see [`docs/TELEGRAM_BOT.md`](TELEGRAM_BOT.md) for
the full @BotFather + @userinfobot walkthrough (and the troubleshooting
list for 409 Conflict, token rotation, allowlist sanity check).

**Skip:** `runos setup --skip-telegram` (implies
`--skip-bot-scheduler` — the bot launchd step is only offered when
Telegram is configured).

**Recover:** re-run `runos setup --only=telegram`. Existing values are
shown as `[set]` with a keep/change/skip choice; secrets are NEVER
echoed back, even on re-run.

### 7. Daily-sync launchd scheduler (optional)

**What it does:** renders `~/Library/LaunchAgents/com.runos.daily.plist`
with absolute paths to `uv`, the project root, and the IANA timezone.
`runos run-daily` then fires every day at the chosen local hour:minute
(via `StartCalendarInterval`, which — unlike cron — runs a MISSED job
on wake).

**Wizard prompts:**

1. `Install the daily-sync launchd job?` (Y/n, default Y)
2. `Hour (0-23)` (default 5)
3. `Minute (0-59)` (default 30)

**Files written:** `~/Library/LaunchAgents/com.runos.daily.plist` (the
plist is rendered from a committed template).

**RunOS does NOT load the plist for you.** It prints the
`launchctl bootstrap` line you need to run yourself — running launchctl
is an explicit human step (the same convention as
[`docs/TELEGRAM_BOT.md`](TELEGRAM_BOT.md) and `install-scheduler`):

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.runos.daily.plist
launchctl enable gui/$(id -u)/com.runos.daily
```

To unload later:

```bash
launchctl bootout gui/$(id -u)/com.runos.daily
```

**Manual equivalent:**

```bash
uv run runos install-scheduler --to-launch-agents --hour 5 --minute 30
```

**Skip:** `runos setup --skip-scheduler`. You can still run
`uv run runos run-daily` by hand any time.

**Recover:** delete `~/Library/LaunchAgents/com.runos.daily.plist`
(after `bootout`) and re-run `runos setup --only=scheduler`. If
`plutil -lint` fails on the rendered plist, the wizard catches it before
asking you to load it — a broken substitution can never reach launchd.

### 8. Telegram-bot launchd scheduler (optional)

**What it does:** renders
`~/Library/LaunchAgents/com.runos.telegram-bot.plist` with
`KeepAlive=true` so the bot survives crashes / sleep/wake / reboots.

**Wizard prompts:**

1. `Install the Telegram-bot launchd job?` (Y/n, default Y)

**Offer condition:** this step is OFFERED ONLY IF the Telegram step
completed in this run, OR Telegram was already configured before this
run started. Skipping or declining the Telegram step automatically
skips this one (locked behaviour).

**Files written:**
`~/Library/LaunchAgents/com.runos.telegram-bot.plist`.

**Launchctl invocation (you run this, not the wizard):**

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.runos.telegram-bot.plist
launchctl enable gui/$(id -u)/com.runos.telegram-bot
launchctl kickstart -k gui/$(id -u)/com.runos.telegram-bot
```

**Manual equivalent:**

```bash
uv run runos bot install-scheduler --to-launch-agents
```

**Skip:** `runos setup --skip-bot-scheduler`, or (implicitly)
`--skip-telegram`.

**Recover:** delete the plist (after `bootout`) and re-run
`runos setup --only=bot-scheduler`. See
[`docs/TELEGRAM_BOT.md`](TELEGRAM_BOT.md) "Always-on under launchd" for
the full operational runbook (install, kickstart, bootout, removal).

### 9. Smoke test

**What it does:** runs `runos sync` (Strava-then-isolated-Garmin)
IN-PROCESS — not as a subprocess — and prints a per-source status line:

```
Strava: ✓ (42 raw rows)
Garmin: skipped -- 429
```

A Garmin skip is NEVER terminal (Garmin is an isolated failure domain,
per project convention). A terminal Strava failure (auth-error, not
rate-limit) causes the wizard to exit with code 1 and points you at
`runos setup --only=strava` for recovery.

**Wizard prompts:** none.

**Files written:** rows into `raw_response` (whatever the sync pulls).

**Manual equivalent:**

```bash
uv run runos sync
```

**Skip:** `runos setup --skip-smoke`.

**Recover:** if Strava failed terminally, follow the remediation
printed by the wizard (typically: re-run `runos setup --only=strava`).
If Garmin skipped with a 429, wait a few hours and run
`uv run runos garmin sync` by hand.

### 10. Finish

**What it does:** prints a summary mirroring the welcome banner
(`Strava ✓ · Garmin ✓ · Telegram ✗ · daily-scheduler ✓ · bot-scheduler ✗`)
plus next-step hints: where your latest report lives
(`cat ~/.runos/reports/<latest>.md`), how to load the launchd plists
(`launchctl bootstrap …`), how to test the Telegram bot
(`uv run runos bot run`), and where to find this doc.

**Wizard prompts:** none.

**Files written:** none.

**Manual equivalent:** none (this is purely informational).

**Skip:** not applicable (the finish banner only prints if the wizard
ran at all).

## Re-running setup

`runos setup` is idempotent. You can re-run it any time:

- **Full re-run:** `runos setup`. Steps already done print `[done]`
  and are skipped. Steps not yet done prompt as usual. Steps in a
  partial state (creds set but no token, say) prompt with `[set]
  keep / change / fresh`.
- **Single step:** `runos setup --only=<step>`. Re-runs only the
  named step. Valid step ids: `db`, `content`, `strava`, `garmin`,
  `telegram`, `scheduler`, `bot-scheduler`, `smoke`. Stackable:
  `--only=telegram --only=bot-scheduler` runs both.

Typical re-run scenarios:

- Add the Telegram bot to an existing Strava-only install:
  `runos setup --only=telegram --only=bot-scheduler`
- Rotate Strava creds:
  `runos setup --only=strava`
- Move your content dir:
  `runos setup --only=content`
- Re-run the smoke test after fixing a network issue:
  `runos setup --only=smoke`

The wizard NEVER silently overwrites a non-empty `.env` value without
confirmation — this is what makes re-runs safe.

## Flags reference

| Flag | Effect |
|------|--------|
| `--only=<step>` | Run only the named step. Stackable. Valid: `db`, `content`, `strava`, `garmin`, `telegram`, `scheduler`, `bot-scheduler`, `smoke`. |
| `--skip-garmin` | Skip the Garmin login step. |
| `--skip-telegram` | Skip Telegram bot setup. **Implies `--skip-bot-scheduler`.** |
| `--skip-scheduler` | Skip daily launchd install. |
| `--skip-bot-scheduler` | Skip bot launchd install (but still set up bot creds). |
| `--skip-smoke` | Skip the final `runos sync` smoke test. |
| `--non-interactive` | Fail fast on any prompt (testing-only; not for end-user use). |

Exit codes:

- `0` — success (every non-skipped step completed without a terminal failure).
- `1` — a non-skipped step failed terminally (Strava auth error during smoke
  test, plist `plutil -lint` failure, etc.).
- `2` — Ctrl-C / Abort, or `--non-interactive` triggered an unanswerable prompt.

## Recovering from a failed step

Every step in the wizard is recoverable. The pattern:

1. Read the error message + the remediation hint the wizard printed.
2. Re-run with `--only=<step>` for the failing step.
3. If a credential is wrong, the wizard will re-prompt; type the
   correct value. The previous bad value is overwritten atomically.

A few specific recoveries:

- **Strava OAuth failed** (code expired, network blip): re-run
  `runos setup --only=strava`. The client_id + client_secret you
  typed earlier are still in `.env` — the wizard re-prompts only
  for the missing pieces (the auth URL + the new `code`).
- **Garmin login 429**: STOP. Wait at least a few hours. Garmin's
  per-account lockout compounds. Do NOT loop logins. RunOS itself
  never retries a Garmin 429 for exactly this reason.
- **Launchd plist won't load**: usually a `plutil -lint` issue; run
  `plutil -lint ~/Library/LaunchAgents/com.runos.daily.plist` to see
  the error. The wizard runs `plutil -lint` automatically on render
  so this should be rare.
- **Telegram bot 409 Conflict** on first run: another poller or a
  stale webhook holds the token. See
  [`docs/TELEGRAM_BOT.md`](TELEGRAM_BOT.md) "Troubleshooting" for
  the `deleteWebhook` curl one-liner.

## Privacy boundary

- `.env` is **0600 and gitignored**. The wizard's atomic-write helper
  (`runos/setup/env_io.py::atomic_write_env`) enforces 0600 on every
  write — temp file → fsync → `os.replace` → chmod 0600 → fsync parent
  dir, so a crash mid-write never leaves a corrupt or world-readable
  `.env`.
- Secret values entered at hidden-input prompts (Strava client
  secret, Garmin password, Telegram bot token) are NEVER echoed back
  to the terminal after entry, and are NEVER logged. On re-run, set
  secrets show as `[set]` — the value is never re-displayed.
- Token files (`~/.runos/tokens/strava_tokens.json`,
  `~/.runos/tokens/garmin/`) are written with the same atomic 0600
  pattern.
- Voice files (if you set up the Telegram bot) are transcribed
  locally via `faster-whisper` — audio bytes never leave the laptop.
  Transcripts + Claude Code tool calls flow through your existing
  Claude subscription. See [`docs/PRIVACY.md`](PRIVACY.md) for the
  full contract.

The authoritative list of every env var RunOS reads (with explanatory
comments) lives in `.env.example` at the repo root. The wizard's
prompts cover the subset a typical user needs; for advanced knobs
(rolling-baseline windows, ACWR thresholds, etc.) edit `.env`
directly.

## Uninstall

The wizard does not have an uninstall mode (deferred — single-user
local tool, manual `rm` is fine). The 4-step manual uninstall:

```bash
# 1. Stop the launchd jobs (if installed) FIRST so they don't hold files open.
launchctl bootout gui/$(id -u)/com.runos.daily || true
launchctl bootout gui/$(id -u)/com.runos.telegram-bot || true

# 2. Remove the plists.
rm -f ~/Library/LaunchAgents/com.runos.daily.plist
rm -f ~/Library/LaunchAgents/com.runos.telegram-bot.plist

# 3. Remove the data dir (DB, tokens, reports, voice cache).
rm -rf ~/.runos/

# 4. Remove the .env (creds).
rm -f .env
```

After this RunOS leaves no trace on the laptop beyond the cloned repo
itself (which is code-only). The Hugging Face Whisper model cache under
`~/.cache/huggingface/hub/` is shared with other tools and is left alone
— delete it manually if you want to reclaim the ~480 MB.

## Related docs

- [`docs/TELEGRAM_BOT.md`](TELEGRAM_BOT.md) — full Telegram bot setup +
  operational runbook (@BotFather + @userinfobot walkthrough, launchd
  lifecycle, voice cache retention, error handler, troubleshooting)
- [`docs/PRIVACY.md`](PRIVACY.md) — the authoritative privacy contract
  (what stays local, what leaves, per-credential leak-response)
- [`docs/DATE_BUCKETING.md`](DATE_BUCKETING.md) — local-date attribution
  rule (relevant if you're debugging a wrong-date activity)
- [`docs/JOURNALING.md`](JOURNALING.md) — the journal-entry contract for
  Claude / the bot
- `.env.example` — authoritative list of every env var RunOS reads,
  with explanatory comments
