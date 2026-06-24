"""
cogs/security.py — антирейд (0.8).

Слушает заходы участников, прогоняет через antiraid.evaluate_join и применяет
реакцию (alert/kick/ban/lockdown) к подозрительным/свежим аккаунтам во время
рейда. Логику решений держит antiraid.py — здесь только эффекты Discord.
"""
from __future__ import annotations

import logging

import discord
from discord import Color
from discord.ext import commands

import antiraid
from guild_config import get_settings as get_guild_settings
from logging_utils import send_log_embed
from config import POS_OWNER_USER_IDS, POS_CREATOR_ID

logger = logging.getLogger(__name__)


class SecurityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _alert_owner(self, guild: discord.Guild, text: str) -> None:
        owner_id = POS_OWNER_USER_IDS[0] if POS_OWNER_USER_IDS else POS_CREATOR_ID
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

        try:
            settings = await get_guild_settings(guild.id)
        except Exception:
            settings = {}
        if not settings.get("enabled", True) or not settings.get("filter_raid", True):
            return

        try:
            decision = antiraid.evaluate_join(member, settings)
        except Exception as e:
            logger.error(f"Ошибка antiraid.evaluate_join: {e}", exc_info=True)
            return

        action = decision.get("action", "none")
        reason = decision.get("reason", "")
        signals = decision.get("signals", [])

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
                    guild, "moderation", "🛑 Антирейд: режим рейда включён",
                    f"Заходов за окно: {decision.get('join_count')}. Реакция: {settings.get('raid_action', 'kick')}.",
                    color=Color.red(),
                )
            except Exception:
                pass

        if action == "none":
            return

        member_label = f"{member} (`{member.id}`)"
        sig_text = ", ".join(signals) or "—"

        if action == "alert":
            try:
                await send_log_embed(
                    guild, "moderation", "⚠️ Антирейд: подозрительный заход",
                    f"{member_label}\nФлаги: {sig_text}\n{reason}",
                    color=Color.orange(),
                )
            except Exception:
                pass
            return

        # kick / ban / lockdown — применяем действие к этому аккаунту.
        applied = "ничего"
        try:
            if action == "ban":
                await guild.ban(member, reason=f"Антирейд: {reason}"[:512], delete_message_days=1)
                applied = "бан"
            else:  # kick и lockdown — кикаем подозрительный аккаунт
                # Сначала пытаемся предупредить пользователя в ЛС.
                try:
                    await member.send(
                        f"На сервере **{guild.name}** сейчас действует антирейд-защита. "
                        f"Твой заход отклонён автоматикой. Если это ошибка — зайди позже."
                    )
                except Exception:
                    pass
                await guild.kick(member, reason=f"Антирейд: {reason}"[:512])
                applied = "кик"
        except discord.Forbidden:
            applied = "нет прав на действие"
        except Exception as e:
            applied = f"ошибка: {e}"

        try:
            await send_log_embed(
                guild, "moderation", "🛡️ Антирейд: меры приняты",
                f"{member_label}\nФлаги: {sig_text}\nДействие: **{applied}**\n{reason}",
                color=Color.red(),
            )
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(SecurityCog(bot))
