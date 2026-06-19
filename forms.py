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
        self.resolved = False

    def _disable_buttons(self):
        for item in self.children:
            item.disabled = True

    async def _finalize_view(self, interaction: discord.Interaction):
        self.resolved = True
        self._disable_buttons()
        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except Exception:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.resolved:
            await interaction.response.send_message("Эта заявка уже обработана.", ephemeral=True)
            return False
        if interaction.user.bot:
            await interaction.response.send_message("Ботам тут делать нечего.", ephemeral=True)
            return False
        if not interaction.guild:
            await interaction.response.send_message("❌ Не удалось проверить участника сервера.", ephemeral=True)
            return False
        # #9: Проверяем, что нажимающий имеет одну из разрешённых ролей проверяющего
        if self.allowed_checker_role_ids:
            member = interaction.guild.get_member(interaction.user.id)
            if not member:
                try:
                    member = await interaction.guild.fetch_member(interaction.user.id)
                except Exception:
                    member = None
            if not member:
                await interaction.response.send_message("❌ Не удалось найти участника сервера.", ephemeral=True)
                return False
            user_role_ids = {role.id for role in member.roles}
            if not user_role_ids.intersection(self.allowed_checker_role_ids):
                await interaction.response.send_message("❌ У вас нет прав для обработки этой заявки.", ephemeral=True)
                return False
        return True

    @button(label="Принять", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.guild.get_member(self.target_user_id)
        if not member:
            try:
                member = await interaction.guild.fetch_member(self.target_user_id)
            except Exception:
                pass
        if member:
            granted_roles: list[str] = []
            failed_roles: list[str] = []
            for role_id in self.role_ids:
                role = interaction.guild.get_role(role_id)
                if not role:
                    failed_roles.append(f"`{role_id}` (роль не найдена на сервере)")
                    continue
                try:
                    await member.add_roles(role, reason="Принят в отряд через форму")
                    granted_roles.append(role.name)
                except discord.Forbidden:
                    failed_roles.append(f"{role.name} (недостаточно прав / иерархия ролей)")
                except Exception as e:
                    failed_roles.append(f"{role.name} ({e})")
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
                log_fields = [
                    ("Модератор", interaction.user.mention, False),
                    ("Выданные роли", ", ".join(granted_roles) or "—", False),
                ]
                if failed_roles:
                    log_fields.append(("⚠️ НЕ выданы", "\n".join(failed_roles), False))
                await send_log_embed(
                    interaction.guild,
                    "forms",
                    "✅ Заявка принята" if not failed_roles else "⚠️ Заявка принята (роли выданы частично)",
                    f"{member.mention} принят в отряд **{self.squad_name}**.",
                    color=Color.green() if not failed_roles else Color.orange(),
                    fields=log_fields,
                )
            except Exception:
                pass
        await self._finalize_view(interaction)
        if not member:
            confirm_text = "⚠️ Участник не найден на сервере — роли не выданы."
        elif failed_roles:
            confirm_text = "⚠️ Принято, но часть ролей не выдана:\n" + "\n".join(failed_roles)
        else:
            confirm_text = "✅ Принято, роли выданы."
        await interaction.response.send_message(confirm_text, ephemeral=True)
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
        await self._finalize_view(interaction)
        await interaction.response.send_message("Отказ зарегистрирован.", ephemeral=True)
        self.stop()


class PosActionConfirmView(View):
    """Кнопки подтверждения для запросов P.OS на управляющие действия.

    Когда не-владелец (или сам P.OS) инициирует управляющее действие, P.OS отправляет
    владельцу это сообщение с кнопками «Разрешить»/«Запретить». Нажатие реально
    выполняет действие через переданный async-callback или отклоняет его.

    `executor` — async-функция без аргументов, возвращающая текст-результат (str).
    Так мы избегаем циклического импорта pos_ai <-> forms: вся логика выполнения
    остаётся в pos_ai, сюда передаётся только замыкание.
    """

    def __init__(self, owner_user_ids, executor, action_summary: str, requester_label: str = ""):
        super().__init__(timeout=3600)  # 1 час на решение
        self.owner_user_ids = set(owner_user_ids or [])
        self.executor = executor
        self.action_summary = action_summary
        self.requester_label = requester_label
        self.resolved = False

    def _disable_buttons(self):
        for item in self.children:
            item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.resolved:
            await interaction.response.send_message("Этот запрос уже обработан.", ephemeral=True)
            return False
        if self.owner_user_ids and interaction.user.id not in self.owner_user_ids:
            await interaction.response.send_message("Решение по этому запросу может принять только владелец.", ephemeral=True)
            return False
        return True

    async def _finalize(self, interaction: discord.Interaction):
        self.resolved = True
        self._disable_buttons()
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @button(label="✅ Разрешить", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            result = await self.executor()
        except Exception as e:
            result = f"Ошибка при выполнении: {e}"
        await self._finalize(interaction)
        try:
            await interaction.followup.send(f"✅ Действие выполнено.\n{result}", ephemeral=True)
        except Exception:
            pass
        try:
            await interaction.message.reply(
                f"✅ Запрос одобрен владельцем и выполнен:\n> {self.action_summary}\n{result}",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass
        self.stop()

    @button(label="❌ Запретить", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finalize(interaction)
        await interaction.response.send_message("❌ Запрос отклонён. Действие не выполнено.", ephemeral=True)
        try:
            await interaction.message.reply(
                f"❌ Запрос отклонён владельцем:\n> {self.action_summary}",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass
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
