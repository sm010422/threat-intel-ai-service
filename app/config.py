from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-2.5-flash"
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

    # RAG
    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 3

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key)


settings = Settings()
