import re
from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel


class QuarantineCheckResult(BaseModel):
    is_safe: bool
    status: str  # "APPROVED" or "QUARANTINED"
    reasons: List[str] = []


class QuarantineManager:
    """
    Security and validation scanner for knowledge base documents (Section 6.2 / Scenario 23).
    Quarantines invalid, corrupt, or unsafe documents before they reach vector storage.
    """

    DANGEROUS_PATTERNS = [
        (re.compile(r"<script[\s\S]*?>[\s\S]*?<\/script>", re.I), "Embedded executable script detected"),
        (re.compile(r"(?:powershell|cmd\.exe|bash|sh\s+-c)\s+", re.I), "Shell execution command detected"),
        (re.compile(r"(?:__import__|eval\(|exec\()", re.I), "Python execution payload detected"),
        (re.compile(r"^[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]{4,}", re.I), "Corrupted binary file header"),
    ]

    @classmethod
    def inspect_document(
        cls,
        title: str,
        content: str,
        effective_date: Optional[datetime] = None,
        expiry_date: Optional[datetime] = None
    ) -> QuarantineCheckResult:
        reasons = []

        # 1. Content completeness
        if not content or len(content.strip()) < 10:
            reasons.append("Document content too short or empty")

        # 2. Malicious / Dangerous code or script injection
        for pattern, desc in cls.DANGEROUS_PATTERNS:
            if pattern.search(content):
                reasons.append(desc)

        # 3. Metadata temporal consistency
        if effective_date and expiry_date:
            if effective_date >= expiry_date:
                reasons.append("Invalid temporal metadata: effective_date must be strictly before expiry_date")

        if reasons:
            return QuarantineCheckResult(
                is_safe=False,
                status="QUARANTINED",
                reasons=reasons
            )

        return QuarantineCheckResult(
            is_safe=True,
            status="APPROVED",
            reasons=[]
        )


quarantine_manager = QuarantineManager()
