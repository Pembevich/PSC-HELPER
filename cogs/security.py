"""
cogs/security.py — антирейд (0.8).

Слушает заходы участников, прогоняет через antiraid.evaluate_join и применяет
реакцию (alert/quarantine/kick/ban) к подозрительным/свежим аккаунтам во время
рейда. Логику решений держит antiraid.py — здесь только эффекты Discord.
"""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import Color
from discord.ext import commands, tasks

import antiraid
from guild_config import get_settings as get_guild_settings
from join_gate import begin_join_security, finish_join_security
from logging_utils import send_log_embed
from moderation import quarantine_member
from config import POS_CREATOR_ID, SECURITY_MONITOR_INTERVAL_SECONDS
from security_monitor import (
    assess_security_snapshot,
    collect_security_snapshot,
    diff_security_snapshots,
    security_snapshot_hash,
    summarize_security_snapshot,
)
from storage import (
    add_ai_event,
    get_security_posture,
    set_raid_state,
    set_security_posture,
)

logger = logging.getLogger(__name__)
RAID_STATE_PERSIST_GRANULARITY_SECONDS = 30.0
POSTURE_EVENT_COALESCE_SECONDS = 2.0


class SecurityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._persisted_raid_until: dict[int, float] = {}
        self._posture_locks: dict[int, asyncio.Lock] = {}
        self._owner_posture_alert_at: dict[int, float] = {}
        self._posture_tasks: dict[int, asyncio.Task[None]] = {}
        self._posture_sources: dict[int, set[str]] = {}
        self._posture_guilds: dict[int, discord.Guild] = {}
        self._posture_event_coalesce_seconds = POSTURE_EVENT_COALESCE_SECONDS
        self._unloading = False

    async def cog_load(self) -> None:
        if not self.security_monitor.is_running():
            self.security_monitor.start()

    async def cog_unload(self) -> None:
        self._unloading = True
        self.security_monitor.cancel()
        tasks_to_cancel = list(self._posture_tasks.values())
        for task in tasks_to_cancel:
            task.cancel()
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        self._posture_tasks.clear()
        self._posture_sources.clear()
        self._posture_guilds.clear()

    def _posture_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._posture_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._posture_locks[guild_id] = lock
        return lock

    def _queue_security_posture_check(self, guild: discord.Guild, source: str) -> None:
        if self._unloading:
            return
        guild_id = guild.id
        self._posture_guilds[guild_id] = guild
        self._posture_sources.setdefault(guild_id, set()).add(source[:80])
        current_task = self._posture_tasks.get(guild_id)
        if current_task is not None and not current_task.done():
            return
        self._posture_tasks[guild_id] = asyncio.create_task(
            self._run_queued_posture_checks(guild_id),
            name=f"pos-security-posture-{guild_id}",
        )

    async def _run_queued_posture_checks(self, guild_id: int) -> None:
        try:
            await asyncio.sleep(self._posture_event_coalesce_seconds)
            while not self._unloading:
                sources = self._posture_sources.pop(guild_id, set())
                guild = self._posture_guilds.get(guild_id)
                if not sources or guild is None:
                    return
                try:
                    await self._check_security_posture(
                        guild,
                        source="+".join(sorted(sources))[:500],
                    )
                except Exception:
                    logger.exception(
                        "Ошибка событийного security posture scan для сервера %s.",
                        guild_id,
                    )
                if not self._posture_sources.get(guild_id):
                    return
                await asyncio.sleep(self._posture_event_coalesce_seconds)
        finally:
            self._posture_tasks.pop(guild_id, None)
            if not self._posture_sources.get(guild_id):
                self._posture_sources.pop(guild_id, None)
                self._posture_guilds.pop(guild_id, None)

    async def _alert_owner(self, guild: discord.Guild, text: str) -> None:
        owner_id = POS_CREATOR_ID
        owner = self.bot.get_user(owner_id)
        if not owner:
            try:
                owner = await self.bot.fetch_user(owner_id)
            except Exception:
                owner = None
        if owner:
            try:
                await owner.send(text[:1900])
            except Exception:
                pass

    async def _check_security_posture(
        self,
        guild: discord.Guild,
        *,
        source: str,
    ) -> None:
        async with self._posture_lock(guild.id):
            current = await collect_security_snapshot(guild)
            previous = await get_security_posture(guild.id)
            current_hash = security_snapshot_hash(current)
            if previous is not None and security_snapshot_hash(previous) == current_hash:
                return

            alerts = diff_security_snapshots(previous, current) if previous else []
            if previous is None:
                baseline_alerts = assess_security_snapshot(current)
                await add_ai_event(
                    guild_id=guild.id,
                    event_type="security:posture_baseline",
                    summary="P.OS сохранил baseline безопасности сервера",
                    details={
                        "source": source,
                        "summary": summarize_security_snapshot(current),
                        "alerts": baseline_alerts[:50],
                    },
                )
                await set_security_posture(guild.id, current)
                if baseline_alerts:
                    details_text = "\n".join(
                        f"• [{alert['severity'].upper()}] {alert['title']}: {alert['detail']}"
                        for alert in baseline_alerts[:20]
                    )
                    try:
                        await send_log_embed(
                            guild,
                            "security",
                            "🛡️ Исходный аудит безопасности",
                            details_text[:3900],
                            color=Color.dark_red(),
                        )
                    except Exception as exc:
                        logger.warning("Не удалось записать baseline security alert: %s", exc)
                    if any(
                        alert["severity"] in {"critical", "high"}
                        for alert in baseline_alerts
                    ):
                        await self._alert_owner(
                            guild,
                            f"🛡️ P.OS обнаружил слабые настройки на **{guild.name}**:\n"
                            + details_text[:1600],
                        )
                return
            if not alerts:
                await set_security_posture(guild.id, current)
                return

            details_text = "\n".join(
                f"• [{alert['severity'].upper()}] {alert['title']}: {alert['detail']}"
                for alert in alerts[:20]
            )
            await add_ai_event(
                guild_id=guild.id,
                event_type="security:posture_change",
                summary=f"P.OS обнаружил опасные изменения security posture: {len(alerts)}",
                details={"source": source, "alerts": alerts[:50]},
            )
            # Accept the new baseline only after the durable alert exists. If
            # persistence fails, the next scan must detect and retry the change.
            await set_security_posture(guild.id, current)
            try:
                await send_log_embed(
                    guild,
                    "security",
                    "🛡️ Изменение контура безопасности",
                    details_text[:3900],
                    color=Color.dark_red(),
                )
            except Exception as exc:
                logger.warning("Не удалось записать security posture alert: %s", exc)

            if not any(alert["severity"] in {"critical", "high"} for alert in alerts):
                return
            now = time.monotonic()
            if now - self._owner_posture_alert_at.get(guild.id, 0.0) < 60.0:
                return
            self._owner_posture_alert_at[guild.id] = now
            await self._alert_owner(
                guild,
                f"🛡️ P.OS обнаружил изменение безопасности на **{guild.name}**:\n"
                + details_text[:1600],
            )

    @tasks.loop(seconds=SECURITY_MONITOR_INTERVAL_SECONDS)
    async def security_monitor(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                await self._check_security_posture(guild, source="periodic")
            except Exception as exc:
                logger.error(
                    "Ошибка периодического security posture scan для %s: %s",
                    guild.id,
                    exc,
                    exc_info=True,
                )

    @security_monitor.before_loop
    async def before_security_monitor(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        self._queue_security_posture_check(role.guild, "role_create")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        self._queue_security_posture_check(after.guild, "role_update")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        self._queue_security_posture_check(role.guild, "role_delete")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        self._queue_security_posture_check(channel.guild, "channel_create")

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        self._queue_security_posture_check(after.guild, "channel_update")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        self._queue_security_posture_check(channel.guild, "channel_delete")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.bot and {role.id for role in before.roles} != {role.id for role in after.roles}:
            self._queue_security_posture_check(after.guild, "bot_role_update")

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel) -> None:
        self._queue_security_posture_check(channel.guild, "webhooks_update")

    @commands.Cog.listener()
    async def on_automod_rule_create(self, rule: discord.AutoModRule) -> None:
        self._queue_security_posture_check(rule.guild, "automod_rule_create")

    @commands.Cog.listener()
    async def on_automod_rule_update(self, rule: discord.AutoModRule) -> None:
        self._queue_security_posture_check(rule.guild, "automod_rule_update")

    @commands.Cog.listener()
    async def on_automod_rule_delete(self, rule: discord.AutoModRule) -> None:
        self._queue_security_posture_check(rule.guild, "automod_rule_delete")

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        self._queue_security_posture_check(after, "guild_update")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if guild is None:
            return
        if member.bot:
            try:
                await add_ai_event(
                    guild_id=guild.id,
                    event_type="security:bot_join",
                    actor_id=member.id,
                    actor_name=str(member),
                    summary=f"На сервер добавлен бот {member} (`{member.id}`)",
                    details={
                        "administrator": bool(member.guild_permissions.administrator),
                        "roles": [role.id for role in member.roles if not role.is_default()],
                    },
                )
            except Exception as exc:
                logger.error("Не удалось сохранить добавление бота %s: %s", member.id, exc)
            try:
                await send_log_embed(
                    guild,
                    "security",
                    "🤖 На сервер добавлен бот",
                    (
                        f"{member} (`{member.id}`)\n"
                        f"Administrator: `{bool(member.guild_permissions.administrator)}`"
                    ),
                    color=Color.red() if member.guild_permissions.administrator else Color.orange(),
                )
            except Exception as exc:
                logger.warning("Не удалось записать Discord-лог нового бота %s: %s", member.id, exc)
            await self._alert_owner(
                guild,
                f"🤖 На сервер **{guild.name}** добавлен бот {member} (`{member.id}`). "
                f"Administrator: `{bool(member.guild_permissions.administrator)}`.",
            )
            self._queue_security_posture_check(guild, "bot_join")
            return

        begin_join_security(guild.id, member.id)
        suppress_roles = True
        try:
            suppress_roles = await self._process_member_join(member)
        except Exception as exc:
            logger.error("Необработанная ошибка антирейда для %s: %s", member.id, exc, exc_info=True)
        finally:
            finish_join_security(guild.id, member.id, suppress_roles=suppress_roles)

    async def _process_member_join(self, member: discord.Member) -> bool:
        guild = member.guild

        try:
            settings = await get_guild_settings(guild.id)
        except Exception:
            settings = {}
        if not settings.get("enabled", True) or not settings.get("filter_raid", True):
            return False

        try:
            decision = antiraid.evaluate_join(member, settings)
        except Exception as e:
            logger.error(f"Ошибка antiraid.evaluate_join: {e}", exc_info=True)
            return True

        action = decision.get("action", "none")
        raid_mode = bool(decision.get("raid_mode"))
        reason = decision.get("reason", "")
        signals = decision.get("signals", [])

        raid_until = decision.get("raid_until")
        if isinstance(raid_until, (int, float)):
            previous_persisted = self._persisted_raid_until.get(guild.id, 0.0)
            should_persist = bool(decision.get("raid")) or (
                float(raid_until) - previous_persisted >= RAID_STATE_PERSIST_GRANULARITY_SECONDS
            )
            if should_persist:
                try:
                    await set_raid_state(guild.id, float(raid_until))
                    self._persisted_raid_until[guild.id] = float(raid_until)
                except Exception as exc:
                    logger.error("Не удалось сохранить состояние антирейда: %s", exc, exc_info=True)

        # Оповещение при срабатывании порога рейда (один раз на старте волны).
        if decision.get("raid"):
            await self._alert_owner(
                guild,
                f"🛑 Возможный РЕЙД на сервере **{guild.name}**: "
                f"{decision.get('join_count')} заходов за окно. Режим рейда включён. "
                f"Реакция на свежие/подозрительные аккаунты: `{settings.get('raid_action', 'kick')}`.",
            )
            try:
                await send_log_embed(
                    guild, "security", "🛑 Антирейд: режим рейда включён",
                    f"Заходов за окно: {decision.get('join_count')}. Реакция: {settings.get('raid_action', 'kick')}.",
                    color=Color.red(),
                )
            except Exception:
                pass

        if action == "none":
            return raid_mode

        member_label = f"{member} (`{member.id}`)"
        sig_text = ", ".join(signals) or "—"

        if action == "alert":
            try:
                await send_log_embed(
                    guild, "security", "⚠️ Антирейд: подозрительный заход",
                    f"{member_label}\nФлаги: {sig_text}\n{reason}",
                    color=Color.orange(),
                )
            except Exception:
                pass
            return raid_mode

        # quarantine (по умолчанию) / kick / ban — действие к аккаунту.
        applied = "ничего"
        try:
            if action == "ban":
                await guild.ban(member, reason=f"Антирейд: {reason}"[:512], delete_message_days=1)
                applied = "бан"
            elif action == "kick":
                try:
                    await member.send(
                        f"На сервере **{guild.name}** сейчас действует антирейд-защита. "
                        f"Твой заход отклонён автоматикой. Если это ошибка — зайди позже."
                    )
                except Exception:
                    pass
                await guild.kick(member, reason=f"Антирейд: {reason}"[:512])
                applied = "кик"
            else:
                # quarantine: мутим и ограничиваем доступ, но ОСТАВЛЯЕМ на
                # сервере. Владелец потом сам снимет ограничения (lift_restrictions)
                # или режим рейда (deactivate_raid_mode), если сочтёт аккаунт нормальным.
                applied = await quarantine_member(member, reason)
        except discord.Forbidden:
            applied = "нет прав на действие"
        except Exception:
            logger.exception("Не удалось применить антирейд-действие %s к %s.", action, member.id)
            applied = "ошибка Discord API (подробности в локальном журнале)"

        try:
            await send_log_embed(
                guild, "security", "🛡️ Антирейд: меры приняты",
                f"{member_label}\nФлаги: {sig_text}\nДействие: **{applied}**\n{reason}",
                color=Color.red(),
            )
        except Exception:
            pass
        return raid_mode


async def setup(bot: commands.Bot):
    await bot.add_cog(SecurityCog(bot))
