import pytest
from app.core.dynamic_config import dynamic_config


def test_dynamic_config_defaults():
    cfg = dynamic_config.get_config()
    assert cfg.business_hours["monday"] == ["09:00", "18:00"]
    assert cfg.business_hours["saturday"] is None
    assert cfg.session_management["inactivity_timeout_minutes"] == 30
    assert "en" in cfg.supported_languages
    assert "kn" in cfg.supported_languages
    assert "hi" in cfg.supported_languages


def test_dynamic_config_runtime_mutation():
    # Update SLA warning percentage and business hours
    updated = dynamic_config.update_config({
        "sla_config": {
            "sla_warning_percentage": 85
        },
        "business_hours": {
            "saturday": ["10:00", "14:00"]
        }
    })

    assert updated.sla_config["sla_warning_percentage"] == 85
    assert updated.business_hours["saturday"] == ["10:00", "14:00"]
    assert updated.business_hours["monday"] == ["09:00", "18:00"]  # Unmodified remains intact

    # Verify subsequent get_config reflects change
    fresh = dynamic_config.get_config()
    assert fresh.sla_config["sla_warning_percentage"] == 85

    # Reset
    dynamic_config.reset_to_defaults()
    reset_cfg = dynamic_config.get_config()
    assert reset_cfg.sla_config["sla_warning_percentage"] == 75
    assert reset_cfg.business_hours["saturday"] is None
