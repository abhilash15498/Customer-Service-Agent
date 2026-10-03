import re
from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.ticket import SupportTicket


def compute_text_similarity(t1: str, t2: str) -> float:
    """Computes word-level Jaccard similarity between two issue descriptions."""
    words1 = set(re.findall(r"\b\w{3,}\b", t1.lower()))
    words2 = set(re.findall(r"\b\w{3,}\b", t2.lower()))
    if not words1 or not words2:
        return 0.0
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    return len(intersection) / len(union)


class DuplicateDetector:
    """
    Detects duplicate and related tickets (Scenarios 11, 12, 13).
    - Links duplicate requests for the same issue/order.
    - Prevents false-merging of unrelated requests from the same customer (Scenario 13).
    """

    PAYMENT_KEYWORDS = {"payment", "charged", "deducted", "double", "twice", "billing", "refund", "debited"}
    DEFECT_KEYWORDS = {"damaged", "broken", "defective", "torn", "scratched", "faulty", "quality"}
    SHIPPING_KEYWORDS = {"delivery", "late", "shipping", "courier", "delayed", "not arrived", "tracking"}

    @classmethod
    def get_issue_domain(cls, text: str) -> str:
        words = set(re.findall(r"\b\w+\b", text.lower()))
        if words.intersection(cls.PAYMENT_KEYWORDS):
            return "payments"
        if words.intersection(cls.DEFECT_KEYWORDS):
            return "defect"
        if words.intersection(cls.SHIPPING_KEYWORDS):
            return "shipping"
        return "general"

    @classmethod
    async def find_duplicate_or_related(
        cls,
        db: AsyncSession,
        customer_id: str,
        title: str,
        description: str,
        order_id: Optional[str] = None
    ) -> Tuple[bool, Optional[SupportTicket], str]:
        """
        Returns (is_duplicate, parent_ticket, relationship_type)
        Relationship types: 'DUPLICATE', 'RELATED', 'NONE'
        """
        # Search active/open tickets for the customer
        stmt = (
            select(SupportTicket)
            .where(
                SupportTicket.customer_id == customer_id,
                SupportTicket.status.in_(["OPEN", "ASSIGNED", "IN_PROGRESS", "PENDING_INFO"])
            )
            .order_by(SupportTicket.created_at.desc())
        )
        result = await db.execute(stmt)
        existing_tickets = result.scalars().all()

        if not existing_tickets:
            return False, None, "NONE"

        new_domain = cls.get_issue_domain(title + " " + description)

        for exist_t in existing_tickets:
            exist_domain = cls.get_issue_domain(exist_t.title + " " + exist_t.description)

            # Scenario 13: Unrelated issues from the same customer must NOT be merged
            if new_domain != exist_domain:
                continue

            # Check matching order_id
            same_order = (order_id and exist_t.order_id and order_id == exist_t.order_id)
            sim_score = compute_text_similarity(description, exist_t.description)

            # Scenario 11: Exact duplicate issue for same order
            if same_order and sim_score >= 0.3:
                return True, exist_t, "DUPLICATE"

            # Scenario 12: Related issue (same order or high semantic overlap in same domain)
            if same_order or sim_score >= 0.5:
                return True, exist_t, "RELATED"

        return False, None, "NONE"


duplicate_detector = DuplicateDetector()
