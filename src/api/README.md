# API Layer Guidelines & Coding Standards

> **Package**: `src/api/`  
> **Audience**: AI Agents & Engineers maintaining or extending the Pet-Plant client-facing HTTP API.  
> **Pattern**: **MVCS** (Model-View/Controller-Service-Repository) with isolated `constant/` and `exception/` packages.

---

## 1. Directory Structure & Layer Responsibilities

```
src/api/
├── __init__.py                      # Package overview and architectural boundaries
├── constant/                        # Centralized API constants (SSOT)
│   ├── __init__.py
│   ├── error_code.py                # Machine-readable error codes (e.g. PLANT_NOT_FOUND)
│   ├── status_code.py               # HTTP status code aliases (e.g. StatusCode.NOT_FOUND)
│   └── message.py                   # Standard human-readable messages
├── exception/                       # Custom exception hierarchy (isolated from model/)
│   ├── __init__.py
│   └── api_exception.py             # ApiException base + concrete domain exceptions
├── model/                           # Pydantic request/response schemas
│   ├── __init__.py
│   ├── base_response.py             # Generic BaseResponse[T] envelope + ErrorDetail
│   └── <domain>_state.py            # Domain-specific response and request models
├── repository/                      # Data access layer (cross-context orchestration)
│   ├── __init__.py
│   └── <domain>_repository.py       # Thin delegator to published bounded context interfaces
├── service/                         # Business logic & aggregation layer
│   ├── __init__.py
│   └── <domain>_service.py          # State assembly, business rules, fallback handling
└── controller/                      # HTTP presentation & routing layer
    ├── __init__.py
    └── <domain>_controller.py       # FastAPI router, authentication, OpenAPI/Swagger docs
```

---

## 2. Mandatory Architectural Invariants

### 2.1 Separation of Concerns
1. **Controller**: Handles HTTP concerns only (extracting headers, parameters, auth tokens). Calls `Service`. Never puts business rules or DB queries here.
2. **Service**: Pure business logic and domain orchestration. Assembles models, applies conditional logic, raises `ApiException` subclasses on failure. Never imports `Request` or HTTP status codes directly.
3. **Repository**: Pure data access. Delegates strictly to published context interfaces (`<context>.interface`). Contains zero business logic.
4. **Model**: Defines data transfer shapes using Pydantic v2. Never contains DB logic.
5. **Constant**: Single source of truth. Never hardcode error strings, codes, or numeric status codes anywhere in the codebase.
6. **Exception**: Custom exceptions inheriting from `ApiException`. Never raise raw FastAPI `HTTPException` inside services or repositories.

### 2.2 Do NOT Reinvent the Wheel (Context Interface Reuse)
- The Pet-Plant server is a modular monolith with strict bounded contexts (`registry`, `knowledge`, `assessment`, `advice`, `companion`, `action`, `core`).
- **NEVER** import SQLAlchemy ORM models from another context into `src/api/` or run raw cross-schema queries.
- **ALWAYS** check for and reuse existing functions in `<context>.interface.py`.
- If a query is missing in a bounded context:
  1. Add a clean, typed helper function to that context's `interface.py` (e.g., `assessment.interface.count_observations()`).
  2. Call that published function from `src/api/repository/`.

---

## 3. Layer Coding Standards

### 3.1 Constants (`src/api/constant/`)
- `error_code.py`: Uppercase string constants for `BaseResponse.error.code` (e.g., `ErrorCode.PLANT_NOT_FOUND = "PLANT_NOT_FOUND"`).
- `status_code.py`: Numeric status aliases (e.g., `StatusCode.NOT_FOUND = 404`).
- `message.py`: User-facing message strings (e.g., `Message.PLANT_NOT_FOUND = "..."`).

### 3.2 Exceptions (`src/api/exception/`)
- Every exception inherits from `ApiException`:
  ```python
  class ApiException(Exception):
      def __init__(
          self,
          status_code: int = StatusCode.INTERNAL_SERVER_ERROR,
          error_code: str = ErrorCode.INTERNAL_SERVER_ERROR,
          message: str = Message.INTERNAL_ERROR,
          details: dict[str, Any] | None = None,
      ) -> None:
          ...
  ```
- Concrete domain exceptions provide pre-configured defaults:
  ```python
  class PlantNotFoundError(ApiException):
      def __init__(self, plant_id: Any) -> None:
          super().__init__(
              status_code=StatusCode.NOT_FOUND,
              error_code=ErrorCode.PLANT_NOT_FOUND,
              message=Message.PLANT_NOT_FOUND,
              details={"plant_id": str(plant_id)},
          )
  ```
- Handled automatically by the global exception handler in `src/main_web.py`.

### 3.3 Models & Envelope (`src/api/model/`)
- All responses MUST be wrapped in `BaseResponse[T]`:
  ```json
  // Success
  {
    "success": true,
    "data": { ... },
    "message": null,
    "error": null
  }

  // Error
  {
    "success": false,
    "data": null,
    "message": "Human readable description",
    "error": {
      "code": "ERROR_CODE",
      "details": { ... }
    }
  }
  ```
- Use Python 3.12+ generic syntax: `class BaseResponse[T](BaseModel):`
- Use `BaseResponse.ok(data)` for success, `BaseResponse.fail(message, error_code, details)` for errors.
- Every schema field MUST include rich OpenAPI documentation via `Field(...)`:
  ```python
  class ExampleData(BaseModel):
      name: str = Field(..., description="Entity display name", examples=["Monty"])
      level: int = Field(default=1, ge=1, description="Character level", examples=[2])
  ```

### 3.4 Repositories (`src/api/repository/`)
- Methods accept `(session: Session, ...)` and return published schema types or `None`:
  ```python
  class CompanionRepository:
      def get_plant(self, session: Session, plant_id: uuid.UUID) -> PlantRead | None:
          return registry_get_plant(session, plant_id)
  ```

### 3.5 Services (`src/api/service/`)
- Services instantiate their default repository via dependency injection:
  ```python
  class CompanionService:
      def __init__(self, repository: CompanionRepository | None = None) -> None:
          self.repo = repository or CompanionRepository()
  ```
- Provide factory dependencies for FastAPI:
  ```python
  def get_companion_service() -> CompanionService:
      return CompanionService()
  ```

### 3.6 Controllers & Swagger / OpenAPI (`src/api/controller/`)
- Prefix routes with domain (`/companion`, `/plant`, etc.).
- Every endpoint MUST define:
  1. `summary`: Concise title for Swagger UI.
  2. `description`: Detailed Markdown explaining behavior, delivery modes, and rules.
  3. `response_model`: `BaseResponse[<Schema>]`
  4. `responses`: Explicit dictionary documenting status codes (`200`, `400`, `401`, `403`, `404`, `500`).
  5. `tags`: Matches the OpenAPI tag registered in `main_web.py`.

---

## 4. Testing & Verification Checklist

When adding or editing any code in `src/api/`, you must verify:

1. **Unit & Integration Tests**:
   - Add tests to `tests/api/test_<feature>.py`.
   - Verify unauthenticated requests return 401.
   - Verify invalid tokens return 401.
   - Verify invalid/missing parameters return 400.
   - Verify forbidden access returns 403.
   - Verify missing records return 404.
   - Verify happy path returns 200 with `BaseResponse.ok`.
   - Verify `/openapi.json` registers endpoint and tags correctly.
2. **Quality Gates**:
   - `uv run pytest` -> 100% passing tests (zero regressions across all suites).
   - `uv run ruff check` -> 0 lint or formatting errors (max line length 100).
   - `uv run mypy src` -> 0 static typing issues across all source files.

---

## 5. Companion API Scenarios Testing Guide

### 5.1 Target Audience & When to Use
- **👤 For Humans (Engineers & Reviewers)**:
  - Run `scripts/seed_companion_scenarios.py` to seed real states into your local database.
  - Open **Postman Desktop** and run the collection runner interactively to visually inspect response schemas and status badges.
  - Run the `curl` commands to test hardware edge token vs mobile user JWT simulation.
  - Run `--clean` afterwards to reset your database.
- **🤖 For AI Agents & Automated CI**:
  - Run `uv run pytest tests/api/test_companion_state.py` for headless unit/integration assertions.
  - Run `uv run ruff check` and `uv run mypy src` before committing code to ensure zero lint or typing regressions.
  - Use `scripts/seed_companion_scenarios.py` + `--clean` only in end-to-end integration environments with a live database.

### 5.2 Seeding & Running Tests
1. **Seed test data** (creates test user and plants for all 3 scenarios):
   ```bash
   uv run python scripts/seed_companion_scenarios.py
   ```
2. **Execute tests**:
   - **Scenario 1 (Steady / `NO_ACTION`)**:
     ```bash
     curl -s -H "Authorization: Bearer ppd_steady_device_token_secret_123" http://localhost:8000/companion/devices/me/state | jq .
     ```
   - **Scenario 2 (Action Needed / `CARE_ADVICE_REQUIRED`)**:
     ```bash
     curl -s -H "Authorization: Bearer ppd_care_device_token_secret_456" http://localhost:8000/companion/devices/me/state | jq .
     ```
   - **Scenario 3 (Photo Retake / `REQUEST_MORE_INFORMATION`)**:
     ```bash
     curl -s -H "Authorization: Bearer ppd_retake_device_token_secret_789" http://localhost:8000/companion/devices/me/state | jq .
     ```
3. **Run Postman suite**:
   - Import `tests/postman/Pet_Plant_Companion_State_API.postman_collection.json`.
   - Run Section 3 to validate all scenarios automatically.
4. **Clean up test data (Zero leftover DB garbage)**:
   ```bash
   uv run python scripts/seed_companion_scenarios.py --clean
   ```

