import pytest
from app.core.masking import masker


def test_credit_card_masking():
    text = "Customer says: My visa card is 4111 2222 3333 4444 and order is 1234."
    masked = masker.mask_text(text)
    assert "4111 2222 3333 4444" not in masked
    assert "[CARD_MASKED]" in masked
    assert "1234" in masked  # Order numbers should NOT be masked!


def test_secret_and_token_masking():
    text = "System log: password=superSecretPassword123 with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    masked = masker.mask_text(text)
    assert "superSecretPassword123" not in masked
    assert "[REDACTED_SECRET]" in masked
    assert "[REDACTED_TOKEN]" in masked


def test_dict_deep_masking():
    data = {
        "user": "Alice",
        "password": "plainPassword!",
        "payload": {
            "credit_card": "4532-1111-2222-3333",
            "api_key": "sk-1234567890abcdef"
        }
    }
    masked = masker.mask_dict(data)
    assert masked["user"] == "Alice"
    assert masked["password"] == "[REDACTED]"
    assert masked["payload"]["api_key"] == "[REDACTED]"
    assert "[CARD_MASKED]" in masked["payload"]["credit_card"]
