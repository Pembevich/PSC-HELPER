"""Owner-issued, durable administration scopes bound to real Discord identities."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from config import POS_CREATOR_ID
import storage

logger = logging.getLogger(__name__)
BETA_MANAGEMENT_TOOLS = frozenset({"manage_beta_server_group", "set_beta_administrator"})
BETA_ACCESS_READ_TOOLS = frozenset({"list_beta_access"})
# Explicit allowlist: global memory, owner bridges, ignore policy, process
# control and leaving servers are never inherited from a beta grant.
BETA_GUILD_TOOLS = frozenset({
    "list_servers", "ban_user", "unban_user", "timeout_user", "untimeout_user",
    "kick_user", "set_nickname", "add_role", "remove_role", "create_role",
    "delete_role", "edit_role", "create_channel", "delete_channel", "edit_channel",
    "set_channel_permission", "lock_channel", "unlock_channel", "create_thread",
    "archive_thread", "edit_server", "voice_action", "security_scan",
    "set_security_preset", "create_invite", "delete_messages", "setup_logging",
    "send_message", "get_settings", "update_settings", "list_members", "user_info",
    "read_messages", "search_logs", "search_pings", "bulk_user_action",
    "list_channels", "list_roles", "read_audit_log", "ping_user", "dm_user",
    "lift_restrictions", "deactivate_raid_mode", "manage_message", "manage_reaction",
    "list_invites", "revoke_invite", "list_webhooks", "delete_webhook",
    "list_automod_rules", "manage_automod_rule", "list_scheduled_events",
    "manage_scheduled_event", "create_forum_post", "set_server_safety",
    "list_emojis", "manage_emoji", "list_stickers", "manage_sticker",
    "list_bans", "list_threads", "send_poll", "create_automation",
    "update_automation", "delete_automation", "list_automations", "automation_runs",
})


@dataclass(frozen=True, slots=True)
class BetaAccess:
    actor_id: int
    source_guild_id: int
    guild_ids: frozenset[int]
    group_ids: tuple[str, ...]

    def allows(self, guild_id: int) -> bool:
        return self.source_guild_id in self.guild_ids and guild_id in self.guild_ids


async def get_beta_access(
    actor_id: int, source_guild_id: int, *, db_path: str = storage.DEFAULT_DB_PATH,
) -> BetaAccess | None:
    """A source outside the assigned groups cannot act as an administration hub."""
    if actor_id == POS_CREATOR_ID:
        return None
    groups = await storage.list_beta_server_groups(actor_id, db_path)
    groups = [group for group in groups if str(source_guild_id) in group["server_ids"]]
    if not groups:
        return None
    return BetaAccess(actor_id, source_guild_id,
                      frozenset(int(value) for group in groups for value in group["server_ids"]),
                      tuple(group["group_id"] for group in groups))


async def beta_can_manage_guild(actor_id: int, guild_id: int) -> bool:
    return await get_beta_access(actor_id, guild_id) is not None


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "additionalProperties": False,
                           "properties": properties, "required": required}}}


_GROUP_ID = {"type": "string", "pattern": "^[a-z0-9_-]{1,64}$", "maxLength": 64,
             "description": "Точный постоянный ключ группы, например psc-beta."}
BETA_TOOLS = [
    _schema("manage_beta_server_group", "Только Пумба: создаёт/изменяет или удаляет группу бета-администрирования по ID серверов. upsert полностью заменяет состав группы, сохраняя её назначения; delete отзывает все назначения. Сначала прочитай list_beta_access.", {
        "action": {"type": "string", "enum": ["upsert", "delete"]},
        "group_id": _GROUP_ID,
        "name": {"type": "string", "minLength": 1, "maxLength": 100},
        "server_ids": {"type": "array", "minItems": 1, "maxItems": 50, "uniqueItems": True,
                       "items": {"type": "string", "pattern": "^[0-9]{1,19}$"}},
    }, ["action", "group_id"]),
    _schema("set_beta_administrator", "Только Пумба: выдаёт (enabled=true) или отзывает (false) бета-доступ реальному Discord user_id к существующей группе. Не назначает владельцем P.OS и не даёт доступа к другим группам.", {
        "group_id": _GROUP_ID, "user_id": {"type": "string", "pattern": "^[0-9]{1,19}$"},
        "enabled": {"type": "boolean"},
    }, ["group_id", "user_id", "enabled"]),
    _schema("list_beta_access", "Читает проверенные бета-группы и их серверы по ID. Пумба видит все группы и назначения; бета-администратор видит только доступные из текущего сервера группы. Для цепочки по группе сначала получи этот список, затем вызывай инструменты отдельно с точным server_id_or_name каждого сервера.", {}, []),
]


async def execute_beta_tool(bot: Any, message: Any, name: str, args: dict) -> str:
    actor_id = message.author.id
    if name in BETA_MANAGEMENT_TOOLS and actor_id != POS_CREATOR_ID:
        return "Отказано: группы и бета-доступ назначает только Пумба."
    try:
        if name == "manage_beta_server_group":
            if args["action"] == "delete":
                if set(args) != {"action", "group_id"}:
                    raise ValueError("Для удаления нужны только action и group_id.")
                removed = await storage.delete_beta_server_group(args["group_id"], actor_id=actor_id)
                return "Бета-группа удалена; её доступ отозван." if removed else "Ошибка: бета-группа не найдена."
            if args["action"] != "upsert" or set(args) != {"action", "group_id", "name", "server_ids"}:
                raise ValueError("Для upsert нужны group_id, name и полный список server_ids.")
            await storage.save_beta_server_group(args["group_id"], args["name"], args["server_ids"], actor_id=actor_id)
            return "Бета-группа сохранена. Состав серверов: " + ", ".join(args["server_ids"]) + "."
        if name == "set_beta_administrator":
            if set(args) != {"group_id", "user_id", "enabled"}:
                raise ValueError("Нужны только group_id, user_id и enabled.")
            changed = await storage.set_beta_administrator(args["group_id"], args["user_id"], args["enabled"], actor_id=actor_id)
            verb = "выдан" if args["enabled"] else "отозван"
            suffix = "" if changed else " (состояние уже было таким)"
            return f"Бета-доступ пользователя {args['user_id']} к группе {args['group_id']} {verb}{suffix}."
        if name != "list_beta_access":
            raise ValueError("Неизвестный инструмент бета-доступа.")
        groups = await storage.list_beta_server_groups(actor_id)
        if actor_id != POS_CREATOR_ID:
            groups = [group for group in groups if str(message.guild.id) in group["server_ids"]]
        if actor_id != POS_CREATOR_ID and not groups:
            return "Отказано: на текущем сервере нет назначенного бета-доступа."
        connected = {guild.id for guild in getattr(bot, "guilds", ())}
        result = [{**group, "connected_server_ids": [value for value in group["server_ids"] if int(value) in connected]}
                  for group in groups]
        return json.dumps({"beta_groups": result}, ensure_ascii=False)
    except (ValueError, TypeError, KeyError, PermissionError) as exc:
        return f"Ошибка: бета-доступ — {exc}"
