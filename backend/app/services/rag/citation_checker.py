import re
from typing import Any, Dict, List, Optional, Tuple
from app.services.rag.policy_solver import RetrievedCandidate


class Citation:
    def __init__(self, document: str, version: int, section: Optional[str] = None):
        self.document = document
        self.version = version
        self.section = section or "General"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document": self.document,
            "version": self.version,
            "section": self.section
        }


class CitationChecker:
    """
    Validates factual citations and detects unsupported claims (Scenarios 40, 41, 43).
    Guarantees:
    - Never fabricates citations.
    - Matches citations against retrieved and verified chunks.
    - If evidence is inadequate, generates controlled refusal rather than hallucinated answers.
    """

    # Matches citation tags like: [Source: Document Title, v2, Section: Refund Policy]
    CITATION_PATTERN = re.compile(
        r"\[(?:Source|Citation):\s*([^,\]]+),\s*v(\d+)(?:,\s*Section:\s*([^\]]+))?\]",
        re.IGNORECASE
    )

    SAFE_REFUSAL_MESSAGE = (
        "I do not have sufficient authorized company policy or documentation to answer this question. "
        "Please provide more specific details or contact a human support representative."
    )

    @classmethod
    def extract_citations(cls, text: str) -> List[Citation]:
        citations = []
        matches = cls.CITATION_PATTERN.findall(text)
        for doc_title, ver_str, sec_str in matches:
            citations.append(
                Citation(
                    document=doc_title.strip(),
                    version=int(ver_str),
                    section=sec_str.strip() if sec_str else None
                )
            )
        return citations

    @classmethod
    def verify_citations(
        cls,
        citations: List[Citation],
        verified_chunks: List[RetrievedCandidate]
    ) -> Tuple[bool, List[Citation]]:
        """
        Validates that every citation in the response corresponds to an authorized, retrieved chunk.
        """
        if not citations:
            return False, []

        valid_citations: List[Citation] = []
        for cit in citations:
            # Check if any retrieved chunk matches document title and version
            matched = any(
                chunk.document_title.lower() == cit.document.lower() and chunk.version_int == cit.version
                for chunk in verified_chunks
            )
            if matched:
                valid_citations.append(cit)

        # All citations must be valid
        all_valid = len(valid_citations) == len(citations) and len(valid_citations) > 0
        return all_valid, valid_citations

    @classmethod
    def detect_unsupported_claims(
        cls,
        answer: str,
        verified_chunks: List[RetrievedCandidate]
    ) -> Tuple[bool, str]:
        """
        Heuristic grounding verification (Scenario 41):
        Ensures response does not assert facts (e.g. refund days, percentage discounts, policy numbers)
        that are absent from retrieved chunks.
        """
        if not verified_chunks:
            return True, "No verified evidence available for claims."

        combined_evidence = " ".join([c.content.lower() for c in verified_chunks])

        # Check key numeric claims (e.g. "30 days", "14 days", "100% refund")
        numbers_in_answer = re.findall(r"\b(\d+)\s*(days?|hours?|%|percent|rupees?|rs\.?|inr|\$)\b", answer, re.IGNORECASE)
        for num, unit in numbers_in_answer:
            claim_fragment = f"{num} {unit}".lower()
            alt_fragment = f"{num}{unit}".lower()
            if num not in combined_evidence:
                return True, f"Unsupported numeric claim: '{claim_fragment}' not found in authorized evidence."

        return False, "All identified numeric claims are supported."

    @classmethod
    def format_citation_tag(cls, chunk: RetrievedCandidate) -> str:
        return f"[Source: {chunk.document_title}, v{chunk.version_int}, Section: {chunk.section or 'General'}]"


citation_checker = CitationChecker()
