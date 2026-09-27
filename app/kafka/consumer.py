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

import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer

from app.config import settings
from app.models.schemas import TargetEvent
from app.rag.pattern_store import upsert_target_event

logger = logging.getLogger(__name__)

is_running = False

# 2026-09-27: 정전/재부팅으로 모든 파드가 동시에 재시작될 때, 이 서비스가
# Kafka 브로커보다 먼저 뜨면 consumer.start()가 ConnectionError로 죽고
# is_running=False가 영구히 고정되는 사건이 실제로 발생했다 (재시도 로직이
# 아예 없었음 -- 최초 1회 연결 실패 = 그 프로세스 생명 동안 영구 다운).
# 브로커가 늦게 뜨는 건 정상적인 기동 순서 경쟁이므로 예외가 아니라 재시도로 처리한다.
_RETRY_BACKOFF_SECONDS = 5


async def run_consumer() -> None:
    global is_running

    while True:
        consumer = AIOKafkaConsumer(
            settings.kafka_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_group_id,
            auto_offset_reset="earliest",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )

        try:
            await consumer.start()
        except Exception:
            logger.exception(
                "Kafka consumer failed to connect, retrying in %ss", _RETRY_BACKOFF_SECONDS
            )
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
            continue

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
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Kafka consumer loop crashed, restarting in %ss", _RETRY_BACKOFF_SECONDS)
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
        finally:
            is_running = False
            await consumer.stop()
