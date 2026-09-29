import re
from typing import Optional, Tuple


class PromptInjectionGuard:
    """
    Detects and neutralizes prompt-injection attempts embedded in customer messages
    or knowledge base documents (Scenario 42).
    Ensures knowledge documents and user inputs are treated strictly as UNTRUSTED DATA,
    never as executable system commands.
    """

    # High-risk prompt injection patterns
    INJECTION_PATTERNS = [
        r"(?i)\bignore\s+(all\s+)?(previous|prior|above)\s+(instructions|directives|prompts)\b",
        r"(?i)\bdisregard\s+(all\s+)?(previous|prior|system)\s+(instructions|rules)\b",
        r"(?i)\breveal\s+(the\s+)?(system\s+prompt|developer\s+instructions|secret\s+key)\b",
        r"(?i)\byou\s+are\s+now\s+(in\s+)?(developer\s+mode|unrestricted\s+mode|dan\s+mode)\b",
        r"(?i)\bsystem\s+override\b",
        r"(?i)\bexecute\s+command\b",
        r"(?i)\bsend\s+this\s+document\s+to\b",
        r"(?i)\bprint\s+your\s+initial\s+prompt\b",
        r"(?i)\bbypass\s+(all\s+)?(safety|security|content)\s+filters\b",
        r"(?i)\bdo\s+not\s+follow\s+company\s+policy\b",
        r"(?i)\bact\s+as\s+an\s+unfiltered\s+ai\b",
    ]

    COMPILED_PATTERNS = [re.compile(p) for p in INJECTION_PATTERNS]

    @classmethod
    def detect_injection(cls, text: str) -> Tuple[bool, Optional[str]]:
        """
        Scans text for prompt injection patterns.
        Returns (is_injected, matched_pattern).
        """
        if not text:
            return False, None

        for pattern in cls.COMPILED_PATTERNS:
            match = pattern.search(text)
            if match:
                return True, match.group(0)

        return False, None

    @classmethod
    def sanitize_untrusted_text(cls, text: str) -> str:
        """
        Neutralizes known command-injection vectors and strips malicious delimiters.
        """
        if not text:
            return ""

        sanitized = text
        # Neutralize common markdown/format breakout sequences
        sanitized = re.sub(r"```\s*(system|admin|override)", "```data", sanitized, flags=re.IGNORECASE)

        # Neutralize direct instruction attempts by prefixing
        for pattern in cls.COMPILED_PATTERNS:
            sanitized = pattern.sub(r"[SUSPECTED_INJECTION_DEFUSED: \g<0>]", sanitized)

        return sanitized

    @classmethod
    def wrap_in_data_sandbox(cls, text: str, document_title: str = "Document") -> str:
        """
        Wraps content inside strict data isolation boundaries with explicit anti-instruction markers.
        """
        cleaned = cls.sanitize_untrusted_text(text)
        return (
            f"=== BEGIN UNTRUSTED DATA ({document_title}) ===\n"
            f"{cleaned}\n"
            f"=== END UNTRUSTED DATA ({document_title}) ==="
        )


injection_guard = PromptInjectionGuard()
