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
                "filter_mention_spam, filter_crosschannel, ai_moderation, allow_profanity, log_messages, log_reactions. "
                "Числовые ключи: spam_window_seconds, spam_duplicates_threshold, flood_window_seconds, "
                "flood_messages_threshold, mention_limit, raid_join_window_seconds, raid_join_threshold, "
                "raid_mode_cooldown_seconds, min_account_age_hours, timeout_hours, crosschannel_window_seconds, "
                "crosschannel_channels_threshold. Строковый ключ raid_action: alert|quarantine|kick|ban. "
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
            "name": "ping_user",
            "description": "Пингует (упоминает) пользователя в канале так, чтобы пинг реально прошёл (пришло уведомление). ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Discord ID пользователя, которого пингнуть."},
                    "text": {"type": "string", "description": "Необязательно. Текст сообщения рядом с пингом."},
                    "channel_id_or_name": {"type": "string", "description": "Необязательно. Канал для пинга. Если не указан — текущий канал."},
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер (ID или имя). Если не указан — текущий."}
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dm_user",
            "description": "Отправляет личное сообщение (ЛС) пользователю от имени P.OS. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Discord ID пользователя, которому написать в ЛС."},
                    "text": {"type": "string", "description": "Текст личного сообщения."}
                },
                "required": ["user_id", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lift_restrictions",
            "description": "Снимает ограничения с пользователя (тайм-аут/карантин/роль-мут) и уведомляет его в ЛС. Используй, когда владелец решил, что аккаунт нормальный. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Discord ID пользователя, с которого снять ограничения."},
                    "reason": {"type": "string", "description": "Необязательно. Причина снятия."},
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер (ID или имя). Если не указан — текущий."}
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "deactivate_raid_mode",
            "description": "Снимает (деактивирует) режим рейда на сервере по команде владельца. ТОЛЬКО для владельца.",
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


POS_AI_TOOLS.extend([
    {
        "type": "function",
        "function": {
            "name": "edit_server",
            "description": "Изменяет базовые настройки сервера: название, описание, уровень проверки, фильтр контента, режим уведомлений. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Необязательно. Новое название сервера."},
                    "description": {"type": "string", "description": "Необязательно. Новое описание сервера."},
                    "verification_level": {"type": "string", "description": "Необязательно: none|low|medium|high|highest."},
                    "explicit_content_filter": {"type": "string", "description": "Необязательно: disabled|no_role|all_members."},
                    "default_notifications": {"type": "string", "description": "Необязательно: all_messages|only_mentions."},
                    "reason": {"type": "string", "description": "Необязательно. Причина изменения."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lock_channel",
            "description": "Закрывает канал для @everyone или указанной роли/пользователя: просмотр, отправку сообщений или оба права. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id_or_name": {"type": "string", "description": "ID или имя канала."},
                    "target_role_or_user": {"type": "string", "description": "Необязательно. Роль/пользователь. Если пусто — @everyone."},
                    "mode": {"type": "string", "description": "view|send|both. По умолчанию both."},
                    "reason": {"type": "string", "description": "Необязательно. Причина блокировки."}
                },
                "required": ["channel_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "unlock_channel",
            "description": "Снимает запрет просмотра/отправки сообщений в канале для @everyone или указанной роли/пользователя. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id_or_name": {"type": "string", "description": "ID или имя канала."},
                    "target_role_or_user": {"type": "string", "description": "Необязательно. Роль/пользователь. Если пусто — @everyone."},
                    "mode": {"type": "string", "description": "view|send|both. По умолчанию both."},
                    "reason": {"type": "string", "description": "Необязательно. Причина разблокировки."}
                },
                "required": ["channel_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_thread",
            "description": "Создаёт ветку в текстовом канале или от конкретного сообщения. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id_or_name": {"type": "string", "description": "ID или имя текстового канала."},
                    "name": {"type": "string", "description": "Название ветки."},
                    "message_id": {"type": "string", "description": "Необязательно. ID сообщения, от которого создать ветку."},
                    "private": {"type": "string", "description": "Необязательно. true — приватная ветка, false — публичная."},
                    "reason": {"type": "string", "description": "Необязательно. Причина создания."}
                },
                "required": ["channel_id_or_name", "name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "archive_thread",
            "description": "Архивирует/разархивирует и при необходимости блокирует/разблокирует ветку. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id_or_name": {"type": "string", "description": "ID или имя ветки."},
                    "archived": {"type": "string", "description": "true/false. По умолчанию true."},
                    "locked": {"type": "string", "description": "Необязательно. true/false — заблокировать ветку."},
                    "reason": {"type": "string", "description": "Необязательно. Причина."}
                },
                "required": ["channel_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "voice_action",
            "description": "Выполняет действие с участником в голосе: disconnect, mute, unmute, deafen, undeafen, move. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Discord ID пользователя."},
                    "action": {"type": "string", "description": "disconnect|mute|unmute|deafen|undeafen|move."},
                    "channel_id_or_name": {"type": "string", "description": "Для move: ID или имя голосового канала назначения."},
                    "reason": {"type": "string", "description": "Необязательно. Причина."}
                },
                "required": ["user_id", "action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "security_scan",
            "description": "Проводит быстрый аудит безопасности сервера: права P.OS, настройки модерации, raid mode, публичные каналы и рискованные роли. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "summary|channels|roles|moderation|all. По умолчанию summary."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_security_preset",
            "description": "Быстро применяет профиль безопасности P.OS: normal, strict или raid. Меняет настройки автомодерации/антирейда. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "preset": {"type": "string", "description": "normal|strict|raid."},
                    "reason": {"type": "string", "description": "Необязательно. Почему включается профиль."}
                },
                "required": ["preset"]
            }
        }
    },
])

POS_AI_TOOLS.extend([
    {
        "type": "function",
        "function": {
            "name": "list_channels",
            "description": "Показывает фактическую структуру каналов сервера с типами, категориями и Discord ID. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_threads": {"type": "string", "description": "true/false. Включить активные ветки."},
                    "limit": {"type": "string", "description": "Сколько объектов вернуть, 1-100."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_roles",
            "description": "Показывает фактические роли сервера с Discord ID, позицией, числом участников и ключевыми правами. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "string", "description": "Сколько ролей вернуть, 1-100."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_audit_log",
            "description": "Читает фактический Discord Audit Log: действие, исполнитель, цель, время и причина. Можно фильтровать по названию действия. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Необязательно. Фильтр, например ban, role, channel, kick."},
                    "limit": {"type": "string", "description": "Сколько записей вернуть, 1-50."},
                },
                "required": [],
            },
        },
    },
])

POS_AI_TOOLS.extend([
    {
        "type": "function",
        "function": {
            "name": "list_members",
            "description": "Показывает фактический список участников сервера с username/login, display name и ID. Можно фильтровать по query или роли. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Необязательно. Часть username/login/global/display для поиска."},
                    "role_id_or_name": {"type": "string", "description": "Необязательно. Показать только участников с этой ролью."},
                    "limit": {"type": "string", "description": "Необязательно. Сколько показать, 1-50."},
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер (ID или имя). Если не указан — текущий."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "user_info",
            "description": "Показывает фактическую карточку участника: username/login, display/global, ID, роли, ключевые права, даты, timeout. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Discord ID, mention или username/login пользователя."},
                    "user_identifier": {"type": "string", "description": "Username/login/global/display пользователя, если ID неизвестен."},
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер (ID или имя). Если не указан — текущий."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_messages",
            "description": "Читает последние сообщения в указанном канале (или текущем канале) с фактическими message_id, авторами и временем. Можно фильтровать по тексту и автору. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id_or_name": {"type": "string", "description": "ID или имя канала. Для другого сервера обязательно."},
                    "limit": {"type": "string", "description": "Сколько сообщений вернуть, 1-50."},
                    "query": {"type": "string", "description": "Необязательно. Фильтр по тексту сообщения."},
                    "user_identifier": {"type": "string", "description": "Необязательно. Фильтр по автору: ID, mention или username/login."},
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер (ID или имя). Если не указан — текущий."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": "Ищет фактические события в журнале P.OS: серверные логи, действия P.OS, сообщения, удаления и пинги. Не выдумывает, возвращает только найденное. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Необязательно. Поиск по summary/details/actor."},
                    "event_type": {"type": "string", "description": "Необязательно. Например pos_tool, message_mention, log:members, log:message_deletes, log:security."},
                    "limit": {"type": "string", "description": "Сколько событий вернуть, 1-50."},
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер (ID или имя). Если не указан — текущий."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_pings",
            "description": "Ищет, кто пинговал пользователя напрямую или через роль, которая у него есть. Находит даже удалённые сообщения, если P.OS видел исходный пинг. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "Discord ID, mention или username/login. Если пусто и спрашивает владелец — ищет пинги владельца."},
                    "user_identifier": {"type": "string", "description": "Username/login/global/display пользователя, если ID неизвестен."},
                    "include_roles": {"type": "string", "description": "true/false. По умолчанию true — учитывать пинги ролей пользователя."},
                    "limit": {"type": "string", "description": "Сколько событий вернуть, 1-50."},
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер (ID или имя). Если не указан — текущий."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "bulk_user_action",
            "description": "Выполняет массовое действие по списку username/login/ID: ban, kick, timeout, untimeout, add_role, remove_role, lift_restrictions. ТОЛЬКО для владельца.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "ban|kick|timeout|untimeout|add_role|remove_role|lift_restrictions."},
                    "user_identifiers": {"type": "string", "description": "Список пользователей: username/login/ID/mentions через запятую, пробел или с новой строки."},
                    "role_id_or_name": {"type": "string", "description": "Для add_role/remove_role: ID или имя роли."},
                    "minutes": {"type": "string", "description": "Для timeout: минуты."},
                    "reason": {"type": "string", "description": "Необязательно. Причина действия."},
                    "server_id_or_name": {"type": "string", "description": "Необязательно. Сервер (ID или имя). Если не указан — текущий."}
                },
                "required": ["action", "user_identifiers"]
            }
        }
    },
])


# 0.8: кросс-серверность. Владелец может выполнять управляющие действия на любом
# сервере, где есть P.OS, не находясь на нём. Добавляем необязательный параметр
# server_id_or_name ко всем гильдийным инструментам (если его там ещё нет), чтобы
# модель знала о такой возможности. Резолвинг сервера выполняется в pos_ai.
_CROSS_SERVER_TOOLS = {
    "ban_user", "unban_user", "timeout_user", "untimeout_user", "kick_user", "set_nickname",
    "add_role", "remove_role", "create_role", "delete_role", "edit_role",
    "create_channel", "delete_channel", "edit_channel", "set_channel_permission",
    "lock_channel", "unlock_channel", "create_thread", "archive_thread",
    "edit_server", "voice_action", "security_scan", "set_security_preset",
    "create_invite", "delete_messages", "setup_logging", "send_message",
    "get_settings", "update_settings", "dm_user", "mute_ai_for_user", "unmute_ai_for_user",
    "list_members", "user_info", "read_messages", "search_logs", "search_pings", "bulk_user_action",
    "list_channels", "list_roles", "read_audit_log",
    "ping_user", "lift_restrictions", "deactivate_raid_mode",
}

_USER_IDENTIFIER_TOOLS = {
    "ban_user", "unban_user", "timeout_user", "untimeout_user", "kick_user", "set_nickname",
    "add_role", "remove_role", "voice_action", "ping_user", "dm_user", "lift_restrictions",
    "mute_ai_for_user", "unmute_ai_for_user",
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


def _inject_user_identifier_param(tools: list) -> None:
    for tool in tools:
        fn = tool.get("function", {})
        if fn.get("name") not in _USER_IDENTIFIER_TOOLS:
            continue
        params = fn.setdefault("parameters", {})
        props = params.setdefault("properties", {})
        if "user_id" in props:
            props["user_id"]["description"] = (
                props["user_id"].get("description", "Пользователь.")
                + " Можно передать ID, mention или username/login; код проверит фактического участника."
            )
        props.setdefault(
            "user_identifier",
            {
                "type": "string",
                "description": "Необязательно. Username/login/global/display пользователя, если ID неизвестен.",
            },
        )
        required = params.get("required") or []
        if "user_id" in required:
            params["required"] = [item for item in required if item != "user_id"]


_inject_user_identifier_param(POS_AI_TOOLS)
