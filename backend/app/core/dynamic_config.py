import copy
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BusinessHoursDay(BaseModel):
    start: str = "09:00"
    end: str = "18:00"


class DynamicConfigSchema(BaseModel):
    business_hours: Dict[str, Optional[List[str]]] = Field(
        default_factory=lambda: {
            "monday": ["09:00", "18:00"],
            "tuesday": ["09:00", "18:00"],
            "wednesday": ["09:00", "18:00"],
            "thursday": ["09:00", "18:00"],
            "friday": ["09:00", "18:00"],
            "saturday": None,
            "sunday": None,
        }
    )
    timezone: str = "Asia/Kolkata"
    holidays: List[str] = Field(
        default_factory=lambda: [
            "2026-01-26",
            "2026-08-15",
            "2026-10-02",
            "2026-12-25"
        ]
    )
    sentiment_thresholds: Dict[str, float] = Field(
        default_factory=lambda: {
            "frustration_escalation_confidence": 0.85,
            "urgency_escalation_confidence": 0.80,
            "negative_streak_escalation_count": 3.0,
            "low_confidence_threshold": 0.70,
        }
    )
    escalation_rules: Dict[str, Any] = Field(
        default_factory=lambda: {
            "negative_escalation_minutes": 15,
            "allow_on_call_after_hours": True,
            "high_risk_categories": [
                "account_compromise",
                "duplicate_payment",
                "legal_threat"
            ],
        }
    )
    sla_config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "default_resolution_hours": 24,
            "priority_multipliers": {
                "LOW": 2.0,
                "MEDIUM": 1.0,
                "HIGH": 0.5,
                "CRITICAL": 0.25,
            },
            "sla_warning_percentage": 75,
        }
    )
    session_management: Dict[str, int] = Field(
        default_factory=lambda: {
            "inactivity_timeout_minutes": 30,
            "summary_restoration_hours": 24,
            "history_message_limit": 10,
        }
    )
    file_policy: Dict[str, Any] = Field(
        default_factory=lambda: {
            "file_retention_days": 7,
            "max_file_size_mb": 15,
            "ocr_sync_timeout_seconds": 30,
            "allowed_mime_types": [
                "image/png",
                "image/jpeg",
                "application/pdf"
            ],
        }
    )
    knowledge_pipeline: Dict[str, Any] = Field(
        default_factory=lambda: {
            "maintenance_window": {
                "start_time": "02:00",
                "end_time": "03:00"
            },
            "retry_intervals_minutes": [15, 30, 60],
            "health_check_grace_minutes": 5,
            "minimum_grounding_score": 0.85,
        }
    )
    supported_languages: List[str] = Field(
        default_factory=lambda: ["en", "hi", "kn", "es"]
    )


class DynamicConfigRegistry:
    """
    In-memory dynamic configuration manager with atomic updates.
    Allows runtime reconfiguration during evaluation scenarios without restarting the server.
    """
    def __init__(self):
        self._config = DynamicConfigSchema()

    def get_config(self) -> DynamicConfigSchema:
        return copy.deepcopy(self._config)

    def update_config(self, updates: Dict[str, Any]) -> DynamicConfigSchema:
        current_data = self._config.model_dump()
        self._deep_update(current_data, updates)
        self._config = DynamicConfigSchema.model_validate(current_data)
        return copy.deepcopy(self._config)

    def reset_to_defaults(self) -> None:
        self._config = DynamicConfigSchema()

    def _deep_update(self, base_dict: dict, update_dict: dict) -> None:
        for k, v in update_dict.items():
            if isinstance(v, dict) and k in base_dict and isinstance(base_dict[k], dict):
                self._deep_update(base_dict[k], v)
            else:
                base_dict[k] = v


dynamic_config = DynamicConfigRegistry()
