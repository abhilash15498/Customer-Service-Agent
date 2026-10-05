import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from app.core.dynamic_config import dynamic_config


class IntentMatch(BaseModel):
    intent: str
    confidence: float
    trigger_tokens: List[str]
    suggested_queue: str
    is_high_risk: bool = False


class IntentAnalysisResult(BaseModel):
    primary_intent: str
    all_intents: List[str]
    intent_matches: List[IntentMatch]
    confidence: float
    is_compound: bool
    low_confidence: bool
    requires_escalation: bool
    suggested_queue: str
    clarification_prompt: Optional[str] = None


class IntentClassifier:
    """
    Classifies customer message intents, decomposing compound multi-request inquiries
    and enforcing low-confidence clarification boundaries (Scenarios 60, 61).
    """

    INTENT_RULES = {
        "duplicate_payment": {
            "patterns": [
                r"\b(?:charged?\s*(?:twice|double|2\s*times)|duplicate\s*(?:charge|payment|debit))\b",
                r"\b(?:paise\s*do\s*baar|do\s*baar\s*kat\s*gaye|doppelt\s*abgebucht|cobrado\s*doble)\b",
            ],
            "queue": "payments",
            "high_risk": True,
            "base_conf": 0.95
        },
        "account_compromise": {
            "patterns": [
                r"\b(?:hacked|compromised|unauthorized\s*(?:access|login)|someone\s*accessed\s*my\s*account)\b",
                r"\b(?:cuenta\s*hackeada|compte\s*pirate)\b",
            ],
            "queue": "security",
            "high_risk": True,
            "base_conf": 0.98
        },
        "legal_threat": {
            "patterns": [
                r"\b(?:lawyer|attorney|sue\s*you|lawsuit|consumer\s*court|legal\s*action)\b",
                r"\b(?:tribunal|demanda\s*judicial|avocat)\b",
            ],
            "queue": "legal",
            "high_risk": True,
            "base_conf": 0.95
        },
        "order_delay": {
            "patterns": [
                r"\b(?:late|delayed?|not\s*delivered|where\s*is\s*my\s*order|track(?:ing)?\s*order|hasn'?t\s*arrived)\b",
                r"\b(?:innu\s*bandilla|abhi\s*tak\s*nahi\s*aaya|retard|colis\s*retard|lieferung\s*verspatet)\b",
                r"\b(?:deliv(?:e)?ry\s*(?:late|delay)|ord(?:e)?r\s*late)\b",
            ],
            "queue": "general_support",
            "high_risk": False,
            "base_conf": 0.90
        },
        "refund_request": {
            "patterns": [
                r"\b(?:refund|refunnd|money\s*back|return\s*and\s*refund|reembolso|remboursement|ruckerstattung)\b",
                r"\b(?:paise\s*wapas|paise\s*refund)\b",
            ],
            "queue": "billing",
            "high_risk": False,
            "base_conf": 0.90
        },
        "cancellation": {
            "patterns": [
                r"\b(?:cancel|cancell|cancellation|cancelar|annuler|stornieren)\b",
            ],
            "queue": "general_support",
            "high_risk": False,
            "base_conf": 0.88
        },
        "technical_support": {
            "patterns": [
                r"\b(?:error|err_[0-9a-z_]+|crash|not\s*working|bug|glitch|app\s*down)\b",
            ],
            "queue": "technical_support",
            "high_risk": False,
            "base_conf": 0.85
        }
    }

    # Greeting / generic patterns that lack substantive intent
    GREETING_PATTERNS = [
        r"^(?:hello|hi|hey|greetings|hola|bonjour|hallo|namaste|namaskara)[!\.\s]*$",
        r"^(?:help|help\s*me|i\s*need\s*help|assist\s*me)[!\.\s]*$",
        r"^(?:status|issue|problem)[!\.\s]*$"
    ]

    @classmethod
    def analyze_intent(cls, message: str) -> IntentAnalysisResult:
        clean_msg = message.strip()
        if not clean_msg:
            return IntentAnalysisResult(
                primary_intent="unclear",
                all_intents=[],
                intent_matches=[],
                confidence=0.0,
                is_compound=False,
                low_confidence=True,
                requires_escalation=False,
                suggested_queue="general_support",
                clarification_prompt=(
                    "Could you please specify how we can help you today? "
                    "(e.g., checking an order status, requesting a refund, or resolving a payment problem?)"
                )
            )

        # Check for vague/greeting only input (Scenario 60)
        is_generic_greeting = any(re.match(pat, clean_msg, re.IGNORECASE) for pat in cls.GREETING_PATTERNS)

        matches: List[IntentMatch] = []
        for intent_name, data in cls.INTENT_RULES.items():
            matched_tokens = []
            for pat in data["patterns"]:
                found = re.findall(pat, clean_msg, re.IGNORECASE)
                if found:
                    for item in found:
                        if isinstance(item, tuple):
                            matched_tokens.extend([x for x in item if x])
                        else:
                            matched_tokens.append(item)
            if matched_tokens:
                matches.append(
                    IntentMatch(
                        intent=intent_name,
                        confidence=data["base_conf"],
                        trigger_tokens=list(set(matched_tokens)),
                        suggested_queue=data["queue"],
                        is_high_risk=data["high_risk"]
                    )
                )

        # If it's a generic greeting or no intent rules match
        if not matches or is_generic_greeting:
            return IntentAnalysisResult(
                primary_intent="greeting" if is_generic_greeting else "unclear",
                all_intents=["greeting"] if is_generic_greeting else [],
                intent_matches=[],
                confidence=0.45,
                is_compound=False,
                low_confidence=True,
                requires_escalation=False,
                suggested_queue="general_support",
                clarification_prompt=(
                    "I would be glad to help! Could you please clarify what you need assistance with? "
                    "(For example: order tracking, requesting a refund, or reporting a payment issue.)"
                )
            )

        # Sort matches so high-risk or highest confidence comes first
        matches.sort(key=lambda m: (1 if m.is_high_risk else 0, m.confidence), reverse=True)

        all_intent_names = [m.intent for m in matches]
        primary = matches[0].intent
        highest_conf = matches[0].confidence
        is_compound = len(matches) > 1
        requires_esc = any(m.is_high_risk for m in matches)
        target_queue = matches[0].suggested_queue

        return IntentAnalysisResult(
            primary_intent=primary,
            all_intents=all_intent_names,
            intent_matches=matches,
            confidence=highest_conf,
            is_compound=is_compound,
            low_confidence=False,
            requires_escalation=requires_esc,
            suggested_queue=target_queue,
            clarification_prompt=None
        )


intent_classifier = IntentClassifier()
