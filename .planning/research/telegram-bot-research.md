# Telegram Bot Research — python-telegram-bot

**Researched:** 2026-05-27
**Target use case:** Personal, single-chat voice memo bot. Receives `.ogg` voice notes, hands transcripts to a Claude Agent SDK loop, replies in the same chat. Runs locally on macOS under `launchd`.
**Overall confidence:** HIGH (official docs + core.telegram.org cited throughout).

## Recommendation

Use **`python-telegram-bot` v22.x** (current stable: v22.7) with its **async** API. Run it with **long polling** via `Application.run_polling()` — no public URL, no port forwarding, no certificates needed. Lock the bot to a single chat by combining filters at the handler level: `filters.VOICE & filters.Chat(chat_id=OWNER_CHAT_ID)`. Download voice notes with `await update.message.voice.get_file()` then `await file.download_to_drive(path)`. Send replies as **HTML** (`parse_mode=ParseMode.HTML`) — far less brittle than MarkdownV2 for agent-generated output, since only `&`, `<`, `>` need escaping. Run as a macOS `LaunchAgent` with `KeepAlive=true` so launchd restarts it on crash and re-runs after sleep/wake. `Application.run_polling()` already handles SIGTERM gracefully by default.

PTB is the right pick for a single-user, low-volume bot that needs to coexist with the rest of the Tempo Python 3.14 stack: it's the most mature option, uses `httpx` (compatible with our stack), is fully async, has the largest community / best-maintained docs, and ships first-class filter primitives we need for the allowlist.

---

## Library choice + transport (Q1, Q2)

### Library: python-telegram-bot (async API)

| Library | Verdict for this use case |
|---|---|
| **python-telegram-bot v22.x** | **Recommended.** Fully async, built on `httpx`, mature (10+ yrs), excellent docs, 18 official examples, first-class `filters` for allowlisting. Supports Python 3.9+. |
| aiogram v3 | Solid alternative — fully async, more "modern" router-style API. Slightly smaller community. No compelling advantage for a single-chat bot; PTB's filter API is more concise here. |
| Telethon | **Wrong tool.** Telethon is an MTProto **user-client** library — talks to Telegram as a *user account*, not via the Bot API. Heavier, requires `api_id`/`api_hash`, overkill. Use only if you need user-account features (reading another user's messages, MTProto-only features) or to bypass the 20 MB getFile limit. |
| Pyrogram | Same caveat as Telethon (MTProto). Maintenance has been spottier. Avoid. |

**Use the async API, not the legacy sync wrapper.** PTB v20+ is async-native; the v13 sync API is dead. The rest of Tempo can stay sync — the bot is its own long-lived process.

### Transport: long polling

Use `Application.run_polling()`. Webhooks require a public HTTPS endpoint on ports 443/80/88/8443 (per core.telegram.org) — not viable for a Mac at home behind NAT without ngrok or a reverse proxy. Long polling has no such requirement: the bot makes outbound `getUpdates` calls.

For a personal bot:
- **Latency:** Long polling adds ~0–10s vs. webhooks. Imperceptible for a voice-memo workflow.
- **Rate limits:** Telegram allows ~1 msg/sec to a private chat, 30 msgs/sec globally (core.telegram.org/bots/faq). A personal bot will not approach this.
- **Polling cost:** `getUpdates` is a long-poll (default 10s timeout in PTB) — Telegram holds the connection open until an update arrives. Essentially free.

```python
# tempo/bot/main.py
from telegram.ext import ApplicationBuilder

app = ApplicationBuilder().token(settings.telegram_bot_token).build()
# ... add handlers ...
app.run_polling()   # SIGINT/SIGTERM/SIGABRT handled by default on Unix
```

---

## Single-chat allowlist (Q3)

Idiomatic pattern: combine `filters.Chat(chat_id=...)` with the message-type filter using `&`. Any update not from the owner's chat simply has no matching handler and is silently dropped (no reply, no log spam).

```python
from telegram.ext import ApplicationBuilder, MessageHandler, filters

OWNER_CHAT_ID = settings.telegram_owner_chat_id  # int, from pydantic-settings

owner_only = filters.Chat(chat_id=OWNER_CHAT_ID)

app = ApplicationBuilder().token(settings.telegram_bot_token).build()
app.add_handler(MessageHandler(owner_only & filters.VOICE, on_voice))
app.add_handler(MessageHandler(owner_only & filters.TEXT & ~filters.COMMAND, on_text))
app.add_handler(MessageHandler(owner_only & filters.COMMAND, on_command))
app.run_polling()
```

`filters.Chat(chat_id=N)` is the documented constructor (v22.7). Operators: `&` AND, `|` OR, `~` NOT. Drops everything else by design — no global fallback handler needed unless you want one for diagnostics.

**Belt-and-braces:** Also re-check `update.effective_chat.id == OWNER_CHAT_ID` inside each handler. Cheap; protects against a misconfigured filter.

---

## Voice message → local file (Q4)

`update.message.voice` is a `telegram.Voice` object with attributes `file_id`, `file_unique_id`, `duration`, `mime_type` (typically `audio/ogg`; codec is Opus), `file_size`. Call `get_file()` to get a `telegram.File`, then `download_to_drive(path)`.

```python
from pathlib import Path
from telegram import Update
from telegram.ext import ContextTypes

VOICE_DIR = Path(settings.tempo_content_dir) / "voice"

# Telegram bot API getFile limit (core.telegram.org/bots/faq)
MAX_VOICE_BYTES = 20 * 1024 * 1024  # 20 MB

async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice = update.message.voice
    if voice.file_size and voice.file_size > MAX_VOICE_BYTES:
        await update.message.reply_text(
            f"Voice note too big ({voice.file_size // 1024} KB > 20 MB). "
            "Telegram bot API cannot download it."
        )
        return

    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    target = VOICE_DIR / f"{update.message.message_id}-{voice.file_unique_id}.ogg"

    tg_file = await voice.get_file()
    await tg_file.download_to_drive(custom_path=target)

    # tg_file.file_path is the Telegram CDN URL if you ever need it
    # target is now a local .ogg/Opus file ready for transcription
    await update.message.reply_text("Got it, transcribing…")
    # hand off to Claude Agent SDK with `target` as input
```

**Notes:**
- **20 MB cap is a hard Telegram limit** on the cloud bot API (`getFile`). Workaround is running a self-hosted Bot API server — not worth it. At ~16 kbps Opus, 20 MB ≈ ~2.7 hours of voice; in practice you won't hit it.
- Voice notes are always Opus-in-Ogg from the Telegram app. Whisper / `openai-whisper` / `faster-whisper` handle this natively (no transcode needed).
- `file_id` is reusable to re-send the same file later via `send_voice(file_id=...)` without re-uploading. Useful if you ever want to echo it back.

---

## Sending formatted replies (Q5)

**Use HTML, not MarkdownV2.**

MarkdownV2 requires escaping **18 characters** anywhere they appear outside formatting markers: `_ * [ ] ( ) ~ ` ` ` > # + - = | { } . !` — including inside text content. A Claude agent reply containing a sentence like "It's at 3:30 p.m." will silently fail to parse because of `.` and `:`. PTB does ship `telegram.helpers.escape_markdown(text, version=2)`, but you have to be careful not to escape your own intentional formatting markers, so it ends up requiring you to assemble the message in fragments.

HTML, by contrast, needs only `&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`. Telegram supports a fixed subset: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<pre><code class="language-python">…</code></pre>`, `<a href="…">`, `<blockquote>`. No tables (Telegram has no table primitive — render as a `<pre>` block or aligned text). Lists must be rendered as plain text with `• ` or `1. ` prefixes; Telegram has no `<ul>`/`<ol>`.

```python
import html
from telegram.constants import ParseMode

async def send_agent_reply(update: Update, agent_text: str) -> None:
    # Agent should produce text using <b>, <i>, <code>, <pre> tags directly,
    # OR produce plain text and we escape it.
    # If agent emits literal user content, escape it:
    safe = html.escape(agent_text, quote=False)
    await update.message.reply_text(safe, parse_mode=ParseMode.HTML)
```

**Tables / structured output:** wrap in `<pre>…</pre>` for monospace fixed-width, then format columns with spaces. Telegram's mobile fonts are not monospace by default outside `<pre>`, so this matters.

**Length cap:** 4096 chars per message. For longer agent replies, split on paragraph boundaries and `reply_text` in sequence. PTB does not auto-split.

**Disable web preview** for replies that contain URLs you don't want previewed: `disable_web_page_preview=True` (or `link_preview_options=LinkPreviewOptions(is_disabled=True)` in v21+).

---

## One-time setup (Q6)

1. **Create the bot in @BotFather.**
   - Open Telegram, search for **@BotFather**, `/start`.
   - `/newbot` → choose a display name → choose a username ending in `bot` (e.g. `tempo_ross_bot`).
   - BotFather replies with the **HTTP API token**, e.g. `1234567890:AAH...`. Treat this as a password.
   - Optional: `/setprivacy` → **Disable** if you want it to also read group messages (not needed for a 1-on-1 chat).
   - Optional: `/setcommands` to register `/start`, `/status`, etc. so Telegram autocompletes them.

2. **Find your own `chat_id`.**
   - Open a chat with your new bot in Telegram, send any message (e.g. `hi`).
   - In a browser, visit: `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Find `result[0].message.chat.id` — a positive integer for a private 1-on-1 chat (groups/channels are negative).
   - If `result` is empty, send another non-command message and refresh.

3. **Store the secrets in `.env` (gitignored).**
   ```env
   # .env  (NEVER commit)
   TELEGRAM_BOT_TOKEN=1234567890:AAH...
   TELEGRAM_OWNER_CHAT_ID=987654321
   ```

4. **Load via `pydantic-settings`** (already in the Tempo stack):
   ```python
   # tempo/config.py
   from pydantic_settings import BaseSettings, SettingsConfigDict

   class Settings(BaseSettings):
       model_config = SettingsConfigDict(env_file=".env", extra="ignore")
       telegram_bot_token: str
       telegram_owner_chat_id: int
       # ... strava, garmin, etc.
   ```

5. **Sanity test:** `uv run python -c "from tempo.config import Settings; print(Settings().telegram_owner_chat_id)"` then run the bot and send it `/start`.

---

## launchd lifecycle (Q7)

Run the bot as a **per-user LaunchAgent** in `~/Library/LaunchAgents/`. `KeepAlive=true` restarts it on any exit; `RunAtLoad=true` starts it at login; launchd handles sleep/wake (unlike cron). Same scheduler family already chosen for Tempo's daily sync — no new infra.

```xml
<!-- ~/Library/LaunchAgents/com.tempo.telegram-bot.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.tempo.telegram-bot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/rossheadington/.local/bin/uv</string>
    <string>run</string>
    <string>tempo</string>
    <string>bot</string>
  </array>
  <key>WorkingDirectory</key>  <string>/Users/rossheadington/Projects/tempo</string>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>ThrottleInterval</key>  <integer>10</integer>
  <key>StandardOutPath</key>   <string>/Users/rossheadington/Projects/tempo/.logs/bot.out.log</string>
  <key>StandardErrorPath</key> <string>/Users/rossheadington/Projects/tempo/.logs/bot.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
```

Load / unload:
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tempo.telegram-bot.plist
launchctl print gui/$(id -u)/com.tempo.telegram-bot     # status
launchctl bootout gui/$(id -u)/com.tempo.telegram-bot   # stop+unload
```

### Graceful shutdown

`Application.run_polling()` on macOS handles `SIGINT`, `SIGTERM`, and `SIGABRT` by default — it stops the updater, drains pending handler tasks, and runs `shutdown()`. launchd sends `SIGTERM` on `bootout` and waits ~20s before `SIGKILL` (`ExitTimeOut` plist key controls this; 20s is fine for short voice handler tasks). **Don't** override `stop_signals=()` — that disables graceful shutdown.

### Concurrent voice handling

By default, PTB processes updates **sequentially** (`concurrent_updates=0`). For multiple in-flight transcriptions you have two options:

1. **Per-update concurrency (recommended for this bot):** enable parallel update processing.
   ```python
   app = ApplicationBuilder() \
       .token(token) \
       .concurrent_updates(True) \
       .build()
   ```
   PTB then dispatches each update on its own asyncio task. A second voice memo arriving while the first is still being transcribed will not block.

2. **Per-handler `block=False`:** `MessageHandler(..., block=False)` makes that handler non-blocking but still serialises other updates. Less flexible than option 1.

Concurrency is safe here because: each handler downloads to a distinct path keyed on `message_id` + `file_unique_id`; the Claude Agent SDK call is per-message stateless; SQLite writes (if any) use the stdlib `sqlite3` connection-per-thread / WAL pattern Tempo already uses.

**Caveat:** if you ever add a `ConversationHandler`, official docs say to set `concurrent_updates=False` because it relies on sequential update ordering. Not relevant for a stateless voice bot.

---

## Pitfalls (Q8)

- **409 Conflict on startup** (`Conflict: terminated by other getUpdates request`). Happens if (a) you accidentally have two bot instances polling, or (b) a webhook is set on the bot. Fix: ensure only one process is running, and once per bot lifetime call `https://api.telegram.org/bot<TOKEN>/deleteWebhook?drop_pending_updates=false` — or pass `drop_pending_updates=False` and let PTB's bootstrap clear it. PTB's `Updater.start_polling()` already calls `deleteWebhook` for you on startup, but if you've been experimenting with webhooks the conflict can persist; explicit `deleteWebhook` from a curl is the deterministic fix.
- **Dropped updates after restart:** by default, after `bootout` and `bootstrap`, Telegram **buffers up to ~24 hours** of pending updates and PTB will replay them on next start. If you want a clean slate (e.g. you don't want to retranscribe a queue of old voice memos), pass `Application.run_polling(drop_pending_updates=True)`. For a personal bot, leaving it `False` is usually right — you don't lose memos sent while the laptop was asleep.
- **Token leakage:** the bot token is a full-access credential. Anyone with it can impersonate the bot. Keep `.env` gitignored, `chmod 600 .env`, and never log `settings.telegram_bot_token`. If leaked, `/revoke` in BotFather rotates it.
- **20 MB getFile cap** is silent — `get_file()` will raise a `BadRequest` for oversized files. Check `voice.file_size` up front and reply with a friendly error instead of crashing.
- **MarkdownV2 parse errors:** if you do choose MarkdownV2 and forget to escape a `.` or `-` in agent output, the entire `reply_text` call raises `BadRequest: Can't parse entities`. Use HTML to avoid this whole category of bug.
- **Privacy mode for groups:** by default a bot in a group only sees `/commands` directed at it. Doesn't matter for a 1-on-1 chat, but if you ever add the bot to a group expecting to see all messages, disable privacy via `/setprivacy` in BotFather.
- **Allowlist bypass:** `filters.Chat` filters at the handler level, but a misconfigured "catch-all" `MessageHandler(filters.ALL, ...)` for debugging will see every chat that messages your bot. Anyone who finds the bot's username can message it. Either remove the catch-all in production, or also gate it on `chat_id`.
- **launchd crash loops:** if the bot dies immediately on launch (bad token, missing `.env`), `KeepAlive=true` will restart it instantly. Set `ThrottleInterval=10` (above) so launchd backs off to once-per-10s and check `.logs/bot.err.log`. macOS will eventually give up if it crashes too fast for too long.
- **Working directory + PATH:** launchd starts processes with a minimal `PATH` and `$HOME`-relative working dir. Use **absolute paths** for `ProgramArguments` (full path to `uv`) and set `WorkingDirectory` explicitly, otherwise `uv run tempo bot` may fail to find the project.
- **Voice file accumulation:** every memo writes an `.ogg` to disk. Add a retention policy (e.g. delete after successful transcript ingest, or nightly cleanup of files older than N days) or the directory will grow unboundedly.

---

## Sources

- [python-telegram-bot v22.7 docs — Examples](https://docs.python-telegram-bot.org/en/stable/examples.html) — current stable version, example bot list. Confidence: HIGH.
- [python-telegram-bot v22.7 — telegram.Voice](https://docs.python-telegram-bot.org/en/stable/telegram.voice.html) — Voice attributes, `get_file()` pattern. Confidence: HIGH.
- [python-telegram-bot v22.7 — telegram.ext.filters](https://docs.python-telegram-bot.org/en/stable/telegram.ext.filters.html) — `filters.Chat(chat_id=...)`, `filters.VOICE`, `&` / `|` / `~` operators. Confidence: HIGH.
- [python-telegram-bot v22.7 — Application](https://docs.python-telegram-bot.org/en/stable/telegram.ext.application.html) — `run_polling()` signature, default `stop_signals` (SIGINT/SIGTERM/SIGABRT), `concurrent_updates`, lifecycle. Confidence: HIGH.
- [python-telegram-bot v22.7 — telegram.helpers](https://docs.python-telegram-bot.org/en/stable/telegram.helpers.html) — `escape_markdown(text, version=2)`, `mention_html`. Confidence: HIGH.
- [core.telegram.org Bots FAQ](https://core.telegram.org/bots/faq) — 20 MB `getFile` download cap, per-chat 1 msg/sec and 30 msg/sec global rate limits. Confidence: HIGH.
- [core.telegram.org Bots API](https://core.telegram.org/bots/api) — getUpdates vs. webhooks are mutually exclusive; webhook port requirements (443/80/88/8443). Confidence: HIGH.
- [aiogram GitHub](https://github.com/aiogram/aiogram) — alternative async lib (Python 3.10+, aiohttp-based). Confidence: HIGH.
- [Apple — launchd.info tutorial](https://www.launchd.info/) and [Apple Creating Launch Daemons and Agents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) — `KeepAlive`, `RunAtLoad`, `ThrottleInterval`, launch-agent plist location. Confidence: HIGH.
- [Telegraf issue #475 — 409 Conflict on getUpdates while webhook active](https://github.com/telegraf/telegraf/issues/475) — `deleteWebhook` resolution. Confidence: HIGH.
