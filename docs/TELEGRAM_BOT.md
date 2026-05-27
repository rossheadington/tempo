# Telegram bot (v1.1)

A personal, owner-only Telegram bot. Runs locally on your Mac via long-polling
(no public URL, no webhook, no port forwarding), accepts messages from a single
chat id you control, and silently drops everything else at the filter level.

This is the **v1.1 voice-coach intake**: Phase 9 wires the worker and the
allowlist; Phase 10 adds voice-memo download + local transcription; Phase 11
hands transcripts to a Claude Code agent loop; Phase 12 runs the whole thing
under launchd.

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
in-flight handler tasks, and shuts down cleanly. Phase 12 will run the same
process under launchd, which sends `SIGTERM` on `bootout`.

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

What this phase does NOT yet do (Phase 12 lands these): a top-level
"something went wrong" error boundary around the full pipeline (today
non-`AgentInvocationError` exceptions propagate to PTB's default
handler), a launchd `LaunchAgent` for unattended running across reboots,
and the retention policy for the `voice/` cache + bot logs.

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

## What's next

- **Phase 10:** voice-memo download (`update.message.voice.get_file()` ->
  `download_to_drive()`) + local `faster-whisper` transcription, all gated on
  the same `filters.Chat(chat_id=owner)` filter.
- **Phase 11:** Claude Code agent loop -- transcripts in, structured journal
  entries / data queries / acknowledgements out, with persistent session id.
- **Phase 12:** launchd `LaunchAgent` with `KeepAlive=true` + retention
  policy for accumulated `.ogg` files and bot logs.
