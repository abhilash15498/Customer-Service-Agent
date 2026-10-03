import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ExtractedTicketData(BaseModel):
    title: str
    description: str
    customer_id: str
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    order_id: Optional[str] = None
    product_id: Optional[str] = None
    issue_type: str = "general"
    evidence_attached: bool = False
    missing_fields: List[str] = []
    is_complete: bool = True
    clarification_prompt: Optional[str] = None


class TicketExtractor:
    """
    Extracts structured entities from customer messages and conversation history.
    Validates mandatory fields (Scenario 10) without inventing missing information.
    """

    ORDER_PATTERNS = [
        r"(?i)\border\s*(?:id|number|#)?\s*[:#-]?\s*([a-zA-Z0-9_-]{3,20})\b",
        r"#([a-zA-Z0-9_-]{4,20})\b"
    ]

    PRODUCT_PATTERNS = [
        r"(?i)\b(?:item|product|item\s+name|model)\s*[:#-]?\s*([a-zA-Z0-9\s_-]{3,30})\b",
        r"(?i)\b(laptop|phone|tv|headphone|watch|shoes|shirt|camera|tablet)\b"
    ]

    # Issues that strictly require an order ID to take action
    ORDER_DEPENDENT_ISSUES = [
        "damaged", "broken", "defective", "return", "refund", "duplicate",
        "charged", "late", "delayed", "not delivered", "missing", "tracking", "cancel"
    ]

    @classmethod
    def extract_order_id(cls, text: str) -> Optional[str]:
        for pat in cls.ORDER_PATTERNS:
            match = re.search(pat, text)
            if match:
                order_val = match.group(1).strip()
                # Exclude common false positives
                if order_val.lower() not in {"id", "number", "details", "info", "is", "was"}:
                    return order_val
        return None

    @classmethod
    def extract_product_id(cls, text: str) -> Optional[str]:
        for pat in cls.PRODUCT_PATTERNS:
            match = re.search(pat, text)
            if match:
                return match.group(1).strip()
        return None

    @classmethod
    def extract_and_validate(
        cls,
        customer_id: str,
        customer_name: Optional[str],
        customer_email: Optional[str],
        issue_text: str,
        conversation_history: Optional[List[str]] = None,
        attachments_count: int = 0
    ) -> ExtractedTicketData:
        combined_text = issue_text
        if conversation_history:
            combined_text = "\n".join(conversation_history) + "\n" + issue_text

        order_id = cls.extract_order_id(combined_text)
        product_id = cls.extract_product_id(combined_text)
        evidence_attached = attachments_count > 0

        # Classify issue category
        lower_issue = combined_text.lower()
        if any(w in lower_issue for w in ["payment", "charged", "deducted", "refund", "billing", "double"]):
            issue_type = "payments"
        elif any(w in lower_issue for w in ["damaged", "broken", "defective", "faulty", "quality"]):
            issue_type = "product_defect"
        elif any(w in lower_issue for w in ["delivery", "shipping", "late", "delayed", "courier", "tracking"]):
            issue_type = "shipping"
        elif any(w in lower_issue for w in ["hacked", "compromise", "unauthorized", "login", "password"]):
            issue_type = "security"
        else:
            issue_type = "general"

        # Mandatory Field Validation (Scenario 10)
        missing_fields: List[str] = []

        # Check if issue is order-dependent
        is_order_dependent = any(kw in lower_issue for kw in cls.ORDER_DEPENDENT_ISSUES)
        if is_order_dependent and not order_id:
            missing_fields.append("order_id")

        if "damaged" in lower_issue and not evidence_attached:
            missing_fields.append("evidence")

        # Generate customer clarification request if mandatory info is missing
        clarification_prompt = None
        if missing_fields:
            missing_items = ", ".join([f.replace("_", " ") for f in missing_fields])
            clarification_prompt = (
                f"To help resolve your issue, could you please provide the missing {missing_items}? "
                "Once provided, we will create your support ticket right away."
            )

        title = f"{issue_type.replace('_', ' ').title()} Inquiry: {issue_text[:60].strip()}"
        if len(issue_text) > 60:
            title += "..."

        return ExtractedTicketData(
            title=title,
            description=issue_text.strip(),
            customer_id=customer_id,
            customer_name=customer_name,
            customer_email=customer_email,
            order_id=order_id,
            product_id=product_id,
            issue_type=issue_type,
            evidence_attached=evidence_attached,
            missing_fields=missing_fields,
            is_complete=len(missing_fields) == 0,
            clarification_prompt=clarification_prompt
        )


ticket_extractor = TicketExtractor()
