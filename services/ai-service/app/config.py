from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_SERVICE_")

    chroma_host: str = "chromadb"
    chroma_port: int = 8000
    core_api_url: str = "http://core-api:8000"
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "qwen3:14b"
    # Separate from `ollama_model` — chat models make weak embeddings for
    # anything but near-exact text (verified empirically: paraphrased
    # queries scored *worse* against their true match than against
    # unrelated documents), so retrieval needs a model trained for it.
    embed_model: str = "nomic-embed-text"
    model_adapter: str = "qwen"
    rag_top_k: int = 4
    # Chroma L2 distance above which a retrieved chunk is dropped before it
    # can become prompt context or a citation — `n_results` alone always
    # returns `rag_top_k` chunks even when nothing in the corpus is
    # actually related. Calibrated against `nomic-embed-text`: real matches
    # measured <=0.80, unrelated documents measured >=0.96, on this
    # household's corpus — see docs/architecture.md for the query/distance
    # table this was based on. Re-check this gap if the corpus or embedding
    # model changes.
    rag_max_distance: float = 0.9
    frontend_origin: str = "http://localhost:5173"
    entity_match_limit: int = 3
    entity_logs_limit: int = 5
    # A dedicated Redis Stack instance (`agent-store` in docker-compose),
    # separate from the plain `redis:7-alpine` core-api/worker use for
    # Celery. LangGraph's Redis checkpointer needs the RediSearch/RedisJSON
    # modules for its indices (confirmed live: plain Redis fails with
    # "unknown command 'FT._LIST'") — rather than upgrade the
    # Celery-critical shared instance, ai-service gets its own, so an
    # agent-orchestration outage has zero blast radius on Celery.
    redis_url: str = "redis://agent-store:6379/0"
    # Bounds the Research Assistant's tool-choice loop (decide whether to
    # search documents, optionally again with a refined query) — a
    # confused model that always says "search again" must still terminate.
    agent_max_tool_calls: int = 2
    # Every chat turn checkpoints under a brand-new `thread_id` (no
    # cross-request conversation memory this milestone — see the M7 plan),
    # so a checkpoint is never read back after the request that wrote it
    # completes. Without a TTL, `agent-store` grows by one permanent,
    # never-reused entry per chat message forever — caught in code review.
    # 60 minutes is generous headroom for inspecting a recent request's
    # state while debugging, without accumulating indefinitely.
    agent_checkpoint_ttl_minutes: int = 60


settings = Settings()
