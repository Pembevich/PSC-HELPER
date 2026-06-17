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
