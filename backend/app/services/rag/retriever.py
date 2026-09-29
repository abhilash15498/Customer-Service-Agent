import math
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.clock import Clock
from app.core.llm_provider import get_llm_provider
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeVersion
from app.services.rag.policy_solver import RetrievedCandidate, policy_solver


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


class RAGRetriever:
    """
    Retrieves and filters knowledge chunks for customer inquiries.
    Supports historical date extraction (Scenario 38) and semantic similarity scoring.
    """

    # Regex for detecting historical date inquiries (e.g. December 5, 2025 or 2025-12-05)
    DATE_PATTERNS = [
        r"(?i)\b(?:on|in|during|as\s+of|purchased\s+on)?\s*([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})\b",
        r"\b(\d{4})-(\d{2})-(\d{2})\b",
    ]

    MONTH_MAP = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
    }

    @classmethod
    def extract_target_date_from_query(cls, query: str) -> Optional[datetime]:
        """
        Detects if query asks about a historical policy date (Scenario 38).
        Example: 'What was the policy when I purchased this on December 5, 2025?'
        """
        for pat in cls.DATE_PATTERNS:
            match = re.search(pat, query)
            if match:
                groups = match.groups()
                try:
                    if len(groups) == 3 and groups[0].lower() in cls.MONTH_MAP:
                        month = cls.MONTH_MAP[groups[0].lower()]
                        day = int(groups[1])
                        year = int(groups[2])
                        return datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc)
                    elif len(groups) == 3 and groups[0].isdigit():
                        year = int(groups[0])
                        month = int(groups[1])
                        day = int(groups[2])
                        return datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc)
                except Exception:
                    continue
        return None

    @classmethod
    async def retrieve_candidates(
        cls,
        db: AsyncSession,
        query: str,
        user_role: str = "CUSTOMER",
        top_k: int = 5,
        target_product: Optional[str] = None,
        target_region: Optional[str] = None,
        force_as_of_date: Optional[datetime] = None
    ) -> List[RetrievedCandidate]:
        """
        Performs semantic vector retrieval, queries chunks with document & version metadata,
        and applies the PolicyConflictSolver.
        """
        llm = get_llm_provider()
        query_embedding = await llm.generate_embedding(query)

        # Detect historical date in query if not explicitly passed
        eval_date = force_as_of_date or cls.extract_target_date_from_query(query) or Clock.now()

        # Fetch all chunks with active versions
        stmt = (
            select(KnowledgeChunk, KnowledgeVersion, KnowledgeDocument)
            .join(KnowledgeVersion, KnowledgeChunk.version_id == KnowledgeVersion.id)
            .join(KnowledgeDocument, KnowledgeVersion.document_id == KnowledgeDocument.id)
        )
        result = await db.execute(stmt)
        rows = result.all()

        candidates: List[RetrievedCandidate] = []
        for chunk, version, doc in rows:
            # Calculate similarity
            chunk_emb = chunk.embedding or []
            sim = cosine_similarity(query_embedding, chunk_emb) if chunk_emb else 0.0

            candidates.append(
                RetrievedCandidate(
                    chunk_id=chunk.id,
                    document_id=doc.id,
                    document_title=doc.title,
                    version_id=version.id,
                    version_int=version.version_int,
                    effective_date=version.effective_date,
                    expiry_date=version.expiry_date,
                    status=version.status,
                    access_level=doc.access_level,
                    product=doc.product,
                    region=doc.region,
                    section=chunk.section,
                    content=chunk.content,
                    similarity_score=sim,
                    metadata=chunk.chunk_metadata
                )
            )

        # Filter and arbitrate policies
        arbitrated = policy_solver.filter_and_arbitrate(
            candidates=candidates,
            user_role=user_role,
            as_of_date=eval_date,
            target_product=target_product,
            target_region=target_region
        )

        # Return top_k
        return arbitrated[:top_k]


rag_retriever = RAGRetriever()
