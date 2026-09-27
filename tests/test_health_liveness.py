"""2026-09-27 사건 이후 추가된 /live 엔드포인트 검증. run_consumer()에 재시도
로직을 넣어 그 사건의 근본 원인은 고쳤지만(app/kafka/consumer.py), 컨슈머가
다른 이유로 다시 죽어있는 상태가 되는 미래의 버그까지 잡아내려면
livenessProbe가 실제로 kafka_consumer_running을 파드 재시작 트리거로
써야 한다 -- 기존 /health는 상태와 무관하게 항상 200이라 이걸 못 잡았다.
"""

import asyncio

from app.config import settings
from app.kafka import consumer as kafka_consumer
from app.routers.health import liveness


class TestLivenessProbe:
    def setup_method(self):
        self._original_is_running = kafka_consumer.is_running
        self._original_kafka_enabled = settings.kafka_enabled

    def teardown_method(self):
        kafka_consumer.is_running = self._original_is_running
        settings.kafka_enabled = self._original_kafka_enabled

    def test_returns_503_when_kafka_enabled_and_consumer_not_running(self):
        settings.kafka_enabled = True
        kafka_consumer.is_running = False

        response = asyncio.run(liveness())

        assert response.status_code == 503

    def test_returns_200_when_consumer_running(self):
        settings.kafka_enabled = True
        kafka_consumer.is_running = True

        response = asyncio.run(liveness())

        assert response.status_code == 200

    def test_returns_200_when_kafka_disabled_even_if_consumer_never_started(self):
        settings.kafka_enabled = False
        kafka_consumer.is_running = False

        response = asyncio.run(liveness())

        assert response.status_code == 200
