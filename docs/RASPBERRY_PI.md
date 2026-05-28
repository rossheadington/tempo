# Raspberry Pi 5 deployment

Long-term home for RunOS: a Raspberry Pi 5 running 24/7 on the local network,
talked to via the Telegram bot. This file holds the non-secret connection
details and the current state of the Pi-side install. The password lives in
`.env` (gitignored) as `PI_SSH_PASSWORD` — never put it here.

## Host

| Field | Value |
|-------|-------|
| Hostname | `raspberrypi5.local` (mDNS on the LAN) |
| User | `rossheadington` |
| OS | Debian GNU/Linux 13 (trixie), aarch64, kernel 6.12 |
| System Python | 3.13.5 |
| Project path | `/home/rossheadington/Repos/tempo` |
| Venv | `venv/` inside the project path (planned — see gaps below) |

## SSH

```bash
# Interactive
ssh rossheadington@raspberrypi5.local

# Scripted (password in .env)
sshpass -p "$PI_SSH_PASSWORD" ssh rossheadington@raspberrypi5.local '<command>'
```

Long-term: replace password auth with a key (`ssh-copy-id rossheadington@raspberrypi5.local`)
once a key exists on the laptop. Until then `sshpass` (via Homebrew) is required
for scripted access.

## Pull latest code

```bash
sshpass -p "$PI_SSH_PASSWORD" ssh rossheadington@raspberrypi5.local \
  'cd /home/rossheadington/Repos/tempo && git pull --ff-only'
```

## Runtime gaps (v1.2 work, NOT done by a clone)

The repo cloning step is the easy part. To actually run RunOS on the Pi you
also need:

1. **Python 3.14** — system Python is 3.13. Install via `uv python install 3.14`
   once `uv` is on the Pi, or via deadsnakes / pyenv.
2. **`uv` package manager** — not installed. `curl -LsSf https://astral.sh/uv/install.sh | sh`.
3. **Project venv** — `uv sync` inside the project. Will fail until (4) is sorted.
4. **`curl_cffi` ARM wheel** — the Garmin connector depends on it for Cloudflare
   bypass. Confirm an `aarch64` / `manylinux` wheel exists for the current
   `curl_cffi` version, or be ready to build from source (needs `libcurl-impersonate`
   build deps).
5. **`.env` + `~/.runos/` contents** — Strava + Garmin tokens, Telegram bot token,
   `races.md` / `heat.md`. These are all gitignored and must be copied across
   (e.g. `rsync -av ~/.runos/ rossheadington@raspberrypi5.local:~/.runos/`),
   minus anything you'd rather regenerate on the Pi (OAuth re-auth is fine).
6. **systemd units** — replace the macOS launchd plists (`com.runos.daily.plist`,
   `com.runos.telegram-bot.plist`) with systemd services. `runos/scheduler.py`
   currently only renders launchd; needs a Linux path. The bot service needs
   `Restart=always` to mirror the launchd `KeepAlive`.
7. **faster-whisper on ARM** — works on CPU, but `small.en` int8 inference is
   slower on the Pi than on the laptop. Worth benchmarking before assuming
   voice memos stay under Telegram's "typing…" patience.

None of this is wired up yet — that's the v1.2 milestone in `.planning/ROADMAP.md`.
A `git clone` is step zero, not step done.
