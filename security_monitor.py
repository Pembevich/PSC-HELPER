"""Read-only Discord security posture snapshots and deterministic diffs."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import discord


CRITICAL_PERMISSIONS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "ban_members",
    "kick_members",
    "moderate_members",
    "mention_everyone",
)
POS_REQUIRED_PERMISSIONS = (
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_messages",
    "manage_webhooks",
    "moderate_members",
    "ban_members",
    "kick_members",
    "view_audit_log",
)
CHANNEL_MONITORED_PERMISSIONS = (
    "view_channel",
    "manage_channels",
    "manage_webhooks",
    "manage_messages",
    "mention_everyone",
    "create_instant_invite",
)


def _enum_level(value: object) -> int:
    raw = getattr(value, "value", value)
    if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
        return -1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def _enabled_permissions(permissions: object, names: tuple[str, ...]) -> list[str]:
    return sorted(name for name in names if bool(getattr(permissions, name, False)))


def _role_snapshot(role: discord.Role) -> dict[str, Any]:
    return {
        "id": int(role.id),
        "name": str(role.name)[:100],
        "position": int(getattr(role, "position", 0)),
        "managed": bool(getattr(role, "managed", False)),
        "permissions": _enabled_permissions(role.permissions, CRITICAL_PERMISSIONS),
    }


def _member_is_admin_bot(member: discord.Member) -> bool:
    return bool(
        getattr(member, "bot", False)
        and getattr(getattr(member, "guild_permissions", None), "administrator", False)
    )


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_set(value: object) -> set[str]:
    return {item for item in _items(value) if isinstance(item, str)}


def _integer_set(value: object) -> set[int]:
    result: set[int] = set()
    for item in _items(value):
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            continue
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _channel_snapshot(
    channel: discord.abc.GuildChannel,
    default_role: discord.Role,
) -> dict[str, Any]:
    effective = channel.permissions_for(default_role)
    explicit_allow, _ = channel.overwrites_for(default_role).pair()
    return {
        "id": int(channel.id),
        "name": str(channel.name)[:100],
        "type": str(getattr(channel, "type", "unknown"))[:50],
        "effective_permissions": _enabled_permissions(
            effective,
            CHANNEL_MONITORED_PERMISSIONS,
        ),
        "explicit_allows": _enabled_permissions(
            explicit_allow,
            CHANNEL_MONITORED_PERMISSIONS,
        ),
    }


async def collect_security_snapshot(guild: discord.Guild) -> dict[str, Any]:
    me = guild.me
    bot_permissions = getattr(me, "guild_permissions", discord.Permissions.none())
    roles = sorted(
        (_role_snapshot(role) for role in guild.roles if not role.is_default()),
        key=lambda role: role["id"],
    )
    admin_bots = sorted(
        (
            {"id": int(member.id), "name": str(member)[:100]}
            for member in guild.members
            if _member_is_admin_bot(member)
            and (me is None or int(member.id) != int(me.id))
        ),
        key=lambda member: str(member["id"]),
    )
    channels = sorted(
        (_channel_snapshot(channel, guild.default_role) for channel in guild.channels),
        key=lambda channel: channel["id"],
    )

    webhook_state: dict[str, Any] = {"available": False, "items": []}
    if bool(getattr(bot_permissions, "manage_webhooks", False)):
        try:
            webhooks = await guild.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            pass
        else:
            webhook_state = {
                "available": True,
                "items": sorted(
                    (
                        {
                            "id": int(webhook.id),
                            "name": str(webhook.name or "без имени")[:100],
                            "channel_id": int(webhook.channel_id) if webhook.channel_id else None,
                        }
                        for webhook in webhooks
                    ),
                    key=lambda webhook: int(webhook["id"]),
                ),
            }

    automod_state: dict[str, Any] = {"available": False, "items": []}
    if bool(getattr(bot_permissions, "manage_guild", False)):
        try:
            rules = await guild.fetch_automod_rules()
        except (discord.Forbidden, discord.HTTPException):
            pass
        else:
            automod_state = {
                "available": True,
                "items": sorted(
                    (
                        {
                            "id": int(rule.id),
                            "name": str(rule.name)[:100],
                            "enabled": bool(rule.enabled),
                            "event_type": _enum_level(rule.event_type),
                            "trigger_type": _enum_level(getattr(rule.trigger, "type", -1)),
                            "trigger_hash": _stable_hash(
                                rule.trigger.to_metadata_dict() or {}
                            ),
                            "actions": sorted(
                                _enum_level(getattr(action, "type", -1))
                                for action in rule.actions
                            ),
                            "actions_hash": _stable_hash(
                                [action.to_dict() for action in rule.actions]
                            ),
                            "exempt_role_ids": sorted(int(value) for value in rule.exempt_role_ids),
                            "exempt_channel_ids": sorted(
                                int(value) for value in rule.exempt_channel_ids
                            ),
                        }
                        for rule in rules
                    ),
                    key=lambda rule: rule["id"],
                ),
            }

    default_permissions = getattr(guild.default_role, "permissions", discord.Permissions.none())
    return {
        "schema": 3,
        "guild_id": int(guild.id),
        "guild_name": str(guild.name)[:100],
        "mfa_level": _enum_level(getattr(guild, "mfa_level", -1)),
        "verification_level": _enum_level(getattr(guild, "verification_level", -1)),
        "explicit_content_filter": _enum_level(getattr(guild, "explicit_content_filter", -1)),
        "default_notifications": _enum_level(getattr(guild, "default_notifications", -1)),
        "everyone_permissions": _enabled_permissions(default_permissions, CRITICAL_PERMISSIONS),
        "bot_permissions": _enabled_permissions(bot_permissions, POS_REQUIRED_PERMISSIONS),
        "roles": roles,
        "channels": channels,
        "admin_bots": admin_bots,
        "webhooks": webhook_state,
        "automod": automod_state,
    }


def security_snapshot_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _index_by_id(items: object) -> dict[int, dict[str, Any]]:
    if not isinstance(items, list):
        return {}
    indexed: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if not isinstance(raw_id, (str, int)) or isinstance(raw_id, bool):
            continue
        try:
            indexed[int(raw_id)] = item
        except (TypeError, ValueError):
            continue
    return indexed


def assess_security_snapshot(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Return actionable risks already present in a single posture snapshot."""
    alerts: list[dict[str, str]] = []
    mfa_level = _enum_level(snapshot.get("mfa_level", -1))
    verification_level = _enum_level(snapshot.get("verification_level", -1))
    content_filter = _enum_level(snapshot.get("explicit_content_filter", -1))

    if mfa_level == 0:
        alerts.append({
            "severity": "critical",
            "title": "Для действий модерации не требуется 2FA",
            "detail": "mfa_level=0",
        })
    if 0 <= verification_level <= 1:
        alerts.append({
            "severity": "high",
            "title": "Слишком низкий уровень проверки участников",
            "detail": f"verification_level={verification_level}",
        })
    if 0 <= content_filter < 2:
        alerts.append({
            "severity": "high",
            "title": "Фильтр медиа Discord включён не для всех участников",
            "detail": f"explicit_content_filter={content_filter}",
        })

    everyone_permissions = _string_set(snapshot.get("everyone_permissions"))
    if everyone_permissions:
        alerts.append({
            "severity": "critical",
            "title": "@everyone уже имеет опасные права",
            "detail": ", ".join(sorted(everyone_permissions)),
        })

    bot_permissions = _string_set(snapshot.get("bot_permissions"))
    missing_bot_permissions = sorted(set(POS_REQUIRED_PERMISSIONS) - bot_permissions)
    if missing_bot_permissions:
        alerts.append({
            "severity": "high",
            "title": "P.OS не хватает прав для полного контура защиты",
            "detail": ", ".join(missing_bot_permissions),
        })

    risky_channels: list[str] = []
    for channel in _items(snapshot.get("channels")):
        if not isinstance(channel, dict):
            continue
        risky = (
            _string_set(channel.get("effective_permissions"))
            - {"view_channel"}
            - everyone_permissions
        )
        if risky:
            risky_channels.append(
                f"{channel.get('name', channel.get('id', '?'))}: {', '.join(sorted(risky))}"
            )
    if risky_channels:
        alerts.append({
            "severity": "high",
            "title": "В каналах @everyone имеет опасные права",
            "detail": "; ".join(risky_channels[:10]),
        })

    automod = _mapping(snapshot.get("automod"))
    automod_items = _items(automod.get("items"))
    if automod.get("available") and not any(
        isinstance(rule, dict) and rule.get("enabled") for rule in automod_items
    ):
        alerts.append({
            "severity": "medium",
            "title": "Нет активных правил Discord AutoMod",
            "detail": "Нужен независимый резервный слой фильтрации Discord",
        })

    admin_bots = _items(snapshot.get("admin_bots"))
    if admin_bots:
        labels = [
            f"{item.get('name', 'бот')} (`{item.get('id', '?')}`)"
            for item in admin_bots[:10]
            if isinstance(item, dict)
        ]
        alerts.append({
            "severity": "high",
            "title": "Сторонние боты имеют Administrator",
            "detail": ", ".join(labels) or f"Количество: {len(admin_bots)}",
        })

    webhooks = _mapping(snapshot.get("webhooks"))
    webhook_items = _items(webhooks.get("items"))
    if webhooks.get("available") and webhook_items:
        alerts.append({
            "severity": "medium",
            "title": "На сервере есть webhook",
            "detail": f"Количество: {len(webhook_items)}; проверь владельцев и каналы",
        })
    return alerts


def diff_security_snapshots(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []

    for key, title, severity in (
        ("mfa_level", "Ослаблено требование 2FA для модерации", "critical"),
        ("verification_level", "Ослаблен уровень проверки участников", "high"),
        ("explicit_content_filter", "Ослаблен фильтр медиа Discord", "high"),
    ):
        old_level = _enum_level(previous.get(key, -1))
        new_level = _enum_level(current.get(key, -1))
        if old_level >= 0 and new_level >= 0 and new_level < old_level:
            alerts.append({
                "severity": severity,
                "title": title,
                "detail": f"{old_level} -> {new_level}",
            })

    old_everyone = _string_set(previous.get("everyone_permissions"))
    new_everyone = _string_set(current.get("everyone_permissions"))
    gained_everyone = sorted(new_everyone - old_everyone)
    if gained_everyone:
        alerts.append({
            "severity": "critical",
            "title": "@everyone получил опасные права",
            "detail": ", ".join(gained_everyone),
        })

    old_channels = _index_by_id(previous.get("channels"))
    new_channels = _index_by_id(current.get("channels"))
    for channel_id, channel in new_channels.items():
        old_channel = old_channels.get(channel_id)
        if old_channel is None:
            # Do not report every existing channel when upgrading a schema-1
            # baseline. A genuinely new channel is covered when the prior
            # snapshot already contained channel posture data.
            if not isinstance(previous.get("channels"), list):
                continue
            risky_explicit = sorted(
                _string_set(channel.get("explicit_allows")) - {"view_channel"}
            )
            if risky_explicit:
                alerts.append({
                    "severity": "high",
                    "title": f"Новый канал даёт @everyone опасные права: {channel.get('name', channel_id)}",
                    "detail": ", ".join(risky_explicit),
                })
            continue

        old_effective = _string_set(old_channel.get("effective_permissions"))
        new_effective = _string_set(channel.get("effective_permissions"))
        gained_effective = new_effective - old_effective
        if "view_channel" in gained_effective:
            alerts.append({
                "severity": "medium",
                "title": f"Канал стал доступен @everyone: {channel.get('name', channel_id)}",
                "detail": f"channel_id={channel_id}",
            })
        risky_gained = sorted(
            gained_effective - {"view_channel"} - set(gained_everyone)
        )
        if risky_gained:
            alerts.append({
                "severity": "high",
                "title": f"@everyone получил опасные права в канале: {channel.get('name', channel_id)}",
                "detail": ", ".join(risky_gained),
            })

    old_roles = _index_by_id(previous.get("roles"))
    new_roles = _index_by_id(current.get("roles"))
    for role_id, role in new_roles.items():
        new_permissions = _string_set(role.get("permissions"))
        old_permissions = _string_set(old_roles.get(role_id, {}).get("permissions"))
        gained = sorted(new_permissions - old_permissions)
        if not gained:
            continue
        alerts.append({
            "severity": "critical" if "administrator" in gained else "high",
            "title": f"Роль получила опасные права: {role.get('name', role_id)}",
            "detail": ", ".join(gained),
        })

    old_bot_permissions = _string_set(previous.get("bot_permissions"))
    new_bot_permissions = _string_set(current.get("bot_permissions"))
    lost_bot_permissions = sorted(old_bot_permissions - new_bot_permissions)
    if lost_bot_permissions:
        alerts.append({
            "severity": "high",
            "title": "P.OS потерял права защиты",
            "detail": ", ".join(lost_bot_permissions),
        })

    old_admin_bots = _index_by_id(previous.get("admin_bots"))
    new_admin_bots = _index_by_id(current.get("admin_bots"))
    for bot_id in sorted(set(new_admin_bots) - set(old_admin_bots)):
        alerts.append({
            "severity": "high",
            "title": "Новый бот с Administrator",
            "detail": f"{new_admin_bots[bot_id].get('name', 'бот')} (`{bot_id}`)",
        })

    old_webhooks = _mapping(previous.get("webhooks"))
    new_webhooks = _mapping(current.get("webhooks"))
    if old_webhooks.get("available") and new_webhooks.get("available"):
        old_items = _index_by_id(old_webhooks.get("items"))
        new_items = _index_by_id(new_webhooks.get("items"))
        for webhook_id in sorted(set(new_items) - set(old_items)):
            alerts.append({
                "severity": "high",
                "title": "Создан новый webhook",
                "detail": f"{new_items[webhook_id].get('name', 'webhook')} (`{webhook_id}`)",
            })
        for webhook_id in sorted(set(new_items) & set(old_items)):
            old_webhook = old_items[webhook_id]
            new_webhook = new_items[webhook_id]
            if old_webhook.get("channel_id") != new_webhook.get("channel_id"):
                alerts.append({
                    "severity": "high",
                    "title": "Webhook перенесён в другой канал",
                    "detail": (
                        f"{new_webhook.get('name', 'webhook')} (`{webhook_id}`): "
                        f"{old_webhook.get('channel_id')} -> {new_webhook.get('channel_id')}"
                    ),
                })

    old_automod = _mapping(previous.get("automod"))
    new_automod = _mapping(current.get("automod"))
    if old_automod.get("available") and new_automod.get("available"):
        compare_rule_details = _enum_level(previous.get("schema", 1)) >= 3
        old_items = _index_by_id(old_automod.get("items"))
        new_items = _index_by_id(new_automod.get("items"))
        for rule_id, old_rule in old_items.items():
            if not old_rule.get("enabled"):
                continue
            new_rule = new_items.get(rule_id)
            if new_rule is None:
                alerts.append({
                    "severity": "high",
                    "title": "Удалено правило Discord AutoMod",
                    "detail": f"{old_rule.get('name', 'правило')} (`{rule_id}`)",
                })
            elif not new_rule.get("enabled"):
                alerts.append({
                    "severity": "high",
                    "title": "Отключено правило Discord AutoMod",
                    "detail": f"{old_rule.get('name', 'правило')} (`{rule_id}`)",
                })
            else:
                removed_actions = sorted(
                    _integer_set(old_rule.get("actions"))
                    - _integer_set(new_rule.get("actions"))
                )
                if removed_actions:
                    alerts.append({
                        "severity": "high",
                        "title": "Ослаблены действия Discord AutoMod",
                        "detail": (
                            f"{old_rule.get('name', 'правило')} (`{rule_id}`), "
                            f"удалены action types: {removed_actions}"
                        ),
                    })
                if not compare_rule_details:
                    continue
                added_role_exemptions = sorted(
                    _integer_set(new_rule.get("exempt_role_ids"))
                    - _integer_set(old_rule.get("exempt_role_ids"))
                )
                added_channel_exemptions = sorted(
                    _integer_set(new_rule.get("exempt_channel_ids"))
                    - _integer_set(old_rule.get("exempt_channel_ids"))
                )
                if added_role_exemptions or added_channel_exemptions:
                    alerts.append({
                        "severity": "high",
                        "title": "Расширены исключения Discord AutoMod",
                        "detail": (
                            f"{old_rule.get('name', 'правило')} (`{rule_id}`), "
                            f"roles={added_role_exemptions[:20]}, "
                            f"channels={added_channel_exemptions[:20]}"
                        ),
                    })
                action_details_changed = (
                    old_rule.get("actions_hash") != new_rule.get("actions_hash")
                )
                if (
                    old_rule.get("event_type") != new_rule.get("event_type")
                    or (action_details_changed and not removed_actions)
                ):
                    alerts.append({
                        "severity": "high",
                        "title": "Изменены действия Discord AutoMod",
                        "detail": f"{old_rule.get('name', 'правило')} (`{rule_id}`)",
                    })
                if old_rule.get("trigger_hash") != new_rule.get("trigger_hash"):
                    alerts.append({
                        "severity": "medium",
                        "title": "Изменены условия Discord AutoMod",
                        "detail": f"{old_rule.get('name', 'правило')} (`{rule_id}`)",
                    })
    return alerts


def summarize_security_snapshot(snapshot: dict[str, Any]) -> str:
    risky_roles = [
        role for role in _items(snapshot.get("roles"))
        if isinstance(role, dict) and role.get("permissions")
    ]
    webhooks = _mapping(snapshot.get("webhooks"))
    automod = _mapping(snapshot.get("automod"))
    risky_channels = [
        channel for channel in _items(snapshot.get("channels"))
        if isinstance(channel, dict)
        and _string_set(channel.get("effective_permissions")) - {"view_channel"}
    ]
    everyone_permissions = _items(snapshot.get("everyone_permissions"))
    admin_bots = _items(snapshot.get("admin_bots"))
    webhook_items = _items(webhooks.get("items"))
    automod_items = _items(automod.get("items"))
    return (
        f"2FA={snapshot.get('mfa_level')}; verification={snapshot.get('verification_level')}; "
        f"content_filter={snapshot.get('explicit_content_filter')}; "
        f"опасных прав @everyone={len(everyone_permissions)}; "
        f"опасных channel-overwrite={len(risky_channels)}; "
        f"привилегированных ролей={len(risky_roles)}; admin-ботов={len(admin_bots)}; "
        f"webhooks={len(webhook_items) if webhooks.get('available') else 'нет доступа'}; "
        f"AutoMod={len(automod_items) if automod.get('available') else 'нет доступа'}"
    )
