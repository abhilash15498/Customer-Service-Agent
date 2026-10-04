import hashlib
import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_admin, get_current_user, get_db
from app.core.clock import Clock
from app.models.file import ExtractedEvidence, UploadedFile
from app.models.user import User
from app.schemas.file import (
    FileDetailRead,
    FileUploadResponse,
    MultimodalAnalysisRequest,
    MultimodalAnalysisResponse,
    RetentionCleanupResponse,
)
from app.services.multimodal.comparator import evidence_comparator
from app.services.multimodal.extractor import structured_extractor
from app.services.multimodal.ocr import ocr_processor
from app.services.multimodal.retention import retention_manager
from app.services.multimodal.validator import file_validator

router = APIRouter(prefix="/files", tags=["Multimodal & Evidence"])


@router.post("/upload", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    conversation_id: Optional[str] = Form(None),
    simulated_processing_seconds: Optional[int] = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Uploads evidence file (invoice, screenshot, receipt, PDF).
    Performs MIME validation, magic byte checking, size verification, and quarantine checks (Section 8.5 / Scenario 51).
    """
    content = await file.read()
    file_name = file.filename or "uploaded_file"
    sha256_hash = hashlib.sha256(content).hexdigest()

    # 1. Validation & Quarantine Guard (Scenario 51)
    val_res = file_validator.validate_file(
        file_name=file_name,
        content=content,
        declared_mime=file.content_type
    )

    now = Clock.now()
    expires_at = retention_manager.calculate_expiry(now)

    # Local storage path simulation
    storage_dir = os.path.join("uploads", str(now.year), f"{now.month:02d}")
    os.makedirs(storage_dir, exist_ok=True)
    storage_path = os.path.join(storage_dir, f"{sha256_hash}_{file_name}")

    if not val_res.is_valid:
        # Quarantine invalid/unsafe file
        quarantined_record = UploadedFile(
            conversation_id=conversation_id,
            file_name=file_name,
            mime_type=val_res.detected_mime or file.content_type or "application/octet-stream",
            file_size_bytes=len(content),
            storage_path=storage_path,
            sha256_hash=sha256_hash,
            status="QUARANTINED",
            rejection_reason="; ".join(val_res.reasons),
            expires_at=expires_at,
            created_at=now
        )
        db.add(quarantined_record)
        await db.commit()
        await db.refresh(quarantined_record)

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "QUARANTINED",
                "message": "File failed security/MIME validation and has been quarantined",
                "reasons": val_res.reasons,
                "file_id": quarantined_record.id
            }
        )

    # 2. Check for Long Processing / Timeout (> 30s) (Scenario 50)
    processing_mode = "SYNC"
    file_status = "READY"
    mode_message = None

    if simulated_processing_seconds and simulated_processing_seconds > 30:
        processing_mode = "ASYNC"
        file_status = "PROCESSING_ASYNC"
        mode_message = (
            "File analysis requires extended processing and has been queued asynchronously. "
            "You may continue interacting while your document processes."
        )

    # Save to disk
    try:
        with open(storage_path, "wb") as f_out:
            f_out.write(content)
    except OSError:
        pass

    uploaded_record = UploadedFile(
        conversation_id=conversation_id,
        file_name=file_name,
        mime_type=val_res.detected_mime or file.content_type,
        file_size_bytes=len(content),
        storage_path=storage_path,
        sha256_hash=sha256_hash,
        status=file_status,
        expires_at=expires_at,
        created_at=now
    )
    db.add(uploaded_record)
    await db.commit()
    await db.refresh(uploaded_record)

    return FileUploadResponse(
        id=uploaded_record.id,
        file_name=uploaded_record.file_name,
        mime_type=uploaded_record.mime_type,
        file_size_bytes=uploaded_record.file_size_bytes,
        sha256_hash=uploaded_record.sha256_hash,
        status=uploaded_record.status,
        expires_at=uploaded_record.expires_at,
        created_at=uploaded_record.created_at,
        processing_mode=processing_mode,
        message=mode_message
    )


@router.post("/{file_id}/analyze", response_model=MultimodalAnalysisResponse)
async def analyze_file(
    file_id: str,
    req: MultimodalAnalysisRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Performs OCR extraction, entity parsing, prompt-injection defense, and evidence comparison.
    Covers Scenarios 44, 45, 46, 47, 48, 49, 50, 52.
    """
    stmt = (
        select(UploadedFile)
        .where(UploadedFile.id == file_id)
        .options(selectinload(UploadedFile.evidence))
    )
    res = await db.execute(stmt)
    uf = res.scalar_one_or_none()

    if not uf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if uf.status == "QUARANTINED":
        return MultimodalAnalysisResponse(
            file_id=uf.id,
            status="QUARANTINED",
            confidence=0.0,
            comparison_result="QUARANTINED",
            customer_prompt="This file was quarantined due to security violations and cannot be analyzed.",
            details={"reasons": uf.rejection_reason}
        )

    # 1. Read binary content
    file_bytes = b""
    if os.path.exists(uf.storage_path):
        try:
            with open(uf.storage_path, "rb") as f_in:
                file_bytes = f_in.read()
        except OSError:
            pass

    # 2. Run OCR Processing (Scenarios 44, 45, 49, 50)
    ocr_res = await ocr_processor.process_document(
        content=file_bytes,
        file_name=uf.file_name,
        simulated_text=req.simulated_ocr_text,
        simulated_quality=req.simulated_quality,
        simulated_duration_seconds=req.simulated_processing_seconds
    )

    if ocr_res.is_async:
        return MultimodalAnalysisResponse(
            file_id=uf.id,
            status="PROCESSING_ASYNC",
            confidence=0.0,
            comparison_result="UNCHECKED",
            customer_prompt="Document processing is taking longer than 30 seconds. Your evidence has been queued for background analysis.",
            details=ocr_res.metadata
        )

    if not ocr_res.success:
        # OCR Failure (Scenario 49)
        return MultimodalAnalysisResponse(
            file_id=uf.id,
            status="OCR_FAILURE",
            confidence=0.0,
            comparison_result="LOW_QUALITY",
            customer_prompt="We were unable to read the contents of this document. Please re-upload a clear file or manually enter your Order ID and amount.",
            details={"error": ocr_res.error_message}
        )

    # 3. Structured Entity Extraction & Prompt Injection Defense (Scenarios 47, 48, 52)
    evidence = structured_extractor.extract_from_text(
        raw_text=ocr_res.raw_text,
        base_confidence=ocr_res.confidence
    )

    # 4. Compare Evidence with Customer Message / Claim (Scenario 44 & 46)
    comparison = evidence_comparator.compare(
        evidence=evidence,
        claimed_amount=req.claimed_amount,
        claimed_order_id=req.claimed_order_id,
        customer_message=req.customer_message,
        is_low_quality=ocr_res.is_blurred or ocr_res.is_unreadable
    )

    # 5. Persist or Update ExtractedEvidence Record
    if uf.evidence:
        ev_record = uf.evidence
        ev_record.extracted_text = evidence.sanitized_text
        ev_record.order_id = evidence.order_id
        ev_record.amount = evidence.amount
        ev_record.currency = evidence.currency
        ev_record.confidence = evidence.confidence
        ev_record.is_conflicting = comparison.is_conflicting
        ev_record.conflict_notes = comparison.conflict_notes
    else:
        ev_record = ExtractedEvidence(
            file_id=uf.id,
            extracted_text=evidence.sanitized_text,
            order_id=evidence.order_id,
            amount=evidence.amount,
            currency=evidence.currency,
            confidence=evidence.confidence,
            is_conflicting=comparison.is_conflicting,
            conflict_notes=comparison.conflict_notes
        )
        db.add(ev_record)

    await db.commit()

    return MultimodalAnalysisResponse(
        file_id=uf.id,
        status=comparison.result,
        confidence=evidence.confidence,
        extracted_order_id=evidence.order_id,
        extracted_amount=evidence.amount,
        extracted_currency=evidence.currency,
        extracted_date=evidence.document_date,
        comparison_result=comparison.result,
        is_conflicting=comparison.is_conflicting,
        customer_prompt=comparison.clarification_prompt,
        details={
            "missing_fields": evidence.missing_fields,
            "has_injection": evidence.has_injection,
            "injection_warnings": evidence.injection_warnings,
            "comparison_details": comparison.details
        }
    )


@router.get("/{file_id}", response_model=FileDetailRead)
async def get_file_detail(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Fetches uploaded file details and extracted evidence."""
    stmt = (
        select(UploadedFile)
        .where(UploadedFile.id == file_id)
        .options(selectinload(UploadedFile.evidence))
    )
    res = await db.execute(stmt)
    uf = res.scalar_one_or_none()
    if not uf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return uf


@router.post("/cleanup-expired", response_model=RetentionCleanupResponse)
async def trigger_retention_cleanup(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Enforces retention policy (Scenario 53). Purges files exceeding retention period.
    """
    res = await retention_manager.purge_expired_files(db)
    return RetentionCleanupResponse(
        files_scanned=res["files_scanned"],
        files_purged=res["files_purged"],
        purged_file_ids=res["purged_file_ids"],
        message=res["message"]
    )
