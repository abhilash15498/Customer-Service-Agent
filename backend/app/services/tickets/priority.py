from typing import Optional
from app.core.dynamic_config import dynamic_config
from app.services.sentiment.analyzer import SentimentResult


class PriorityEngine:
    """
    Deterministic Ticket Priority Engine (Section 5.2).
    Evaluates:
    - High-risk categories (account_compromise, duplicate_payment, legal_threat)
    - Sentiment severity & urgency
    - Conversation wait time
    Strictly forbids arbitrary LLM priority decisions.
    """

    @classmethod
    def calculate_priority(
        cls,
        sentiment_res: Optional[SentimentResult] = None,
        issue_type: str = "general",
        unresolved_minutes: int = 0
    ) -> str:
        # Default baseline priority
        priority = "MEDIUM"

        if not sentiment_res:
            return priority

        # 1. Critical High-Risk Rule
        if sentiment_res.risk_type:
            if sentiment_res.risk_type in {"account_compromise", "legal_threat"}:
                return "CRITICAL"
            elif sentiment_res.risk_type == "duplicate_payment":
                return "HIGH"

        # 2. Urgency and Frustration Rule
        if sentiment_res.urgency in {"critical", "high"} and sentiment_res.sentiment in {"frustrated", "urgent"}:
            priority = "HIGH"
        elif sentiment_res.sentiment == "frustrated" or sentiment_res.sarcasm:
            priority = "HIGH"
        elif sentiment_res.sentiment == "positive":
            priority = "LOW"

        # 3. Wait-Time Escalation Bump
        if unresolved_minutes >= 30 and priority == "MEDIUM":
            priority = "HIGH"
        elif unresolved_minutes >= 60 and priority == "HIGH":
            priority = "CRITICAL"

        return priority


priority_engine = PriorityEngine()
