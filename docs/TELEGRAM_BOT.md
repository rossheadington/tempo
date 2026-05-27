# Telegram bot (v1.1)

A personal, owner-only Telegram bot. Runs locally on your Mac via long-polling
(no public URL, no webhook, no port forwarding), accepts messages from a single
chat id you control, and silently drops everything else at the filter level.

This is the **v1.1 voice-coach intake**: Phase 9 wires the worker and the
allowlist; Phase 10 adds voice-memo download + local transcription; Phase 11
hands transcripts to a Claude Code agent loop; Phase 12 runs the whole thing
under launchd with a top-level error boundary and a configurable voice-cache
retention policy.

For the privacy contract (what data stays on the laptop, what leaves, and
the retention rules), see [`docs/PRIVACY.md`](PRIVACY.md).

## What this is

A long-running Python process talking to Telegram's Bot API via
[`python-telegram-bot`](https://docs.python-telegram-bot.org/) v22.x. It polls
for new messages from your account, ignores every other chat by design, and
(today) replies to `/start` with a fixed greeting. Future phases hang voice +
text handlers off the same allowlist.

## Prerequisites

- A Telegram account (the @BotFather flow happens entirely inside Telegram).
- Tempo configured and runnable (`uv run tempo --help` works).

## Step 1: Create the bot via @BotFather

1. Open Telegram, search for **@BotFather**, and `/start` it.
2. Send `/newbot`.
3. Choose a **display name** (e.g. "Tempo Coach"). This shows up in chat.
4. Choose a **username** ending in `bot` (e.g. `tempo_<yourname>_bot`).
5. @BotFather replies with the **HTTP API token** in the form
   `1234567890:AAH...`. **Treat this like a password.** Anyone with this token
   can fully impersonate the bot.

You can rotate the token any time with `/revoke` in @BotFather; the old token
dies immediately.

## Step 2: Add the token to .env

```bash
cp .env.example .env       # only if you don't already have a .env
chmod 600 .env             # owner read/write only -- do this BEFORE writing the token
```

Open `.env` and add:

```
TELEGRAM_BOT_TOKEN=1234567890:AAH...
```

Note: the env-var name is **NOT** prefixed with `TEMPO_`. The standard Telegram
convention is preserved (Tempo's `Settings` reads this bare name via a
`validation_alias`). The token is loaded as a `SecretStr` so it never appears
in logs or `repr(settings)`.

## Step 3: Find your owner chat id

1. Open Telegram, find the bot you just created (search its username), and
   send it any message -- e.g. `hi`.
2. In a browser, open:

   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

   (Replace `<TOKEN>` with the full token from Step 1.)
3. In the JSON, find `result[0].message.chat.id`. For a 1-on-1 chat it's a
   **positive integer** (groups are negative). If `result` is empty, send
   another non-command message to the bot and refresh.
4. Add this id to `.env`:

   ```
   TELEGRAM_OWNER_CHAT_ID=987654321
   ```

   (This name is also bare -- not prefixed with `TEMPO_`.)

## Step 4: Run the bot

```bash
uv run tempo bot run
```

Expected stdout:

```
... tempo.bot INFO Bot configured -- owner_chat_id=987654321, concurrent_updates=True
... tempo.bot INFO Bot started -- waiting for messages...
```

From your phone, send `/start` to the bot. Expected reply:

> Tempo bot online. Send a voice memo to journal a session, or text for any other request.

(Voice-memo handling lands in Phase 10. The greeting is the only handler this
phase ships.)

## Step 5: Sanity-check the allowlist

The most important property of this bot is **silent drop on non-owner**. To
verify it:

1. From a different Telegram account -- a second device, a family member's
   phone, or a friend who happens to have your bot's username -- send `/start`.
2. Expected behaviour: **nothing**. The bot does not reply. The terminal
   does not log a "start command received" line either (`filters.Chat` drops
   the update at the dispatcher level before any handler runs).

If you see a reply, stop the bot, double-check `TELEGRAM_OWNER_CHAT_ID` matches
the chat id of *your* account, and re-run.

## Stop the bot

`Ctrl-C` in the terminal. `python-telegram-bot`'s `run_polling()` handles
`SIGINT` / `SIGTERM` / `SIGABRT` by default: it stops the updater, drains
in-flight handler tasks, and shuts down cleanly. Under launchd (Phase 12),
the same process receives `SIGTERM` on `bootout` and shuts down on the same
code path.

## Always-on under launchd (Phase 12)

For unattended operation across reboots, sleep/wake cycles, and crashes the
bot runs as a `launchd` `LaunchAgent` with `KeepAlive=true`. Tempo writes
the plist for you and prints the manual `launchctl` commands; it never runs
`launchctl` itself.

```bash
uv run tempo bot install-scheduler        # writes ~/.tempo/launchd/com.tempo.telegram-bot.plist + prints next steps
cp ~/.tempo/launchd/com.tempo.telegram-bot.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tempo.telegram-bot.plist
launchctl kickstart -k gui/$(id -u)/com.tempo.telegram-bot
```

To stop / remove:

```bash
launchctl bootout gui/$(id -u)/com.tempo.telegram-bot
rm ~/Library/LaunchAgents/com.tempo.telegram-bot.plist
```

The plist sets:

* `KeepAlive=true` + `ThrottleInterval=10` -- the bot is restarted if it
  exits for any reason, but the 10-second throttle stops a fast crash loop
  from pinning CPU.
* `RunAtLoad=true` -- starts automatically on boot / `bootstrap`.
* `WorkingDirectory` -- the Tempo project root, so the agent's `cwd` is
  the repo (the bot logs the resolved cwd + `data_dir` at startup so you
  can see this in the launchd log).
* `StandardOutPath` / `StandardErrorPath` -- `logs/tempo-bot.out.log` and
  `logs/tempo-bot.err.log` under the project root (gitignored).

The template lives at
[`launchd/com.tempo.telegram-bot.plist`](../launchd/com.tempo.telegram-bot.plist);
`install-scheduler` substitutes `{{PROJECT_DIR}}`, `{{UV_PATH}}`, and
`{{PATH}}` into the user-specific copy under `~/.tempo/launchd/` and
`plutil -lint`s the result BEFORE asking you to copy it into
`~/Library/LaunchAgents/` -- a broken substitution can never reach
launchd. See `docs/PRIVACY.md` "What leaves the laptop, and to whom" for
what data the unattended worker touches.

## Voice cache retention

The bot transcribes voice memos locally with `faster-whisper` (audio never
leaves the laptop). Once the transcript flows to the agent, the raw `.ogg`
is governed by `VOICE_RETENTION_DAYS` in `.env`:

* `VOICE_RETENTION_DAYS=0` (**default, privacy-safe**) -- the `.ogg` is
  deleted in the handler's `finally` block immediately after the agent
  turn. The audio never persists on disk past one turn.
* `VOICE_RETENTION_DAYS=N` for N>0 -- the `.ogg` stays for N days.
  `tempo/bot/app.py::_post_init` sweeps `<voice_cache_dir>/` on every
  bot startup and deletes anything older than `N * 86400` seconds, so a
  long-running bot under launchd cannot accumulate unbounded audio
  across restarts. Use only when debugging Whisper misfires.

Manual purge any time (the bot does not need to be running):

```bash
uv run tempo bot purge-voice         # asks for confirmation
uv run tempo bot purge-voice --yes   # non-interactive (scripts / launchd)
```

`purge-voice` deletes every file under `<voice_cache_dir>/` regardless of
the retention setting.

## Error handler behaviour

The bot registers a single top-level error handler
(`tempo/bot/error_handler.py::telegram_error_handler`) on the PTB
`Application` via `add_error_handler`. Any exception raised by a
registered handler that the handler itself does not catch routes through
this boundary:

1. The full traceback is logged at ERROR (`Bot handler crashed: <repr>`)
   so the launchd log file (`logs/tempo-bot.err.log`) shows what
   actually failed.
2. A single fixed reply is sent back to the offending chat:

   > Sorry -- something went wrong on my end. Check the logs.

3. The handler never re-raises. If the reply itself fails (Telegram
   unreachable, chat blocked the bot, etc.), the failure is logged
   (`error reply failed: chat=... original=... reply_error=...`) and
   swallowed -- the worker stays up.

The Phase 11 `_run_agent_turn` still catches `AgentInvocationError`
specifically and replies with the more useful
`Claude Code isn't running. Try claude login in a terminal.` before the
top-level handler would ever see it. The top-level handler is the
last-line-of-defence for sqlite errors, faster-whisper crashes, OSError
on voice cache writes, and any internal PTB failure.

Combined with the launchd `KeepAlive=true` plist, this means a single
bad message can never take the worker down: at worst, the handler
boundary handles it; in the absolute worst case (the worker process
itself crashes hard), launchd restarts it within the 10-second throttle.

## Phase 11 prerequisites (Claude Code agent loop)

Phase 11 routes every voice memo and text message through Claude Code via the
`claude-agent-sdk` Python package, which spawns the `claude` Node CLI as a
subprocess and uses your existing Claude Code login. The bot does NOT use
`ANTHROPIC_API_KEY`.

Prerequisites:

1. **Node 18+ on PATH.** `brew install node` on macOS. The Tempo bot worker
   process must inherit a PATH that includes Node; under launchd (Phase 12),
   the LaunchAgent plist will need an `EnvironmentVariables.PATH` that finds
   it.
2. **The `claude` CLI must be installed and logged in.** Run `claude login`
   once in a terminal and complete the OAuth flow; the SDK reuses those
   credentials.
3. **The `claude-agent-sdk` Python package is installed by `uv sync`** (added
   to `pyproject.toml` in Plan 11-02). Nothing else to do here.

Quick check before starting the bot:

```bash
command -v claude || echo "claude CLI missing"
```

Auth precedence: if `ANTHROPIC_API_KEY` is set in the user's environment, the
SDK may prefer it over the Claude Code subscription credentials. Tempo's
invocation explicitly does NOT pass an API key; leave `ANTHROPIC_API_KEY`
unset for v1.1 so the bot uses your Claude subscription via `claude login`.

Session memory: the bot remembers the last 4 hours of conversation per chat
(resumed via the session id stored in the `bot_session` table). Send `/new`
to start a fresh session before the window expires.

## Phase 11: the agent loop

With Phase 11 wired (Plan 11-03), every voice memo and every non-command text
message from the owner chat routes through Claude Code via the
`claude-agent-sdk` Python package. For voice memos, the bot still transcribes
locally with `faster-whisper` first (so the audio bytes never leave the
laptop) and then hands the transcript to the agent. For text, the message
goes straight to the agent. In both cases the final assistant reply comes
back to Telegram as HTML; intermediate tool-call activity is hidden by
design — the bot relays only the agent's final user-facing prose. The
raw transcript is still logged to stdout for developer visibility, but is
no longer echoed back to chat (this is a deliberate change from Phase 10).

Session memory works per chat: each Telegram chat owns a Claude Code
session id, persisted in the local SQLite `bot_session` table (migration
0005), with a 4-hour resume window. When you send a new message within
4 hours of the previous one, the
SDK resumes the same session — the agent remembers what you said last.
Outside that window, the next message starts fresh. Send `/new` at any
time to clear the stored id and force a clean slate on the next turn.

Observability is one INFO line per turn:

    agent turn · chat=987654321 · session=abc12345 · tokens_in=42 · tokens_out=180 · cost=$0.0123 · wall=4.21s

If you are signed in via `claude login` against your Claude Code
subscription (the recommended path for v1.1), the SDK does not surface a
per-turn cost figure — that field is logged as `cost=subscription`.
Token counts are always present and are the primary usage signal.

Auth and Node prerequisites are reiterated from above: `ANTHROPIC_API_KEY`
is NOT used (Tempo deliberately does not pass one); the bot relies on
`claude login` having been run once and `claude` being on the bot's PATH.
If `claude` is missing at `tempo bot run` startup the bot exits before
any Telegram traffic with `Set up the Claude Code CLI before starting the
bot -- see docs/TELEGRAM_BOT.md Phase 11 prerequisites (Node 18+ +
`claude login`).` — no silent boot followed by a Telegram-only error.

Long replies are split. Telegram's hard cap on a `sendMessage` body is
4096 characters. Agent replies that exceed that are split on paragraph
boundaries by `format_for_telegram`, with each chunk prefixed `[k/N] ` so
you can follow the order in chat. The prefix budget is reserved up front
so no chunk can exceed the cap.

While the agent is running, Telegram shows the typing indicator for the
chat: the bot fires `send_action(TYPING)` immediately and refreshes it
every 4 seconds (the server-side TYPING action lasts ~5s) until
`run_turn` returns. Cancellation of the keepalive task is the only exit
path; the resulting `CancelledError` is absorbed via
`asyncio.gather(..., return_exceptions=True)` so it never surfaces as an
uncaught task exception.

Phase 12 closes the remaining lifecycle/privacy gaps on top of this
pipeline: the launchd `LaunchAgent` with `KeepAlive=true` (see "Always-on
under launchd" above), the `VOICE_RETENTION_DAYS` retention policy + the
`tempo bot purge-voice` hatch (see "Voice cache retention" above), and the
top-level `telegram_error_handler` that catches every other handler
exception (see "Error handler behaviour" above). With those wired,
`tempo bot run` -- whether you start it manually or via launchd -- is
v1.1-complete.

## Troubleshooting

- **`409 Conflict: terminated by other getUpdates request`** -- either another
  copy of the bot is polling, or a webhook is still set on the token from a
  prior experiment. The bot already calls
  `deleteWebhook(drop_pending_updates=False)` at startup as a precaution
  (research note "Pitfalls"); if you still see persistent 409s, run:

  ```
  curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
  ```

  and ensure no other process holds the same token.

- **Bot doesn't reply to `/start`** -- the most common cause is a
  `TELEGRAM_OWNER_CHAT_ID` that doesn't match the chat id `getUpdates` returns
  for your account. The filter silently drops mismatches *by design*. Re-run
  Step 3 and compare values.

- **Lost the token** -- send `/revoke` to @BotFather. It rotates the token
  and the old one dies immediately. Update `.env` and restart `tempo bot run`.

- **`.env` is world-readable** -- `chmod 600 .env` and verify with `ls -l .env`
  (you want `-rw-------`). Anything else and another local user on the same
  Mac could read your token.

## Privacy note

The bot token is a **full-access credential** for the bot itself. Anyone with
it can send arbitrary messages as the bot. The owner chat id allowlist means a
leaked token alone can't interact with *you* (the bot won't reply to anyone
else), but rotate via `/revoke` immediately if it ever leaks. The token is
treated as a real secret throughout Tempo: `SecretStr` in `Settings`, never
logged, `.env` is gitignored from day one, and the README enforces
`chmod 600 .env` alongside the Strava and Garmin token guidance.

The full privacy contract -- what data stays on the laptop, what leaves it,
the voice retention policy, and the per-credential leak-response steps --
lives in [`docs/PRIVACY.md`](PRIVACY.md). Read it once and it stays true.

## What's next

v1.1 is **feature-complete** with Phase 12. The Telegram voice-coach
intake now covers: owner-only allowlist, local Whisper transcription,
Claude Code agent loop with per-chat 4h session memory and `/new` reset,
launchd `KeepAlive` lifecycle, top-level error boundary, and
configurable voice retention. See `.planning/ROADMAP.md` for the next
milestone (v1.2 — Pi port).
