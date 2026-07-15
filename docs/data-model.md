# ARIA — Data Model (v0 Draft)

Status: **proposal, not yet implemented**. This covers the MongoDB-side structured
data only (the CRUD layer that the PRD says must work with zero AI/vector-store
dependency). ChromaDB chunk/embedding shape is sketched at the end for how it
cross-references this model, but isn't the focus here.

Decisions locked in for this pass:
- **Multi-user/household from day one** — every top-level collection carries `household_id`.
- **Documents are many-to-many with entities** — a receipt can cover multiple items.
- **Logs are a single unified collection** across all four domains, discriminated by `type`.
- **`specs`/`metrics` stay free-form for v0** — no fields promoted to typed/indexed columns yet; revisit once real usage shows what's worth it.
- **Archival cascades** — archiving an entity hides its logs/documents from default views too; no independent `archived_at` on logs/documents, visibility derives from the parent entity's status.
- **Scheduled/upcoming maintenance gets its own `schedules` collection** — logs stay purely retrospective ("what happened"); `schedules` is the forward-looking counterpart ("what's due").

---

## 1. Collections overview

| Collection  | Purpose |
|---|---|
| `households` | The tenant boundary. One per household; everything else scopes to it. |
| `users` | Household members. |
| `entities` | The "things" being tracked — polymorphic across the 4 domains. |
| `logs` | Everything that *happened* to an entity — service, repairs, inspections, expenses, notes, project milestones. |
| `schedules` | Everything that's *due* — recurring maintenance rules and their next-due state. |
| `documents` | Uploaded files (manuals, receipts, invoices, photos) and their processing state. |

Four collections, not four-per-domain. Domain-specific shape lives in typed
sub-documents/discriminated unions, not in separate collections — so a "what's
due this month across the whole house" or "everything that happened in June"
query never has to fan out across N collections.

---

## 2. `households` / `users`

```python
class Household(BaseModel):
    id: ObjectId
    name: str
    created_at: datetime
    updated_at: datetime

class User(BaseModel):
    id: ObjectId
    household_id: ObjectId
    name: str
    email: EmailStr
    role: Literal["owner", "member"] = "member"
    created_at: datetime
```

Nothing fancy — this exists so `household_id` has something to point at, and so
auth/permissions have a home later without a schema migration.

---

## 3. `entities` (polymorphic, discriminated by `domain`)

### Shared envelope

Every entity, regardless of domain, has:

```python
class EntityBase(BaseModel):
    id: ObjectId
    household_id: ObjectId
    domain: Literal["home", "vehicle", "equipment", "project"]
    name: str                      # display name, e.g. "2021 Ford Ranger"
    status: str                    # domain-specific enum, see below
    tags: list[str] = []
    location: str | None = None    # room/area/garage bay — free text is fine for v0
    specs: dict[str, str] = {}     # escape hatch for long-tail fields not worth
                                    # formally modeling yet (sparkplug gap, belt
                                    # spec, cabin filter part #, ...)
    created_by: ObjectId
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None   # soft delete
    attributes: HomeAttrs | VehicleAttrs | EquipmentAttrs | ProjectAttrs
        # = Field(discriminator="domain")  — actual discriminator wiring TBD in code
```

`specs` is the deliberate pressure-release valve: instead of trying to
pre-model every field the PRD's examples imply (sparkplug gap, deck grease
interval, brake rotor spec, cabin filter schedule...), those go in a flat
string→string map. If a field turns out to matter enough to filter/sort/chart
on, we promote it out of `specs` into a typed field later — that's a additive
migration, not a breaking one.

### `HomeAttrs` (domain = "home")

The PRD's Home/Facility examples are actually heterogeneous — rooms, systems,
appliances, structural elements — so this domain gets its own sub-discriminator:

```python
class HomeAttrs(BaseModel):
    domain: Literal["home"] = "home"
    entity_type: Literal["room", "system", "appliance", "structure"]

    # common, all optional since relevance varies by entity_type
    make: str | None = None
    model: str | None = None
    serial_number: str | None = None
    paint_brand: str | None = None       # e.g. "Sherwin Williams"
    paint_code: str | None = None        # e.g. "SW-7005"
    install_date: date | None = None
    warranty_expires_at: date | None = None
```

`status` for home entities: `"active" | "needs_attention" | "archived"`.

### `VehicleAttrs` (domain = "vehicle")

```python
class VehicleAttrs(BaseModel):
    domain: Literal["vehicle"] = "vehicle"
    make: str
    model: str
    year: int
    vin: str | None = None
    license_plate: str | None = None
    current_mileage: int | None = None
    purchase_date: date | None = None
```

`status`: `"active" | "in_service" | "sold" | "archived"`.

### `EquipmentAttrs` (domain = "equipment")

```python
class EquipmentAttrs(BaseModel):
    domain: Literal["equipment"] = "equipment"
    make: str | None = None
    model: str | None = None
    serial_number: str | None = None
    purchase_date: date | None = None
```

`status`: `"active" | "in_service" | "retired"`.

### `ProjectAttrs` (domain = "project")

Projects are the odd one out — not a possession, but a body of work that
*touches* possessions (a shower remodel affects the "Primary Bathroom" home
entity). That relationship is worth modeling explicitly:

```python
class ProjectAttrs(BaseModel):
    domain: Literal["project"] = "project"
    related_entity_ids: list[ObjectId] = []   # e.g. the bathroom this remodel touches
    start_date: date | None = None
    target_end_date: date | None = None
    completed_date: date | None = None
    budget_estimate: float | None = None
```

`status`: `"planning" | "in_progress" | "on_hold" | "completed"`.

Actual cost isn't stored here — it's a rollup (`sum(logs where entity_id ==
this project and type == "expense")`), computed at read time so it can't drift
from the underlying log entries.

---

## 4. `logs` (unified, discriminated by `type`)

```python
class LogEntry(BaseModel):
    id: ObjectId
    household_id: ObjectId
    entity_id: ObjectId
    domain: Literal["home", "vehicle", "equipment", "project"]  # denormalized from
                                                                  # entity, for filtering
                                                                  # without a join
    type: Literal["service", "repair", "inspection", "expense", "note", "milestone"]
    occurred_at: date          # when it happened — distinct from created_at
    title: str
    description: str | None = None
    cost: float | None = None                 # promoted to top-level: filtered/
                                                # summed constantly across types
    metrics: dict[str, str] = {}               # type-specific: odometer_reading,
                                                # hours_used, parts_used, result, ...
    document_ids: list[ObjectId] = []          # receipts/photos for this entry
    schedule_id: ObjectId | None = None        # set when this log *satisfies* a
                                                # recurring schedule (see §6) —
                                                # explicit link, not title-matching
    created_by: ObjectId
    created_at: datetime
    updated_at: datetime
```

One collection, one timeline. "Everything that happened in the last 30 days"
is `find({household_id, occurred_at: {$gte: ...}})` with no fan-out. A
vehicle's oil-change history is the same query shape as a project's expense
log — `type` and `metrics` carry the domain-specific meaning, not the
collection choice.

`metrics` conventions by type (documented, not enforced at the schema level
for v0):
- `service`/`repair`: `odometer_reading` or `hours_used`, `parts_used`
- `inspection`: `result` (pass/fail/flagged), `findings`
- `expense`: `category`
- `milestone` (projects): `phase`, `percent_complete`
- `note`: none — just `description`

---

## 5. `schedules`

Logs are purely retrospective — they only exist once something happened. This
is the forward-looking counterpart: a recurring rule tied to an entity, plus
enough state to compute what's currently due.

```python
class Schedule(BaseModel):
    id: ObjectId
    household_id: ObjectId
    entity_id: ObjectId
    domain: Literal["home", "vehicle", "equipment", "project"]  # denormalized,
                                                                  # same reason as on LogEntry
    title: str                                 # e.g. "Oil change", "HVAC filter replacement"
    active: bool = True

    interval_type: Literal["time", "usage"]
    interval_days: int | None = None           # interval_type == "time"
    usage_metric: str | None = None            # interval_type == "usage", e.g.
                                                # "odometer_reading" — must match a
                                                # key used in matching logs' metrics
    interval_usage_amount: float | None = None # e.g. 5000 (miles)

    last_completed_log_id: ObjectId | None = None
    last_completed_at: date | None = None
    last_completed_usage_value: float | None = None

    next_due_at: date | None = None            # cached/derived, recomputed on write
    next_due_usage_value: float | None = None  # cached/derived, recomputed on write

    created_by: ObjectId
    created_at: datetime
    updated_at: datetime
```

How it stays in sync: when a `LogEntry` is created with `schedule_id` set, that
write updates the schedule's `last_completed_*` fields from the log's
`occurred_at`/`metrics[usage_metric]`, then recomputes `next_due_at` /
`next_due_usage_value` from `interval_days` / `interval_usage_amount`. Nothing
scans historical logs to figure out what's due — the schedule carries its own
current state, updated incrementally. Logging a completion without linking a
schedule is still fully valid (not every oil change needs to be tied to a
tracked interval) — `schedule_id` is opt-in.

`next_due_at`/`next_due_usage_value` being cached rather than computed
on-the-fly is a deliberate tradeoff: cheap reads for "what's due this week"
dashboard queries, at the cost of needing to keep them in sync on every
completing write (and on schedule edits, e.g. changing the interval).

---

## 6. `documents`

```python
class Document(BaseModel):
    id: ObjectId
    household_id: ObjectId
    entity_ids: list[ObjectId] = []    # many-to-many — one receipt can cover
                                        # multiple items
    log_ids: list[ObjectId] = []       # optional: this receipt is *for* this
                                        # specific service log entry
    document_type: Literal["manual", "receipt", "invoice", "photo", "diagram", "other"]
    original_filename: str
    storage_path: str                  # where the actual file bytes live
    mime_type: str
    file_size_bytes: int
    page_count: int | None = None

    processing_status: Literal["pending", "ocr_complete", "chunked", "embedded", "failed"]
    processing_error: str | None = None

    uploaded_by: ObjectId
    uploaded_at: datetime
```

This is the collection that has to keep working if Chroma/the embedding
pipeline is down — list, view, download, and manually link documents are pure
Mongo CRUD. `processing_status` just tracks how far the async pipeline (OCR →
chunk → embed) has gotten for *that* document; a `"failed"` or `"pending"`
document is still a fully usable document, it's just not semantically
searchable yet.

---

## 7. Relationship diagram (textual)

```
household
 ├─ users (members)
 ├─ entities (domain: home | vehicle | equipment | project)
 │    └─ project entities also hold related_entity_ids → other entities
 ├─ logs
 │    ├─ entity_id → entities
 │    ├─ document_ids → documents
 │    └─ schedule_id → schedules  (optional — this log satisfies a schedule)
 ├─ schedules
 │    ├─ entity_id → entities
 │    └─ last_completed_log_id → logs
 └─ documents
      ├─ entity_ids → entities  (many-to-many)
      └─ log_ids → logs         (many-to-many)
```

Archival is a derived view, not a stored flag on children: hiding an
archived entity's logs/documents from default lists means the query joins
against `entities.archived_at`, rather than every child collection carrying
its own `archived_at`.

---

## 8. How this meets Chroma at the boundary (for later phases)

Not implementing yet, but so the seam is clear: Chroma stores chunk-level
embeddings with metadata `{mongo_document_id, page_number, section_header,
chunk_index}`. Mongo never stores chunk text or vectors. When the LLM cites a
chunk, the backend resolves `mongo_document_id` back to a `documents` record
to render the filename/link in the citation. This is what "strict decoupling"
buys us concretely: delete/rebuild the whole Chroma collection and the
`documents` records — and every CRUD screen — are untouched.

---

## 9. Decisions log

Resolved this pass:

- **Indexing** — no known hot lookup fields beyond the baseline
  (`entities(household_id, domain, status)`, `logs(household_id, entity_id,
  occurred_at)`, `schedules(household_id, entity_id, active, next_due_at)`,
  `documents(household_id, entity_ids)`). Nothing promoted out of `specs` yet.
- **`specs`/`metrics` free-form maps** — confirmed fine for v0. Revisit once
  real data exists to see which keys are worth promoting to typed/indexed
  fields.
- **Soft delete** — `archived_at` stays on `entities` only. Logs/documents
  don't get their own; visibility cascades from the parent entity via a join
  at query time (see §7).
- **Scheduled/upcoming maintenance** — added the `schedules` collection (§5)
  rather than deferring or going fully derived-from-logs, so "what's due"
  scales as a direct query rather than a scan.

Still open, deliberately deferred to implementation time rather than blocking
this design pass:

- Exact Pydantic discriminated-union wiring for `attributes` (the commented-out
  `Field(discriminator=...)` in §3) — a code-level detail, not a modeling one.
- Whether `next_due_at`/`next_due_usage_value` recomputation happens in the
  same request that writes the completing log, or via a Celery task — a
  transactional-boundary decision that belongs with backend scaffolding, not
  the schema.
