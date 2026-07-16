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

Runtime is pinned to Python 3.11 (`runtime.txt`). `ffmpeg` is a system dependency (`apt.txt`) required for the adaptive video→GIF pipeline.

## Architecture

### Startup flow (`main.py`)
`run_bot()` validates the Discord token before opening resources → initializes async SQLite → creates a `commands.Bot` with the `p.` prefix and minimal required intents → loads cogs in a fixed order → logs in without opening the gateway → restores and validates persistent state → connects the gateway. SIGTERM/SIGINT share one idempotent shutdown path that flushes AI memory, backs up only trusted restored state, closes Discord, and then closes SQLite.

### Cogs vs. top-level modules
Behavior lives in `cogs/` (each exposes `async def setup(bot)`), but the heavy logic is in **top-level modules** that the cogs delegate to. Cogs are thin listeners/command wrappers:

- `cogs/ai_chat.py` → `pos_ai.handle_pos_ai` / `pos_ai.ask_pos`
- `cogs/mod.py` → `moderation.py` functions (URL checks, spam, ad/NSFW detection, timeouts)
- `cogs/general.py` → `commands.py` (GIF generation and the only user command, `p.gif`); also owns the 10-minute DB backup loop and `on_ready` setup
- `cogs/forms.py` → `forms.py` (UI views/modals) + `utils.py`
- `cogs/security.py` → anti-raid effects plus coalesced, persisted Discord security-posture monitoring
- `cogs/logging_events.py` → large self-contained server event logger
- `cogs/ai_tools.py` → `POS_AI_TOOLS` schema (the tool definitions P.OS may call)

When changing AI or moderation behavior, edit the top-level module, not the cog.

### Configuration (`config.py`)
All env parsing and hardcoded Discord IDs (channels, roles, guilds) live here. Read this first when behavior depends on a specific channel/role. Helpers: `_env_int`, `_env_float`, `_env_csv`, `_env_int_list`. Key points:
- AI provider resolution is layered for backward compatibility: `GITHUB_MODELS_TOKEN` → falls back to `POS_AI_API_KEY` → `NVIDIA_API_KEY`. `POS_AI_PROVIDER` is `"github_models"` when a GitHub token is present, else `"generic_openai_compatible"`.
- `POS_OWNER_USER_IDS` contains only the immutable hardcoded owner ID `968698192411652176` (Pumba); environment variables cannot extend this trust boundary.
- Link-filtering lists (`SUSPICIOUS_*`, `WHITELIST_DOMAINS`, keyword lists) and the immutable P.OS identity/safety core are defined here. `POS_AI_SYSTEM_PROMPT` may extend that core but cannot replace it.

### AI client (`ai_client.py`)
`pos_chat_completion()` is the single entry point for all model calls (both P.OS chat and Gemini-based moderation, selected via `provider_type=`). It implements an OpenAI-compatible client with a **provider pool** (`POS_AI_PROVIDER_KEYS/URLS/MODELS`, CSV, index-aligned) for rate-limit spreading. Features: round-robin provider cursor, per-provider and global backoff/cooldown with `Retry-After` parsing, automatic failover to the next provider on 429/5xx, and tolerant response parsing (`_extract_message_from_payload`, `extract_json_block`). Returns `None` on failure rather than raising — callers must handle `None`.

### P.OS logic (`pos_ai.py`)
The largest module. `handle_pos_ai(message, bot)` decides whether P.OS should respond (mention, reply, name-mention, ongoing context), builds the message payload via `_build_messages` (system prompt + chronological channel history with per-author identity headers + guild/author/server-memory snapshots), and calls `request_pos_reply`. Supports image/video vision inputs and a `p.gif`-style request path.

**Tool-call security model (critical):** the model can emit tool calls (`ban_user`, `unban_user`, `timeout_user`, `add_role`, `remove_role`, `mute_ai_for_user`, `unmute_ai_for_user`), but `execute_pos_tool` enforces the policy in code, not in the prompt:
- Pumba's commands are authenticated by the immutable creator ID and execute immediately after intent, target, permission, and hierarchy checks. State-changing requests from other users are DM'd to Pumba for approval; owner-only factual reads are denied to outsiders.
- The owner and the bot itself are protected from being targeted by any tool.
Never let the model perform privileged actions directly — keep the verified code layer between AI intent and execution.

### Moderation (`moderation.py`)
Canonical URL screening, independent Google Safe Browsing/VirusTotal reputation, duplicate/flood/cross-channel/mention spam detection, bounded attachment magic/archive inspection, media review, and persistent anti-raid state. AI findings are advisory unless corroborated by deterministic evidence; moderation finishes through `message_gate.py` before forms, AI, or `p.gif` may process the message.

### Storage (`storage.py`)
Async SQLite via `aiosqlite`, with one shared connection and serialized write transactions/snapshots. Runtime tables cover AI context/mutes, guild settings, factual event/recipient journals, raid state, security posture, form decisions, and owner entries. Railway persistence uses gzip-compressed, SHA-256-checked snapshots uploaded to a private Discord channel (`DB_BACKUP_CHANNEL_ID`) every 10 minutes and at safe shutdown. Restore runs before gateway dispatch, accepts both new `bot_data.db.gz` and legacy `bot_data.db`, enforces a 100 MB decompressed cap, runs SQLite `quick_check`, and rolls back atomically on failure.

### Logging (`logging_utils.py`)
`ensure_log_category_and_channels` only discovers explicitly configured log channels. `setup_guild_logging` creates the private category/channels only after an owner request. `send_log_embed` persists the factual event first, then emits a Discord-limit-safe embed; `is_log_channel`/`is_log_category` also recognize threads under log channels to prevent recursive logging/moderation.

## Conventions

- Code and most strings/log messages are Russian; match the existing language when editing user-facing text.
- Discord IDs are hardcoded in `config.py` rather than discovered at runtime — add new channel/role IDs there.
- AI calls return `None` on failure; always handle the empty/None path with a user-facing fallback (see `cogs/ai_chat.py`).
- Discord messages are chunked to ~1900 chars (`AI_MAX_RESPONSE_CHARS`, and manual chunking in cogs) to stay under the 2000-char limit.
