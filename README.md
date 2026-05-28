# RunOS

Personal, local-first training and health data pipeline. Pulls running and
wellness data from Strava and Garmin into a structured SQLite store, captures
subjective trackers in markdown, and exposes the whole thing through a
Telegram bot driven by a Claude Code agent loop.

Single-user by design. If you train seriously and want your own coach that
actually knows your data, this might be for you.

## How it runs

A single always-on machine (Linux with `systemd`, or macOS with `launchd`) runs two services:

- **`runos-sync`** — hourly Strava + Garmin pull into the SQLite raw store, transforms downstream, notifies on failure.
- **`runos-bot`** — long-running Telegram bot. Voice memos transcribed locally with `faster-whisper`; text + transcripts routed to a Claude Code agent that has filesystem access to the project + trackers.

Reports (`recovery`, `load-trend`, `race-readiness`, `correlations`, `nutrition`)
are generated on demand by asking the bot, not on a schedule. A Raspberry Pi 5
is plenty; a laptop that's mostly on works too.

## What it tracks

| Data | Where |
|---|---|
| Strava activities (runs, rides, swims, anything) | `activity` table in SQLite (auto via hourly sync) |
| Garmin wellness (HRV, sleep, resting HR, body battery, stress) | `wellness_day` table in SQLite (auto via hourly sync) |
| Subjective journal entries (RPE, feel, notes per session) | `journal` table in SQLite |
| Heat sessions (sauna, hot tub) | `training/heat.md` |
| Strength sessions | `training/strength.md` |
| Weight readings | `training/weight.md` |
| Food / macros | `training/food.md` |
| Races (planned + completed) | `training/races.md` |
| Personal physiology + units + nutrition target | `training/preferences.md` |

## Prerequisites

You'll need accounts and tooling before `runos setup`:

**Tooling**
- Python 3.14+
- [`uv`](https://docs.astral.sh/uv/) (package manager)
- Node.js 18+ and the [`claude` CLI](https://docs.anthropic.com/en/docs/claude-code/setup) — logged in via `claude auth login` on the host where the bot runs

**Accounts you create yourself**
- A [Strava API application](https://www.strava.com/settings/api) (free; gives you a client ID + secret for your own data)
- A Telegram bot via [@BotFather](https://t.me/BotFather) (free; one-time token)
- A Claude subscription (for the agent loop — Pro or Max plan)
- *Optional:* Garmin Connect account for wellness data

**Host**
- macOS or Linux with systemd. Windows is not supported.
- Always on (or close to it) if you want the hourly sync + 24/7 bot.

## Local development

```bash
git clone https://github.com/rossheadington/RunOS
cd RunOS
uv sync
cp .env.example .env  # fill in Strava/Garmin/Telegram creds
uv run runos setup    # interactive wizard handles DB init, OAuth, schedulers
```

Tests: `uv run pytest tests/ --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty`

## Stack

Python 3.14 · `uv` · raw `sqlite3` (no ORM) · pure-stdlib analysis · `stravalib` ·
`garminconnect` (via `curl_cffi`) · `python-telegram-bot` · `faster-whisper` ·
`claude-agent-sdk`.

## Docs

| Topic | File |
|---|---|
| First-run setup walkthrough | [`docs/SETUP.md`](docs/SETUP.md) |
| Telegram bot setup | [`docs/TELEGRAM_BOT.md`](docs/TELEGRAM_BOT.md) |
| Raspberry Pi deployment notes | [`docs/RASPBERRY_PI.md`](docs/RASPBERRY_PI.md) |
| Privacy contract | [`docs/PRIVACY.md`](docs/PRIVACY.md) |
| Tracker formats | [`docs/STRENGTH.md`](docs/STRENGTH.md) · [`docs/WEIGHT.md`](docs/WEIGHT.md) · [`docs/NUTRITION.md`](docs/NUTRITION.md) · [`docs/PREFERENCES.md`](docs/PREFERENCES.md) · [`docs/JOURNALING.md`](docs/JOURNALING.md) |
| Date-bucketing invariant | [`docs/DATE_BUCKETING.md`](docs/DATE_BUCKETING.md) |
| Engineering conventions | [`ENGINEERING.md`](ENGINEERING.md) |

## Privacy

This repo holds **code only**. Credentials, tokens, the SQLite DB, voice cache,
generated reports, and all tracker `.md` files are gitignored and stay on
whatever machine you run it on. Non-negotiable — see [`docs/PRIVACY.md`](docs/PRIVACY.md).

## License

MIT — see [`LICENSE`](LICENSE).
