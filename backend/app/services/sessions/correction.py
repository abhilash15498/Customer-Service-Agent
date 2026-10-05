import re
from typing import Any, Dict, Optional, Tuple
from pydantic import BaseModel


class CorrectionResult(BaseModel):
    has_correction: bool
    field: Optional[str] = None
    previous_value: Optional[str] = None
    updated_value: Optional[str] = None
    confirmation_message: Optional[str] = None


class CorrectionDetector:
    """
    Detects customer self-corrections mid-conversation (Scenario 62).
    Replaces stale entity values with confirmed updates and maintains audit history.
    """

    # Patterns indicating user self-correction
    CORRECTION_PATTERNS = [
        # "Sorry, I meant 4251" or "I mean ORD-9901"
        re.compile(
            r"(?i)\b(?:sorry|oops|apologies|wait|no)?\s*[,.\s]*(?:i\s*meant|i\s*mean|correction\s*:?|actually\s*(?:it\s*is)?)\s*[:#\-]?[ \t]*([a-zA-Z0-9_\-]{3,20})\b"
        ),
        # "Wrong order, it is 4251" or "wrong number, it's 4251"
        re.compile(
            r"(?i)\b(?:wrong\s*(?:order|id|number|amount)?)[,\s]+(?:it\s*(?:is|'s)|its)?\s*[:#\-]?[ \t]*([a-zA-Z0-9_\-]{3,20})\b"
        ),
        # "Not 4521, but 4251" or "Not 4521 it is 4251"
        re.compile(
            r"(?i)\bnot\s+([a-zA-Z0-9_\-]{3,20})[,\s]+(?:but|it\s*is|its)?\s*([a-zA-Z0-9_\-]{3,20})\b"
        ),
    ]

    AMOUNT_CORRECTION_PATTERNS = [
        re.compile(
            r"(?i)\b(?:sorry|oops|wait)?\s*[,.\s]*(?:i\s*meant|actually|correction)\s*[:#\-]?[ \t]*(?:₹|rs\.?|inr|\$|eur|€)?\s*([0-9,]+(?:\.[0-9]{2})?)\b"
        )
    ]

    @classmethod
    def detect_correction(
        cls,
        message: str,
        active_entities: Optional[Dict[str, Any]] = None
    ) -> CorrectionResult:
        active = active_entities or {}
        clean_msg = message.strip()

        # Check explicit "Not X, but Y"
        match_not_but = cls.CORRECTION_PATTERNS[2].search(clean_msg)
        if match_not_but:
            old_val = match_not_but.group(1).strip()
            new_val = match_not_but.group(2).strip()
            return CorrectionResult(
                has_correction=True,
                field="order_id",
                previous_value=old_val,
                updated_value=new_val,
                confirmation_message=f"Understood. I have updated your order number from {old_val} to {new_val}."
            )

        # Check generic correction cues
        for pat in cls.CORRECTION_PATTERNS[:2]:
            m = pat.search(clean_msg)
            if m:
                new_val = m.group(1).strip()
                old_val = active.get("order_id")
                return CorrectionResult(
                    has_correction=True,
                    field="order_id",
                    previous_value=str(old_val) if old_val else None,
                    updated_value=new_val,
                    confirmation_message=(
                        f"Understood. Updating your order number from {old_val} to {new_val}."
                        if old_val else f"Understood. Setting your order number to {new_val}."
                    )
                )

        return CorrectionResult(has_correction=False)


correction_detector = CorrectionDetector()
