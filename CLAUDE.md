# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
# Local development (requires Ollama already running on localhost:11434)
./run.sh

# Or manually (requires Ollama already running on localhost:11434)
cd bot && python main.py

# Unraid (pulls pre-built image from GHCR — see README for full docker run command)
docker pull ghcr.io/mikedelcastillo/minomnom-ai:latest
```

## Local development setup

```bash
cd bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The bot reads `.env` from the **project root** (one level above `bot/`). Copy `.env.example` and fill in at minimum `BOT_TOKEN`.

## Architecture

The bot has a strict three-layer pipeline for every incoming message:

1. **Classify** (`llm.classify`) — determines if the message is `"tracking"` (a meal log) or `"general"` (chat)
2. **Plan** (`llm.plan`) — for tracking messages, decides whether enough info exists to estimate immediately or which clarifying questions to ask (0–3, presented as inline keyboard buttons)
3. **Finalize** (`llm.finalize`) — called after all clarifying answers are collected; produces the final macro estimate

All LLM calls go to a local Ollama instance via HTTP (`/api/generate` for structured JSON, `/api/chat` for general conversation). Every response is validated and retried once on failure.

### Conversation state

The `python-telegram-bot` `ConversationHandler` manages multi-turn clarification. State is stored in `context.user_data["pending"]`:

```python
{
    "original": str,            # original meal text
    "planned_questions": list,  # all questions decided upfront by plan()
    "clarifications": list,     # answers collected so far
    "current_q_idx": int,       # which question we're on
}
```

Questions are decided **all at once** in the `plan()` call, not one at a time — this avoids multiple round-trips to the LLM.

### Data model

Macros are stored as min/max ranges (e.g. `calories_min`, `calories_max`) throughout — in the LLM responses, in `context.user_data`, and in the SQLite schema. All values are integers.

### Module responsibilities

| File | Purpose |
|---|---|
| `bot/main.py` | Entry point; wires up `ConversationHandler` and command handlers |
| `bot/llm.py` | All Ollama calls, prompts, and response validation |
| `bot/handlers/meal.py` | Message handling, clarification flow, delete callback |
| `bot/handlers/stats.py` | `/today`, `/week`, `/history`, `/undo`, `/help` |
| `bot/db.py` | All SQLite queries via aiosqlite |
| `bot/config.py` | Env var loading; resolves `DB_PATH` relative to project root |

### Adding a new LLM call

Follow the pattern in `llm.py`: define system prompt + prompt template as module-level constants, write an `async def` that POSTs to Ollama, validates the JSON response, and retries once. Use `format: "json"` and `/api/generate` for structured outputs; use `/api/chat` (no `format` key) for free-text conversation like `general_reply`.

### Prompt engineering notes

`PLAN_SYSTEM_PROMPT` in `llm.py` contains the detailed rules for when to ask clarifying questions. The core invariant: only ask if the answer would change the calorie estimate by >20%. If you modify this prompt, verify that the bot doesn't regress to over-asking (especially for named dishes that already imply preparation).

`_validate_plan` in `llm.py` includes a normalisation path for `type: "question"` (singular) — phi3.5 sometimes returns this instead of `type: "questions"`. Keep this fallback in place when changing models until you've confirmed the new model always uses the correct key.
