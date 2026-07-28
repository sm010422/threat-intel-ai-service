from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-flash-lite-latest"
    gemini_embedding_model: str = "models/gemini-embedding-001"
    embedding_dimension: int = 768

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_doc_collection: str = "threat_documents"
    qdrant_pattern_collection: str = "target_history"

    # Kafka (shared broker with target-tracking-service)
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "target-tracking"
    kafka_group_id: str = "threat-intel-ai-service"
    kafka_enabled: bool = True

    # 기본값 False -- consumer.py는 필터링/쿨다운 없이 Kafka 메시지 하나마다
    # embed_text()를 호출해서 Qdrant에 색인했다. ADS-B가 20초마다 지역당 수십 대씩
    # 흘려보내는 상황에서 이게 Gemini 무료 tier 일일 임베딩 한도(1000건)를
    # 실제로 다 써버린 주범이었다 (target-tracking-service의 자동분석보다 훨씬
    # 빠르게 소진시킴 -- 사람 개입이 전혀 없는 무제한 색인이었으니까).
    # true로 켜면 실시간 표적 이력 색인(→ pattern_search RAG)이 복원된다.
    auto_index_enabled: bool = False

    # RAG
    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 3

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key)


settings = Settings()
