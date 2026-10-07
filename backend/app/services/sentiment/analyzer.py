import re
from typing import Any, Dict, List, Optional
from app.core.dynamic_config import dynamic_config
from app.services.sentiment.sarcasm import sarcasm_detector


class SentimentResult:
    def __init__(
        self,
        sentiment: str,
        confidence: float,
        urgency: str,
        sarcasm: bool = False,
        risk_type: Optional[str] = None,
        negative_streak_count: int = 0,
        raw_scores: Optional[Dict[str, float]] = None
    ):
        self.sentiment = sentiment
        self.confidence = confidence
        self.urgency = urgency
        self.sarcasm = sarcasm
        self.risk_type = risk_type
        self.negative_streak_count = negative_streak_count
        self.raw_scores = raw_scores or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sentiment": self.sentiment,
            "confidence": self.confidence,
            "urgency": self.urgency,
            "sarcasm": self.sarcasm,
            "risk_type": self.risk_type,
            "negative_streak_count": self.negative_streak_count,
            "raw_scores": self.raw_scores
        }


class ConversationSentimentAnalyzer:
    """
    Multilingual, context-aware sentiment, urgency, sarcasm, and risk classification engine.
    Strictly separates cognitive perception (NLP metrics) from downstream deterministic escalation rules.
    """

    # High-Risk Signatures (Scenarios 2, 3, 4)
    RISK_PATTERNS = {
        "account_compromise": [
            r"(?i)\b(?:someone\s+(?:just\s+|has\s+)?(?:accessed|hacked|charged)\s+(?:into\s+|to\s+)?my\s+account|account\s+(?:is\s+|was\s+)?(?:hacked|compromised|stolen)|(?:my\s+)?account\s+(?:got\s+)?hacked|unauthorized\s+(?:access|login|activity|transaction|charge)|password\s+(?:was\s+)?changed\s+without|identity\s+theft|locked\s+out)\b",
            r"(?i)\b(?:not\s+me\s+who\s+logged|suspicious\s+login|unknown\s+location|changed\s+(?:my\s+)?(?:email|password|phone))\b"
        ],
        "duplicate_payment": [
            r"(?i)\b(?:charged\s+(?:twice|double|multiple\s+times)|double\s+(?:charge|payment|deduction)|payment\s+(?:was\s+|is\s+|got\s+)?deducted\s+twice|debited\s+twice|paid\s+two\s+times|deducted\s+twice)\b"
        ],
        "legal_threat": [
            r"(?i)\b(?:contact(?:ing)?\s+(?:my\s+)?(?:lawyer|attorney|legal\s+team|legal\s+counsel)|legal\s+action|sue\s+you|court\s+case|consumer\s+(?:court|protection\s+bureau)|police\s+complaint|regulatory\s+complaint|lodge\s+an\s+fir)\b"
        ]
    }

    # Urgency indicators
    CRITICAL_URGENCY_REGEX = re.compile(
        r"(?i)\b(?:immediately|urgent(?:ly)?|emergency|asap|critical|right\s+now|without\s+delay)\b"
    )

    # Frustration / Negative tokens
    FRUSTRATION_PATTERNS = [
        r"(?i)\b(?:frustrated|unacceptable|fed\s+up|ridiculous|terrible|horrible|worst|disaster|pathetic|angry|furious)\b",
        r"(?i)\b(?:still\s+waiting|nothing\s+happened|nobody\s+replied|waste\s+of\s+time|already\s+told\s+you|again\s+and\s+again)\b"
    ]

    # Positive tokens
    POSITIVE_PATTERNS = [
        r"(?i)\b(?:thank\s*you|thanks|helpful|appreciate|great|awesome|solved|resolved|perfect|good\s+job|pleased)\b"
    ]

    @classmethod
    def detect_high_risk(cls, text: str) -> Optional[str]:
        """
        Detects critical business risk conditions regardless of calm or neutral language.
        (Scenario 2: Calm account compromise, Scenario 3: Duplicate payment, Scenario 4: Legal threat)
        """
        for risk_category, patterns in cls.RISK_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, text):
                    return risk_category
        return None

    @classmethod
    def analyze_message(
        cls,
        message: str,
        recent_history: Optional[List[str]] = None
    ) -> SentimentResult:
        if not message or not message.strip():
            return SentimentResult(
                sentiment="neutral",
                confidence=1.0,
                urgency="low",
                sarcasm=False,
                risk_type=None,
                negative_streak_count=0
            )

        clean_text = message.strip()

        # 1. Independent Risk Detection (Scenarios 2, 3, 4)
        risk_type = cls.detect_high_risk(clean_text)

        # 2. Context-Aware Sarcasm Detection (Scenario 1)
        is_sarcastic, sarcasm_conf, _ = sarcasm_detector.evaluate_sarcasm(
            current_message=clean_text,
            recent_history_messages=recent_history
        )

        # 3. Urgency Detection
        is_urgent = bool(cls.CRITICAL_URGENCY_REGEX.search(clean_text))
        urgency_level = "critical" if risk_type or is_urgent else "low"

        # 4. History-Aware Negative Streak Tracking (Scenario 5)
        negative_streak = 0
        if recent_history:
            for past_msg in reversed(recent_history):
                past_lower = past_msg.lower()
                is_past_negative = any(re.search(p, past_lower) for p in cls.FRUSTRATION_PATTERNS)
                if is_past_negative:
                    negative_streak += 1
                else:
                    break

        # 5. Core Sentiment Scoring
        if is_sarcastic:
            sentiment = "sarcastic"
            confidence = sarcasm_conf
            urgency_level = "medium" if urgency_level == "low" else urgency_level
            negative_streak += 1
        elif any(re.search(p, clean_text) for p in cls.FRUSTRATION_PATTERNS):
            sentiment = "frustrated"
            confidence = 0.92
            urgency_level = "high" if is_urgent else "medium"
            negative_streak += 1
        elif is_urgent:
            sentiment = "urgent"
            confidence = 0.90
            urgency_level = "high"
        elif any(re.search(p, clean_text) for p in cls.POSITIVE_PATTERNS):
            sentiment = "positive"
            confidence = 0.94
            negative_streak = 0  # Genuine positive resets negative streak
        else:
            sentiment = "neutral"
            confidence = 0.88

        # Note: Even if sentiment is neutral, if risk_type is detected, urgency is elevated
        if risk_type and urgency_level in ["low", "medium"]:
            urgency_level = "high"

        raw_scores = {
            "frustration_score": 0.9 if sentiment == "frustrated" else 0.1,
            "positivity_score": 0.9 if sentiment == "positive" else 0.1,
            "urgency_score": 0.9 if urgency_level in ["high", "critical"] else 0.2,
            "sarcasm_score": sarcasm_conf if is_sarcastic else 0.0
        }

        return SentimentResult(
            sentiment=sentiment,
            confidence=confidence,
            urgency=urgency_level,
            sarcasm=is_sarcastic,
            risk_type=risk_type,
            negative_streak_count=negative_streak,
            raw_scores=raw_scores
        )


sentiment_analyzer = ConversationSentimentAnalyzer()
