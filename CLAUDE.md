# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git commit policy

- Do NOT add a `Co-Authored-By: Claude ...` trailer or any "Generated with Claude" line to commit messages or PR descriptions in this repository.
- Keep commit messages concise and plain. The repository owner (Pembevich) is the sole author.

## Overview

PSC-HELPER is a Discord bot (discord.py) for the P.S.C. server. It combines automoderation, application/complaint forms, detailed server logging, GIF generation, and an in-character AI persona called **P.OS** that talks to members and can request moderation actions through tool calls. The codebase and most user-facing strings are in Russian. Deployment target is Railway (env-var driven); a local `.env` is only for development.

## Commands

```bash
# Run locally
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py                 # or ./run.sh (installs deps then runs main.py)

# Tests (pytest is configured via pyproject.toml; testpaths=["tests"])
python -m pip install -r requirements-dev.txt
pytest                         # run all tests
pytest tests/test_build_messages.py                        # single file
pytest tests/test_build_messages.py::BuildMessagesTests    # single class
# The README also documents the stdlib runner: python -m unittest discover -s tests -p "test_*.py"

# Type checking (mypy, python 3.11, tests/ excluded)
mypy .
```

Runtime is pinned to Python 3.11 (`runtime.txt`). `ffmpeg` is a system dependency (`apt.txt`) required for video→GIF conversion via moviepy/imageio-ffmpeg.

## Architecture

### Startup flow (`main.py`)
`run_bot()` loads env → `await init_db()` (async SQLite) → creates a `commands.Bot` with `Intents.all()` and `!` prefix → loads cogs in a fixed order (see `COGS` list) → registers SIGTERM/SIGINT handlers that back up the DB to Discord before closing → `bot.start(token)`. The Discord token is run through `sanitize_discord_token()` before use.

### Cogs vs. top-level modules
Behavior lives in `cogs/` (each exposes `async def setup(bot)`), but the heavy logic is in **top-level modules** that the cogs delegate to. Cogs are thin listeners/command wrappers:

- `cogs/ai_chat.py` → `pos_ai.handle_pos_ai` / `pos_ai.ask_pos`
- `cogs/mod.py` → `moderation.py` functions (URL checks, spam, ad/NSFW detection, timeouts)
- `cogs/general.py` → `commands.py` (GIF generation, `!gif`, `!health`, `/sbor`); also owns the 10-minute DB backup loop and `on_ready` setup (DB restore, log-channel creation, slash-command sync)
- `cogs/forms.py` → `forms.py` (UI views/modals) + `utils.py`
- `cogs/logging_events.py` → large self-contained server event logger
- `cogs/ai_tools.py` → `POS_AI_TOOLS` schema (the tool definitions P.OS may call)

When changing AI or moderation behavior, edit the top-level module, not the cog.

> Note: the README's "Файлы" section references some files that no longer exist as top-level modules (e.g. `events.py`, and it lists `commands.py` as just "commands"). The actual layout is cogs-delegating-to-modules as described above. `rewrite_pos_ai.py` and `scratch.py` are one-off migration scripts, not part of the running bot.

### Configuration (`config.py`)
All env parsing and hardcoded Discord IDs (channels, roles, guilds) live here. Read this first when behavior depends on a specific channel/role. Helpers: `_env_int`, `_env_float`, `_env_csv`, `_env_int_list`. Key points:
- AI provider resolution is layered for backward compatibility: `GITHUB_MODELS_TOKEN` → falls back to `POS_AI_API_KEY` → `NVIDIA_API_KEY`. `POS_AI_PROVIDER` is `"github_models"` when a GitHub token is present, else `"generic_openai_compatible"`.
- `POS_OWNER_USER_IDS` always includes the hardcoded owner ID `968698192411652176` (Pumba), merged with the env list.
- Link-filtering lists (`SUSPICIOUS_*`, `WHITELIST_DOMAINS`, keyword lists) and the default `POS_AI_SYSTEM_PROMPT` (the P.OS persona) are defined here.

### AI client (`ai_client.py`)
`pos_chat_completion()` is the single entry point for all model calls (both P.OS chat and Gemini-based moderation, selected via `provider_type=`). It implements an OpenAI-compatible client with a **provider pool** (`POS_AI_PROVIDER_KEYS/URLS/MODELS`, CSV, index-aligned) for rate-limit spreading. Features: round-robin provider cursor, per-provider and global backoff/cooldown with `Retry-After` parsing, automatic failover to the next provider on 429/5xx, and tolerant response parsing (`_extract_message_from_payload`, `extract_json_block`). Returns `None` on failure rather than raising — callers must handle `None`.

### P.OS logic (`pos_ai.py`)
The largest module. `handle_pos_ai(message, bot)` decides whether P.OS should respond (mention, reply, name-mention, ongoing context), builds the message payload via `_build_messages` (system prompt + chronological channel history with per-author identity headers + guild/author/server-memory snapshots), and calls `request_pos_reply`. Supports image/video vision inputs and a `!gif`-style request path.

**Tool-call security model (critical):** the model can emit tool calls (`ban_user`, `unban_user`, `timeout_user`, `add_role`, `remove_role`, `mute_ai_for_user`, `unmute_ai_for_user`), but `execute_pos_tool` enforces the policy in code, not in the prompt:
- Tools in `_OWNER_ONLY_TOOLS` are refused unless `message.author.id` is in `POS_OWNER_USER_IDS`; instead a confirmation request is DM'd to the owner.
- The owner and the bot itself are protected from being targeted by any tool.
Never let the model perform privileged actions directly — keep the verified code layer between AI intent and execution.

### Moderation (`moderation.py`)
URL/domain/path screening against the config lists, spam detection (duplicate-message window), ad/scam/NSFW text and attachment detection, and AI-assisted checks routed through `pos_chat_completion(..., provider_type="gemini")`. Violations delete the message, apply a max timeout, log evidence, and DM the user.

### Storage (`storage.py`)
Async SQLite via `aiosqlite` (chosen specifically to avoid blocking the event loop). Tables: `entries`, `private_chats`, `chat_messages`, `ai_context` (keyed by `user_id`+`guild_id`; `user_id=0` is the guild-level shared memory slot), `ai_muted`. Because Railway dynos have ephemeral disk, the DB is persisted by **uploading `bot_data.db` to a Discord channel** (`DB_BACKUP_CHANNEL_ID`): `backup_db_to_discord` (on a 10-min loop and on shutdown) and `restore_db_from_discord` (on `on_ready`). Restore validates SQLite magic bytes and a 100 MB size cap. If `DB_BACKUP_CHANNEL_ID` is unset, backup/restore are skipped and data stays local only.

### Logging (`logging_utils.py`)
`ensure_log_category_and_channels` creates/locates the log category and per-type channels; `send_log_embed(guild, log_type, ...)` is the standard way to emit a structured log; `is_log_channel`/`is_log_category` gate event handlers so logging activity isn't itself logged or moderated.

## Conventions

- Code and most strings/log messages are Russian; match the existing language when editing user-facing text.
- Discord IDs are hardcoded in `config.py` rather than discovered at runtime — add new channel/role IDs there.
- AI calls return `None` on failure; always handle the empty/None path with a user-facing fallback (see `cogs/ai_chat.py`).
- Discord messages are chunked to ~1900 chars (`AI_MAX_RESPONSE_CHARS`, and manual chunking in cogs) to stay under the 2000-char limit.
