from dataclasses import dataclass, field
from datetime import date

from app.agents.nodes import RESEARCH_SCRATCHPAD
from app.entity_grounding import EntityContext

TODAY = date(2026, 11, 25)
END_OF_MONTH = date(2026, 11, 30)
SUNDAY = date(2026, 11, 29)
NEW_YEARS_EVE = date(2026, 12, 31)
NEW_YEARS_DAY = date(2027, 1, 1)

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
    today: date = TODAY


@dataclass(frozen=True)
class FuzzyCase:
    query: str
    entity_id: str | None


@dataclass(frozen=True)
class GateCase:
    query: str
    needs_grounding: bool


@dataclass(frozen=True)
class ResearchCase:
    """`tool` None means the round should stop. `location` is checked
    after `_validated_location`: None means none should survive, a string
    must appear in it. `days` None means any (or omitted). `scratchpad`
    is what earlier rounds already did."""

    query: str
    tool: str | None
    location: str | None = None
    days: int | None = None
    history: tuple[dict, ...] = ()
    scratchpad: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChainCase:
    """One message through the compiled graph up to the card. `tool` None
    means no card. Each `summary` item must appear in the card's summary;
    a tuple item is a set of acceptable alternatives."""

    query: str
    agent: str
    tool: str | None = None
    summary: tuple = ()
    today: date = TODAY
    history: tuple[dict, ...] = ()


@dataclass(frozen=True)
class AnswerCase:
    """A chat answer from the `persona` named in `app.agents.state` over
    `entities`. Each `mentions` item must appear in the answer (a tuple is
    a set of alternatives), and every date it states must be in the
    context it was given."""

    query: str
    persona: str
    entities: tuple[EntityContext, ...] = ()
    mentions: tuple = ()


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
            "starting_at": None,
        },
    ),
    ActionCase(
        "remind me to clean the Furnace intake on the first Saturday of every month, I last did it on November 7",
        "create_schedule",
        {
            "entity_id": "h-furnace",
            "interval_type": "monthly",
            "monthly_weekday": 5,
            "monthly_week_index": 1,
            "starting_at": "2026-11-07",
        },
    ),
    ActionCase(
        "remind me to check the Furnace filter on the first of every month",
        "create_schedule",
        {"entity_id": "h-furnace", "interval_type": "monthly", "monthly_day": 1},
    ),
    ActionCase(
        "remind me on the fifteenth of each month to check the Mower oil",
        "create_schedule",
        {"entity_id": "q-mower", "interval_type": "monthly", "monthly_day": 15},
    ),
    ActionCase(
        "remind me to wash the Ranger every 2nd Tues of the month",
        "create_schedule",
        {
            "entity_id": "v-ranger",
            "interval_type": "monthly",
            "monthly_weekday": 1,
            "monthly_week_index": 2,
        },
    ),
    ActionCase(
        "replaced the Furnace filter yesterday",
        "create_log",
        {"entity_id": "h-furnace", "type": "service", "occurred_at": "2026-12-31"},
        today=NEW_YEARS_DAY,
    ),
    ActionCase(
        "called Mom yesterday to wish her happy birthday",
        "create_log",
        {"entity_id": "p-mom", "type": "call", "occurred_at": "2026-11-29"},
        today=END_OF_MONTH,
    ),
    ActionCase(
        "had the Ranger inspected on Monday",
        "create_log",
        {"entity_id": "v-ranger", "type": "inspection", "occurred_at": "2026-11-23"},
        today=SUNDAY,
    ),
    ActionCase(
        "remind me to stain the Deck tomorrow",
        "create_schedule",
        {"entity_id": "j-deck", "interval_type": "once", "planned_at": "2026-12-01"},
        today=END_OF_MONTH,
    ),
    ActionCase(
        "remind me to stain the Deck next Saturday",
        "create_schedule",
        {"entity_id": "j-deck", "interval_type": "once", "planned_at": "2026-12-05"},
        today=SUNDAY,
    ),
    ActionCase(
        "remind me to call Mom on January 2",
        "create_schedule",
        {"entity_id": "p-mom", "interval_type": "once", "planned_at": "2027-01-02"},
        today=NEW_YEARS_EVE,
    ),
    ActionCase(
        "remind me to clean the Furnace intake on the first Saturday of every month, I last did it on December 5",
        "create_schedule",
        {
            "entity_id": "h-furnace",
            "interval_type": "monthly",
            "monthly_weekday": 5,
            "monthly_week_index": 1,
            "starting_at": "2026-12-05",
        },
        today=NEW_YEARS_DAY,
    ),
    ActionCase("remind me to wash the Ranger on the 2nd and 4th Tuesday of every month", None),
    ActionCase("remind me to check the Furnace on the fifth Friday of every month", None),
    ActionCase("should I change the Ranger's oil soon?", None),
    ActionCase("how much did the Furnace repair cost last time?", None),
)

FUZZY = (
    FuzzyCase("when is my Ford due for service?", "v-ford"),
    FuzzyCase("is the furnace filter due yet?", "h-carrier"),
    FuzzyCase("what did grandma say when I called?", "p-jo"),
    FuzzyCase("what's the weather tomorrow?", None),
)

GATE = (
    GateCase("what's the warranty on our dishwasher?", True),
    GateCase("is the Furnace due for service?", True),
    GateCase("what should I get Mom for her birthday?", True),
    GateCase("how do I reset the garage door opener?", True),
    GateCase("how much have we spent on the Ranger this year?", True),
    GateCase("thanks, that's all!", False),
    GateCase("hi there", False),
    GateCase("what were we just talking about?", False),
    GateCase("what's the capital of Australia?", False),
    GateCase("how many ounces are in a cup?", False),
)

RESEARCH = (
    ResearchCase("what does the dishwasher manual say about error code E24?", "search_household_documents"),
    ResearchCase("how much was the last plumber invoice?", "search_household_documents"),
    ResearchCase("any news about Ford truck recalls this month?", "search_web"),
    ResearchCase("what's the latest firmware version for the Ecobee thermostat?", "search_web"),
    ResearchCase("what's the weather like right now in Macon?", "get_weather", location="Macon"),
    ResearchCase("how hot is it outside?", "get_weather"),
    ResearchCase("will it rain this weekend?", "get_weather_forecast"),
    ResearchCase(
        "what's the forecast for the next 3 days in Warner Robins?",
        "get_weather_forecast",
        location="Warner Robins",
        days=3,
    ),
    ResearchCase("find me two dry days this week to stain the Deck", "get_weather_forecast"),
    ResearchCase(
        "what about Warner Robins?",
        "get_weather_forecast",
        location="Warner Robins",
        history=(
            {"role": "user", "content": "will it rain tomorrow in Macon?"},
            {"role": "assistant", "content": "Macon has a 70% chance of rain tomorrow."},
        ),
    ),
    ResearchCase(
        "any news about Ford truck recalls this month?",
        None,
        scratchpad=(RESEARCH_SCRATCHPAD["web"].format(query="Ford truck recalls", count=5),),
    ),
    ResearchCase(
        "what does the dishwasher manual say about error code E24?",
        None,
        scratchpad=(RESEARCH_SCRATCHPAD["documents"].format(query="dishwasher error code E24", count=3),),
    ),
)

CHAIN = (
    ChainCase(
        "I just changed the oil in the Ranger",
        "action",
        "create_log",
        ("Log service:", "for Ranger, 2026-11-25"),
    ),
    ChainCase("changed the oil in the truck this morning", "action", "create_log", ("for Ranger, 2026-11-25",)),
    ChainCase("log that I called Mom yesterday", "action", "create_log", ("Log call:", "for Mom, 2026-11-24")),
    ChainCase(
        "remind me to replace the Furnace filter every 3 months",
        "action",
        "create_schedule",
        ("for Furnace, every 90 days",),
    ),
    ChainCase(
        "remind me to stain the Deck next Saturday",
        "action",
        "create_schedule",
        ("for Deck, on ", ("2026-11-28", "2026-12-05")),
    ),
    ChainCase(
        "remind me to sharpen the Mower blade on the first Saturday of every month",
        "action",
        "create_schedule",
        ("for Mower, monthly on the first Saturday",),
    ),
    ChainCase(
        "remind me to call Mom on January 2",
        "action",
        "create_schedule",
        ("for Mom, on 2027-01-02",),
        today=NEW_YEARS_EVE,
    ),
    ChainCase(
        "replaced the Furnace filter yesterday",
        "action",
        "create_log",
        ("for Furnace, 2026-12-31",),
        today=NEW_YEARS_DAY,
    ),
    ChainCase("log that I mowed the lawn today", "action"),
    ChainCase("should I change the Ranger's oil soon?", "vehicle"),
    ChainCase("when did we last service the Furnace?", "maintenance"),
)

_RANGER_RECORDS = EntityContext(
    id="v-ranger",
    domain="vehicle",
    name="Ranger",
    tags=["truck"],
    specs={"mileage": "61,500"},
    attrs={"Make": "Ford", "Model": "Ranger", "Year": "2021"},
    logs=[
        {"occurred_at": "2026-09-14", "type": "service", "title": "Oil change", "description": None},
        {"occurred_at": "2026-06-02", "type": "repair", "title": "Replaced wiper blades", "description": None},
    ],
    schedules=[{"title": "Oil change", "next_due_at": "2027-03-14"}],
)
_FURNACE_RECORDS = EntityContext(
    id="h-furnace",
    domain="home",
    name="Furnace",
    tags=["hvac"],
    specs={},
    attrs=None,
    logs=[{"occurred_at": "2026-06-01", "type": "service", "title": "Replaced filter", "description": None}],
    schedules=[{"title": "Replace filter", "next_due_at": "2026-12-01"}],
)

ANSWERS = (
    AnswerCase(
        "when did I last change the Ranger's oil?",
        "VEHICLE_PERSONA",
        (_RANGER_RECORDS,),
        ("Ranger", ("September 14", "Sept 14", "Sep 14", "2026-09-14", "9/14")),
    ),
    AnswerCase("how many miles are on the Ranger?", "VEHICLE_PERSONA", (_RANGER_RECORDS,), (("61,500", "61500"),)),
    AnswerCase(
        "when is the Furnace filter due?",
        "MAINTENANCE_PERSONA",
        (_FURNACE_RECORDS,),
        ("Furnace", ("December 1", "Dec 1", "2026-12-01", "12/1")),
    ),
    AnswerCase("when is the Mower due for a blade sharpening?", "MAINTENANCE_PERSONA"),
    AnswerCase("when did I last change the Ranger's oil?", "VEHICLE_PERSONA"),
)
