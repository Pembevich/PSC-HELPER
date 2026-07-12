from __future__ import annotations

import asyncio
import re

import discord
from discord import Embed, Color
from discord.ui import View, button, Modal, TextInput

from logging_utils import send_log_embed
from utils import safe_send_dm

# ID из embed: "ID пользователя: 123" (футер заявки) / "(ID: 123)" (поле жалобы).
_EMBED_USER_ID = re.compile(r"ID(?:\s*пользователя)?\s*[:#]?\s*(\d{17,21})", re.IGNORECASE)

# Защита от двойного клика в рамках процесса: message.id уже обработанных заявок.
# Долговременная защита — отключённые кнопки, которые сохраняются в самом сообщении.
_processed_messages: set[int] = set()


def _claim_message(message: discord.Message | None) -> bool:
    """True, если сообщение ещё не обрабатывалось (и помечает его обработанным)."""
    if message is None:
        return True
    if message.id in _processed_messages:
        return False
    _processed_messages.add(message.id)
    if len(_processed_messages) > 5000:
        for mid in list(_processed_messages)[:2500]:
            _processed_messages.discard(mid)
    return True


def _claim_message_id(message_id: int | None) -> bool:
    if not message_id:
        return True
    if message_id in _processed_messages:
        return False
    _processed_messages.add(message_id)
    if len(_processed_messages) > 5000:
        for mid in list(_processed_messages)[:2500]:
            _processed_messages.discard(mid)
    return True


def _extract_user_id_from_embed(message: discord.Message | None) -> int | None:
    """Достать ID пользователя из embed (футер заявки или поле «Отправитель»)."""
    if not message or not message.embeds:
        return None
    emb = message.embeds[0]
    candidates = [emb.footer.text if emb.footer else None]
    candidates.extend(field.value for field in emb.fields)
    candidates.append(emb.description)
    for text in candidates:
        if not text:
            continue
        m = _EMBED_USER_ID.search(text)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


async def _disable_view_on_message(interaction: discord.Interaction, view: View) -> None:
    for item in view.children:
        if isinstance(item, discord.ui.Button):
            item.disabled = True
    try:
        if interaction.message:
            await interaction.message.edit(view=view)
    except Exception:
        pass


class ConfirmView(View):
    """Кнопки принятия/отказа заявки в отряд.

    Persistent view: живёт с timeout=None и фиксированными custom_id, поэтому
    кнопки работают и после рестарта бота. Контекст (кандидат) восстанавливается
    из embed сообщения, награды — из конфига; конструктор без аргументов создаёт
    «пустой» экземпляр для bot.add_view().
    """

    def __init__(self, allowed_checker_role_ids=None, target_message=None,
                 squad_name: str = "Arbaiter", role_ids=None, target_user_id: int | None = None):
        super().__init__(timeout=None)
        self.allowed_checker_role_ids = allowed_checker_role_ids or []
        self.message = target_message
        self.squad_name = squad_name
        self.role_ids = role_ids
        self.target_user_id = target_user_id

    def _resolve_role_ids(self) -> list[int]:
        if self.role_ids:
            return list(self.role_ids)
        try:
            from config import TAC_ROLE_REWARDS
            return list(TAC_ROLE_REWARDS)
        except Exception:
            return []

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
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

    @button(label="Принять", style=discord.ButtonStyle.green, custom_id="psc:arbaiter:accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Сразу defer: дальше выдача ролей/DM/логи легко превышают 3-секундный
        # лимит первичного ответа interaction.
        await interaction.response.defer(ephemeral=True)

        if not _claim_message(interaction.message):
            await interaction.followup.send("Эта заявка уже обработана.", ephemeral=True)
            return

        target_user_id = self.target_user_id or _extract_user_id_from_embed(interaction.message)
        if not target_user_id:
            await interaction.followup.send(
                "⚠️ Не удалось определить кандидата (в заявке нет ID).", ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Сервер недоступен; заявка не обработана.", ephemeral=True)
            return

        member = guild.get_member(target_user_id)
        if not member:
            try:
                member = await guild.fetch_member(target_user_id)
            except Exception:
                member = None

        failed_roles: list[str] = []
        if member:
            granted_roles: list[str] = []
            for role_id in self._resolve_role_ids():
                role = guild.get_role(role_id)
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
            # Ответ на исходное сообщение-заявку возможен только пока бот не
            # рестартовал (self.message теряется) — это некритично, DM ушёл.
            if self.message:
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

        await _disable_view_on_message(interaction, self)
        if not member:
            confirm_text = "⚠️ Участник не найден на сервере — роли не выданы."
        elif failed_roles:
            confirm_text = "⚠️ Принято, но часть ролей не выдана:\n" + "\n".join(failed_roles)
        else:
            confirm_text = "✅ Принято, роли выданы."
        await interaction.followup.send(confirm_text, ephemeral=True)

    @button(label="Отказать", style=discord.ButtonStyle.red, custom_id="psc:arbaiter:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        if not _claim_message(interaction.message):
            await interaction.followup.send("Эта заявка уже обработана.", ephemeral=True)
            return

        target_user_id = self.target_user_id or _extract_user_id_from_embed(interaction.message)
        if self.message:
            try:
                await self.message.add_reaction("❌")
            except Exception:
                pass
        try:
            await send_log_embed(
                interaction.guild,
                "forms",
                "❌ Заявка отклонена",
                f"Заявка пользователя <@{target_user_id or '?'}> в отряд **{self.squad_name}** отклонена.",
                color=Color.red(),
                fields=[("Модератор", interaction.user.mention, False)]
            )
        except Exception:
            pass
        await _disable_view_on_message(interaction, self)
        await interaction.followup.send("Отказ зарегистрирован.", ephemeral=True)


class PosActionConfirmView(View):
    """Кнопки подтверждения для запросов P.OS на управляющие действия.

    Когда не-владелец (или сам P.OS) инициирует управляющее действие, P.OS отправляет
    владельцу это сообщение с кнопками «Разрешить»/«Запретить». Нажатие реально
    выполняет действие через переданный async-callback или отклоняет его.

    `executor` — async-функция без аргументов, возвращающая текст-результат (str).
    Так мы избегаем циклического импорта pos_ai <-> forms: вся логика выполнения
    остаётся в pos_ai, сюда передаётся только замыкание.

    Замыкание нельзя восстановить после рестарта, поэтому этот view осознанно
    НЕ persistent: по истечении 10 минут (или после рестарта) кнопки гаснут и
    запрос надо повторить через P.OS.
    """

    def __init__(self, owner_user_ids, executor, action_summary: str, requester_label: str = ""):
        super().__init__(timeout=600)  # Контекст и Discord-права могут быстро измениться.
        self.owner_user_ids = set(owner_user_ids or [])
        self.executor = executor
        self.action_summary = action_summary
        self.requester_label = requester_label
        self.resolved = False
        self._message: discord.Message | None = None
        self._resolve_lock = asyncio.Lock()

    def bind_message(self, message: discord.Message) -> None:
        self._message = message

    def _disable_buttons(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    async def on_timeout(self) -> None:
        # Гасим кнопки визуально, чтобы просроченный запрос не выглядел живым.
        async with self._resolve_lock:
            self.resolved = True
            self._disable_buttons()
        if self._message:
            try:
                await self._message.edit(view=self)
            except Exception:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self._message = interaction.message
        if self.owner_user_ids and interaction.user.id not in self.owner_user_ids:
            await interaction.response.send_message("Решение по этому запросу может принять только владелец.", ephemeral=True)
            return False
        return True

    async def _claim(self, interaction: discord.Interaction) -> bool:
        async with self._resolve_lock:
            if self.resolved:
                return False
            self.resolved = True
            self._disable_buttons()
        if interaction.message:
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
        return True

    @button(label="✅ Разрешить", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not await self._claim(interaction):
            await interaction.followup.send("Этот запрос уже обработан.", ephemeral=True)
            return
        try:
            result = await self.executor()
        except Exception as e:
            result = f"Ошибка при выполнении: {e}"
        try:
            await interaction.followup.send(f"✅ Действие выполнено.\n{result}", ephemeral=True)
        except Exception:
            pass
        if interaction.message:
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
        await interaction.response.defer(ephemeral=True)
        if not await self._claim(interaction):
            await interaction.followup.send("Этот запрос уже обработан.", ephemeral=True)
            return
        await interaction.followup.send("❌ Запрос отклонён. Действие не выполнено.", ephemeral=True)
        if interaction.message:
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
    def __init__(
        self,
        submitter: discord.User | discord.Member | None,
        complaint_channel_id: int,
        source_message_id: int | None = None,
    ):
        super().__init__(title="Причина отклонения")
        self.submitter = submitter
        self.complaint_channel_id = complaint_channel_id
        self.source_message_id = source_message_id
        self.reason: TextInput[RejectModal] = TextInput(
            label="Причина отклонения",
            style=discord.TextStyle.long,
            required=True,
            placeholder="Объясните почему отклоняете жалобу",
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        # Сразу defer: чтение истории (до 200 сообщений) + DM занимают дольше
        # 3 секунд — без defer interaction «умирал» с ошибкой у модератора.
        await interaction.response.defer(ephemeral=True)
        if not _claim_message_id(self.source_message_id):
            await interaction.followup.send("Эта жалоба уже обработана.", ephemeral=True)
            return
        reason_text = self.reason.value
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Сервер недоступен; жалоба не изменена.", ephemeral=True)
            return
        try:
            c = guild.get_channel(self.complaint_channel_id)
            history = []
            if isinstance(c, (discord.TextChannel, discord.Thread)):
                async for m in c.history(limit=200, oldest_first=True):
                    history.append(f"{m.author.display_name}: {m.content}")
            history_text = "\n".join(history) if history else "(нет истории)"
        except Exception:
            history_text = "(не удалось получить историю)"

        if self.submitter:
            embed = Embed(title="❌ Ваша жалоба отклонена", color=Color.red())
            embed.add_field(name="Причина", value=reason_text, inline=False)
            embed.add_field(name="История жалобы", value=f"```{history_text[:1900]}```", inline=False)
            dm_sent = await safe_send_dm(self.submitter, embed)
        else:
            dm_sent = False

        try:
            await send_log_embed(
                guild,
                "forms",
                "❌ Жалоба отклонена",
                f"Жалоба от {self.submitter.mention if self.submitter else 'неизвестного автора'} отклонена.",
                color=Color.red(),
                fields=[
                    ("Администратор", interaction.user.mention, False),
                    ("Причина", reason_text[:1024], False)
                ]
            )
        except Exception:
            pass

        notification = "Автор уведомлён." if dm_sent else "ЛС автору отправить не удалось."
        await interaction.followup.send(f"Отклонение зарегистрировано. {notification} Канал жалобы будет удалён.", ephemeral=True)
        try:
            channel = guild.get_channel(self.complaint_channel_id)
            if channel:
                await channel.delete(reason=f"Жалоба отклонена админом {interaction.user}")
        except Exception:
            pass


class ComplaintView(View):
    """Кнопки решения по жалобе. Persistent: работает и после рестарта —
    автор жалобы восстанавливается из embed, канал жалобы = канал сообщения."""

    def __init__(
        self,
        submitter: discord.User | discord.Member | None = None,
        complaint_channel_id: int | None = None,
    ):
        super().__init__(timeout=None)
        self.submitter = submitter
        self.complaint_channel_id = complaint_channel_id

    async def _resolve_submitter(
        self,
        interaction: discord.Interaction,
    ) -> discord.User | discord.Member | None:
        if self.submitter:
            return self.submitter
        uid = _extract_user_id_from_embed(interaction.message)
        if not uid:
            return None
        user = interaction.client.get_user(uid)
        if user is None:
            try:
                user = await interaction.client.fetch_user(uid)
            except Exception:
                user = None
        return user

    def _resolve_channel_id(self, interaction: discord.Interaction) -> int:
        # Сообщение с кнопками живёт в самом канале жалобы.
        return self.complaint_channel_id or (interaction.channel.id if interaction.channel else 0)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            isinstance(interaction.user, discord.Member)
            and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild)
        ):
            return True
        await interaction.response.send_message("❌ Только администраторы могут взаимодействовать.", ephemeral=True)
        return False

    @button(label="Одобрено", style=discord.ButtonStyle.green, custom_id="psc:complaint:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # defer до чтения истории/DM (3-секундный лимит первичного ответа).
        await interaction.response.defer(ephemeral=True)

        if not _claim_message(interaction.message):
            await interaction.followup.send("Эта жалоба уже обработана.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Сервер недоступен; жалоба не изменена.", ephemeral=True)
            return
        submitter = await self._resolve_submitter(interaction)
        channel_id = self._resolve_channel_id(interaction)
        history = []
        channel = guild.get_channel(channel_id)
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                async for m in channel.history(limit=200, oldest_first=True):
                    history.append(f"{m.author.display_name}: {m.content}")
            except Exception:
                pass
        history_text = "\n".join(history) if history else "(нет истории)"

        if submitter:
            embed = Embed(title="✅ Ваша жалоба одобрена", color=Color.green())
            embed.add_field(name="История жалобы", value=f"```{history_text[:1900]}```", inline=False)
            dm_sent = await safe_send_dm(submitter, embed)
        else:
            dm_sent = False

        try:
            await send_log_embed(
                guild,
                "forms",
                "✅ Жалоба одобрена",
                f"Жалоба от {submitter.mention if submitter else 'неизвестного автора'} одобрена.",
                color=Color.green(),
                fields=[("Администратор", interaction.user.mention, False)]
            )
        except Exception:
            pass

        notification = "автор уведомлён" if dm_sent else "ЛС автору отправить не удалось"
        await interaction.followup.send(f"Жалоба одобрена, {notification}.", ephemeral=True)
        try:
            if channel:
                await channel.delete(reason=f"Жалоба одобрена админом {interaction.user}")
        except Exception:
            pass

    @button(label="Отклонено", style=discord.ButtonStyle.red, custom_id="psc:complaint:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Модалка обязана быть ПЕРВЫМ ответом на interaction — defer здесь нельзя.
        submitter = await self._resolve_submitter(interaction)
        modal = RejectModal(
            submitter=submitter,
            complaint_channel_id=self._resolve_channel_id(interaction),
            source_message_id=interaction.message.id if interaction.message else None,
        )
        await interaction.response.send_modal(modal)
