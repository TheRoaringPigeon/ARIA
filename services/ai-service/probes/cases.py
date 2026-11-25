from dataclasses import dataclass, field
from datetime import date

TODAY = date(2026, 11, 25)

RANGER = {"id": "v-ranger", "name": "Ranger", "domain": "vehicle", "tags": ["truck"]}
FURNACE = {"id": "h-furnace", "name": "Furnace", "domain": "home", "tags": ["hvac"]}
MOM = {"id": "p-mom", "name": "Mom", "domain": "person", "tags": []}
MOWER = {"id": "q-mower", "name": "Mower", "domain": "equipment", "tags": []}
DECK = {"id": "j-deck", "name": "Deck", "domain": "project", "tags": []}
HOUSEHOLD = [RANGER, FURNACE, MOM, MOWER, DECK]

FUZZY_HOUSEHOLD = [
    {"id": "v-ford", "name": "2021 Ford Ranger", "domain": "vehicle", "tags": []},
    {"id": "h-carrier", "name": "Carrier Gas Furnace", "domain": "home", "tags": []},
    {"id": "p-jo", "name": "Grandma Jo", "domain": "person", "tags": []},
]


@dataclass(frozen=True)
class RoutingCase:
    query: str
    agent: str
    history: tuple[dict, ...] = ()


@dataclass(frozen=True)
class ActionCase:
    """`tool` None means no card should be proposed. Each `expect` value is
    the wanted arg, or a tuple of acceptable ones."""

    query: str
    tool: str | None
    expect: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FuzzyCase:
    query: str
    entity_id: str | None


ROUTING = (
    RoutingCase("I just changed the oil in the Ranger", "action"),
    RoutingCase("log that I called Mom yesterday", "action"),
    RoutingCase("remind me to replace the furnace filter every 3 months", "action"),
    RoutingCase("schedule a gutter cleaning for next Saturday", "action"),
    RoutingCase("when is the Ranger due for its next oil change?", "vehicle"),
    RoutingCase("how many miles are on the Ranger?", "vehicle"),
    RoutingCase("what maintenance is overdue around the house?", "maintenance"),
    RoutingCase("when did we last service the water heater?", "maintenance"),
    RoutingCase("what does the dishwasher manual say about error code E24?", "research"),
    RoutingCase("will it rain this weekend?", "research"),
    RoutingCase("any news about Ford truck recalls this month?", "research"),
    RoutingCase("thanks, that's all!", "general"),
    RoutingCase("what's a good name for a golden retriever?", "general"),
    RoutingCase(
        "what about Warner Robins?",
        "research",
        (
            {"role": "user", "content": "will it rain tomorrow in Macon?"},
            {"role": "assistant", "content": "Macon has a 70% chance of rain tomorrow."},
        ),
    ),
)

ACTIONS = (
    ActionCase(
        "I just changed the oil in the Ranger",
        "create_log",
        {"entity_id": "v-ranger", "type": "service", "occurred_at": "2026-11-25"},
    ),
    ActionCase(
        "replaced the Furnace filter yesterday",
        "create_log",
        {"entity_id": "h-furnace", "type": "service", "occurred_at": "2026-11-24"},
    ),
    ActionCase(
        "fixed the leaking Furnace drain line today, it cost $45",
        "create_log",
        {"entity_id": "h-furnace", "type": "repair", "cost": 45.0},
    ),
    ActionCase(
        "had the Ranger inspected on Monday",
        "create_log",
        {"entity_id": "v-ranger", "type": "inspection", "occurred_at": "2026-11-23"},
    ),
    ActionCase(
        "called Mom yesterday to wish her happy birthday",
        "create_log",
        {"entity_id": "p-mom", "type": "call", "occurred_at": "2026-11-24"},
    ),
    ActionCase(
        "log that the Ranger is at 61500 miles after today's oil change",
        "create_log",
        {"entity_id": "v-ranger", "type": "service", "metrics": ({"mileage": "61500"},)},
    ),
    ActionCase(
        "bought a new blade for the Mower for $30",
        "create_log",
        {"entity_id": "q-mower", "type": ("expense", "service"), "cost": 30.0},
    ),
    ActionCase(
        "remind me to change the Furnace filter every 90 days",
        "create_schedule",
        {"entity_id": "h-furnace", "interval_type": "time", "interval_days": 90},
    ),
    ActionCase(
        "remind me every 6 months to service the Furnace",
        "create_schedule",
        {"entity_id": "h-furnace", "interval_type": "time", "interval_days": 180},
    ),
    ActionCase(
        "remind me to rotate the Ranger's tires every 5000 miles",
        "create_schedule",
        {
            "entity_id": "v-ranger",
            "interval_type": "usage",
            "usage_metric": ("mileage", "miles"),
            "interval_usage_amount": 5000.0,
        },
    ),
    ActionCase(
        "remind me to call Mom on December 24",
        "create_schedule",
        {"entity_id": "p-mom", "interval_type": "once", "planned_at": "2026-12-24"},
    ),
    ActionCase(
        "remind me to stain the Deck next Saturday",
        "create_schedule",
        {
            "entity_id": "j-deck",
            "interval_type": "once",
            "planned_at": ("2026-11-28", "2026-12-05"),
        },
    ),
    ActionCase(
        "remind me to sharpen the Mower blade on the first Saturday of every month",
        "create_schedule",
        {
            "entity_id": "q-mower",
            "interval_type": "monthly",
            "monthly_weekday": 5,
            "monthly_week_index": 1,
        },
    ),
    ActionCase("should I change the Ranger's oil soon?", None),
    ActionCase("how much did the Furnace repair cost last time?", None),
)

FUZZY = (
    FuzzyCase("when is my Ford due for service?", "v-ford"),
    FuzzyCase("is the furnace filter due yet?", "h-carrier"),
    FuzzyCase("what did grandma say when I called?", "p-jo"),
    FuzzyCase("what's the weather tomorrow?", None),
)
