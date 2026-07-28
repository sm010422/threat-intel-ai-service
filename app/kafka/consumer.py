"""Consumes the same `target-tracking` Kafka topic as target-tracking-service.

Uses its own consumer group (settings.kafka_group_id) so this service reads
the full stream independently of the Java service's `target-tracking-group`
consumer -- Kafka fans the same topic out to every distinct group.

Each event is embedded and stored in the `target_history` Qdrant collection,
building the actual detection history that pattern_search queries against
(the Java service only compares against a fixed 10-pattern knowledge base,
never against real historical detections).

Gated behind settings.auto_index_enabled (default False) -- with no
filtering this consumed the Gemini free-tier daily embedding quota (1000/day)
in well under an hour once the ADS-B feed was flowing.
"""

import json
import logging

from aiokafka import AIOKafkaConsumer

from app.config import settings
from app.models.schemas import TargetEvent
from app.rag.pattern_store import upsert_target_event

logger = logging.getLogger(__name__)

is_running = False


async def run_consumer() -> None:
    global is_running

    consumer = AIOKafkaConsumer(
        settings.kafka_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_group_id,
        auto_offset_reset="earliest",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )

    await consumer.start()
    is_running = True
    logger.info("Kafka consumer started: topic=%s group=%s", settings.kafka_topic, settings.kafka_group_id)
    try:
        async for message in consumer:
            try:
                if not settings.auto_index_enabled:
                    continue
                event = TargetEvent.model_validate(message.value)
                await upsert_target_event(event)
                logger.info("Indexed target event: %s", event.targetId)
            except Exception:
                logger.exception("Failed to process Kafka message: %s", message.value)
    finally:
        is_running = False
        await consumer.stop()
