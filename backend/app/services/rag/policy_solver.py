from datetime import datetime
from typing import Any, Dict, List, Optional
from app.core.clock import Clock


class RetrievedCandidate:
    def __init__(
        self,
        chunk_id: str,
        document_id: str,
        document_title: str,
        version_id: str,
        version_int: int,
        effective_date: datetime,
        expiry_date: datetime,
        status: str,
        access_level: str,
        product: Optional[str],
        region: Optional[str],
        section: Optional[str],
        content: str,
        similarity_score: float,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.chunk_id = chunk_id
        self.document_id = document_id
        self.document_title = document_title
        self.version_id = version_id
        self.version_int = version_int
        self.effective_date = effective_date
        self.expiry_date = expiry_date
        self.status = status
        self.access_level = access_level
        self.product = product
        self.region = region
        self.section = section
        self.content = content
        self.similarity_score = similarity_score
        self.metadata = metadata or {}


class PolicyConflictSolver:
    """
    Arbitrates candidate knowledge chunks according to:
    1. Authorization & Role Access (Scenario 39)
    2. Temporal Validity: Current vs Expired (Scenario 36) vs Future (Scenario 37)
    3. Historical Inquiries: As-of Date Range Filtering (Scenario 38)
    4. Conflict Resolution: Selection of Latest Applicable Policy over raw vector similarity (Scenarios 34 & 35)
    """

    ROLE_PERMISSIONS = {
        "CUSTOMER": ["PUBLIC"],
        "AGENT": ["PUBLIC", "INTERNAL"],
        "ADMIN": ["PUBLIC", "INTERNAL", "RESTRICTED"]
    }

    @classmethod
    def filter_and_arbitrate(
        cls,
        candidates: List[RetrievedCandidate],
        user_role: str = "CUSTOMER",
        as_of_date: Optional[datetime] = None,
        target_product: Optional[str] = None,
        target_region: Optional[str] = None
    ) -> List[RetrievedCandidate]:
        if not candidates:
            return []

        eval_date = as_of_date if as_of_date is not None else Clock.now()
        # Normalize timezone
        if eval_date.tzinfo is None:
            from datetime import timezone
            eval_date = eval_date.replace(tzinfo=timezone.utc)

        allowed_access_levels = cls.ROLE_PERMISSIONS.get(user_role.upper(), ["PUBLIC"])
        valid_candidates: List[RetrievedCandidate] = []

        for c in candidates:
            # 1. Authorization check (Scenario 39)
            if c.access_level.upper() not in allowed_access_levels:
                continue

            # 2. Product and Region filter if specified
            if target_product and c.product and c.product.lower() != target_product.lower():
                continue
            if target_region and c.region and c.region.lower() != target_region.lower():
                continue

            # Normalize candidate dates
            c_eff = c.effective_date
            c_exp = c.expiry_date
            if c_eff.tzinfo is None:
                from datetime import timezone
                c_eff = c_eff.replace(tzinfo=timezone.utc)
            if c_exp.tzinfo is None:
                from datetime import timezone
                c_exp = c_exp.replace(tzinfo=timezone.utc)

            # 3. Temporal Validity Check (Scenarios 36, 37, 38)
            # Future policy check (Scenario 37): effective_date > eval_date -> Exclude
            if c_eff > eval_date:
                continue

            # Expired policy check (Scenario 36): expiry_date < eval_date -> Exclude
            if c_exp < eval_date:
                continue

            # Check status: must be ACTIVE
            if c.status.upper() != "ACTIVE":
                continue

            valid_candidates.append(c)

        if not valid_candidates:
            return []

        # 4. Policy Conflict Arbitration (Scenarios 34 & 35)
        # When multiple versions exist for the same document or topic, group by document_id
        # and choose the latest applicable version (effective_date DESC, version_int DESC).
        docs_by_id: Dict[str, List[RetrievedCandidate]] = {}
        for vc in valid_candidates:
            docs_by_id.setdefault(vc.document_id, []).append(vc)

        arbitrated_results: List[RetrievedCandidate] = []

        for doc_id, doc_candidates in docs_by_id.items():
            # Find the max version / latest effective date for this document
            latest_version_int = max(c.version_int for c in doc_candidates)
            # Keep only chunks corresponding to the latest applicable version
            for c in doc_candidates:
                if c.version_int == latest_version_int:
                    arbitrated_results.append(c)

        # Sort final list prioritizing latest effective date and similarity score
        arbitrated_results.sort(
            key=lambda x: (x.effective_date, x.version_int, x.similarity_score),
            reverse=True
        )

        return arbitrated_results


policy_solver = PolicyConflictSolver()
