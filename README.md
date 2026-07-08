# PSC-HELPER

**Current release:** [v0.8.1](https://github.com/Pembevich/PSC-HELPER/releases/tag/v0.8.1) · **License:** [MIT](./LICENSE)

A self-hosted Discord bot for communities that need automated moderation, an in-character AI assistant (**P.OS**), application/complaint workflows, detailed audit logging, and media utilities such as on-the-fly GIF generation.

PSC-HELPER started in the [P.S.C. (Provision Security Complex)](https://github.com/Pembevich/PSC-HELPER) ecosystem and is offered **free of charge** for self-hosting.

## Who is this for?

**For Discord server owners who need free self-hosted moderation, AI assistance, application workflows, and audit logging.**

Typical use cases:

- Community servers that want layered automoderation without a paid SaaS bot
- Staff teams that need structured application and complaint forms
- Admins who want searchable audit trails across channels, roles, and moderation actions
- Servers that want a configurable AI persona with hardcoded safety boundaries around privileged tools

## Adoption / Community

PSC-HELPER is in **early adoption**. Numbers are small but real:

| Signal | Status |
| --- | --- |
| Servers actively testing | **1** production deployment (P.S.C.) plus a **small private pilot group** (under 5 servers) |
| Interested server owners | **~10** owners/operators who asked about setup, hosting, or feature fit (via DMs and GitHub) |
| Project Discord server | **Not launched yet** — support and discussion currently happen through [GitHub Issues](https://github.com/Pembevich/PSC-HELPER/issues) |
| Feedback | Early feedback from moderators on raid handling, form flows, and P.OS tool safety; please open issues for bugs and ideas |
| Pricing | **Free** — self-host on Railway or your own infrastructure; bring your own AI API keys |

If you try PSC-HELPER, an issue describing your server size and setup helps the roadmap.

## Core features

- **Automated moderation** — link/attachment screening, spam and flood detection, scam/NSFW signals, mass-mention protection
- **P.OS AI assistant** — responds to mentions and replies, keeps conversational context, supports vision inputs
- **Owner-gated tools** — bans, timeouts, roles, channels, cross-server actions; enforced in code, not prompt-only
- **Application workflows** — interactive forms for applications, reports, and staff review
- **Audit logging** — structured server event journals with per-type log channels
- **Media utilities** — `!gif` conversion from images or short video clips

## Security model (important)

High-privilege operations are **never delegated directly to the LLM**. The model may request actions through tool calls; `pos_ai.py` enforces owner checks, protected targets, and confirmation flows before execution.

Administrative commands are restricted to Discord IDs listed in `POS_OWNER_USER_IDS` (owner ID `968698192411652176` is included by default).

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env   # fill in DISCORD_TOKEN and AI keys
python main.py
```

Run tests:

```bash
python -m pip install -r requirements-dev.txt
pytest
```

System dependency: `ffmpeg` (see `apt.txt`) for video→GIF conversion.

## Railway deployment

Configure environment variables in Railway. A local `.env` is for development only.

### Required

| Variable | Description |
| --- | --- |
| `DISCORD_TOKEN` | Discord bot token |

### P.OS / AI (GitHub Models recommended)

| Variable | Description |
| --- | --- |
| `GITHUB_MODELS_TOKEN` | GitHub PAT with Models API access |
| `GITHUB_MODELS_MODEL` | Model id (default: `openai/gpt-4.1`) |
| `GITHUB_MODELS_ENDPOINT` | Default: `https://models.github.ai/inference/chat/completions` |
| `GITHUB_MODELS_API_VERSION` | Default: `2022-11-28` |

Legacy-compatible variables still work: `POS_AI_API_KEY`, `POS_AI_API_URL`, `POS_AI_MODEL`, `NVIDIA_API_KEY`, etc.

### Provider pool (rate-limit spreading)

| Variable | Description |
| --- | --- |
| `POS_AI_PROVIDER_KEYS` | CSV of API keys |
| `POS_AI_PROVIDER_URLS` | CSV of endpoints (index-aligned) |
| `POS_AI_PROVIDER_MODELS` | CSV of model names (index-aligned) |

Example:

```env
POS_AI_PROVIDER_KEYS=gh_key_1,gh_key_2
POS_AI_PROVIDER_URLS=https://models.github.ai/inference/chat/completions,https://models.github.ai/inference/chat/completions
POS_AI_PROVIDER_MODELS=openai/gpt-4.1,openai/gpt-4.1
```

### Other useful settings

- `POS_OWNER_USER_IDS` — comma-separated admin Discord IDs
- `POS_AI_SYSTEM_PROMPT` — override P.OS persona prompt
- `POS_AI_MAX_TOKENS`, `POS_AI_TEMPERATURE`, `POS_AI_TOP_P`, `POS_AI_TIMEOUT_SECONDS`
- `PRIMARY_LOG_CHANNEL_ID`, `UPDATE_LOG_CHANNEL_ID`, `LOG_CATEGORY_ID`, `LOG_CATEGORY_NAME`
- `DB_BACKUP_CHANNEL_ID` — Discord channel for SQLite backup uploads (recommended on Railway)

See `.env.example` for the full list.

### Suggested free/low-cost AI providers

OpenRouter, Groq, Hugging Face Inference, Together AI, and Cloudflare Workers AI often have free tiers or credits. Verify current limits and terms before production use.

## Repository layout

| Path | Role |
| --- | --- |
| `main.py` | Entry point, cog loading, shutdown DB backup |
| `config.py` | Environment parsing, static Discord IDs, filter lists |
| `moderation.py` | Text/attachment filters, spam, AI moderation hooks |
| `antiraid.py` | Join-rate raid detection and reactions |
| `pos_ai.py` | P.OS orchestration, context, tool execution policy |
| `ai_client.py` | OpenAI-compatible client with provider pooling |
| `commands.py` | Classic and slash commands (`!gif`, `/sbor`, health) |
| `forms.py` | Application/report UI (views, modals) |
| `storage.py` | Async SQLite persistence and Discord backup |
| `guild_config.py` | Per-guild settings merged with defaults |
| `logging_utils.py` | Log category/channel helpers |
| `cogs/` | Thin Discord event and command wrappers |

Cogs delegate to the top-level modules above. When changing behavior, edit the module, not only the cog.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). Roadmap areas use labels: `good first issue`, `documentation`, `security`, `ai-system`, `moderation`.

## Releases

| Version | Notes |
| --- | --- |
| [v0.8.0](https://github.com/Pembevich/PSC-HELPER/releases/tag/v0.8.0) | Multi-server settings, expanded owner tools, AI-assisted moderation |
| [v0.8.1](https://github.com/Pembevich/PSC-HELPER/releases/tag/v0.8.1) | Raid quarantine default, owner ping/DM/lift tools, form fixes |
| [v0.9.0](https://github.com/Pembevich/PSC-HELPER/releases/tag/v0.9.0) | Planned milestone (pre-release) |

## GitHub Models token hygiene

When using a fine-grained GitHub PAT, enable Models API access explicitly. If a token is exposed in a public channel or log, revoke it immediately and rotate Railway environment variables.

## License

MIT — see [LICENSE](./LICENSE).
