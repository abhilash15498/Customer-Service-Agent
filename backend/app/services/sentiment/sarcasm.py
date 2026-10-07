import re
from typing import List, Optional, Tuple


class SarcasmDetector:
    """
    Context-aware sarcasm detection (Scenario 1).
    Distinguishes genuine compliments from sarcastic remarks by analyzing:
    1. Intra-sentence contradiction (e.g. 'Great service, still nothing arrived')
    2. Conversational history (e.g. 'Great service' or 'Thanks a lot' after repeated customer complaints)
    """

    SUPERFICIAL_PRAISE_PATTERNS = [
        r"(?i)\b(?:great|wonderful|fantastic|amazing|excellent|brilliant|outstanding|awesome)\s*(?:service|job|help|support|work)?\b",
        r"(?i)\bthanks\s*(?:a\s*lot|so\s*much|for\s*nothing)?\b",
        r"(?i)\bwow\b",
        r"(?i)\bvery\s*helpful\b",
        r"(?i)\blove\s*(?:this|the)\s*(?:service|product|delay)\b",
    ]

    NEGATIVE_COMPLAINT_PATTERNS = [
        r"(?i)\b(?:still\s*(?:nothing|waiting|not|no|haven't)|already\s*contacted|charged\s*twice|worst|useless|broken|never|delayed|fraud|scam)\b",
        r"(?i)\b(?:where\s*is|no\s*update|ridiculous|unacceptable|waste|fed\s*up)\b"
    ]

    INTRA_SENTENCE_SARCASM_REGEXES = [
        re.compile(r"(?i)\b(?:thanks|thank\s*you)\s+for\s+nothing\b"),
        re.compile(r"(?i)\b(?:great|wonderful|awesome)\s*(?:service|job)?[,!.]?\s*(?:still|yet|nothing|never|charged|broken|nowhere)\b"),
        re.compile(r"(?i)\b(?:wow|amazing)[,!.]?\s*(?:what\s*a\s*mess|took\s*forever|waste\s*of\s*time)\b"),
    ]

    @classmethod
    def evaluate_sarcasm(
        cls,
        current_message: str,
        recent_history_messages: Optional[List[str]] = None
    ) -> Tuple[bool, float, Optional[str]]:
        """
        Returns (is_sarcastic, confidence, reason).
        """
        if not current_message:
            return False, 0.0, None

        msg_clean = current_message.strip()

        # 1. Intra-sentence sarcasm check
        for pattern in cls.INTRA_SENTENCE_SARCASM_REGEXES:
            if pattern.search(msg_clean):
                return True, 0.94, "Intra-sentence contradiction between praise and complaint."

        # Check if the message contains superficial praise
        has_praise = any(re.search(pat, msg_clean) for pat in cls.SUPERFICIAL_PRAISE_PATTERNS)

        if not has_praise:
            return False, 0.0, None

        # Intra-sentence contradiction: praise combined with complaint in the same message
        has_complaint = any(re.search(pat, msg_clean) for pat in cls.NEGATIVE_COMPLAINT_PATTERNS)
        if has_complaint:
            return True, 0.95, "Intra-sentence contradiction between praise and complaint."

        # 2. Conversational context check (Scenario 1)
        # If the message is short praise (e.g. "Great service.") following negative complaints in history:
        if recent_history_messages:
            combined_history = " ".join([h.lower() for h in recent_history_messages[-5:]])
            has_history_complaints = any(
                re.search(pat, combined_history) for pat in cls.NEGATIVE_COMPLAINT_PATTERNS
            )
            if has_history_complaints:
                # Superficial praise after unresolved complaints = SARCASTIC
                return True, 0.92, "Superficial praise in conversational context with prior complaints."

        return False, 0.0, None


sarcasm_detector = SarcasmDetector()
