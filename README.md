# PSC-HELPER

A powerful Discord bot designed for the P.S.C. (Provision Security Complex) multi-server ecosystem, featuring advanced automated moderation, application forms, detailed audit logging, dynamic GIF generation, and the integrated P.OS AI reasoning persona.

## Core Features

- **Automated Moderation:** Real-time filtering of links, attachments, spam, and suspicious content to secure connected server environments.
- **P.OS AI Reasoning:** An integrated AI character capable of responding to mentions, direct replies, and maintaining multi-turn conversational context.
- **Visual Intelligence:** Native image analysis capabilities powered by GitHub Models API.
- **Automated Workflows:** Interactive application forms, user reports, and disciplinary tracking modules.
- **Advanced Auditing:** Comprehensive server logs and real-time update journals across channels.
- **Media Processing (`!gif`):** Optimized on-the-fly GIF compilation from image sequences or short video assets.

## Railway Deployment

For production deployment via Railway, configure the environment variables detailed below. A local `.env` file is only required for development environments.

### Required Configuration

- `DISCORD_TOKEN` — Your core Discord Bot application token.

### P.OS Configuration (via GitHub Models)

- `GITHUB_MODELS_TOKEN` — GitHub Personal Access Token (PAT) with Models API access.
- `GITHUB_MODELS_MODEL` — Target model identifier (Default: `openai/gpt-4.1`).
- `GITHUB_MODELS_ENDPOINT` — API Inference endpoint (Default: `https://models.github.ai/inference/chat/completions`).
- `GITHUB_MODELS_API_VERSION` — Targeted API version (Default: `2022-11-28`).

### Legacy Configuration Compatibility

The bot retains full backwards compatibility with legacy environment schemas:

- `POS_AI_API_KEY` / `POS_AI_API_URL` / `POS_AI_MODEL`
- `NVIDIA_API_KEY` / `NVIDIA_API_URL` / `NVIDIA_MODEL`

### Advanced P.OS Settings

- `POS_OWNER_USER_IDS` — Authorized administrator Discord IDs (Pre-configured with `968698192411652176` by default).
- `POS_AI_SYSTEM_PROMPT` — Custom system prompt defining P.OS behavioral boundaries.
- `POS_AI_MAX_TOKENS`
- `POS_AI_TEMPERATURE`
- `POS_AI_TOP_P`
- `POS_AI_TIMEOUT_SECONDS`

*Security Guardrails:* Administrative owner commands are strictly restricted to Discord IDs specified within `POS_OWNER_USER_IDS`. High-privilege operations (assigning roles, bans, unbans, and direct database queries) are **never delegated directly to the LLM**. The AI layer strictly parses user intent, while execution is handled by an independent, verified, and hardcoded application layer.

### Rate-Limit Mitigation (Provider Pooling)

To prevent API rate-limiting issues, you can configure a fallback/load-balanced pool of multiple API keys and endpoints:

- `POS_AI_PROVIDER_KEYS` — CSV list of API keys.
- `POS_AI_PROVIDER_URLS` — CSV list of endpoint URLs (Optional; mapped sequentially by index).
- `POS_AI_PROVIDER_MODELS` — CSV list of model names (Optional; mapped sequentially by index).

Example:
`POS_AI_PROVIDER_KEYS=gh_key_1,gh_key_2,alt_key_3`

#### Production Deployment Examples for Railway

1) Multi-Key GitHub Models Pool:
- `POS_AI_PROVIDER_KEYS=gh_key_1,gh_key_2`
- `POS_AI_PROVIDER_URLS=https://models.github.ai/inference/chat/completions,https://models.github.ai/inference/chat/completions`
- `POS_AI_PROVIDER_MODELS=openai/gpt-4.1,openai/gpt-4.1`

2) Mixed Architecture Pool (GitHub + OpenAI-compatible APIs):
- `POS_AI_PROVIDER_KEYS=gh_key_1,openrouter_key,groq_key`
- `POS_AI_PROVIDER_URLS=https://models.github.ai/inference/chat/completions,https://openrouter.ai/api/v1/chat/completions,https://api.groq.com/openai/v1/chat/completions`
- `POS_AI_PROVIDER_MODELS=openai/gpt-4.1,meta-llama/llama-3.1-8b-instruct:free,llama-3.1-8b-instant`

3) Single-Key Default Mode:
If no pool is defined, the bot seamlessly falls back to single-key execution using `GITHUB_MODELS_TOKEN` or `POS_AI_API_KEY`.

### Recommended Freemium AI API Providers

- **OpenRouter:** Offers various free-tier models utilizing an OpenAI-compatible endpoint.
- **Groq:** Exceptional inference speeds, generous free-tier allotments, OpenAI-compatible.
- **Hugging Face Inference API:** Limited free tier tailored for open-source architectures.
- **Together AI:** Frequently provides initial platform credits upon registration.
- **Cloudflare Workers AI:** Accessible daily complimentary request limits.

*Please verify current allocation rates and terms of service directly with each provider prior to integration.*

### Automated System Logging Channels

- `PRIMARY_LOG_CHANNEL_ID`
- `UPDATE_LOG_CHANNEL_ID`
- `LOG_CATEGORY_ID`
- `LOG_CATEGORY_NAME`

## Local Installation & Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
