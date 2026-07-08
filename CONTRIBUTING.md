# Contributing to PSC-HELPER

Thank you for helping improve PSC-HELPER. This project is a self-hosted Discord bot with moderation, AI assistance, application workflows, and audit logging.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest
```

For runtime setup, see [README.md](./README.md).

## How to contribute

1. Open an issue before large changes (new moderation rules, AI tool behavior, storage schema).
2. Fork the repository and create a branch from `main`.
3. Keep pull requests focused. Match existing code style and Russian user-facing strings unless you are updating English docs.
4. Add or update tests when behavior changes.
5. Open a pull request with a clear summary and test notes.

### Branch naming

| Prefix | Use for |
| --- | --- |
| `feat/` | New feature |
| `fix/` | Bug fix |
| `docs/` | Documentation only |
| `test/` | Test coverage |
| `refactor/` | Internal cleanup without behavior change |

### Commit messages

Keep messages concise and plain. Do not add AI co-author trailers.

## Roadmap labels

Use these GitHub labels when opening or triaging issues:

| Label | Scope |
| --- | --- |
| `good first issue` | Small, well-scoped tasks for new contributors |
| `documentation` | README, CONTRIBUTING, deployment docs, comments |
| `security` | Auth, permissions, prompt-injection guards, secrets handling |
| `ai-system` | P.OS persona, tool calls, provider routing, context/memory |
| `moderation` | Filters, anti-raid, spam, NSFW/scam detection, guild settings |

Browse the [open roadmap issues](https://github.com/Pembevich/PSC-HELPER/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement) for planned work.

## What needs help

- **Documentation:** deployment guides, per-guild configuration examples, moderation tuning recipes.
- **Security:** hardening tool-call policy, safer defaults, abuse-case tests.
- **AI system:** provider failover, context limits, better fallbacks when models fail.
- **Moderation:** new filter presets, clearer logging, anti-raid tuning for small communities.
- **Tests:** coverage for forms, logging, and cross-guild owner tools.

## Code areas

| Area | Main files |
| --- | --- |
| Bot startup | `main.py`, `cogs/` |
| Moderation | `moderation.py`, `antiraid.py`, `cogs/mod.py`, `cogs/security.py` |
| P.OS AI | `pos_ai.py`, `ai_client.py`, `cogs/ai_chat.py`, `cogs/ai_tools.py` |
| Forms | `forms.py`, `cogs/forms.py` |
| Storage | `storage.py`, `guild_config.py` |
| Logging | `logging_utils.py`, `cogs/logging_events.py` |
| Configuration | `config.py`, `.env.example` |

When changing AI or moderation behavior, prefer editing the top-level module the cog delegates to.

## Security reports

Do not open public issues for exploitable vulnerabilities. Contact the maintainer through GitHub with minimal reproduction details.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](./LICENSE).
