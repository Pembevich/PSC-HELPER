import re

with open("pos_ai.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add imports
content = content.replace(
    "from storage import add_entry, delete_entry, list_entries",
    "from storage import add_entry, delete_entry, list_entries, get_ai_context, update_ai_context, is_ai_muted, set_ai_muted_user\nfrom cogs.ai_tools import POS_AI_TOOLS\nimport json"
)

# 2. Add execute_pos_tool function
tool_logic = """
async def execute_pos_tool(bot: discord.Client, message: discord.Message | None, tool_call: dict) -> str:
    if not message or not message.guild:
        return "Ошибка: инструмент можно использовать только на сервере."
    
    func = tool_call.get("function", {})
    name = func.get("name")
    args_raw = func.get("arguments", "{}")
    try:
        args = json.loads(args_raw)
    except Exception:
        args = {}
        
    user_id = int(args.get("user_id", 0)) if args.get("user_id") else None
    
    if name == "ban_user":
        if not user_id: return "Ошибка: не указан user_id"
        reason = args.get("reason", "Бан от P.OS")
        try:
            await message.guild.ban(discord.Object(id=user_id), reason=reason)
            return f"Пользователь {user_id} успешно забанен."
        except Exception as e:
            return f"Ошибка при бане: {e}"
            
    elif name == "unban_user":
        if not user_id: return "Ошибка: не указан user_id"
        try:
            await message.guild.unban(discord.Object(id=user_id))
            return f"Пользователь {user_id} успешно разбанен."
        except Exception as e:
            return f"Ошибка при разбане: {e}"
            
    elif name == "timeout_user":
        if not user_id: return "Ошибка: не указан user_id"
        minutes = int(args.get("minutes", 10))
        reason = args.get("reason", "Тайм-аут от P.OS")
        member = message.guild.get_member(user_id)
        if not member: return f"Ошибка: пользователь {user_id} не найден на сервере."
        import datetime
        try:
            until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
            await member.timeout(until, reason=reason)
            return f"Пользователю {user_id} выдан тайм-аут на {minutes} минут."
        except Exception as e:
            return f"Ошибка при муте: {e}"
            
    elif name == "add_role":
        if not user_id: return "Ошибка: не указан user_id"
        role_ident = args.get("role_id_or_name", "")
        member = message.guild.get_member(user_id)
        if not member: return "Ошибка: пользователь не найден."
        role = None
        if role_ident.isdigit():
            role = message.guild.get_role(int(role_ident))
        if not role:
            role = discord.utils.find(lambda r: r.name.lower() == role_ident.lower(), message.guild.roles)
        if not role: return f"Ошибка: роль '{role_ident}' не найдена."
        try:
            await member.add_roles(role, reason="Выдано P.OS")
            return f"Роль {role.name} успешно выдана пользователю {user_id}."
        except Exception as e:
            return f"Ошибка при выдаче роли: {e}"
            
    elif name == "remove_role":
        if not user_id: return "Ошибка: не указан user_id"
        role_ident = args.get("role_id_or_name", "")
        member = message.guild.get_member(user_id)
        if not member: return "Ошибка: пользователь не найден."
        role = None
        if role_ident.isdigit():
            role = message.guild.get_role(int(role_ident))
        if not role:
            role = discord.utils.find(lambda r: r.name.lower() == role_ident.lower(), message.guild.roles)
        if not role: return f"Ошибка: роль '{role_ident}' не найдена."
        try:
            await member.remove_roles(role, reason="Снято P.OS")
            return f"Роль {role.name} успешно снята с пользователя {user_id}."
        except Exception as e:
            return f"Ошибка при снятии роли: {e}"
            
    elif name == "mute_ai_for_user":
        if not user_id: return "Ошибка: не указан user_id"
        try:
            await set_ai_muted_user(user_id, message.guild.id, True)
            return f"Я добавил пользователя {user_id} в черный список (я больше не буду ему отвечать)."
        except Exception as e:
            return f"Ошибка базы данных: {e}"
            
    elif name == "unmute_ai_for_user":
        if not user_id: return "Ошибка: не указан user_id"
        try:
            await set_ai_muted_user(user_id, message.guild.id, False)
            return f"Я удалил пользователя {user_id} из черного списка (теперь буду отвечать)."
        except Exception as e:
            return f"Ошибка базы данных: {e}"
            
    return f"Неизвестный инструмент: {name}"

"""

# 3. Modify request_pos_reply
new_request_pos_reply = """
async def request_pos_reply(bot: discord.Client, message: discord.Message | None, messages: list[dict], *, allow_system_fallback: bool = True) -> str | None:
    MAX_TURNS = 5
    for turn in range(MAX_TURNS):
        response_msg = await pos_chat_completion(
            messages,
            tools=POS_AI_TOOLS,
            max_tokens=POS_AI_MAX_TOKENS,
            temperature=POS_AI_TEMPERATURE,
            top_p=POS_AI_TOP_P,
            timeout=POS_AI_TIMEOUT_SECONDS,
        )
        
        if not response_msg:
            return None
            
        tool_calls = response_msg.get("tool_calls")
        if not tool_calls:
            return response_msg.get("content")
            
        messages.append(response_msg)
        
        for tool_call in tool_calls:
            tool_id = tool_call.get("id")
            result = await execute_pos_tool(bot, message, tool_call)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_id,
                "name": tool_call["function"]["name"],
                "content": result
            })
            
    return "Я превысил максимальное число действий."
"""
content = re.sub(r'async def request_pos_reply.*?return await pos_chat_completion.*?\)', new_request_pos_reply, content, flags=re.DOTALL)
content = tool_logic + content

# 4. Modify ask_pos
content = content.replace("async def ask_pos(\n    prompt: str,\n    *,\n    image_urls: list[str] | None = None,\n    author_name: str | None = None,\n) -> str | None:", "async def ask_pos(\n    prompt: str,\n    *,\n    image_urls: list[str] | None = None,\n    author_name: str | None = None,\n    bot: discord.Client | None = None,\n) -> str | None:")
content = content.replace("return await request_pos_reply(messages)", "return await request_pos_reply(bot, None, messages)")

# 5. Modify handle_pos_ai calls to request_pos_reply
content = content.replace("reply = await request_pos_reply(messages)", "reply = await request_pos_reply(bot, message, messages)")

# 6. Mute check in handle_pos_ai
content = content.replace("if _is_user_muted(message):", "if await is_ai_muted(message.author.id, message.guild.id):")

# 7. Remove legacy patterns 
legacy_patterns = [
    "BAN_PATTERN", "UNBAN_PATTERN", "ADD_ROLE_PATTERN", "REMOVE_ROLE_PATTERN",
    "DB_ADD_PATTERN", "DB_LIST_PATTERN", "DB_DELETE_PATTERN", "CONTEXT_SCAN_PATTERN"
]
for p in legacy_patterns:
    content = re.sub(rf'{p} = re\.compile.*?$', '', content, flags=re.MULTILINE)

content = re.sub(r'def _looks_like_admin_action.*?return any\([^)]+\)', 'def _looks_like_admin_action(text: str) -> bool:\n    return False', content, flags=re.DOTALL)

with open("pos_ai.py", "w", encoding="utf-8") as f:
    f.write(content)
