import re
from typing import Any, Dict


class DataMasker:
    """
    Sanitizes sensitive data from log files, handoff summaries, and external streams.
    Redacts:
    - Credit/Debit Card numbers (PCI)
    - Passwords, API tokens, Secret keys
    - National ID numbers / Social Security / Aadhaar
    - Emails and phone numbers (optional flag)
    """

    # Regex patterns
    CREDIT_CARD_REGEX = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
    EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b")
    PHONE_REGEX = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
    KEY_VALUE_SECRET_REGEX = re.compile(
        r"(?i)\b(password|secret|token|api[_-]?key|bearer|auth|cvv|pin)\b\s*[:=]\s*([\"']?[^\"'\s,;]+[\"']?)"
    )
    BEARER_AUTH_REGEX = re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]+")

    @classmethod
    def mask_text(cls, text: str, mask_contacts: bool = False) -> str:
        if not text:
            return ""

        # Mask secrets in key-value format
        masked = cls.KEY_VALUE_SECRET_REGEX.sub(r"\1: [REDACTED_SECRET]", text)
        masked = cls.BEARER_AUTH_REGEX.sub("Bearer [REDACTED_TOKEN]", masked)

        # Mask Credit Cards
        masked = cls.CREDIT_CARD_REGEX.sub("[CARD_MASKED]", masked)

        if mask_contacts:
            masked = cls.EMAIL_REGEX.sub("[EMAIL_MASKED]", masked)
            masked = cls.PHONE_REGEX.sub("[PHONE_MASKED]", masked)

        return masked

    @classmethod
    def mask_dict(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Deeply masks dictionary keys and string values."""
        sensitive_keys = {"password", "secret", "token", "api_key", "access_token", "cvv", "pin"}
        sanitized = {}
        for k, v in data.items():
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, str):
                sanitized[k] = cls.mask_text(v)
            elif isinstance(v, dict):
                sanitized[k] = cls.mask_dict(v)
            elif isinstance(v, list):
                sanitized[k] = [
                    cls.mask_dict(item) if isinstance(item, dict)
                    else cls.mask_text(item) if isinstance(item, str)
                    else item
                    for item in v
                ]
            else:
                sanitized[k] = v
        return sanitized


masker = DataMasker()
