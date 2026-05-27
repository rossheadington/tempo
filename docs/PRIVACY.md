# Tempo privacy contract

Tempo is a **single-user, local-first** tool. The owner of the codebase is
also the only end-user. The repository is public, but every byte of personal
data — runs, heart-rate streams, sleep, HRV, body battery, journal entries,
voice memos, Telegram conversations, Claude Code session ids — lives **only
on the operator's laptop** and is gitignored from the first commit.

This document is the user-facing version of that contract. It exists so
the operator can audit what Tempo touches and so the constraint stays
visible across phases.

## What stays on the laptop, always

| Data | Where it lives | Notes |
| --- | --- | --- |
| Strava raw + structured rows | `~/.tempo/tempo.db` (SQLite) | Pulled via the user's own Strava API app. Never shared. |
| Garmin wellness rows | `~/.tempo/tempo.db` (SQLite) | Token-only access via `garminconnect`; tokens at `~/.tempo/tokens/garmin` (mode 0600). |
| Strava + Garmin tokens | `~/.tempo/tokens/{strava,garmin}` | Atomic rotating token store, mode 0600. |
| Journal entries (RPE, feel, notes, sRPE) | `journal` table in `~/.tempo/tempo.db` | Written ONLY through the validated `tempo journal add` boundary. |
| `races.md`, `plan.md`, `heat.md` | `~/.tempo/` | Free-form markdown the user authors directly. |
| Analysis reports | `~/.tempo/reports/*.md` | Dated markdown produced by `tempo analyze`. |
| Telegram bot token | `.env` (mode 0600, gitignored) | A full-access credential for the bot itself; rotate via `/revoke` to @BotFather if leaked. |
| Owner chat id | `.env` | Plain integer; not secret on its own, but pairs with the token to define the allowlist. |
| Voice memos (raw `.ogg`) | `<voice_cache_dir>/` (default: `~/.tempo/voice/`) | Subject to the `VOICE_RETENTION_DAYS` policy (see below). |
| Voice transcripts | Logged at INFO to stdout / launchd log file at `logs/tempo-bot.{out,err}.log` | Never sent to a third party. |
| Claude Code session ids | `bot_session` table in `~/.tempo/tempo.db` | Per-chat, 4-hour resume window. Send `/new` to reset. |
| Claude Code conversation history | Managed by the `claude` CLI (outside Tempo) | Tempo passes prompts to the local Claude Code subprocess via the user's existing `claude login`. |
| launchd logs | `logs/tempo-{daily,bot}.{out,err}.log` (gitignored) | INFO-level structured logs. May contain transcripts, agent replies, and the per-turn `tokens_in / tokens_out / cost / wall` figures. |

Everything in `~/.tempo/` is created with directory mode 0700 on first
run. Everything in `.env` is required to be mode 0600 by the README setup
steps. The repository's `.gitignore` blocks every one of these paths
defensively even if the operator ever moves them inside the repo tree.

## What leaves the laptop, and to whom

Three third parties touch Tempo data, and only the parts they need:

1. **Strava** — Tempo authenticates as the user's own Strava developer
   app (created at <https://www.strava.com/settings/api>). Tempo reads
   the user's activities and streams; it never writes to Strava and
   never reads other users' data. The Strava API Agreement is documented
   as an accepted conflict for this private, single-user, non-shared use
   (see `README.md` and `.planning/REQUIREMENTS.md` "Known Accepted
   Conflicts").
2. **Garmin** — Tempo authenticates via `garminconnect` against the
   user's own Garmin Connect account, using TLS-impersonated mobile-app
   SSO. Tokens are reused after a one-time `tempo garmin login` so the
   credential flow happens once. Tempo reads wellness data (sleep, HRV,
   body battery, resting HR, stress, steps); it never writes to Garmin.
3. **Anthropic (via Claude Code)** — when a Telegram voice memo or text
   message routes through the agent loop, the prompt (the transcript or
   the message text) and the session-resumed conversation are sent to
   Anthropic by the local `claude` CLI subprocess. Tempo deliberately
   does NOT pass an `ANTHROPIC_API_KEY` and does NOT call Anthropic
   directly; the auth path is the user's `claude login` (Claude Code
   subscription). The raw `.ogg` audio is **never** uploaded to
   Anthropic — Whisper transcription happens locally, on-device.

No other network egress happens in normal operation. The daily
`tempo run-daily` job hits Strava + Garmin only. The bot worker hits
Telegram (long-poll) + the local `claude` subprocess only.

## Voice retention policy

The Telegram bot transcribes voice memos locally with `faster-whisper`
(no audio bytes leave the laptop). Once the transcript exists, the raw
`.ogg` is governed by `VOICE_RETENTION_DAYS` in `.env`:

* `VOICE_RETENTION_DAYS=0` (default — **privacy-safe**): the `.ogg` is
  deleted immediately after the transcript flows to the agent, in the
  `finally` block of `voice_handler`. The audio never persists past the
  agent turn.
* `VOICE_RETENTION_DAYS=N` for N > 0: the `.ogg` stays on disk for N
  days. A startup sweep in `tempo/bot/app.py::_post_init` deletes any
  file in `<voice_cache_dir>/` whose mtime is older than `N * 86400`
  seconds, so a long-running bot under launchd cannot accumulate
  unbounded audio across restarts. Use this only if you need to debug
  Whisper misfires; switch back to `0` once you're done.

You can also flush the cache manually at any time:

```bash
uv run tempo bot purge-voice         # asks for confirmation
uv run tempo bot purge-voice --yes   # non-interactive
```

`purge-voice` deletes every file under `<voice_cache_dir>/` regardless
of the retention setting; the bot itself doesn't have to be running.

## The repository is public; the data is not

The repo is published openly so the code can be reviewed and so the
methodology (load math, baselining, scheduling, the journal contract) is
inspectable. The privacy invariants that keep that safe:

* **No credentials in git.** `.env`, `~/.tempo/`, `.tempo/`, and `logs/`
  are gitignored. `.githooks/pre-commit` runs `gitleaks` on every
  staged change; the commit fails loudly if gitleaks is missing.
* **No fixtures with personal data.** Test fixtures use synthetic
  Strava/Garmin payloads (`tests/strava_fakes.py`, `tests/garmin_fakes.py`)
  and synthetic Telegram updates. No recorded VCR cassettes contain
  real account data.
* **Tokens are 0600.** The token store writes atomically with mode 0600,
  and `.env` is documented to be set to 0600 before any secret is
  written.
* **The repository never reads `~/.tempo/`.** All paths under the data
  dir are resolved at runtime from `Settings.data_dir`; nothing in the
  repo refers to a hard-coded user path.

## If a credential leaks

* **Strava** — revoke at <https://www.strava.com/settings/apps>, then
  rerun `tempo strava auth` to generate a new token pair.
* **Garmin** — change the Garmin Connect account password; delete
  `~/.tempo/tokens/garmin`; rerun `tempo garmin login`. Garth's per-account
  429 rate limit means **do not** loop on retry — if a `tempo garmin
  login` fails with 429, wait it out (15–60 min).
* **Telegram bot token** — send `/revoke` to @BotFather in Telegram; the
  old token dies immediately. Update `.env` with the new token, restart
  `tempo bot run`.
* **Claude Code subscription** — `claude logout` followed by
  `claude login` rotates the subscription auth out-of-band; Tempo
  inherits the new credentials on the next bot turn because every turn
  reads the live `claude` CLI auth state.

## What's NOT private

* **Aggregate metrics in reports** are intended to be shared with the
  user themselves — that's the point. If the user chooses to copy a
  report into a coaching conversation, that's their call.
* **The bot's existence** is observable to anyone who messages the bot's
  Telegram username (they'll see "this account doesn't accept messages
  from you" or similar). The allowlist drops their messages silently at
  the dispatcher; the bot never replies to non-owner chats.
* **Code, prompts, and Claude Code tool-call patterns** are public in
  this repository. The agent prompts are not personalised secrets — they
  shape behaviour, not identity.

## See also

* `README.md` — the top-level setup instructions, including the
  `chmod 600 .env` step.
* `docs/TELEGRAM_BOT.md` — Telegram bot setup, launchd lifecycle,
  troubleshooting.
* `docs/JOURNALING.md` — the validated journal-write contract (Claude
  never writes SQL).
* `.planning/REQUIREMENTS.md` "Known Accepted Conflicts" — the explicit
  record of the Strava API Agreement decision for private, single-user
  use.
