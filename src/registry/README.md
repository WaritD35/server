# 1. Plant Registry

**Package:** `src/registry/`
**Role:** Identity & physical anchoring — the persistent record of which physical
plant is which.
**Owner:** _TBD_

## Responsibilities

- Identify and persist the physical existence of a plant (who, what, where).
- Manage plant profiles: species assignment (resolved once at registration,
  confirmed by the owner, then cached — never re-run) and owner mapping.
- Calibrate and manage camera placement and anchor identity so the system keeps
  tracking the same physical plant over time.

## Data it owns

- PostgreSQL schema `registry`: one table, `plant`.
- MinIO: none.

### `registry.plant`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | **The plant identity** every other context keys its rows on |
| `owner_id` | uuid | `auth.users.id` (`core`). Not a DB foreign key — checked via `core.users` |
| `name` | text | The owner's name for the plant |
| `species_code` | text, nullable | `knowledge.species.species_code`. Not a DB foreign key — checked via `knowledge.get_species`. `NULL` until resolved |
| `species_confirmed_at` | timestamptz, nullable | Set when the owner confirms the species; cleared when the species changes |
| `device_id` | text, nullable | `auth.devices.physical_id` (`core`) of the planter that photographs it. Must be a device **paired to the plant's owner** — checked via `core.devices` |
| `note` | text, nullable | |
| `created_at` / `updated_at` | timestamptz | |
| `archived_at` | timestamptz, nullable | Set instead of deleting — see below |

- **Never deleted.** `DELETE` archives (`archived_at`): capture batches, probe runs,
  advice and companion state elsewhere all point at `plant.id`.
- **One live plant per device**, enforced by a partial unique index:

  ```sql
  CREATE UNIQUE INDEX uq_plant_live_device
      ON registry.plant (device_id)
      WHERE device_id IS NOT NULL AND archived_at IS NULL;
  ```

  Archiving frees the device; the archived row keeps its `device_id` as a record
  of where its frames came from.
- **Devices are paired in `core`, bound here.** A planter is attached to an
  account through `core`'s `/devices` pairing flow (see the `core` README); the
  owner then binds it to one of their plants with `PATCH /registry/plants/{id}`
  `{"device_id": …}`. Revoking the device in `core` stops its uploads but keeps
  the binding, so pairing it again resumes where it left off.
- **A device passed on to someone else.** Once revoked and paired to a new
  owner, the old owner's binding is *stale*: it no longer resolves (device →
  plant lookups check the device's current owner), and it is cleared when the
  new owner binds the device to their own plant.

## Inputs

- Owner registration requests (via HTTP).
- The species, set by the owner / an admin. (Automatic species identification
  from a frame is not planned; there is no `mlops` component for it.)

## Outputs

- **Plant identity** → `capture`, `assessment`, and any context that needs the
  owner, resolved species or device of a plant.

## Driven by

- `main_web` (owner and device API requests).

## HTTP API (`/registry`, mounted by `main_web`)

Every endpoint requires an authenticated user (`core.users.CurrentUser`). An
owner sees and changes only their own plants; a superuser sees all of them.
Another owner's plant answers **404**, not 403, so ids cannot be probed.

| Method & path | Purpose |
|---|---|
| `POST /registry/plants` | register a plant for the caller (a superuser may pass `owner_id`). 422 for an unknown `species_code` / `owner_id` or a `device_id` not paired to the owner, 409 if `device_id` is bound to another live plant |
| `GET /registry/plants?owner_id=&species_code=&include_archived=` | list plants, oldest first; `owner_id` is ignored for non-admins |
| `GET /registry/plants/{id}` | one plant (live or archived) |
| `PATCH /registry/plants/{id}` | partial update; `null` clears a nullable field (`device_id: null` unbinds). `species_confirmed: true/false` sets / clears the confirmation. 409 on an archived plant |
| `DELETE /registry/plants/{id}` | archive (204). 409 if already archived |
| `GET /registry/devices/{device_id}/plant` | owner: the live plant bound to a device |
| `GET /registry/devices/me/plant` | **device** (`CurrentDevice`): the plant I photograph |

## Published interface (in-process)

```python
from registry import (
    list_plant_ids,       # (session, *, owner_id=, species_code=, include_archived=False) -> list[UUID]
    get_plant,            # (session, plant_id) -> PlantRead | None       (archived ones too)
    get_plants,           # (session, [plant_id, ...]) -> {plant_id: PlantRead}
    get_plant_by_device,  # (session, physical_id) -> PlantRead | None    (live, current owner's only)
    is_plant_owned_by,    # (session, plant_id, user_id) -> bool
    PlantRead,
)

plant = get_plant(session, plant_id)
plant.model_dump(mode="json")  # -> {id, owner_id, name, species_code, species_confirmed_at,
                               #     device_id, note, created_at, updated_at, archived_at}
```

- `capture` guards its upload route with `core.devices.CurrentDevice` and resolves
  the frame's plant with `get_plant_by_device(session, device.physical_id)`.
- `assessment` reads `species_code` from `get_plant` to fetch the probe bundle
  from `knowledge`.
- Batch jobs enumerate targets with `list_plant_ids(species_code=...)`.
- Contexts with owner-facing endpoints check access with `is_plant_owned_by`.

## Internal design

```
src/registry/
├── api.py         # /registry router — argument parsing, access checks, status codes
├── interface.py   # the in-process interface above
├── service.py     # DB reads/writes; cross-context checks through core / knowledge interfaces
├── schemas.py     # PlantCreate / PlantUpdate / PlantRead
├── models.py      # Plant
└── db.py          # REGISTRY_SCHEMA, Base (own MetaData), utcnow(), init_models()
```

Until per-schema Alembic migrations land, `registry.db.init_models()` creates the
`registry` schema and its table directly (called from `main_web` on startup).
Other contexts use only the published interface; no access to the `registry`
schema from outside.
