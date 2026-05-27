from __future__ import annotations

import unittest

from agent_app.contracts.unified_objects import ModuleJob
from agent_app.contracts.unified_objects.module_job import TimeRange
from agent_app.modules.event_news_intelligence.repository import InMemoryEventNewsIntelligenceRepository, InstrumentProfile, RawTextItem
from agent_app.modules.event_news_intelligence.service import (
    EventNewsInput,
    EventNewsIntelligenceService,
    LlmEnvelope,
    polza_model_for_task,
)


class EventNewsFastReactivityTests(unittest.TestCase):
    def test_fast_news_uses_flash_and_material_news_escalates_to_qwen(self) -> None:
        self.assertEqual(
            polza_model_for_task("event_extraction"),
            "deepseek/deepseek-v4-flash",
        )
        self.assertEqual(
            polza_model_for_task("multi_source_event_synthesis"),
            "qwen/qwen3.6-35b-a3b",
        )

        service = EventNewsIntelligenceService()
        raw_item = RawTextItem(
            raw_text_item_id="raw_text_test_1",
            universe_id="moex_top20_manual",
            instrument_ids=("moex:VTBR",),
            source="rbc",
            source_type="rbc_news",
            title="ВТБ предупредил о давлении на прибыль",
            body="Новость указывает на существенное давление на акции и финансовые показатели.",
            fetched_at="2026-05-26T12:00:00Z",
            content_hash="hash-1",
        )
        envelope = LlmEnvelope(
            schema_version="event_news:v1",
            model_id="deepseek/deepseek-v4-flash",
            model_version="test",
            task_type="event_extraction",
            instrument_ids=("moex:VTBR",),
            items=(
                {
                    "event_type": "regulation",
                    "event_subtype": "negative_news",
                    "instrument_ids": ["moex:VTBR"],
                    "materiality_score": 0.82,
                    "sentiment_score": -0.7,
                    "evidence": ["существенное давление"],
                    "reason_codes": ["material_negative_news"],
                },
            ),
            confidence_score=0.8,
            evidence=("существенное давление",),
            reason_codes=("material_negative_news",),
        )

        self.assertTrue(service.should_escalate_to_reasoning(envelope, raw_item))

    def test_llm_request_idempotency_is_stable_across_scheduler_jobs(self) -> None:
        service = EventNewsIntelligenceService()
        event_input = EventNewsInput(
            routing_message_refs=(),
            raw_text_refs=(),
            instrument_ids=("moex:VTBR",),
            event_ontology_version="event_ontology:moex:v1",
            llm_prompt_version="event_news_extraction:v1",
            market_reaction_window=("5m", "1h", "1d"),
        )
        raw_item = RawTextItem(
            raw_text_item_id="raw_text_test_2",
            universe_id="moex_top20_manual",
            instrument_ids=("moex:VTBR",),
            source="rbc",
            source_type="rbc_news",
            title="ВТБ новость",
            body="Короткая новость по ВТБ.",
            fetched_at="2026-05-26T12:00:00Z",
            content_hash="same-content-hash",
        )
        base_job = dict(
            module_name="Event & News Intelligence Module",
            contour="intraday_contour",
            trigger_type="scheduled",
            universe_id="moex_top20_manual",
            instrument_ids=("moex:VTBR",),
            horizons=("intraday",),
            time_range=TimeRange("2026-05-26T11:58:00Z", "2026-05-26T12:00:00Z"),
            input_refs=(),
            run_mode="live_trading",
            priority="normal",
            status="pending",
        )
        job_a = ModuleJob(job_id="job_a", idempotency_key="tick-a", **base_job)
        job_b = ModuleJob(job_id="job_b", idempotency_key="tick-b", **base_job)

        request_a = service.create_llm_request(raw_item, event_input, job_a)
        request_b = service.create_llm_request(raw_item, event_input, job_b)

        self.assertEqual(request_a.idempotency_key, request_b.idempotency_key)
        self.assertEqual(request_a.cache_key, request_b.cache_key)
        self.assertEqual(request_a.payload["model"], "deepseek/deepseek-v4-flash")

    def test_scheduled_payload_placeholders_trigger_fresh_text_scan(self) -> None:
        raw_item = {
            "raw_text_item_id": "raw_text_scheduled_scan",
            "universe_id": "moex_top20_manual",
            "instrument_ids": ("moex:VTBR",),
            "source": "rbc",
            "source_type": "rbc_news",
            "title": "ВТБ новость без торгового события",
            "body": "Короткая нерелевантная заметка без существенного факта.",
            "fetched_at": "2026-05-26T12:00:00Z",
            "content_hash": "scheduled-scan-hash",
            "source_payload": {
                "llm_output": {
                    "schema_version": "event_news:v1",
                    "model_id": "deepseek/deepseek-v4-flash",
                    "model_version": "test",
                    "task_type": "event_extraction",
                    "instrument_ids": ["moex:VTBR"],
                    "items": [],
                    "confidence_score": 0.9,
                    "evidence": [],
                    "reason_codes": ["no_material_event"],
                    "warnings": ["no_event_found"],
                }
            },
        }
        repo = InMemoryEventNewsIntelligenceRepository(raw_text_items=(raw_item,))
        service = EventNewsIntelligenceService(repository=repo)
        job = ModuleJob(
            job_id="job_scheduled_scan",
            module_name="Event & News Intelligence Module",
            contour="intraday_contour",
            trigger_type="scheduled",
            universe_id="moex_top20_manual",
            instrument_ids=("moex:VTBR",),
            horizons=("intraday",),
            time_range=TimeRange("2026-05-26T11:58:00Z", "2026-05-26T12:01:00Z"),
            input_refs=("raw_text.raw_text_item:scheduled", "raw_text.event_routing_message:scheduled"),
            run_mode="live_trading",
            idempotency_key="scheduled-scan",
            priority="normal",
            status="pending",
        )

        result = service.process(
            {
                "event_news_input": {
                    "routing_message_refs": ["raw_text.event_routing_message:scheduled"],
                    "raw_text_refs": ["raw_text.raw_text_item:scheduled"],
                    "instrument_ids": ["moex:VTBR"],
                    "event_ontology_version": "event_ontology:moex:v1",
                    "llm_prompt_version": "event_news_extraction:v1",
                    "market_reaction_window": ["5m"],
                }
            },
            job,
        )

        self.assertNotIn("raw_text_items_missing", result.module_job_result.warnings)
        self.assertIn("no_event_found", result.module_job_result.warnings)

    def test_provider_items_payload_is_normalized_to_envelope(self) -> None:
        service = EventNewsIntelligenceService()
        event_input = EventNewsInput(
            routing_message_refs=(),
            raw_text_refs=(),
            instrument_ids=("moex:VTBR",),
            event_ontology_version="event_ontology:moex:v1",
            llm_prompt_version="event_news_extraction:v1",
            market_reaction_window=("5m",),
        )
        job = ModuleJob(
            job_id="job_provider_payload",
            module_name="Event & News Intelligence Module",
            contour="intraday_contour",
            trigger_type="scheduled",
            universe_id="moex_top20_manual",
            instrument_ids=("moex:VTBR",),
            horizons=("intraday",),
            time_range=TimeRange("2026-05-26T11:58:00Z", "2026-05-26T12:01:00Z"),
            input_refs=(),
            run_mode="live_trading",
            idempotency_key="provider-payload",
            priority="normal",
            status="pending",
        )
        raw_item = RawTextItem(
            raw_text_item_id="provider_payload_raw",
            universe_id="moex_top20_manual",
            instrument_ids=("moex:VTBR",),
            source="rbc",
            source_type="rbc_news",
            title="ВТБ допэмиссия",
            body="Акции банка резко упали на информации о допэмиссии.",
            fetched_at="2026-05-26T12:00:00Z",
            content_hash="provider-payload-hash",
        )
        request = service.create_llm_request(raw_item, event_input, job)
        normalized = service.normalize_llm_envelope_payload(
            {
                "schema_version": "event_news_extraction:v1",
                "model_id": "event_news_extraction:v1",
                "model_version": "1.0.0",
                "task_type": "event_extraction",
                "warnings": [],
                "items": [
                    {
                        "event_type": "corporate_action",
                        "event_subtype": "secondary_offering",
                        "instrument_ids": ["moex:VTBR"],
                        "materiality_score": 0.9,
                        "sentiment_score": -0.9,
                        "confidence_score": 0.95,
                        "evidence": ["информация о допэмиссии"],
                        "reason_codes": ["equity_offering_announcement"],
                    }
                ],
            },
            request,
            event_input,
        )
        envelope = service.validate_llm_output(normalized)

        self.assertEqual(envelope.model_id, "deepseek/deepseek-v4-flash")
        self.assertEqual(envelope.instrument_ids, ("moex:VTBR",))
        self.assertEqual(envelope.confidence_score, 0.95)
        self.assertIn("llm_envelope_normalized", envelope.warnings)

    def test_explicit_instrument_ids_prevent_alias_spillover(self) -> None:
        service = EventNewsIntelligenceService()
        raw_item = RawTextItem(
            raw_text_item_id="vtbr_news",
            universe_id="moex_top20_manual",
            instrument_ids=("moex:VTBR",),
            source="finam",
            source_type="finam_news",
            title="ВТБ нокаутировал рынок",
            body='Акции банка упали на новости о допэмиссии <a target="_blank">Далее</a>.',
            fetched_at="2026-05-26T12:00:00Z",
            content_hash="vtbr-news",
        )
        envelope = LlmEnvelope(
            schema_version="event_news:v1",
            model_id="deepseek/deepseek-v4-flash",
            model_version="test",
            task_type="event_extraction",
            instrument_ids=("moex:VTBR",),
            items=(),
            confidence_score=0.9,
            evidence=("допэмиссия",),
            reason_codes=("issuer_specific_negative_news",),
        )
        profiles = (
            InstrumentProfile("moex:VTBR", "moex_top20_manual", "VTBR", issuer_name="ВТБ"),
            InstrumentProfile("moex:MOEX", "moex_top20_manual", "MOEX", aliases=("рынок",)),
            InstrumentProfile("moex:T", "moex_top20_manual", "T"),
        )

        affected = service.extract_affected_instruments(
            item={"instrument_ids": ["moex:VTBR"]},
            envelope=envelope,
            raw_item=raw_item,
            routing_messages=(),
            profiles=profiles,
            event_type="corporate_action",
            requested_instrument_ids=("moex:VTBR", "moex:MOEX", "moex:T"),
        )

        self.assertEqual(affected, ("moex:VTBR",))


if __name__ == "__main__":
    unittest.main()
