"""2026-09-27 정전/재부팅 사건 재현: 모든 파드가 동시에 재시작될 때 이 서비스가
Kafka 브로커보다 먼저 뜨면 consumer.start()가 ConnectionError로 죽고
is_running=False가 영구히 고정됐다 (재시도 로직 부재). run_consumer()가
최초 연결 실패 후 재시도해서 결국 is_running=True로 회복하는지 검증한다.
"""

import asyncio

from app.kafka import consumer as kafka_consumer


class _FakeConsumer:
    attempts = 0

    def __init__(self, *args, **kwargs):
        _FakeConsumer.attempts += 1
        self._attempt = _FakeConsumer.attempts

    async def start(self):
        if self._attempt == 1:
            raise ConnectionError("Connection refused")

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(3600)

    async def stop(self):
        pass


class TestRunConsumerRetriesOnStartupFailure:
    def setup_method(self):
        _FakeConsumer.attempts = 0

    def test_recovers_is_running_after_initial_connection_failure(self, monkeypatch):
        monkeypatch.setattr(kafka_consumer, "AIOKafkaConsumer", lambda *a, **kw: _FakeConsumer())
        monkeypatch.setattr(kafka_consumer, "_RETRY_BACKOFF_SECONDS", 0)

        async def scenario():
            task = asyncio.create_task(kafka_consumer.run_consumer())
            try:
                for _ in range(200):
                    if kafka_consumer.is_running:
                        break
                    await asyncio.sleep(0)
                else:
                    raise AssertionError("consumer never recovered from initial connection failure")
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        asyncio.run(scenario())
        assert _FakeConsumer.attempts >= 2
