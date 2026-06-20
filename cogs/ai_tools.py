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
