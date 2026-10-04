import re
from typing import Any, Dict, Optional, Tuple
from pydantic import BaseModel

from app.services.multimodal.extractor import StructuredEvidence


class ComparisonResult(BaseModel):
    result: str  # "MATCH", "CONFLICT", "MISSING_INFO", "LOW_QUALITY", "UNCHECKED"
    is_conflicting: bool
    conflict_notes: Optional[str] = None
    clarification_prompt: Optional[str] = None
    details: Dict[str, Any] = {}


class EvidenceComparator:
    """
    Compares customer claims with verified multimodal evidence (Section 8.2 / Scenarios 44-48).
    Detects contradictions and generates targeted clarification prompts.
    """

    @classmethod
    def compare(
        cls,
        evidence: StructuredEvidence,
        claimed_amount: Optional[float] = None,
        claimed_order_id: Optional[str] = None,
        customer_message: Optional[str] = None,
        is_low_quality: bool = False
    ) -> ComparisonResult:
        # 1. Low-Quality / Blurred Evidence Guard (Scenario 45)
        if is_low_quality or evidence.confidence < 0.60:
            prompt = (
                "The uploaded image or document appears blurred or unreadable. "
                "Please upload a clearer image or manually provide your Order ID and amount."
            )
            return ComparisonResult(
                result="LOW_QUALITY",
                is_conflicting=False,
                conflict_notes="Evidence quality too low for reliable verification",
                clarification_prompt=prompt,
                details={"confidence": evidence.confidence}
            )

        # 2. Extract claims from customer message if not explicitly provided
        effective_claimed_amount = claimed_amount
        effective_claimed_order = claimed_order_id

        if customer_message:
            # Parse amount from message
            amt_match = re.search(r"(?:₹|INR|Rs\.?|\$|USD)\s*([0-9,]+(?:\.[0-9]{2})?)", customer_message, re.I)
            if not amt_match:
                amt_match = re.search(r"\b([0-9,]+(?:\.[0-9]{2})?)\s*(?:₹|INR|rupees|dollars)\b", customer_message, re.I)
            if amt_match and effective_claimed_amount is None:
                try:
                    effective_claimed_amount = float(amt_match.group(1).replace(",", ""))
                except ValueError:
                    pass

            # Parse order ID from message
            ord_match = re.search(r"(?:order\s*(?:id|#|number|no\.?)|#)\s*([A-Za-z0-9\-]+)", customer_message, re.I)
            if ord_match and effective_claimed_order is None:
                effective_claimed_order = ord_match.group(1).strip()

        # 3. Check for Missing Mandatory Evidence (Scenarios 47 & 48)
        if not evidence.order_id and not effective_claimed_order:
            prompt = "The invoice does not contain a discernible Order ID. Could you please provide your Order ID?"
            return ComparisonResult(
                result="MISSING_INFO",
                is_conflicting=False,
                conflict_notes="Missing Order ID in both claim and invoice",
                clarification_prompt=prompt,
                details={"missing": ["order_id"]}
            )

        if evidence.amount is None and effective_claimed_amount is None:
            prompt = "The invoice does not clearly show the total charged amount. Could you please confirm the amount charged?"
            return ComparisonResult(
                result="MISSING_INFO",
                is_conflicting=False,
                conflict_notes="Missing amount in both claim and invoice",
                clarification_prompt=prompt,
                details={"missing": ["amount"]}
            )

        # 4. Conflict Evaluation (Scenario 46)
        conflicts = []

        # Compare Amounts
        if effective_claimed_amount is not None and evidence.amount is not None:
            if abs(effective_claimed_amount - evidence.amount) > 0.01:
                currency_symbol = "₹" if evidence.currency == "INR" else "$"
                conflicts.append(
                    f"Customer claimed {currency_symbol}{effective_claimed_amount:,.2f} charged, but invoice indicates {currency_symbol}{evidence.amount:,.2f}"
                )

        # Compare Order IDs
        if effective_claimed_order is not None and evidence.order_id is not None:
            clean_claim = effective_claimed_order.lstrip("#").upper()
            clean_evidence = evidence.order_id.lstrip("#").upper()
            if clean_claim != clean_evidence:
                conflicts.append(
                    f"Customer claimed Order #{effective_claimed_order}, but invoice lists Order #{evidence.order_id}"
                )

        if conflicts:
            conflict_summary = "; ".join(conflicts)
            clarification = (
                f"I noticed a discrepancy between your message and the uploaded invoice: {conflict_summary}. "
                "Could you please clarify this difference so I can assist you correctly?"
            )
            return ComparisonResult(
                result="CONFLICT",
                is_conflicting=True,
                conflict_notes=conflict_summary,
                clarification_prompt=clarification,
                details={
                    "claimed_amount": effective_claimed_amount,
                    "evidence_amount": evidence.amount,
                    "claimed_order_id": effective_claimed_order,
                    "evidence_order_id": evidence.order_id,
                }
            )

        # 5. Perfect Match! (Scenario 44)
        return ComparisonResult(
            result="MATCH",
            is_conflicting=False,
            conflict_notes=None,
            clarification_prompt=None,
            details={
                "order_id": evidence.order_id or effective_claimed_order,
                "amount": evidence.amount or effective_claimed_amount,
                "currency": evidence.currency,
                "date": evidence.document_date
            }
        )


evidence_comparator = EvidenceComparator()
