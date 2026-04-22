# PSC-HELPER

Discord-бот для сервера P.S.C. с модерацией, формами, подробными логами, GIF-генерацией и персонажем P.OS.

## Что сейчас внутри

- автомодерация ссылок, вложений, спама и подозрительного контента;
- P.OS с ответами на пинги, реплаи и продолжением диалога по контексту;
- поддержка анализа изображений через GitHub Models;
- формы заявок, жалоб и наказаний;
- серверные логи и журнал обновлений;
- команда `!gif` с нормальной сборкой GIF из изображений или короткого видео.

## Railway

Для продакшена на Railway достаточно переменных окружения. Локальный `.env` нужен только для разработки.

### Обязательное

- `DISCORD_TOKEN`

### Для P.OS через GitHub Models

- `GITHUB_MODELS_TOKEN`
- `GITHUB_MODELS_MODEL`
  По умолчанию: `openai/gpt-4.1`
- `GITHUB_MODELS_ENDPOINT`
  По умолчанию: `https://models.github.ai/inference/chat/completions`
- `GITHUB_MODELS_API_VERSION`
  По умолчанию: `2022-11-28`

### Совместимость со старой схемой

Если нужно, бот всё ещё понимает:

- `POS_AI_API_KEY`
- `POS_AI_API_URL`
- `POS_AI_MODEL`
- `NVIDIA_API_KEY`
- `NVIDIA_API_URL`
- `NVIDIA_MODEL`

### Дополнительные настройки P.OS

- `POS_AI_SYSTEM_PROMPT`
- `POS_AI_MAX_TOKENS`
- `POS_AI_TEMPERATURE`
- `POS_AI_TOP_P`
- `POS_AI_TIMEOUT_SECONDS`

### Логи

- `PRIMARY_LOG_CHANNEL_ID`
- `UPDATE_LOG_CHANNEL_ID`
- `LOG_CATEGORY_ID`
- `LOG_CATEGORY_NAME`

## Установка локально

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Для локальной проверки и типизации:

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -p "test_*.py"
```

## Файлы

- `/Users/vladislavganov/Documents/GitHub/PSC-HELPER/main.py` — запуск бота
- `/Users/vladislavganov/Documents/GitHub/PSC-HELPER/config.py` — переменные окружения и ID
- `/Users/vladislavganov/Documents/GitHub/PSC-HELPER/events.py` — серверные события
- `/Users/vladislavganov/Documents/GitHub/PSC-HELPER/moderation.py` — автомодерация и AI-проверки
- `/Users/vladislavganov/Documents/GitHub/PSC-HELPER/pos_ai.py` — логика P.OS
- `/Users/vladislavganov/Documents/GitHub/PSC-HELPER/ai_client.py` — OpenAI-compatible клиент для GitHub Models и других провайдеров
- `/Users/vladislavganov/Documents/GitHub/PSC-HELPER/commands.py` — текстовые и slash-команды
- `/Users/vladislavganov/Documents/GitHub/PSC-HELPER/logging_utils.py` — логирование

## Важно по токену GitHub Models

Если используешь fine-grained PAT, ему нужен доступ к Models API. Если токен уже светился в чате или логах, лучше сразу выпустить новый и обновить его в Railway.
