from typing import Optional


class ToneAdapter:
    """
    Applies situational tone adaptation instructions to the LLM system prompt
    based on verified sentiment without altering business policy (Requirement 16).
    """

    TONE_DIRECTIVES = {
        "positive": (
            "Tone directive: The customer is satisfied. Maintain a warm, friendly, "
            "and efficient demeanor. Conclude smoothly."
        ),
        "neutral": (
            "Tone directive: Maintain a direct, objective, and professional tone. "
            "Deliver required information clearly."
        ),
        "frustrated": (
            "Tone directive: The customer is frustrated. Be deeply empathetic, calm, "
            "and solution-oriented. Acknowledge their inconvenience respectfully. "
            "CRITICAL: Do NOT invent unapproved refunds, discounts, or exceptions to please them."
        ),
        "urgent": (
            "Tone directive: The inquiry is time-sensitive. Be clear, concise, and action-oriented. "
            "State immediately what steps are being taken or required next."
        ),
        "sarcastic": (
            "Tone directive: The customer is using sarcasm. Do NOT mirror their sarcasm or become defensive. "
            "Remain strictly professional, calm, acknowledge and address the underlying issue directly, and offer practical assistance."
        )
    }

    @classmethod
    def get_system_directive(cls, sentiment: str, risk_type: Optional[str] = None) -> str:
        base_directive = cls.TONE_DIRECTIVES.get(sentiment.lower(), cls.TONE_DIRECTIVES["neutral"])
        if risk_type:
            base_directive += (
                f"\nURGENT RISK DIRECTIVE: Customer has reported '{risk_type}'. "
                "Prioritize reassurance that security/support protocols are activated."
            )
        return base_directive


tone_adapter = ToneAdapter()
