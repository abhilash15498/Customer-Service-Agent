from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_admin, get_current_user, get_db
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeVersion
from app.models.user import User
from app.schemas.knowledge import (
    DocumentCreate,
    DocumentVersionRead,
    KnowledgeQueryRequest,
    KnowledgeQueryResponse,
)
from app.services.knowledge.parser import document_parser
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
    Detects duplicates via content SHA-256 hash.
    Generates chunks and embeddings automatically.
    """
    content_hash = document_parser.compute_sha256(doc_in.content)

    # 1. Check for existing document with same title
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.title == doc_in.title)
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()

    if not doc:
        # Check duplicate hash across all documents (Scenario 21: duplicate document detection)
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
        # Existing document: update content hash
        doc.content_hash = content_hash
        doc.access_level = doc_in.access_level.upper()
        doc.product = doc_in.product
        doc.region = doc_in.region

    # 2. Check if this version number already exists
    ver_stmt = select(KnowledgeVersion).where(
        KnowledgeVersion.document_id == doc.id,
        KnowledgeVersion.version_int == doc_in.version_int
    )
    ver_res = await db.execute(ver_stmt)
    existing_ver = ver_res.scalar_one_or_none()

    if existing_ver:
        # Update existing version
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

    # 3. Parse and chunk document, compute embeddings
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
        "chunks_indexed": len(chunks)
    }


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
