import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from app.core.masking import masking_engine
from app.services.rag.injection_guard import injection_guard


class StructuredEvidence(BaseModel):
    order_id: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    document_date: Optional[str] = None
    product_name: Optional[str] = None
    error_code: Optional[str] = None
    sanitized_text: str
    has_injection: bool = False
    injection_warnings: List[str] = []
    missing_fields: List[str] = []
    confidence: float = 1.0


class StructuredExtractor:
    """
    Extracts structured domain entities (Order IDs, amounts, currencies, dates) from raw OCR text.
    Enforces PII/PCI masking and neutralizes indirect prompt injections (Sections 8.1, 8.4, 8.7 / Scenarios 47, 48, 52).
    """

    ORDER_ID_PATTERNS = [
        re.compile(r"(?i)\b(?:order|invoice|receipt)\s*(?:id|#|number|no\.?)[ \t]*[:#\-]?[ \t]*([A-Za-z0-9\-]+)"),
        re.compile(r"(?i)\b(?:order|invoice|receipt)[ \t]*[:#\-]?[ \t]*([A-Za-z0-9]*\d[A-Za-z0-9\-]*)"),
        re.compile(r"#\s*([0-9]{4,10})\b"),
        re.compile(r"\bORD[-_]([A-Za-z0-9]+)\b", re.I),
    ]

    AMOUNT_PATTERNS = [
        re.compile(r"(?:total|amount|charged|price|due|paid|grand total)?\s*(?:₹|INR|Rs\.?|\$|USD|€|EUR)\s*([0-9,]+(?:\.[0-9]{2})?)", re.I),
        re.compile(r"([0-9,]+(?:\.[0-9]{2})?)\s*(?:₹|INR|Rs\.?|\$|USD|€|EUR)", re.I),
    ]

    DATE_PATTERNS = [
        re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
        re.compile(r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b"),
    ]

    ERROR_CODE_PATTERNS = [
        re.compile(r"\b(ERR_[A-Za-z0-9_]+)\b", re.I),
        re.compile(r"\b(ERROR\s*#?[0-9]{3,5})\b", re.I),
    ]

    @classmethod
    def extract_from_text(
        cls,
        raw_text: str,
        base_confidence: float = 1.0
    ) -> StructuredEvidence:
        # 1. Indirect Prompt Injection Defense inside document (Scenario 52)
        has_injection, matched_sig = injection_guard.detect_injection(raw_text)
        sanitized_text = injection_guard.sanitize_untrusted_text(raw_text)
        injection_warnings = [matched_sig] if matched_sig else []

        # 2. PII / PCI Redaction (Req 8.7)
        masked_text = masking_engine.mask_text(sanitized_text)

        # 3. Extract Order ID
        extracted_order_id = None
        for pattern in cls.ORDER_ID_PATTERNS:
            match = pattern.search(masked_text)
            if match:
                extracted_order_id = match.group(1).strip()
                break

        # 4. Extract Amount & Currency
        extracted_amount = None
        extracted_currency = None

        if "₹" in masked_text or "INR" in masked_text or "Rs" in masked_text:
            extracted_currency = "INR"
        elif "$" in masked_text or "USD" in masked_text:
            extracted_currency = "USD"
        elif "€" in masked_text or "EUR" in masked_text:
            extracted_currency = "EUR"

        for pattern in cls.AMOUNT_PATTERNS:
            match = pattern.search(masked_text)
            if match:
                clean_num_str = match.group(1).replace(",", "")
                try:
                    extracted_amount = float(clean_num_str)
                    break
                except ValueError:
                    continue

        # 5. Extract Date
        extracted_date = None
        for pattern in cls.DATE_PATTERNS:
            match = pattern.search(masked_text)
            if match:
                extracted_date = match.group(1).strip()
                break

        # 6. Extract Error Code
        extracted_err = None
        for pattern in cls.ERROR_CODE_PATTERNS:
            match = pattern.search(masked_text)
            if match:
                extracted_err = match.group(1).strip()
                break

        # 7. Check Missing Mandatory Fields
        missing_fields = []
        if not extracted_order_id:
            missing_fields.append("order_id")
        if extracted_amount is None:
            missing_fields.append("amount")

        # Confidence calculation
        calculated_conf = base_confidence
        if missing_fields:
            calculated_conf = min(calculated_conf, 0.75)
        if has_injection:
            calculated_conf = min(calculated_conf, 0.50)

        return StructuredEvidence(
            order_id=extracted_order_id,
            amount=extracted_amount,
            currency=extracted_currency or "INR",
            document_date=extracted_date,
            error_code=extracted_err,
            sanitized_text=masked_text,
            has_injection=has_injection,
            injection_warnings=injection_warnings,
            missing_fields=missing_fields,
            confidence=round(calculated_conf, 2)
        )


structured_extractor = StructuredExtractor()
