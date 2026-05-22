# PolzaAI Integration Contract

Текст описания — русский. Программные сущности — английский.

## Provider

`provider`: `polza_ai`

## Base URL

`POLZA_BASE_URL=https://polza.ai/api/v1`

## Auth

```http
Authorization: Bearer ${POLZA_API_KEY}
```

## Main endpoint

`POST /chat/completions`

## Default model

`POLZA_LLM_MODEL=deepseek/deepseek-v4-pro`

Точный `model_id` должен проверяться через `GET /models` перед production-запуском.

## Required request policy

```json
{
  "model": "deepseek/deepseek-v4-pro",
  "messages": [],
  "temperature": 0,
  "response_format": {"type": "json_object"},
  "reasoning": {
    "enabled": true,
    "effort": "medium",
    "summary": "auto"
  }
}
```

## Required response policy

Ответ LLM принимается только после JSON validation. Free-form output is rejected.
