# minomnom-ai

A self-hosted Telegram bot that turns casual meal descriptions into calorie and macro estimates — powered by a local LLM, no cloud API required.

## How it works

Send any message like _"had a chicken sandwich and a coke"_ and the bot classifies it, asks smart follow-up questions only when the answer materially changes the estimate (e.g. portion size, cooking method), then returns a calorie + macro range and logs it to SQLite.

## Features

- **Fully local inference** via [Ollama](https://ollama.com) — privacy-first, runs offline
- **Intent classification** — distinguishes meal logs from general chat, handles both naturally
- **Guided clarification** — inline keyboard buttons for portion/cooking questions, skipped when unnecessary
- **Macro tracking** — calories, protein, carbs, fat as min–max ranges per meal
- **Conversation commands** — `/today`, `/week`, `/history`, `/undo`
- **One-tap delete** — inline Delete button on every logged meal
- **User allowlist** — optional whitelist to keep the bot private
- **Unraid-ready** — pre-built image on GHCR, installable via Unraid Docker GUI with no terminal needed

## Stack

- Python 3.12 · [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) · aiosqlite
- [Ollama](https://ollama.com) (default model: `phi3.5`)
- Docker Compose

## Quick start

See **[Deploying on Unraid](#deploying-on-unraid)** for the recommended deployment path, or **[Local development](#local-development-no-docker)** to run without Docker.

## Deploying on Unraid

If you already run Ollama on Unraid, this is the cheapest and lowest-latency option — the bot talks to Ollama directly over localhost.

Pre-built images are published to GitHub Container Registry on every push to `main`:

```
ghcr.io/mikedelcastillo/minomnom-ai:latest
```

**Prerequisites:** Ollama is running on the same Unraid machine (e.g. via Community Applications).

### Via Unraid Docker GUI

1. Go to **Docker** tab → **Add Container**
2. Set **Repository** to `ghcr.io/mikedelcastillo/minomnom-ai:latest`
3. Set **Network type** to `Host`
4. Add a **Path**: Container path `/data` → Host path `/mnt/user/appdata/minomnom-ai`
5. Add the following **Variables**:

| Name | Value |
|---|---|
| `BOT_TOKEN` | your Telegram bot token |
| `OLLAMA_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `phi3.5` |
| `ALLOWED_USER_IDS` | your Telegram user ID |

6. Click **Apply** — Unraid pulls the image and starts the bot.

Data is persisted at `/mnt/user/appdata/minomnom-ai/app.db`.

To update, click the container → **Update** in the Unraid Docker UI.

### Via Docker Compose (terminal)

```bash
# Clone the repo somewhere on Unraid (e.g. /mnt/user/data/minomnom-ai)
git clone https://github.com/mikedelcastillo/minomnom-ai.git
cd minomnom-ai

cp .env.example .env
# Edit .env: set BOT_TOKEN, OLLAMA_URL=http://localhost:11434, ALLOWED_USER_IDS
```

```bash
docker compose -f docker-compose.unraid.yml up -d
```

**To update:**

```bash
docker compose -f docker-compose.unraid.yml pull && docker compose -f docker-compose.unraid.yml up -d
```

## Local development (no Docker)

Requires [Ollama](https://ollama.com) installed on the host.

```bash
cp .env.example .env
# Set BOT_TOKEN and change OLLAMA_URL to http://localhost:11434

chmod +x run.sh
./run.sh
```

`run.sh` starts Ollama if it isn't already running, pulls `phi3.5` on first run, creates the Python virtualenv, and launches the bot. Re-run it any time — it skips steps that are already done.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | required | Telegram bot token |
| `OLLAMA_URL` | `http://ollama:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `phi3.5` | Model to use |
| `ALLOWED_USER_IDS` | _(empty = public)_ | Comma-separated Telegram user IDs |
| `DB_PATH` | `/data/app.db` | SQLite database path |
| `USE_WEBHOOK` | `false` | Set `true` to use Telegram webhooks instead of polling |
| `WEBHOOK_URL` | _(empty)_ | Public HTTPS base URL for webhook mode (e.g. `https://myapp.railway.app`) |
| `PORT` | `8080` | Port to listen on in webhook mode |

## Commands

| Command | Description |
|---|---|
| _(any text)_ | Log a meal or chat with the bot |
| `/today` | Today's meals and totals |
| `/week` | Daily calorie breakdown for the last 7 days |
| `/history` | Last 10 logged meals |
| `/undo` | Remove the most recent meal |
| `/help` | Show command list |
