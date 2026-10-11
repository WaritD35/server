"""Companion state response models and schemas for Swagger/OpenAPI."""

from datetime import datetime

from pydantic import BaseModel, Field


class CarePlanActionResponse(BaseModel):
    """Single actionable care step prescribed for the plant."""

    id: str = Field(
        ...,
        description="Unique action identifier for client completion tracking",
        examples=["act_8e4b1a2c"],
    )
    priority: int = Field(
        ...,
        ge=1,
        description="Relative priority ordering (1 is highest priority)",
        examples=[1],
    )
    action: str = Field(
        ...,
        description="Complete botanical instruction displayed in modal or detailed card",
        examples=["Hold all watering until top 5cm soil is dry."],
    )
    label: str = Field(
        ...,
        max_length=30,
        description="Concise 2–3 word button label for web client action triggers",
        examples=["Pause water"],
    )
    type: str = Field(
        ...,
        description="Categorical action type: water | move | inspect | other",
        examples=["water"],
    )


class CarePlanResponse(BaseModel):
    """Detailed botanical care plan card presented in the UI."""

    id: str = Field(
        ...,
        description="Unique identifier for this care plan",
        examples=["cp_a7b8c9d0e1f2"],
    )
    status_label: str = Field(
        ...,
        description="Short summary headline for the UI alert card",
        examples=["Overwatering stress"],
    )
    assessment: str = Field(
        ...,
        description="Botanical explanation describing diagnosis and root cause",
        examples=["Soil moisture remains elevated with early signs of root hypoxia."],
    )
    actions: list[CarePlanActionResponse] = Field(
        ...,
        description="Prioritized list of recommended action items",
    )


class CompanionStateData(BaseModel):
    """Synthesized real-time companion state for web clients and edge devices."""

    plant_id: str = Field(
        ...,
        description="Target plant identifier",
        examples=["93b3f237-6d2c-47ea-bd50-c83134638706"],
    )
    name: str = Field(
        ...,
        description="Plant nickname displayed at the top of the screen",
        examples=["Monty"],
    )
    species: str = Field(
        ...,
        description="Botanical species reference display name",
        examples=["Monstera deliciosa"],
    )
    dayCount: int = Field(
        ...,
        ge=1,
        description="1-indexed sequential observation count for this plant",
        examples=[12],
    )
    timestamp: datetime = Field(
        ...,
        description="ISO-8601 UTC timestamp of latest processed observation scan",
        examples=["2026-10-10T22:00:00Z"],
    )
    wateredTimestamp: datetime | None = Field(
        default=None,
        description=(
            "ISO-8601 UTC timestamp of last watering event recorded in care_events; "
            "null if unwatered"
        ),
        examples=["2026-10-09T14:30:00Z"],
    )
    level: int = Field(
        default=1,
        ge=1,
        description="Gamification character level (minimum 1)",
        examples=[2],
    )
    xpRatio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Progress ratio towards next level for UI progress bar (0.0 to 1.0)",
        examples=[0.45],
    )
    health_status: str = Field(
        ...,
        description="Overall plant condition: healthy | possibly_unhealthy | unhealthy",
        examples=["possibly_unhealthy"],
    )
    decision: str = Field(
        ...,
        description="Pipeline verdict: NO_ACTION | CARE_ADVICE_REQUIRED | REQUEST_MORE_INFORMATION",
        examples=["CARE_ADVICE_REQUIRED"],
    )
    companion_message: str = Field(
        ...,
        description="1st-person plant voice speech bubble message",
        examples=[
            "Whew, my roots are waterlogged! Hold off watering? 🌱"
        ],
    )
    care_plan: CarePlanResponse | None = Field(
        default=None,
        description=(
            "Botanical action plan on CARE_ADVICE_REQUIRED; null on NO_ACTION / "
            "REQUEST_MORE_INFORMATION"
        ),
    )
