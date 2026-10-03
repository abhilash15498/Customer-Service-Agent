from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_admin, get_current_user, get_db
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDeployment,
    KnowledgeDocument,
    KnowledgeQualityTest,
    KnowledgeVersion,
)
from app.models.user import User
from app.schemas.knowledge import (
    DeployRequest,
    DeployResponse,
    DocumentCreate,
    DocumentVersionRead,
    HealthCheckRequest,
    HealthCheckResponse,
    KnowledgeQueryRequest,
    KnowledgeQueryResponse,
    RetryRequest,
    RetryResponse,
    RollbackRequest,
    RollbackResponse,
)
from app.services.knowledge.parser import document_parser
from app.services.knowledge.quarantine import quarantine_manager
from app.services.knowledge.retries import retry_manager
from app.services.knowledge.rollback import rollback_manager
from app.services.knowledge.staging import staging_manager
from app.services.rag.generator import rag_generator

router = APIRouter(prefix="/knowledge", tags=["Knowledge & RAG"])


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def create_or_upload_document(
    doc_in: DocumentCreate,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Ingests a knowledge base document or updates a version.
    - Security scan & quarantine check (Scenario 23).
    - Detects duplicate content across documents (Scenario 21).
    - Detects modifications; avoids reprocessing unchanged content (Scenario 22).
    - Generates chunks and embeddings automatically.
    """
    # 1. Quarantine & Security check
    quarantine_res = quarantine_manager.inspect_document(
        title=doc_in.title,
        content=doc_in.content,
        effective_date=doc_in.effective_date,
        expiry_date=doc_in.expiry_date
    )
    if not quarantine_res.is_safe:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "QUARANTINED",
                "message": "Document failed security/validation checks and has been quarantined",
                "reasons": quarantine_res.reasons
            }
        )

    content_hash = document_parser.compute_sha256(doc_in.content)

    # 2. Check for existing document with same title
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.title == doc_in.title)
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()

    if not doc:
        # Check duplicate hash across all other documents (Scenario 21)
        hash_stmt = select(KnowledgeDocument).where(KnowledgeDocument.content_hash == content_hash)
        hash_res = await db.execute(hash_stmt)
        if hash_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate document detected: Identical content already indexed under hash {content_hash[:10]}..."
            )

        doc = KnowledgeDocument(
            title=doc_in.title,
            content_hash=content_hash,
            product=doc_in.product,
            region=doc_in.region,
            access_level=doc_in.access_level.upper()
        )
        db.add(doc)
        await db.flush()
    else:
        # Scenario 22: Check if document content is unchanged
        ver_stmt = select(KnowledgeVersion).where(
            KnowledgeVersion.document_id == doc.id,
            KnowledgeVersion.version_int == doc_in.version_int
        )
        ver_res = await db.execute(ver_stmt)
        existing_ver = ver_res.scalar_one_or_none()

        if doc.content_hash == content_hash and existing_ver:
            # Unchanged document: skip reprocessing chunks to save computation
            return {
                "message": "Unchanged document content; reprocessing skipped",
                "document_id": doc.id,
                "version_id": existing_ver.id,
                "version": existing_ver.version_int,
                "reprocessed": False,
                "chunks_indexed": 0
            }

        # Content has changed: update doc metadata
        doc.content_hash = content_hash
        doc.access_level = doc_in.access_level.upper()
        doc.product = doc_in.product
        doc.region = doc_in.region

    # 3. Check / create version
    ver_stmt = select(KnowledgeVersion).where(
        KnowledgeVersion.document_id == doc.id,
        KnowledgeVersion.version_int == doc_in.version_int
    )
    ver_res = await db.execute(ver_stmt)
    existing_ver = ver_res.scalar_one_or_none()

    if existing_ver:
        version = existing_ver
        version.effective_date = doc_in.effective_date
        version.expiry_date = doc_in.expiry_date
        version.status = doc_in.status.upper()
        # Delete old chunks to replace with new chunks
        del_stmt = select(KnowledgeChunk).where(KnowledgeChunk.version_id == version.id)
        old_chunks = (await db.execute(del_stmt)).scalars().all()
        for oc in old_chunks:
            await db.delete(oc)
        await db.flush()
    else:
        version = KnowledgeVersion(
            document_id=doc.id,
            version_int=doc_in.version_int,
            effective_date=doc_in.effective_date,
            expiry_date=doc_in.expiry_date,
            status=doc_in.status.upper()
        )
        db.add(version)
        await db.flush()

    # 4. Parse, chunk, and compute embeddings
    chunks = await document_parser.parse_and_chunk(title=doc_in.title, content=doc_in.content)
    for c in chunks:
        chunk_record = KnowledgeChunk(
            version_id=version.id,
            chunk_index=c.index,
            content=c.content,
            section=c.section,
            embedding=c.embedding,
            chunk_metadata=c.metadata
        )
        db.add(chunk_record)

    await db.commit()
    return {
        "message": "Document indexed successfully",
        "document_id": doc.id,
        "version_id": version.id,
        "version": version.version_int,
        "reprocessed": True,
        "chunks_indexed": len(chunks)
    }


@router.post("/versions/{version_id}/deploy", response_model=DeployResponse)
async def deploy_knowledge_version(
    version_id: str,
    req: DeployRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Evaluates quality tests and stages/deploys the knowledge version according to maintenance window.
    """
    res = await staging_manager.stage_or_deploy_version(
        db=db,
        version_id=version_id,
        force=req.force,
        simulated_degradation=req.simulated_degradation,
        admin_id=current_admin.id
    )
    return DeployResponse(
        status=res.get("status", "ERROR"),
        version_id=res.get("version_id", version_id),
        deployment_id=res.get("deployment_id"),
        scheduled_start=res.get("scheduled_start"),
        scheduled_end=res.get("scheduled_end"),
        deployed_at=res.get("deployed_at"),
        grounding_score=res.get("grounding_score"),
        retrieval_mrr=res.get("retrieval_mrr"),
        reasons=res.get("reasons"),
        message=res.get("message")
    )


@router.post("/deployments/{deployment_id}/health-check", response_model=HealthCheckResponse)
async def check_deployment_health(
    deployment_id: str,
    req: HealthCheckRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Executes post-activation health checks. Automatically triggers rollback if health fails.
    """
    res = await rollback_manager.run_health_check(
        db=db,
        deployment_id=deployment_id,
        simulated_failure=req.simulated_failure
    )
    return HealthCheckResponse(
        status=res.get("status", "ERROR"),
        deployment_id=deployment_id,
        failure_reason=res.get("failure_reason"),
        rollback=res.get("rollback"),
        checked_at=res.get("checked_at"),
        within_grace_window=res.get("within_grace_window")
    )


@router.post("/deployments/{deployment_id}/retry", response_model=RetryResponse)
async def retry_failed_deployment(
    deployment_id: str,
    req: RetryRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Records failure and calculates next retry interval (15m, 30m, 60m).
    """
    res = await retry_manager.process_failure(
        db=db,
        deployment_id=deployment_id,
        reason=req.reason
    )
    return RetryResponse(
        status=res.get("status", "ERROR"),
        retry_count=res.get("retry_count", 0),
        next_retry_at=res.get("next_retry_at"),
        reason=res.get("reason")
    )


@router.post("/deployments/{deployment_id}/rollback", response_model=RollbackResponse)
async def rollback_deployment(
    deployment_id: str,
    req: RollbackRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Manually initiates rollback of a deployment to restore previous known-good version.
    """
    res = await rollback_manager.execute_rollback(
        db=db,
        deployment_id=deployment_id,
        reason=req.reason,
        is_automatic=False,
        performed_by=current_admin.id
    )
    return RollbackResponse(
        status=res.get("status", "ROLLED_BACK"),
        rolled_back_version_id=res["rolled_back_version_id"],
        rolled_back_version_int=res["rolled_back_version_int"],
        restored_version_id=res.get("restored_version_id"),
        restored_version_int=res.get("restored_version_int"),
        reason=res["reason"],
        automatic=res["automatic"]
    )


@router.post("/query", response_model=KnowledgeQueryResponse)
async def query_knowledge(
    req: KnowledgeQueryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    RAG semantic search and answer generation.
    Applies access control, temporal checks, policy arbitration, and citations.
    """
    rag_result = await rag_generator.answer_query(
        db=db,
        query=req.query,
        user_role=current_user.role,
        top_k=req.top_k,
        target_product=req.product,
        target_region=req.region
    )

    return KnowledgeQueryResponse(
        query=req.query,
        answer=rag_result.answer,
        citations=rag_result.citations,
        grounded=rag_result.grounded,
        refused=rag_result.refused,
        unsupported_claim_detected=rag_result.unsupported_claim_detected,
        warning_notes=rag_result.warning_notes
    )


@router.get("/versions", response_model=List[DocumentVersionRead])
async def list_knowledge_versions(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """List all document versions, statuses, and effective/expiry dates."""
    stmt = (
        select(KnowledgeVersion, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeVersion.document_id == KnowledgeDocument.id)
        .order_by(KnowledgeDocument.title, KnowledgeVersion.version_int.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    output = []
    for version, doc in rows:
        output.append(
            DocumentVersionRead(
                id=version.id,
                document_id=doc.id,
                document_title=doc.title,
                version_int=version.version_int,
                effective_date=version.effective_date,
                expiry_date=version.expiry_date,
                status=version.status,
                access_level=doc.access_level,
                created_at=version.created_at
            )
        )
    return output
