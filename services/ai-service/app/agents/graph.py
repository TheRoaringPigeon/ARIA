import asyncio

from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.nodes import (
    general_node,
    maintenance_node,
    research_node,
    supervisor_node,
    vehicle_node,
)
from app.agents.state import AgentState
from app.config import settings

_graph: CompiledStateGraph | None = None
_graph_lock = asyncio.Lock()


def _route(state: AgentState) -> str:
    return state["selected_agent"]


def build_graph_builder() -> StateGraph:
    """Wires nodes and edges only — no checkpointer. Split out from
    `_build_graph()` so tests can compile the same graph shape against an
    in-memory `MemorySaver` instead of the real `AsyncRedisSaver`, without
    duplicating this wiring.
    """
    builder = StateGraph(AgentState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("maintenance", maintenance_node)
    builder.add_node("vehicle", vehicle_node)
    builder.add_node("research", research_node)
    builder.add_node("general", general_node)

    builder.set_entry_point("supervisor")
    builder.add_conditional_edges(
        "supervisor",
        _route,
        {
            "maintenance": "maintenance",
            "vehicle": "vehicle",
            "research": "research",
            "general": "general",
        },
    )
    builder.add_edge("maintenance", END)
    builder.add_edge("vehicle", END)
    builder.add_edge("research", END)
    builder.add_edge("general", END)
    return builder


async def _build_graph() -> CompiledStateGraph:
    # Verified against the installed `langgraph-checkpoint-redis` version
    # (see the plan's design decisions): the historical `AsyncRedisSaver
    # .setup()` "coroutine never awaited" bug (langchain-ai/langgraph#5472)
    # is fixed here — `setup()` properly awaits `asetup()` — so this uses
    # `AsyncRedisSaver` directly for both setup and runtime, no sync-saver
    # workaround needed. `redis_url` points at the dedicated `agent-store`
    # Redis Stack instance (see `config.py`) — this checkpointer needs the
    # RediSearch/RedisJSON modules for its indices, which the plain Redis
    # Celery uses does not have.
    # `default_ttl` (minutes) caught in code review: every chat turn
    # checkpoints under a fresh, never-reused `thread_id` (see
    # `routers/chat.py`), so without a TTL every chat message ever sent
    # permanently grows `agent-store`'s keyspace. `refresh_on_read=False`
    # since a checkpoint is read back at most once, milliseconds after
    # it's written (inside the same request) — there's nothing to refresh.
    checkpointer = AsyncRedisSaver(
        redis_url=settings.redis_url,
        ttl={"default_ttl": settings.agent_checkpoint_ttl_minutes, "refresh_on_read": False},
    )
    await checkpointer.setup()
    return build_graph_builder().compile(checkpointer=checkpointer)


async def get_graph() -> CompiledStateGraph:
    """Lazy singleton — mirrors `chroma.py`/`ollama.py`'s existing
    lazy-client pattern. Built once and reused across requests: the
    compiled graph object holds no per-request state, everything
    request-scoped flows through `config`/`state`, not closures. Guarded
    by a lock so two concurrent first-requests can't each build (and
    `.setup()`) their own checkpointer.
    """
    global _graph
    if _graph is None:
        async with _graph_lock:
            if _graph is None:
                _graph = await _build_graph()
    return _graph
