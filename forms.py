from __future__ import annotations

import discord
from discord import Embed, Color
from discord.ui import View, button, Modal, TextInput

from logging_utils import send_log_embed
from utils import safe_send_dm


class ConfirmView(View):
    def __init__(self, allowed_checker_role_ids, target_message, squad_name, role_ids, target_user_id):
        super().__init__(timeout=None)
        self.allowed_checker_role_ids = allowed_checker_role_ids
        self.message = target_message
        self.squad_name = squad_name
        self.role_ids = role_ids
        self.target_user_id = target_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if any(r.id in self.allowed_checker_role_ids for r in interaction.user.roles):
            return True
        await interaction.response.send_message("❌ У тебя нет прав нажимать эту кнопку.", ephemeral=True)
        return False

    @button(label="Принять", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.guild.get_member(self.target_user_id)
        if member:
            for role_id in self.role_ids:
                role = interaction.guild.get_role(role_id)
                if role:
                    try:
                        await member.add_roles(role)
                    except Exception:
                        pass
            try:
                dm_embed = Embed(
                    title="⏳ Ваша заявка принята!",
                    description="Вы успешно прошли предварительное подтверждение. Ожидайте связи от офицера отряда.",
                    color=Color.green()
                )
                await member.send(embed=dm_embed)
            except Exception:
                pass
            try:
                await self.message.reply(embed=Embed(title="✅ Принято", description=f"Вы зачислены в отряд **{self.squad_name}**!", color=Color.green()))
            except Exception:
                pass

            try:
                role_names = []
                for role_id in self.role_ids:
                    role = interaction.guild.get_role(role_id)
                    if role:
                        role_names.append(role.name)
                await send_log_embed(
                    interaction.guild,
                    "forms",
                    "✅ Заявка принята",
                    f"{member.mention} принят в отряд **{self.squad_name}**.",
                    color=Color.green(),
                    fields=[
                        ("Модератор", interaction.user.mention, False),
                        ("Выданные роли", ", ".join(role_names) or "—", False)
                    ]
                )
            except Exception:
                pass
        await interaction.response.send_message("Принято.", ephemeral=True)
        self.stop()

    @button(label="Отказать", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.message.add_reaction("❌")
        except Exception:
            pass
        try:
            await send_log_embed(
                interaction.guild,
                "forms",
                "❌ Заявка отклонена",
                f"Заявка пользователя `<@{self.target_user_id}>` в отряд **{self.squad_name}** отклонена.",
                color=Color.red(),
                fields=[("Модератор", interaction.user.mention, False)]
            )
        except Exception:
            pass
        await interaction.response.send_message("Отказ зарегистрирован.", ephemeral=True)
        self.stop()


class RejectModal(Modal):
    def __init__(self, submitter: discord.Member, complaint_channel_id: int):
        super().__init__(title="Причина отклонения")
        self.submitter = submitter
        self.complaint_channel_id = complaint_channel_id
        self.reason = TextInput(label="Причина отклонения", style=discord.TextStyle.long, required=True, placeholder="Объясните почему отклоняете жалобу")
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        reason_text = self.reason.value
        guild = interaction.guild
        try:
            c = guild.get_channel(self.complaint_channel_id)
            history = []
            if c:
                async for m in c.history(limit=200, oldest_first=True):
                    history.append(f"{m.author.display_name}: {m.content}")
            history_text = "\n".join(history) if history else "(нет истории)"
        except Exception:
            history_text = "(не удалось получить историю)"

        embed = Embed(title="❌ Ваша жалоба отклонена", color=Color.red())
        embed.add_field(name="Причина", value=reason_text, inline=False)
        embed.add_field(name="История жалобы", value=f"```{history_text[:1900]}```", inline=False)
        await safe_send_dm(self.submitter, embed)

        try:
            await send_log_embed(
                guild,
                "forms",
                "❌ Жалоба отклонена",
                f"Жалоба от {self.submitter.mention} отклонена.",
                color=Color.red(),
                fields=[
                    ("Администратор", interaction.user.mention, False),
                    ("Причина", reason_text[:1024], False)
                ]
            )
        except Exception:
            pass

        await interaction.response.send_message("Отклонение отправлено автору. Канал жалобы будет удалён.", ephemeral=True)
        try:
            channel = guild.get_channel(self.complaint_channel_id)
            if channel:
                await channel.delete(reason=f"Жалоба отклонена админом {interaction.user}")
        except Exception:
            pass


class ComplaintView(View):
    def __init__(self, submitter: discord.Member, complaint_channel_id: int):
        super().__init__(timeout=None)
        self.submitter = submitter
        self.complaint_channel_id = complaint_channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message("❌ Только администраторы могут взаимодействовать.", ephemeral=True)
        return False

    @button(label="Одобрено", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        history = []
        channel = guild.get_channel(self.complaint_channel_id)
        if channel:
            async for m in channel.history(limit=200, oldest_first=True):
                history.append(f"{m.author.display_name}: {m.content}")
        history_text = "\n".join(history) if history else "(нет истории)"

        embed = Embed(title="✅ Ваша жалоба одобрена", color=Color.green())
        embed.add_field(name="История жалобы", value=f"```{history_text[:1900]}```", inline=False)
        await safe_send_dm(self.submitter, embed)

        try:
            await send_log_embed(
                guild,
                "forms",
                "✅ Жалоба одобрена",
                f"Жалоба от {self.submitter.mention} одобрена.",
                color=Color.green(),
                fields=[("Администратор", interaction.user.mention, False)]
            )
        except Exception:
            pass

        await interaction.response.send_message("Жалоба одобрена, автор уведомлён.", ephemeral=True)
        try:
            if channel:
                await channel.delete(reason=f"Жалоба одобрена админом {interaction.user}")
        except Exception:
            pass

    @button(label="Отклонено", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RejectModal(submitter=self.submitter, complaint_channel_id=self.complaint_channel_id)
        await interaction.response.send_modal(modal)
