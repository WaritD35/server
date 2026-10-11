# Companion Device State API Plan

> Expose `GET /companion/devices/me/state` via MVCS architecture under `src/api` with dual auth, OpenAPI/Swagger docs, and clean bounded-context integration.

**Status**: Completed  
**Created**: 2026-10-10  
**Completed**: 2026-10-10  
**Owner**: Antigravity  

## Goal
Implement the `GET /companion/devices/me/state` endpoint under a new `src/api/` MVCS package (`controller`, `service`, `repository`, `model`, `exception`, `constant`). The endpoint aggregates data from 6 backend contexts (`registry`, `knowledge`, `assessment`, `advice`, `companion`, `action`) while strictly adhering to bounded context interface reuse, standard `BaseResponse` envelopment, full OpenAPI/Swagger documentation, and dual-mode authentication (Device Token + User JWT).

## Context
- The client UI requires an aggregated endpoint representing the plant's live state, character voice, and care status across 3 delivery modes: `NO_ACTION` (steady), `CARE_ADVICE_REQUIRED` (action needed), and `REQUEST_MORE_INFORMATION` (retake photo).
- The codebase is structured as a modular monolith with strict bounded-context isolation. Cross-context queries must go through published interfaces (`<context>.interface`).
- Prior design document: [companion_state_api_design.md](file:///Users/tinnapatplangsri/.gemini/antigravity-ide/brain/ece1cf54-c436-40ab-99bc-df76df50d9f6/companion_state_api_design.md).
- Promoted API contract: [companion-device-state.md](file:///Users/tinnapatplangsri/Documents/UTS%20semester%203/Industry%20project/codebase/server/docs/plans/contracts/companion-device-state.md).

## Constraints
- **Framework**: FastAPI with Pydantic v2 and SQLAlchemy 2.0.
- **Folder separation**: `src/api/exception/` must be separate from `src/api/model/`.
- **Centralized constants**: `src/api/constant/` must hold `error_code.py`, `status_code.py`, and `message.py`.
- **No Reinvention**: Reuse existing context interfaces. Enhance `assessment.interface` with `count_observations` and add `action.interface.get_last_watered_at`.
- **Swagger / OpenAPI**: Complete route summary, description, tags, schema descriptions via `Field()`, and HTTP status code documentation (`200`, `400`, `401`, `403`, `404`, `500`).
- **Code Standards**: Type check with `mypy` and lint with `ruff`.

---

## Tasks

| # | Task | Agent | Priority | Status | Dependencies |
|---|------|-------|:--------:|:------:|:------------:|
| 1 | **Context Enhancements**: Add `level` and `xp_ratio` to `registry.models.Plant` and `schemas.PlantRead`. Add `count_observations()` in `assessment.interface`. Create `src/action/interface.py` with `get_last_watered_at()`. | db / backend | 1 | DONE | — |
| 2 | **Constants Layer**: Create `src/api/constant/` (`error_code.py`, `status_code.py`, `message.py`). | backend | 2 | DONE | — |
| 3 | **Exception Layer**: Create `src/api/exception/api_exception.py` with `ApiException`, `PlantNotFoundError`, `DeviceNotBoundError`, `UnauthorizedError`, `PlantIdRequiredError`, `ForbiddenError`. | backend | 2 | DONE | 2 |
| 4 | **Model Layer**: Create `src/api/model/` (`base_response.py` with generic `BaseResponse[T]`, `companion_state.py` with `CompanionStateData`, `CarePlanResponse`, `CarePlanActionResponse` and Swagger docs annotations). | backend | 2 | DONE | — |
| 5 | **Repository Layer**: Create `src/api/repository/companion_repository.py` delegating to context interfaces (`registry`, `knowledge`, `assessment`, `advice`, `companion`, `action`). | backend | 3 | DONE | 1 |
| 6 | **Service Layer**: Create `src/api/service/companion_service.py` aggregating repository data into `CompanionStateData`, resolving species name, observation count, decision logic, and care plan actions. | backend | 3 | DONE | 3, 4, 5 |
| 7 | **Controller & Swagger**: Create `src/api/controller/companion_controller.py` with `APIRouter(prefix="/companion", tags=["Companion"])`, dual-mode auth dependency (Device Bearer + User JWT), OpenAPI documentation, and response codes. | backend | 4 | DONE | 6 |
| 8 | **App Registration**: Register `companion_controller.router` and `ApiException` global handler in `src/main_web.py`. Add `"api"` to `known-first-party` in `pyproject.toml`. | backend | 4 | DONE | 7 |
| 9 | **Test Suite & Verification**: Create comprehensive tests in `tests/api/test_companion_state.py` covering device auth, user auth, unauthenticated 401, missing plant_id 400, unowned plant 403, missing plant 404, `CARE_ADVICE_REQUIRED` and `NO_ACTION` payloads, and OpenAPI schema output. Run `pytest`, `ruff`, and `mypy`. | qa | 5 | DONE | 8 |

---

## Done When
- [x] `GET /companion/devices/me/state` returns HTTP 200 with `BaseResponse[CompanionStateData]` for both device tokens and user JWTs.
- [x] Error responses return structured `BaseResponse.fail()` envelopes for 400, 401, 403, 404, 500 with proper error codes.
- [x] `docs/` or Swagger UI (`/docs`) exposes the endpoint with complete markdown description, models, and response status definitions.
- [x] `pyproject.toml` recognizes `api` module without ruff/mypy import ordering errors.
- [x] All new tests pass, and existing 187 tests remain 100% green (now 208 passing tests total).
- [x] Static type check `mypy` and linter `ruff check` pass with zero errors.

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-10-10 | Single endpoint with dual auth | Allows both the physical edge device and the mobile/web owner app to fetch live companion state without duplicate routes. |
| 2026-10-10 | Separate `exception/` and `constant/` packages | Strict separation of concerns per user architectural feedback. Keeps schemas in `model/` clean and avoids circular dependencies. |
| 2026-10-10 | Reuse existing bounded context interfaces | Preserves architectural boundaries. Prevents direct cross-schema DB foreign keys or queries. |
| 2026-10-10 | Add `level` and `xp_ratio` directly to `registry.plant` | Resolves gamification state directly from persistent plant identity instead of arbitrary hardcoding or secondary tables. |

---

## Progress Notes

- [2026-10-10] Workflow `plan.md` executed: requirements gathered, technical feasibility analyzed, API contract created, task decomposition completed, and plan initialized.
- [2026-10-10] Implemented Tasks 1 through 9 under `/ultrawork`.
- [2026-10-10] All 208 tests passed. Mypy and ruff checks passed with zero errors. Status set to Completed.
