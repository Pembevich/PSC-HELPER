"""Persistent, owner-defined event workflows selected through native model tools.

The model writes a concrete workflow and its wording once. Gateway payloads are
data for allowlisted placeholders; they never become instructions or executable
code. Runs are claimed durably before Discord side effects and never replayed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import discord
import aiohttp

from config import POS_CREATOR_ID, POS_OWNER_USER_IDS
from join_gate import join_token_for_member, wait_for_join_security
from logging_utils import is_log_channel
from message_gate import wait_for_moderation
import storage

logger = logging.getLogger(__name__)
EVENTS = ("member_join", "member_remove", "message_create")
AUTOMATION_READ_TOOLS = {"list_automations", "automation_runs"}
AUTOMATION_WRITE_TOOLS = {"create_automation", "update_automation", "delete_automation"}
VARIABLES = {
    "event.type", "event.label", "event.time", "guild.id", "guild.name",
    "member.id", "member.name", "member.display_name", "member.mention",
    "member.joined_at", "member.created_at", "guild.member_count",
    "channel.id", "channel.name", "channel.mention", "message.id",
    "message.content", "message.url",
}
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_.]*)\}")
_TEMPLATE_HELP = (
    "Текст сочини сам по запросу владельца. Подстановки: "
    + ", ".join("{" + key + "}" for key in sorted(VARIABLES))
    + ". Это текстовый шаблон; Python, выражения и инструкции не исполняются."
)
_EMBED_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "maxLength": 256},
        "description": {"type": "string", "maxLength": 4096},
        "color": {"type": "integer", "minimum": 0, "maximum": 16777215},
        "footer": {"type": "string", "maxLength": 2048},
        "fields": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"name": {"type": "string", "maxLength": 256},
                           "value": {"type": "string", "maxLength": 1024},
                           "inline": {"type": "boolean"}},
            "required": ["name", "value"],
        }},
    },
}
_PROPERTIES: dict[str, Any] = {
    "name": {"type": "string", "maxLength": 100, "description": "Короткое понятное имя постоянного правила."},
    "trigger_events": {"type": "array", "minItems": 1, "maxItems": 3,
                       "items": {"type": "string", "enum": list(EVENTS)}},
    "channel_id": {"type": "string", "description": "Канал результата по умолчанию: ID, имя или current (канал запроса)."},
    "conditions": {"type": "object", "additionalProperties": False, "properties": {
        "source_channel_id": {"type": "string", "description": "Для message_create обязателен канал-источник; current = канал запроса."},
        "ignore_bots": {"type": "boolean", "description": "По умолчанию true. Сообщения любых ботов всегда игнорируются."},
        "role_id": {"type": "string", "description": "Срабатывает только для участников с этой существующей ролью."},
        "content_contains": {"type": "string", "maxLength": 200, "description": "Обязательная подстрока сообщения, без учёта регистра."},
    }},
    "actions": {"type": "array", "minItems": 1, "maxItems": 8, "description": "Последовательные действия. При ошибке цепочка останавливается.", "items": {
        "type": "object", "additionalProperties": False, "properties": {
            "type": {"type": "string", "enum": ["send_message", "add_role", "remove_role"]},
            "events": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": list(EVENTS)},
                       "description": "Необязательно: выполнить этот шаг только для перечисленных событий правила."},
            "channel_id": {"type": "string", "description": "Для send_message: иной канал, иначе канал правила."},
            "content": {"type": "string", "maxLength": 2000, "description": _TEMPLATE_HELP},
            "embed": _EMBED_SCHEMA,
            "role_id": {"type": "string", "description": "Для add_role/remove_role: существующая роль по ID или точному имени. Получатель — участник события; при выходе роли не меняются."},
        }, "required": ["type"],
    }},
    "cooldown_seconds": {"type": "integer", "minimum": 0, "maximum": 604800,
                         "description": "Минимальный промежуток между запусками всего правила; для message_create не менее 5 секунд (по умолчанию 10). Для входов/выходов по умолчанию 0."},
}


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "additionalProperties": False, "properties": properties, "required": required,
    }}}


AUTOMATION_TOOLS = [
    _schema("create_automation", "Создаёт постоянную автоматизацию по запросу владельца. События Discord запускают выбранную моделью цепочку действий даже после перезапуска. Пример: embeds о входе/выходе в текущий канал; роль при входе; сообщение по условию. Это реальное сохранение правила, не обещание наблюдать. Для изменения существующего сначала прочитай list_automations.", _PROPERTIES, ["name", "trigger_events", "actions"]),
    _schema("update_automation", "Изменяет существующее правило владельца по ID. Передаётся только нужная часть; actions/conditions/trigger_events заменяются целиком. enabled=false приостанавливает правило, true возобновляет.", {**_PROPERTIES, "automation_id": {"type": "integer", "minimum": 1}, "enabled": {"type": "boolean"}}, ["automation_id"]),
    _schema("list_automations", "Показывает постоянные правила этого сервера. С automation_id возвращает полное определение для проверки или изменения.", {"automation_id": {"type": "integer", "minimum": 1}}, []),
    _schema("delete_automation", "Удаляет постоянное правило владельца по ID; дальнейшие события его не запускают.", {"automation_id": {"type": "integer", "minimum": 1}}, ["automation_id"]),
    _schema("automation_runs", "Читает фактические запуски автоматизаций, результаты каждого шага, ошибки и Discord ID отправленных сообщений. Не утверждай выполнение без успешной записи.", {"automation_id": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, []),
]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label}: требуется целое число от {minimum} до {maximum}.")
    return value


def _text(value: Any, limit: int, label: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{label}: требуется {'непустой ' if required else ''}текст до {limit} символов.")
    unknown = set(_PLACEHOLDER.findall(value)) - VARIABLES
    if unknown:
        raise ValueError(f"Неизвестная подстановка: {', '.join(sorted(unknown))}.")
    return value


def render_template(template: str, values: dict[str, str]) -> str:
    """One substitution pass: substituted user content cannot introduce templates."""
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), ""), template)


def _object(value: Any, allowed: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"{label}: некорректные поля объекта.")
    return value


def _resolve_channel(guild: discord.Guild, ident: Any, current: Any) -> Any:
    from pos_ai import resolve_channel_smart

    if not isinstance(ident, str):
        raise ValueError("Канал должен быть строкой: ID, имя или current.")
    channel = current if not ident.strip() or ident.strip().lower() == "current" else resolve_channel_smart(guild, ident)
    if (channel is None or getattr(getattr(channel, "guild", None), "id", None) != guild.id
            or not callable(getattr(channel, "send", None)) or not callable(getattr(channel, "permissions_for", None))):
        raise ValueError(f"Не найден доступный текстовый канал этого сервера: {ident}.")
    return channel


def _resolve_role(guild: discord.Guild, ident: Any) -> Any:
    from pos_ai import resolve_role_smart

    if not isinstance(ident, str) or not ident.strip():
        raise ValueError("Нужен ID или точное имя роли.")
    role = resolve_role_smart(guild, ident)
    if role is None or role.is_default() or role.managed:
        raise ValueError(f"Недоступная роль: {ident}.")
    return role


def _check_channel(channel: Any, guild: discord.Guild, *, embed: bool) -> None:
    if guild.me is None:
        raise ValueError("Бот ещё не получил своё состояние на сервере.")
    perms = channel.permissions_for(guild.me)
    send_permission = perms.send_messages_in_threads if isinstance(channel, discord.Thread) else perms.send_messages
    if not perms.view_channel or not send_permission or (embed and not perms.embed_links):
        raise ValueError(f"У бота нет прав чтения/отправки{' embed' if embed else ''} в канале {channel.id}.")
    if isinstance(channel, discord.Thread) and (channel.archived or channel.locked):
        raise ValueError("Поток архивирован или закрыт.")


def _check_role(guild: discord.Guild, role: Any, member: Any = None) -> None:
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles or role >= me.top_role:
        raise ValueError(f"Бот не может управлять ролью {role.id}: права или иерархия.")
    if member is not None:
        protected = set(POS_OWNER_USER_IDS) | {POS_CREATOR_ID, me.id, guild.owner_id}
        if member.id in protected or member.bot or member.top_role >= me.top_role:
            raise ValueError("Автоматизация не меняет роли владельца, ботов и участников выше бота.")


def _normalize_embed(value: Any) -> dict:
    raw = _object(value, set(_EMBED_SCHEMA["properties"]), "embed")
    result: dict[str, Any] = {}
    for key, limit in (("title", 256), ("description", 4096), ("footer", 2048)):
        if key in raw:
            result[key] = _text(raw[key], limit, "embed." + key)
    if "color" in raw:
        result["color"] = _integer(raw["color"], "embed.color", 0, 0xFFFFFF)
    if "fields" in raw:
        if not isinstance(raw["fields"], list) or len(raw["fields"]) > 10:
            raise ValueError("embed.fields: максимум 10 полей.")
        result["fields"] = []
        for field in raw["fields"]:
            field = _object(field, {"name", "value", "inline"}, "embed.field")
            if not isinstance(field.get("inline", False), bool):
                raise ValueError("embed.field.inline должен быть boolean.")
            result["fields"].append({"name": _text(field.get("name"), 256, "field.name", required=True),
                                     "value": _text(field.get("value"), 1024, "field.value", required=True),
                                     "inline": field.get("inline", False)})
    if not any(result.get(key) for key in ("title", "description", "fields", "footer")):
        raise ValueError("Embed должен содержать текст.")
    return result


def normalize_workflow(guild: discord.Guild, current_channel: Any, args: dict) -> tuple[list[str], int, dict]:
    triggers = args.get("trigger_events")
    if not isinstance(triggers, list) or not triggers or any(event not in EVENTS for event in triggers):
        raise ValueError("trigger_events: выбери member_join, member_remove и/или message_create.")
    triggers = list(dict.fromkeys(triggers))
    channel = _resolve_channel(guild, args.get("channel_id", "current"), current_channel)
    raw_conditions = _object(args.get("conditions", {}), {"source_channel_id", "ignore_bots", "role_id", "content_contains"}, "conditions")
    if not isinstance(raw_conditions.get("ignore_bots", True), bool):
        raise ValueError("ignore_bots должен быть boolean.")
    conditions: dict[str, Any] = {"ignore_bots": raw_conditions.get("ignore_bots", True)}
    if "source_channel_id" in raw_conditions or "message_create" in triggers:
        conditions["source_channel_id"] = str(_resolve_channel(guild, raw_conditions.get("source_channel_id", "current"), current_channel).id)
    if "role_id" in raw_conditions:
        conditions["role_id"] = str(_resolve_role(guild, raw_conditions["role_id"]).id)
    if "content_contains" in raw_conditions:
        conditions["content_contains"] = _text(raw_conditions["content_contains"], 200, "content_contains", required=True)
    raw_actions = args.get("actions")
    if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= 8:
        raise ValueError("Нужно от 1 до 8 конкретных действий.")
    actions = []
    for raw in raw_actions:
        raw = _object(raw, {"type", "events", "channel_id", "content", "embed", "role_id"}, "action")
        kind = raw.get("type")
        action_events = raw.get("events", triggers)
        if not isinstance(action_events, list) or not action_events or any(event not in triggers for event in action_events):
            raise ValueError("action.events должны быть подмножеством trigger_events.")
        action: dict[str, Any] = {"type": kind, "events": list(dict.fromkeys(action_events))}
        if kind == "send_message":
            if "role_id" in raw:
                raise ValueError("send_message не принимает role_id.")
            target = _resolve_channel(guild, raw.get("channel_id", str(channel.id)), current_channel)
            action["channel_id"] = str(target.id)
            if "content" in raw:
                action["content"] = _text(raw["content"], 2000, "content")
            if "embed" in raw:
                action["embed"] = _normalize_embed(raw["embed"])
            if not action.get("content") and not action.get("embed"):
                raise ValueError("send_message требует content или embed.")
            _check_channel(target, guild, embed="embed" in action)
        elif kind in {"add_role", "remove_role"}:
            if set(raw) & {"content", "embed", "channel_id"}:
                raise ValueError("Действие с ролью не принимает поля отправки сообщения.")
            if "member_remove" in action_events:
                raise ValueError("Нельзя менять роль вышедшего участника; ограничь action.events.")
            role = _resolve_role(guild, raw.get("role_id"))
            _check_role(guild, role)
            action["role_id"] = str(role.id)
        else:
            raise ValueError("Неизвестное действие автоматизации.")
        actions.append(action)
    for event in triggers:
        if not any(event in action["events"] for action in actions):
            raise ValueError(f"Для события {event} нет действий.")
    cooldown = _integer(args.get("cooldown_seconds", 10 if "message_create" in triggers else 0), "cooldown_seconds", 0, 604800)
    if "message_create" in triggers and cooldown < 5:
        raise ValueError("Для message_create cooldown_seconds должен быть не менее 5.")
    return triggers, channel.id, {"conditions": conditions, "actions": actions, "cooldown_seconds": cooldown}


def _can_read_rule(message: discord.Message, rule: dict) -> bool:
    if message.author.id == POS_CREATOR_ID:
        return True
    guild = message.guild
    if guild is None or not isinstance(message.author, discord.Member):
        return False
    channel_ids = {rule["channel_id"]}
    definition = rule["definition"]
    source_id = definition.get("conditions", {}).get("source_channel_id")
    if source_id:
        channel_ids.add(int(source_id))
    channel_ids.update(int(action["channel_id"]) for action in definition.get("actions", []) if action.get("channel_id"))
    for channel_id in channel_ids:
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None or not channel.permissions_for(message.author).view_channel:
            return False
    return True


async def execute_automation_tool(bot: Any, message: discord.Message, name: str, args: dict) -> str:
    """No write is permitted on the strength of a model-supplied owner identity."""
    del bot
    guild = message.guild
    if guild is None:
        return "Ошибка: постоянные автоматизации доступны только на сервере."
    if name in AUTOMATION_WRITE_TOOLS and message.author.id != POS_CREATOR_ID:
        return "Ошибка: постоянные автоматизации настраивает только владелец P.OS."
    try:
        properties = next(item["function"]["parameters"]["properties"] for item in AUTOMATION_TOOLS if item["function"]["name"] == name)
        _object(args, set(properties), "Аргументы")
        rule_id = _integer(args["automation_id"], "automation_id", 1, 2**63 - 1) if "automation_id" in args else None
        rules = await storage.list_pos_automations(guild.id)
        visible = [rule for rule in rules if _can_read_rule(message, rule)]
        existing = next((rule for rule in visible if rule["id"] == rule_id), None)
        if rule_id is not None and existing is None and not (name == "automation_runs" and message.author.id == POS_CREATOR_ID):
            return "Ошибка: автоматизация с этим ID не найдена или недоступна на этом сервере."
        if name == "list_automations":
            if existing:
                return _json(existing)
            return _json({"automations": [{key: rule[key] for key in ("id", "name", "trigger_events", "channel_id", "enabled")} | {
                "actions": [action["type"] for action in rule["definition"].get("actions", [])],
            } for rule in visible]})
        if name == "automation_runs":
            limit = _integer(args.get("limit", 10), "limit", 1, 20)
            runs = await storage.list_pos_automation_runs(guild.id, automation_id=rule_id, limit=limit)
            visible_ids = {rule["id"] for rule in visible}
            return _json({"runs": [run for run in runs if message.author.id == POS_CREATOR_ID or run["automation_id"] in visible_ids]})
        if name == "delete_automation":
            if rule_id is None:
                raise ValueError("Требуется automation_id.")
            deleted = await storage.delete_pos_automation(guild.id, rule_id)
            return f"Автоматизация {rule_id} удалена." if deleted else "Ошибка: правило уже удалено."
        if name not in {"create_automation", "update_automation"}:
            raise ValueError("Неизвестный инструмент автоматизации.")
        if name == "update_automation" and existing is None:
            raise ValueError("Требуется automation_id существующего правила.")
        if existing is not None and name == "update_automation" and set(args) == {"automation_id", "enabled"} and args["enabled"] is False:
            # A broken destination/removed role must never prevent stopping a
            # rule. Resuming still validates all live Discord resources below.
            stopped = await storage.set_pos_automation_enabled(guild.id, existing["id"], False)
            return _json({"status": "paused", "automation_id": rule_id}) if stopped else "Ошибка: правило уже удалено."
        merged = ({"name": existing["name"], "trigger_events": existing["trigger_events"],
                   "channel_id": str(existing["channel_id"]), **existing["definition"]} if existing else {})
        merged.update({key: value for key, value in args.items() if key not in {"automation_id", "enabled"}})
        rule_name = _text(merged.get("name"), 100, "name", required=True).strip()
        triggers, channel_id, definition = normalize_workflow(guild, message.channel, merged)
        enabled = args.get("enabled", existing["enabled"] if existing else True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled должен быть boolean.")
        if name == "create_automation" and any(rule["name"].casefold() == rule_name.casefold() for rule in rules):
            raise ValueError("Правило с таким именем уже существует. Прочитай его ID и используй update_automation.")
        rule = await storage.upsert_pos_automation(guild.id, POS_CREATOR_ID, rule_name, triggers, channel_id, definition,
                                                  automation_id=rule_id, enabled=enabled)
        return _json({"status": "created" if existing is None else "updated", "automation": rule})
    except (ValueError, TypeError, KeyError, StopIteration) as exc:
        return f"Ошибка: автоматизация — {exc}"


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else "неизвестно"


def event_values(event: str, member: Any, message: Any = None) -> dict[str, str]:
    guild = member.guild
    channel = getattr(message, "channel", None)
    # Values originate from Discord, never from template evaluation. Mentions
    # are display-only; every send also explicitly disables AllowedMentions.
    return {
        "event.type": event,
        "event.label": {"member_join": "Участник вошёл", "member_remove": "Участник вышел", "message_create": "Новое сообщение"}[event],
        "event.time": datetime.now(timezone.utc).isoformat(),
        "guild.id": str(guild.id), "guild.name": str(guild.name),
        "guild.member_count": str(guild.member_count or 0),
        "member.id": str(member.id), "member.name": str(member.name),
        "member.display_name": str(member.display_name), "member.mention": f"<@{member.id}>",
        "member.joined_at": _iso(member.joined_at), "member.created_at": _iso(member.created_at),
        "channel.id": str(channel.id) if channel else "", "channel.name": str(channel.name) if channel else "",
        "channel.mention": f"<#{channel.id}>" if channel else "",
        "message.id": str(message.id) if message else "", "message.content": str(message.content or "") if message else "",
        "message.url": str(message.jump_url) if message else "",
    }


def event_key(event: str, member: Any, message: Any = None) -> str:
    if event == "message_create":
        return f"message_create:{message.id}"
    # joined_at is stable across process restarts and distinguishes re-joins.
    # For incomplete leave events, no session identifier exists: conservatively
    # dedupe that unknown membership instead of inventing a replayable timestamp.
    joined_at = getattr(member, "joined_at", None)
    session = joined_at.isoformat() if isinstance(joined_at, datetime) else "unknown-membership"
    return f"{event}:{member.id}:{session}"


def _matches(rule: dict, event: str, member: Any, message: Any) -> bool:
    conditions = rule["definition"].get("conditions", {})
    if conditions.get("ignore_bots", True) and member.bot:
        return False
    role_id = conditions.get("role_id")
    if role_id and int(role_id) not in {role.id for role in member.roles}:
        return False
    if event == "message_create":
        if str(message.channel.id) != conditions.get("source_channel_id"):
            return False
        contains = conditions.get("content_contains", "")
        if contains and contains.casefold() not in (message.content or "").casefold():
            return False
    return True


def _render_action(action: dict, values: dict[str, str]) -> dict:
    result = dict(action)
    if action["type"] != "send_message":
        return result
    if "content" in action:
        result["content"] = render_template(action["content"], values)[:2000]
    if "embed" in action:
        raw = action["embed"]
        # Discord enforces 6000 characters over all textual embed components.
        remaining = 6000

        def render(text: str, limit: int) -> str:
            nonlocal remaining
            rendered = render_template(text, values)[:min(limit, remaining)]
            remaining -= len(rendered)
            return rendered

        embed = discord.Embed(color=raw.get("color", 0x5865F2))
        embed.title = render(raw.get("title", ""), 256) or None
        embed.description = render(raw.get("description", ""), 4096) or None
        for field in raw.get("fields", []):
            if remaining < 2:
                break
            field_name = render(field["name"], min(256, remaining - 1))
            if not field_name:
                field_name = "\u200b"
                remaining -= 1
            field_value = render(field["value"], 1024)
            if not field_value:
                field_value = "\u200b"
                remaining -= 1
            embed.add_field(name=field_name, value=field_value, inline=field.get("inline", False))
        if raw.get("footer") and remaining:
            embed.set_footer(text=render(raw["footer"], 2048))
        result["embed"] = embed
    return result


class AutomationRuntime:
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._slots = asyncio.Semaphore(4)
        self._active: set[asyncio.Task] = set()
        self._closing = False

    async def close(self) -> None:
        self._closing = True
        tasks = list(self._active)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def handle_event(self, event: str, member: Any, *, message: Any = None) -> None:
        guild = getattr(member, "guild", None)
        if self._closing or event not in EVENTS or guild is None:
            return
        if getattr(getattr(self.bot, "user", None), "id", None) == member.id:
            return
        if event == "message_create" and (message is None or member.bot or getattr(message, "webhook_id", None) or is_log_channel(message.channel)):
            return
        task = asyncio.current_task()
        if task:
            self._active.add(task)
        try:
            rules = await storage.list_pos_automations(guild.id, enabled_only=True, trigger_event=event)
            rules = [rule for rule in rules if rule["owner_id"] == POS_CREATOR_ID and _matches(rule, event, member, message)]
            if not rules:
                return
            if event == "message_create" and await wait_for_moderation(message.id) is not False:
                return
            suppress_roles = False
            if event == "member_join" and not member.bot:
                suppress_roles = await wait_for_join_security(guild.id, member.id, join_token=join_token_for_member(member)) is not False
            async with self._slots:
                if self._closing:
                    return
                for rule in rules:
                    await self._run_rule(rule, event, member, message, suppress_roles=suppress_roles)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка обработки автоматизации %s на сервере %s", event, guild.id)
        finally:
            if task:
                self._active.discard(task)

    async def _run_rule(self, rule: dict, event: str, member: Any, message: Any, *, suppress_roles: bool) -> None:
        run_id = await storage.claim_pos_automation_run(member.guild.id, rule["id"], event_key(event, member, message),
                                                         cooldown_seconds=rule["definition"].get("cooldown_seconds", 0),
                                                         expected_revision=rule["revision"])
        if run_id is None:
            return
        results: list[dict] = []
        status = "completed"
        try:
            actions = [_render_action(action, event_values(event, member, message)) for action in rule["definition"]["actions"] if event in action["events"]]
            for index, action in enumerate(actions):
                # Disabling/deleting a rule also stops a previously queued chain.
                live = await storage.list_pos_automations(member.guild.id, enabled_only=True)
                if self._closing or not any(item["id"] == rule["id"] and item["revision"] == rule["revision"] for item in live):
                    results.append({"step": index + 1, "status": "skipped", "reason": "Правило остановлено или изменено."})
                    status = "partial" if results[:-1] else "skipped"
                    break
                if suppress_roles and action["type"] in {"add_role", "remove_role"}:
                    results.append({"step": index + 1, "status": "skipped", "reason": "Антирейд не разрешил изменение ролей."})
                    status = "partial" if results[:-1] else "skipped"
                    break
                result = await asyncio.wait_for(self._execute_action(member, action, rule["id"]), timeout=60)
                results.append({"step": index + 1, "action": action["type"], "status": "completed", **result})
        except asyncio.CancelledError:
            status = "unknown"
            results.append({"status": "unknown", "reason": "Остановка процесса во время цепочки; автоматического повтора нет."})
            raise
        except (TimeoutError, aiohttp.ClientError) as exc:
            status = "unknown"
            results.append({"status": "unknown", "reason": "Ответ Discord не получен; действие могло выполниться. Автоматического повтора нет."})
            logger.warning("Автоматизация %s, запуск %s: неопределённый результат (%s)", rule["id"], run_id, type(exc).__name__)
        except Exception as exc:
            status = "partial" if results else "failed"
            results.append({"status": "failed", "reason": str(exc)[:500]})
            logger.warning("Автоматизация %s, запуск %s: %s", rule["id"], run_id, exc)
        finally:
            await storage.finish_pos_automation_run(run_id, status=status, result={"event": event, "steps": results})

    async def _execute_action(self, member: Any, action: dict, automation_id: int) -> dict:
        guild = member.guild
        if action["type"] == "send_message":
            channel = guild.get_channel_or_thread(int(action["channel_id"]))
            if channel is None:
                raise ValueError("Канал автоматизации удалён или недоступен.")
            _check_channel(channel, guild, embed="embed" in action)
            sent = await channel.send(content=action.get("content") or None, embed=action.get("embed"), allowed_mentions=discord.AllowedMentions.none())
            return {"channel_id": channel.id, "message_id": sent.id}
        role = guild.get_role(int(action["role_id"]))
        if role is None or role.is_default() or role.managed:
            raise ValueError("Роль автоматизации удалена или управляется интеграцией.")
        # Refresh membership before a role write: cached roles may predate a
        # timeout/quarantine or the preceding step of the same workflow.
        target = await guild.fetch_member(member.id)
        _check_role(guild, role, target)
        has_role = role.id in {item.id for item in target.roles}
        desired = action["type"] == "add_role"
        if has_role == desired:
            return {"member_id": target.id, "role_id": role.id, "unchanged": True}
        if desired:
            await target.add_roles(role, reason=f"P.OS automation {automation_id}; owner {POS_CREATOR_ID}")
        else:
            await target.remove_roles(role, reason=f"P.OS automation {automation_id}; owner {POS_CREATOR_ID}")
        return {"member_id": target.id, "role_id": role.id}
