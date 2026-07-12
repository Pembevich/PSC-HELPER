"""
cogs/security.py — антирейд (0.8).

Слушает заходы участников, прогоняет через antiraid.evaluate_join и применяет
реакцию (alert/quarantine/kick/ban) к подозрительным/свежим аккаунтам во время
рейда. Логику решений держит antiraid.py — здесь только эффекты Discord.
"""
from __future__ import annotations

import logging

import discord
from discord import Color
from discord.ext import commands

import antiraid
from guild_config import get_settings as get_guild_settings
from join_gate import begin_join_security, finish_join_security
from logging_utils import send_log_embed
from moderation import quarantine_member
from config import POS_CREATOR_ID
from storage import set_raid_state

logger = logging.getLogger(__name__)
RAID_STATE_PERSIST_GRANULARITY_SECONDS = 30.0


class SecurityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._persisted_raid_until: dict[int, float] = {}

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

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if guild is None or member.bot:
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
        except Exception as e:
            applied = f"ошибка: {e}"

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
