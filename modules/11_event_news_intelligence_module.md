# Event & News Intelligence Module — Новости и события

## 1. Назначение

Преобразует новости, раскрытия и тексты в структурированные события и event features: relevance, materiality, novelty, sentiment, surprise, expected horizon и market reaction.

## 2. Классификация

| Field | Value |
|---|---|
| `module_name` | `Event & News Intelligence Module` |
| `module_type` | `hybrid_llm` |
| `primary_contour` | `event_contour` |
| `secondary_contours` | `intraday_contour` |
| `execution_mode` | `llm + rules + event_study` |
| `llm_usage` | `required_for_text_semantics` |

## 3. Запуск

Модуль запускается только через `Orchestration Module` и принимает `module_job`. Прямой запуск из другого модуля запрещён.

### Required `module_job` fields

- `job_id`
- `input_refs`
- `instrument_ids`
- `time_range`
- `run_mode`

### Trigger policy

Запускается по `routing_message` от Data Intake, по important event trigger или по scheduled news scan.

## 4. Input classification

- `routing_message`
- `raw_text_item`
- `instrument_profile`
- `market_reaction_data`
- `event_ontology`

## 5. Input contract

```json
{
  "event_news_input": {
    "routing_message_refs": [
      "string"
    ],
    "raw_text_refs": [
      "string"
    ],
    "instrument_ids": [
      "string"
    ],
    "event_ontology_version": "string",
    "llm_prompt_version": "string",
    "market_reaction_window": [
      "5m",
      "1h",
      "1d"
    ]
  }
}
```

## 6. External requests

LLM-запросы выполняются через Gateway с `request_type=llm_completion`. Если нужна реакция рынка, market data запрашивается через Gateway.

MOEX ISS market-reaction requests:

- Market-reaction data is an auxiliary event-study input and must be requested per affected instrument.
- Each request must include exactly one `instrument_id`, explicit `payload.secid`, `payload.board_id`, `payload.timeframe` and `time_range`.
- A multi-instrument news event may create multiple MOEX requests, but raw candles from one `secid` must never be persisted under another `instrument_id`.

## 7. Processing rules

- `load_text_and_metadata`
- `apply_event_ontology`
- `extract_affected_instruments`
- `compute_relevance_score`
- `compute_materiality_score`
- `compute_novelty_score`
- `compute_sentiment_score`
- `compute_surprise_score`
- `extract_evidence`
- `assign_reason_codes`
- `compute_market_reaction_if_available`
- `write_structured_event`
- `write_event_features`

## 8. Output classification

- `structured_event`
- `feature_record`
- `event_reaction`

## 9. Output contract

```json
{
  "structured_event": {
    "event_id": "string",
    "instrument_ids": [
      "string"
    ],
    "event_type": "earnings | dividend | corporate_action | macro | regulation | sanctions | management | sector | market_structure | other",
    "event_subtype": "string",
    "event_ts": "string",
    "source_refs": [
      "string"
    ],
    "relevance_score": "number",
    "materiality_score": "number",
    "novelty_score": "number",
    "surprise_score": "number",
    "sentiment_score": "number",
    "confidence_score": "number",
    "evidence": [
      "string"
    ],
    "reason_codes": [
      "string"
    ],
    "model_version": "string"
  },
  "feature_records": [
    "feature_record"
  ]
}
```

## 10. Metrics / Records

- `news_sentiment_score`
- `news_materiality_score`
- `news_novelty_score`
- `news_surprise_score`
- `source_credibility_score`
- `issuer_relevance_score`
- `sector_relevance_score`
- `event_confidence`
- `event_decay_score`
- `event_reaction_5m`
- `event_reaction_1h`
- `event_reaction_1d`
- `abnormal_return_after_event`
- `abnormal_volume_after_event`
- `underreaction_score`
- `overreaction_score`
- `event_pressure_score`

## 11. Stores

| Direction | Store |
|---|---|
| `read` | `Raw Text Store` |
| `read` | `Event Routing Store` |
| `read` | `Selected Instruments DB` |
| `read/write` | `Event Store` |
| `write` | `Feature Store` |
| `write` | `Audit Log Store` |

## 12. TTL and freshness

News/event features получают TTL по `event_type`: intraday noise — минуты/часы; material corporate event — дни; macro/regulatory event — часы/дни.

## 13. Failure policy

Если LLM response не проходит schema validation, event не попадает в Feature Store. Raw text остаётся в Raw Text Store с error flag.

Corporate-event confirmation policy:

- `news_api`, `rbc_news`, `tass_news`, `interfax_news`, `prime_news`, `finam_news` and `smartlab_news` are early/candidate sources.
- `issuer_disclosure`, `prime_disclosure`, `akm_disclosure`, `corporate_site`, `regulatory_text`, `cbr_macro`, `moex_macro` and `macro_text` are official/confirmation sources.
- For `earnings`, `dividend` and `corporate_action`, ordinary news may create only a candidate event with `payload.confirmation_status = candidate_requires_official_confirmation`.
- A confirmed corporate event requires the official confirmation layer; downstream corporate-action processing must ignore candidate-only events.

## 14. Acceptance criteria

- `llm_output_schema_validated`
- `evidence_required_for_model_scores`
- `event_type_from_ontology`
- `no_freeform_trading_recommendation`
- `market_reaction_separate_from_sentiment`
- `confidence_score_required`
- `ordinary_news_does_not_confirm_corporate_event`

## 15. Metric formulas / calculation rules

| `metric_name` | Formula / rule |
|---|---|
| `news_sentiment_score` | LLM/classifier score in `[-1,1]` with evidence and reason codes |
| `news_materiality_score` | LLM/classifier score `0..1`; criteria: direct financial impact, regulatory impact, issuer relevance, surprise |
| `news_novelty_score` | `1 - max_similarity_to_recent_events` over recent event embeddings/hash clusters |
| `news_surprise_score` | LLM/classifier score `0..1` vs known expectations/history; requires evidence |
| `source_credibility_score` | source registry score `0..1` |
| `issuer_relevance_score` | entity-level relevance `0..1` from issuer/alias/related entity match |
| `sector_relevance_score` | sector-level relevance `0..1` from sector ontology match |
| `event_confidence` | `WAvg([source_credibility_score, issuer_relevance_score, extraction_confidence], [0.3,0.4,0.3])` |
| `event_decay_score` | `exp(-age_seconds / half_life_seconds)` |
| `event_reaction_5m` | `return_5m_after_event - market_return_5m_after_event` |
| `event_reaction_1h` | `return_1h_after_event - market_return_1h_after_event` |
| `event_reaction_1d` | `return_1d_after_event - market_return_1d_after_event` |
| `abnormal_return_after_event` | actual return after event minus expected beta-adjusted return |
| `abnormal_volume_after_event` | `volume_after_event / average_volume_same_window - 1` |
| `underreaction_score` | positive event score with weak price reaction: `positive_event_strength * max(0, 1 - abs(event_reaction_z))` |
| `overreaction_score` | `abs(event_reaction_z) * (1 - materiality_score)` clipped `0..1` |
| `event_pressure_score` | `WAvg([news_sentiment_score, news_materiality_score, news_novelty_score, event_decay_score], active_weights)` |

## 16. Forbidden actions

- Запрещено отправлять заявки или создавать `order_intent`.
- Запрещено записывать LLM free-form output как feature.
- Запрещено анализировать новости вне universe без macro/sector classification.
- Запрещено доверять LLM без JSON schema validation.
- Запрещено изменять `Metric Weights DB` или `Risk Policy Store`.
