# P-OS 
# Бот, или же экосистема дс сервера P.S.C.

## Railway env

Для P.OS AI выстави в Railway:

- `POS_AI_API_KEY`
- `POS_AI_API_URL` — по умолчанию `https://integrate.api.nvidia.com/v1/chat/completions`
- `POS_AI_MODEL` — по умолчанию `meta/llama-3.2-11b-vision-instruct`
- `PRIMARY_LOG_CHANNEL_ID` — по умолчанию `1392124917230731376`
- `UPDATE_LOG_CHANNEL_ID` — по умолчанию `1414265499658748045`

Старые переменные `NVIDIA_API_KEY`, `NVIDIA_API_URL`, `NVIDIA_MODEL` тоже поддерживаются как fallback.

Фильтрация ссылок и медиа тоже использует этот же `POS_AI_API_*` стек.
