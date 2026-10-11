# Companion Device State API Contract

> Author: Antigravity PM/Backend Planner | Session: 20261010-companion-state | Date: 2026-10-10

## Base URL
`/companion`

## Authentication
Dual authentication supported on the same endpoint:
- **Device Caller**: Bearer token starting with `ppd_` in `Authorization: Bearer <token>`. Resolved against `auth.devices`. The endpoint resolves the associated plant from `registry.plant.device_id`.
- **User Caller (Web UI)**: User JWT in `Authorization: Bearer <jwt>`. Requires `plant_id` query parameter; caller must be the verified owner (`registry.plant.owner_id == user.id`).
- **Unauthenticated / Invalid**: 401 Unauthorized (`ErrorCode: UNAUTHORIZED`).

---

## Endpoints

### GET /companion/devices/me/state
- **Description**: Retrieves real-time synthesized state of the plant companion (character dialogue, health status, care plan actions, and gamification level/XP).
- **Auth**: Required (Device Bearer token or User JWT)
- **Query Parameters**:
  - `plant_id` (UUID, optional for device; mandatory for user callers)
- **Response 200 (Success Envelope)**:
  ```json
  {
    "success": true,
    "data": {
      "plant_id": "93b3f237-6d2c-47ea-bd50-c83134638706",
      "name": "Monty",
      "species": "Monstera deliciosa",
      "dayCount": 12,
      "timestamp": "2026-10-10T22:00:00Z",
      "wateredTimestamp": "2026-10-09T14:30:00Z",
      "level": 2,
      "xpRatio": 0.45,
      "health_status": "possibly_unhealthy",
      "decision": "CARE_ADVICE_REQUIRED",
      "companion_message": "Whew, my roots are feeling a bit waterlogged! Can we pause watering for a bit? 🌱",
      "care_plan": {
        "id": "cp_a7b8c9d0e1f2",
        "status_label": "Overwatering stress",
        "assessment": "Soil moisture remains elevated with early signs of root hypoxia.",
        "actions": [
          {
            "id": "act_8e4b1a2c",
            "priority": 1,
            "action": "Hold all watering until top 5cm soil is dry.",
            "label": "Pause water",
            "type": "water"
          }
        ]
      }
    },
    "message": null,
    "error": null
  }
  ```
- **Response 400 (Bad Request)**:
  ```json
  {
    "success": false,
    "data": null,
    "message": "Query parameter 'plant_id' is required when authenticating as a user.",
    "error": {
      "code": "PLANT_ID_REQUIRED",
      "details": null
    }
  }
  ```
- **Response 401 (Unauthorized)**:
  ```json
  {
    "success": false,
    "data": null,
    "message": "Authentication required. Provide a valid device bearer token or user JWT.",
    "error": {
      "code": "UNAUTHORIZED",
      "details": null
    }
  }
  ```
- **Response 403 (Forbidden)**:
  ```json
  {
    "success": false,
    "data": null,
    "message": "You do not have permission to access this plant.",
    "error": {
      "code": "FORBIDDEN",
      "details": {
        "plant_id": "93b3f237-6d2c-47ea-bd50-c83134638706"
      }
    }
  }
  ```
- **Response 404 (Not Found)**:
  ```json
  {
    "success": false,
    "data": null,
    "message": "The requested plant was not found or is not accessible.",
    "error": {
      "code": "PLANT_NOT_FOUND",
      "details": {
        "plant_id": "93b3f237-6d2c-47ea-bd50-c83134638706"
      }
    }
  }
  ```
- **Response 500 (Internal Server Error)**:
  ```json
  {
    "success": false,
    "data": null,
    "message": "An unexpected error occurred while retrieving companion state.",
    "error": {
      "code": "INTERNAL_SERVER_ERROR",
      "details": null
    }
  }
  ```

---

## Data Models

### BaseResponse Envelope
| Field | Type | Required | Description |
|:---|:---|:---:|:---|
| `success` | boolean | yes | Request outcome indicator |
| `data` | object / null | no | Generic payload `CompanionStateData` |
| `message` | string / null | no | Informational message |
| `error` | object / null | no | Structured error details (`code`, `details`) |

### CompanionStateData
| Field | Type | Required | Description |
|:---|:---|:---:|:---|
| `plant_id` | string | yes | Target plant identifier |
| `name` | string | yes | Plant nickname displayed in UI |
| `species` | string | yes | Botanical species display name |
| `dayCount` | integer | yes | Sequential observation count ($\ge 1$) |
| `timestamp` | string (ISO-8601 UTC) | yes | Timestamp of latest scan |
| `wateredTimestamp` | string (ISO-8601 UTC) \| null | no | Timestamp of last watering event |
| `level` | integer | yes | Character level ($\ge 1$, default 1) |
| `xpRatio` | float | yes | Character level progress ($0.0 - 1.0$) |
| `health_status` | string | yes | `healthy` \| `possibly_unhealthy` \| `unhealthy` |
| `decision` | string | yes | `NO_ACTION` \| `CARE_ADVICE_REQUIRED` \| `REQUEST_MORE_INFORMATION` |
| `companion_message` | string | yes | 1st-person dialogue spoken by plant |
| `care_plan` | object \| null | no | Active care card or null |
