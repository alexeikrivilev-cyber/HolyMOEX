# PolzaAI Integration Contract

## Provider

`provider`: `polza_ai`

## Base URL

`POLZA_BASE_URL=https://polza.ai/api/v1`

## Auth

```http
Authorization: Bearer ${POLZA_API_KEY}
```

Secrets must come from environment variables or server secrets. They must not be committed, copied into the Docker image, or written to audit/request logs.

## Endpoints

- `GET /models` for model availability when the endpoint is supported.
- `POST /chat/completions` for strict JSON completion fallback and production LLM tasks.

## Model Routing

Task-specific routing has priority over legacy defaults:

- Fast/light text tasks use `POLZA_FAST_MODEL=deepseek/deepseek-v4-flash`.
- Report, earnings, macro, dividend, multi-source synthesis and other reasoning tasks use `POLZA_REASONING_MODEL=qwen/qwen3.6-35b-a3b`.
- Unknown task fallback uses `POLZA_DEFAULT_MODEL=qwen/qwen3.6-35b-a3b`.
- `POLZA_LLM_MODEL` is legacy compatibility only; server runtime ignores legacy expensive defaults and prefers `POLZA_DEFAULT_MODEL`.

## Required Request Policy

```json
{
  "model": "task-routed model id",
  "messages": [],
  "temperature": 0,
  "response_format": {"type": "json_object"}
}
```

Reasoning mode is enabled only for reasoning task types. Light EventNews classification/extraction uses a short strict JSON prompt and the fast model.

## Required Response Policy

LLM output is accepted only after strict JSON validation. Free-form output is rejected. A validated envelope with `"items": []` is a valid no-event result for irrelevant text and must not be retried as a schema failure.

LLM must not output buy/sell recommendations, order intents, target positions, weight changes, risk-policy changes or any final trading decision.
