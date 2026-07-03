# Telegram Setup via Claude Code Channels

Jarvis uses [Claude Code Channels](https://code.claude.com/docs/en/channels) — the official Anthropic plugin — to connect to Telegram. No custom Python relay needed.

## Prerequisites

- Claude Code v2.1.80+ (`claude --version`)
- [Bun](https://bun.sh) runtime (required by the channels plugin):
  ```bash
  curl -fsSL https://bun.sh/install | bash
  ```

## Step 1 — Create a Telegram bot

1. Open Telegram and start a chat with [@BotFather](https://t.me/botfather)
2. Send `/newbot`
3. Choose a name (display name, e.g. "Jarvis")
4. Choose a username (must end in `bot`, e.g. `jarvis_my_bot`)
5. BotFather gives you a token: `123456789:AAHfiqksKZ8...` — copy it

## Step 2 — Install the plugin

In a Claude Code session:
```
/plugin install telegram@claude-plugins-official
/reload-plugins
```

## Step 3 — Set the bot token

```bash
mkdir -p ~/.claude/channels/telegram
echo "TELEGRAM_BOT_TOKEN=123456789:AAHfiqksKZ8..." > ~/.claude/channels/telegram/.env
```

Or set it as a shell environment variable (takes precedence over the file):
```bash
export TELEGRAM_BOT_TOKEN=123456789:AAHfiqksKZ8...
```

## Step 4 — Start Claude Code with Channels

From the project directory:
```bash
claude --channels plugin:telegram@claude-plugins-official
```

Claude Code starts and the Telegram plugin begins polling.

## Step 5 — Pair your account

1. In Claude Code session, run: `/telegram:access pair`
2. Claude gives you a pairing code
3. Open Telegram, send that code to your bot
4. Back in Claude Code, lock down access:
   ```
   /telegram:access policy allowlist
   ```
   This ensures only your paired account can send messages.

## Step 6 — Test it

Send a message to your bot in Telegram. Claude should respond.

Try: "что у нас в M3?" — Jarvis should recall memory and answer in context.

## Running persistently (Windows)

To keep Jarvis available 24/7 on a home PC, create a Windows Task Scheduler task or a PowerShell service that runs:

```powershell
cd C:\path\to\jarvis
claude --channels plugin:telegram@claude-plugins-official
```

Or use Windows Subsystem for Linux (WSL) with a `screen` or `tmux` session.

## Multi-device

Claude Code Channels runs on whichever machine has an active session. Memory (via Supabase) is shared across devices, so context is preserved regardless of which machine is running.

If you want 24/7 availability, designate one always-on machine (home PC, server, or VPS) as the Channels host.

## Troubleshooting

**Bot doesn't respond:** check that the session is running with `--channels` flag and the token is correct.

**"Plugin not found":** run `/reload-plugins` after install.

**Unauthorized messages getting through:** run `/telegram:access policy allowlist` to lock down to paired users only.

**Token rejected:** make sure there are no extra spaces in the `.env` file.
