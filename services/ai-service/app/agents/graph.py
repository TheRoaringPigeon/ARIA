from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.nodes import (
    execute_action_node,
    general_node,
    maintenance_node,
    propose_action_node,
    research_node,
    supervisor_node,
    vehicle_node,
)
from app.agents.state import AgentState
from app.config import settings
from app.lazy_singleton import AsyncLazySingleton


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
    builder.add_node("propose_action", propose_action_node)
    builder.add_node("execute_action", execute_action_node)

    builder.set_entry_point("supervisor")
    builder.add_conditional_edges(
        "supervisor",
        _route,
        {
            "maintenance": "maintenance",
            "vehicle": "vehicle",
            "research": "research",
            "general": "general",
            "action": "propose_action",
        },
    )
    builder.add_edge("maintenance", END)
    builder.add_edge("vehicle", END)
    builder.add_edge("research", END)
    builder.add_edge("general", END)
    # `execute_action_node` branches internally on its own `interrupt()`
    # result / `proposed_action` (see app/agents/nodes.py) — no conditional
    # *edges* needed here, keeping this as flat as the four-specialist shape
    # above.
    builder.add_edge("propose_action", "execute_action")
    builder.add_edge("execute_action", END)
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
    # permanently grows `agent-store`'s keyspace. `refresh_on_read=True`
    # because M8's confirm/cancel flow reads a checkpoint back a *second*
    # time — whenever the user responds to the action confirmation card —
    # and that response can come well after `default_ttl` has elapsed if
    # they don't respond right away; refreshing on read means a checkpoint
    # only expires after `default_ttl` of genuine inactivity, not a fixed
    # clock started at propose time (caught in code review — this was
    # `False` under the now-stale assumption that a checkpoint is always
    # read back at most once, milliseconds after being written).
    checkpointer = AsyncRedisSaver(
        redis_url=settings.redis_url,
        ttl={"default_ttl": settings.agent_checkpoint_ttl_minutes, "refresh_on_read": True},
    )
    await checkpointer.setup()
    return build_graph_builder().compile(checkpointer=checkpointer)


_graph = AsyncLazySingleton(_build_graph)


async def get_graph() -> CompiledStateGraph:
    """Lazy singleton — mirrors `chroma.py`'s existing lazy-client pattern.
    Built once and reused across requests: the compiled graph object holds
    no per-request state, everything request-scoped flows through
    `config`/`state`, not closures.
    """
    return await _graph.get()
