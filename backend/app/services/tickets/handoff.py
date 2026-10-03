from typing import Any, Dict, List, Optional
from app.core.masking import masker


class HandoffSummaryGenerator:
    """
    Constructs a structured, masked handoff summary for human support agents (Section 5.6).
    Scrubs sensitive credentials, tokens, and payment card numbers while preserving
    operational ticket parameters.
    """

    @classmethod
    def generate_summary(
        cls,
        customer_name: str,
        issue_title: str,
        order_id: Optional[str] = None,
        product_id: Optional[str] = None,
        evidence_summary: Optional[str] = None,
        sentiment: str = "neutral",
        risk_type: Optional[str] = None,
        priority: str = "MEDIUM",
        actions_taken: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        masked_name = customer_name[:2] + "****" if len(customer_name) > 2 else "Customer"
        clean_issue = masker.mask_text(issue_title)
        evidence_str = evidence_summary or "None provided"

        actions_list = actions_taken or ["Automated AI triage completed", "Initial sentiment & policy assessment performed"]
        actions_formatted = [masker.mask_text(a) for a in actions_list]

        summary_markdown = (
            f"### Human Agent Handoff Summary\n"
            f"- **Customer**: {masked_name}\n"
            f"- **Issue**: {clean_issue}\n"
            f"- **Order ID**: {order_id or 'N/A'}\n"
            f"- **Product**: {product_id or 'N/A'}\n"
            f"- **Evidence**: {evidence_str}\n"
            f"- **Customer Sentiment**: {sentiment.capitalize()}\n"
            f"- **Risk Classification**: {risk_type or 'None'}\n"
            f"- **SLA Priority**: {priority}\n"
            f"- **Actions Taken**:\n" + "\n".join([f"  * {a}" for a in actions_formatted])
        )

        return {
            "masked_customer": masked_name,
            "issue": clean_issue,
            "order_id": order_id,
            "product_id": product_id,
            "evidence": evidence_str,
            "sentiment": sentiment,
            "risk_type": risk_type,
            "priority": priority,
            "actions_taken": actions_formatted,
            "formatted_summary": summary_markdown
        }


handoff_generator = HandoffSummaryGenerator()
