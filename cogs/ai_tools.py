POS_AI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ban_user",
            "description": "Банит пользователя на сервере.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Discord ID пользователя, которого нужно забанить."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина бана."
                    }
                },
                "required": ["user_id", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "unban_user",
            "description": "Разбанивает пользователя на сервере.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Discord ID пользователя, которого нужно разбанить."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина разбана."
                    }
                },
                "required": ["user_id", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "timeout_user",
            "description": "Выдает тайм-аут (мут) пользователю.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Discord ID пользователя."
                    },
                    "minutes": {
                        "type": "string",
                        "description": "Длительность тайм-аута в минутах (например, '10' или '30')."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина мута."
                    }
                },
                "required": ["user_id", "minutes", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_role",
            "description": "Выдает роль пользователю.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Discord ID пользователя."
                    },
                    "role_id_or_name": {
                        "type": "string",
                        "description": "ID роли или точное имя роли."
                    }
                },
                "required": ["user_id", "role_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_role",
            "description": "Снимает роль с пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Discord ID пользователя."
                    },
                    "role_id_or_name": {
                        "type": "string",
                        "description": "ID роли или точное имя роли."
                    }
                },
                "required": ["user_id", "role_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_role",
            "description": "Создаёт новую роль на сервере.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Название новой роли."
                    },
                    "color": {
                        "type": "string",
                        "description": "Необязательно. Цвет роли в HEX, например 'ff0000' или '#3498db'."
                    },
                    "hoist": {
                        "type": "string",
                        "description": "Необязательно. 'true', если роль должна отображаться отдельно в списке участников."
                    },
                    "mentionable": {
                        "type": "string",
                        "description": "Необязательно. 'true', если роль можно упоминать."
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_role",
            "description": "Полностью удаляет роль с сервера (не путать с remove_role, который снимает роль с пользователя).",
            "parameters": {
                "type": "object",
                "properties": {
                    "role_id_or_name": {
                        "type": "string",
                        "description": "ID или имя роли, которую нужно удалить с сервера."
                    }
                },
                "required": ["role_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_messages",
            "description": "Удаляет указанное количество последних сообщений в текущем канале.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "string",
                        "description": "Сколько последних сообщений удалить (от 1 до 100)."
                    }
                },
                "required": ["count"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_role",
            "description": "Изменяет существующую роль: имя, цвет, отображение отдельно (hoist), упоминаемость, позицию в иерархии и базовые права. Указывай только те поля, которые надо поменять.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role_id_or_name": {"type": "string", "description": "ID или имя роли, которую нужно изменить."},
                    "new_name": {"type": "string", "description": "Необязательно. Новое название роли."},
                    "color": {"type": "string", "description": "Необязательно. Новый цвет в HEX, например 'ff0000' или '#3498db'."},
                    "hoist": {"type": "string", "description": "Необязательно. 'true'/'false' — отображать ли роль отдельно в списке участников."},
                    "mentionable": {"type": "string", "description": "Необязательно. 'true'/'false' — можно ли упоминать роль."},
                    "position": {"type": "string", "description": "Необязательно. Новая позиция в иерархии (целое число, чем больше — тем выше)."},
                    "permissions": {"type": "string", "description": "Необязательно. Список прав через запятую для выдачи (например 'manage_messages, kick_members, manage_roles'). Имена прав discord.py."}
                },
                "required": ["role_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_channel",
            "description": "Создаёт новый канал на сервере: текстовый, голосовой или категорию.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Название нового канала."},
                    "type": {"type": "string", "description": "Тип канала: 'text', 'voice' или 'category'. По умолчанию 'text'."},
                    "category_id_or_name": {"type": "string", "description": "Необязательно. Категория, в которую поместить канал (ID или имя)."},
                    "topic": {"type": "string", "description": "Необязательно. Описание (topic) для текстового канала."}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_channel",
            "description": "Удаляет канал или категорию с сервера. Это необратимо.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id_or_name": {"type": "string", "description": "ID или имя канала/категории, который нужно удалить."}
                },
                "required": ["channel_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_channel",
            "description": "Изменяет настройки канала: имя, тему (topic), медленный режим (slowmode), NSFW, перемещение в категорию.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id_or_name": {"type": "string", "description": "ID или имя канала, который нужно изменить."},
                    "new_name": {"type": "string", "description": "Необязательно. Новое имя канала."},
                    "topic": {"type": "string", "description": "Необязательно. Новое описание (topic)."},
                    "slowmode_seconds": {"type": "string", "description": "Необязательно. Медленный режим в секундах (0 — выключить, до 21600)."},
                    "nsfw": {"type": "string", "description": "Необязательно. 'true'/'false' — пометить канал как NSFW."},
                    "category_id_or_name": {"type": "string", "description": "Необязательно. Переместить канал в эту категорию (ID или имя)."}
                },
                "required": ["channel_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_channel_permission",
            "description": "Настраивает доступ роли или пользователя к каналу: открыть или закрыть просмотр/отправку сообщений.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id_or_name": {"type": "string", "description": "ID или имя канала."},
                    "target_role_or_user": {"type": "string", "description": "ID или имя роли, либо ID пользователя, для которого настраивается доступ."},
                    "allow": {"type": "string", "description": "'true' — открыть доступ (просмотр+отправка), 'false' — закрыть."}
                },
                "required": ["channel_id_or_name", "target_role_or_user", "allow"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kick_user",
            "description": "Выгоняет (кикает) пользователя с сервера. В отличие от бана, он сможет вернуться по приглашению.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Discord ID пользователя."},
                    "reason": {"type": "string", "description": "Причина кика."}
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_nickname",
            "description": "Меняет никнейм пользователя на сервере. Пустое значение сбрасывает ник к имени аккаунта.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Discord ID пользователя."},
                    "nickname": {"type": "string", "description": "Новый никнейм. Пусто — сбросить."}
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_invite",
            "description": "Создаёт приглашение (invite) на сервер. Действует 24 часа. По умолчанию — текущий сервер; владелец может указать любой сервер, где есть P.OS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_id_or_name": {"type": "string", "description": "Необязательно. ID или название сервера, на который создать приглашение (из числа серверов, где есть P.OS). Если не указан — текущий сервер."},
                    "channel_id_or_name": {"type": "string", "description": "Необязательно. Канал, для которого создать приглашение. Если не указан — берётся первый доступный."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_servers",
            "description": "Возвращает список серверов (гильдий), где присутствует P.OS, с названиями, ID и числом участников. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "setup_logging",
            "description": "Создаёт на текущем сервере систему логов: категорию и набор каналов для логирования (модерация, сообщения, участники, роли, каналы, голос и т.д.), видимых только администраторам. Если что-то уже создано — дополняет недостающее и чинит права. Вызывать ТОЛЬКО когда об этом прямо попросили.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category_name": {
                        "type": "string",
                        "description": "Необязательно. Название категории логов. По умолчанию 'логи'."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "untimeout_user",
            "description": "Снимает тайм-аут (мут) с пользователя досрочно.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Discord ID пользователя."},
                    "reason": {"type": "string", "description": "Необязательно. Причина снятия мута."}
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Отправляет сообщение от имени P.OS в указанный канал (можно на другом сервере, где есть P.OS). ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id_or_name": {"type": "string", "description": "ID или имя канала, куда отправить сообщение."},
                    "text": {"type": "string", "description": "Текст сообщения."},
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер назначения (ID или имя). Если не указан — текущий сервер."}
                },
                "required": ["channel_id_or_name", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_settings",
            "description": "Показывает текущие настройки модерации и поведения P.OS на сервере. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер (ID или имя). Если не указан — текущий."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_settings",
            "description": (
                "Изменяет настройки модерации/безопасности P.OS на сервере. ТОЛЬКО для владельца. "
                "Передавай только те поля, которые нужно изменить. Булевы ключи (true/false): "
                "enabled, filter_ads, filter_spam, filter_flood, filter_scam, filter_nsfw, filter_raid, "
                "filter_mention_spam, filter_crosschannel, ai_moderation, allow_profanity. "
                "Числовые ключи: spam_window_seconds, spam_duplicates_threshold, flood_window_seconds, "
                "flood_messages_threshold, mention_limit, raid_join_window_seconds, raid_join_threshold, "
                "min_account_age_hours, timeout_hours. Строковый ключ raid_action: alert|kick|ban|lockdown. "
                "Маты и оскорбления разрешены по умолчанию (allow_profanity=true) и не модерируются."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "settings_json": {"type": "string", "description": "JSON-объект с изменяемыми настройками, например {\"filter_flood\": false, \"spam_duplicates_threshold\": 6}."},
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер (ID или имя). Если не указан — текущий."}
                },
                "required": ["settings_json"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "leave_server",
            "description": "P.OS покидает (выходит с) указанный сервер. Необратимо. ТОЛЬКО для владельца, требует подтверждения.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_id_or_name": {"type": "string", "description": "ID или имя сервера, который нужно покинуть."}
                },
                "required": ["server_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shutdown_bot",
            "description": "Полностью останавливает P.OS (завершает работу процесса бота). ТОЛЬКО для владельца, требует подтверждения.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Необязательно. Причина остановки."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mute_ai_for_user",
            "description": "Запрещает P.OS отвечать на сообщения этого пользователя (добавляет в черный список общения).",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Discord ID пользователя."
                    }
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "unmute_ai_for_user",
            "description": "Разрешает P.OS снова отвечать на сообщения пользователя (удаляет из черного списка).",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Discord ID пользователя."
                    }
                },
                "required": ["user_id"]
            }
        }
    }
]


# 0.8: кросс-серверность. Владелец может выполнять управляющие действия на любом
# сервере, где есть P.OS, не находясь на нём. Добавляем необязательный параметр
# server_id_or_name ко всем гильдийным инструментам (если его там ещё нет), чтобы
# модель знала о такой возможности. Резолвинг сервера выполняется в pos_ai.
_CROSS_SERVER_TOOLS = {
    "ban_user", "unban_user", "timeout_user", "untimeout_user", "kick_user", "set_nickname",
    "add_role", "remove_role", "create_role", "delete_role", "edit_role",
    "create_channel", "delete_channel", "edit_channel", "set_channel_permission",
    "delete_messages", "setup_logging",
}

def _inject_cross_server_param(tools: list) -> None:
    for tool in tools:
        fn = tool.get("function", {})
        if fn.get("name") in _CROSS_SERVER_TOOLS:
            params = fn.setdefault("parameters", {})
            props = params.setdefault("properties", {})
            props.setdefault(
                "server_id_or_name",
                {
                    "type": "string",
                    "description": "Необязательно. Сервер (ID или имя), на котором выполнить действие. Если не указан — текущий сервер. Только владелец может указывать другой сервер.",
                },
            )


_inject_cross_server_param(POS_AI_TOOLS)
