from datetime import datetime, timedelta
import zoneinfo
import pytest
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.sla import SLARecord
from app.models.ticket import SupportTicket, TicketAssignment, TicketEvent
from app.models.user import Agent, AgentSkill, User
from app.services.routing.matcher import skill_workload_matcher
from app.services.sla.calendar import calendar_engine
from app.services.sla.tracker import sla_tracker
from app.services.tickets.duplicates import duplicate_detector
from app.services.tickets.extractor import ticket_extractor
from app.services.tickets.handoff import handoff_generator
from app.services.tickets.priority import priority_engine


@pytest.fixture(autouse=True)
def reset_system_state():
    Clock.reset()
    dynamic_config.reset_to_defaults()
    yield
    Clock.reset()
    dynamic_config.reset_to_defaults()


# -----------------------------------------------------------------------------
# Scenario 10: Missing Mandatory Information
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_10_missing_mandatory_information(client, db_session):
    """
    Scenario 10: If mandatory information is missing (e.g. order_id for a damaged product claim),
    the system must identify the missing fields and prompt the customer instead of falsely
    creating an incomplete ticket.
    """
    # Register & Login
    await client.post("/api/v1/auth/register", json={
        "email": "missing_info@example.com",
        "name": "Alex Missing",
        "password": "Password123!",
        "role": "CUSTOMER"
    })
    token = (await client.post("/api/v1/auth/login", json={
        "email": "missing_info@example.com",
        "password": "Password123!"
    })).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Customer submits complaint without order ID: "My product arrived damaged."
    resp1 = await client.post(
        "/api/v1/tickets",
        json={"description": "My product arrived completely damaged and broken."},
        headers=headers
    )
    assert resp1.status_code == 201
    data1 = resp1.json()
    assert data1["is_complete"] is False
    assert "order_id" in data1["missing_fields"]
    assert "missing order id" in data1["clarification_prompt"].lower()
    assert data1["ticket"] is None  # Falsely complete ticket was NOT created!

    # 2. Customer provides the missing order_id
    resp2 = await client.post(
        "/api/v1/tickets",
        json={
            "description": "My product arrived completely damaged and broken for order 8841.",
            "order_id": "8841",
            "attachments_count": 1
        },
        headers=headers
    )
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert data2["is_complete"] is True
    assert data2["ticket"] is not None
    assert data2["ticket"]["order_id"] == "8841"
    assert data2["ticket"]["status"] == "OPEN"


# -----------------------------------------------------------------------------
# Scenario 11 & 12: Duplicate and Related Ticket Detection
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_11_and_12_duplicate_and_related_tickets(client, db_session):
    """
    Scenario 11: Duplicate ticket detection (e.g. "Payment deducted twice for order 123"
    and "I was charged twice for order 123" -> identified as duplicate).
    Scenario 12: Related ticket linking.
    """
    await client.post("/api/v1/auth/register", json={
        "email": "dup_user@example.com",
        "name": "Dup Customer",
        "password": "Password123!",
        "role": "CUSTOMER"
    })
    token = (await client.post("/api/v1/auth/login", json={
        "email": "dup_user@example.com",
        "password": "Password123!"
    })).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Ticket A
    resp_a = await client.post(
        "/api/v1/tickets",
        json={
            "description": "Payment deducted twice for order 4521.",
            "order_id": "4521"
        },
        headers=headers
    )
    ticket_a = resp_a.json()["ticket"]
    assert resp_a.json()["is_duplicate"] is False

    # Ticket B: Same order, same issue phrased differently
    resp_b = await client.post(
        "/api/v1/tickets",
        json={
            "description": "I was charged twice for order 4521, please refund.",
            "order_id": "4521"
        },
        headers=headers
    )
    data_b = resp_b.json()
    assert data_b["is_duplicate"] is True
    assert data_b["parent_ticket_id"] == ticket_a["id"]
    assert data_b["ticket"]["parent_ticket_id"] == ticket_a["id"]


# -----------------------------------------------------------------------------
# Scenario 13: Unrelated Issues from Same Customer Must NOT Merge
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_13_unrelated_issues_same_customer_do_not_merge(client, db_session):
    """
    Scenario 13: Customer has:
    1. Duplicate payment
    2. Damaged product
    These should NOT automatically become one issue merely because they belong to the same customer.
    """
    await client.post("/api/v1/auth/register", json={
        "email": "multi_issue@example.com",
        "name": "Multi Customer",
        "password": "Password123!",
        "role": "CUSTOMER"
    })
    token = (await client.post("/api/v1/auth/login", json={
        "email": "multi_issue@example.com",
        "password": "Password123!"
    })).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Issue 1: Payment issue for order 101
    resp1 = await client.post(
        "/api/v1/tickets",
        json={
            "description": "Payment was deducted twice for order 101.",
            "order_id": "101"
        },
        headers=headers
    )
    t1 = resp1.json()["ticket"]

    # Issue 2: Damaged product for order 202
    resp2 = await client.post(
        "/api/v1/tickets",
        json={
            "description": "My laptop arrived with a damaged cracked screen for order 202.",
            "order_id": "202"
        },
        headers=headers
    )
    t2_data = resp2.json()

    # Must NOT be merged as duplicate
    assert t2_data["is_duplicate"] is False
    assert t2_data["parent_ticket_id"] is None
    assert t2_data["ticket"]["parent_ticket_id"] is None
    assert t2_data["ticket"]["id"] != t1["id"]


# -----------------------------------------------------------------------------
# Scenario 14: Unavailable Support Team (Queueing with Priority Intact)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_14_unavailable_support_team_queuing(db_session):
    """
    Scenario 14: If the appropriate team is unavailable, queue the ticket,
    preserve priority, and schedule according to business rules.
    """
    customer = User(email="cust_queue@example.com", hashed_password="pw", role="CUSTOMER", name="Queue Cust")
    db_session.add(customer)
    await db_session.flush()

    ticket = SupportTicket(
        customer_id=customer.id,
        title="Payment Dispute",
        description="Duplicate deduction",
        priority="CRITICAL",
        status="OPEN",
        required_skill="payments",
        created_at=Clock.now()
    )
    db_session.add(ticket)
    await db_session.commit()

    # No agents exist with 'payments' skill in DB -> routing should queue
    assigned, agent, status_msg = await skill_workload_matcher.match_and_assign(
        db=db_session,
        ticket=ticket,
        required_skill="payments",
        strict_skill=True
    )

    assert assigned is False
    assert agent is None
    assert "QUEUED" in status_msg
    assert ticket.status == "OPEN"
    assert ticket.priority == "CRITICAL"  # Priority strictly preserved


# -----------------------------------------------------------------------------
# Scenario 15: High Workload Agent (Workload Balancing)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_15_workload_balancing(db_session):
    """
    Scenario 15: Selects the available agent with the lowest appropriate workload.
    """
    customer = User(email="cust_load@example.com", hashed_password="pw", role="CUSTOMER", name="Cust Load")
    u1 = User(email="heavy_agent@example.com", hashed_password="pw", role="AGENT", name="Heavy Agent")
    u2 = User(email="light_agent@example.com", hashed_password="pw", role="AGENT", name="Light Agent")
    db_session.add_all([customer, u1, u2])
    await db_session.flush()

    # Agent 1 has workload 4 (near max 5); Agent 2 has workload 1
    agent_heavy = Agent(id=u1.id, tier="tier_1", is_available=True, max_workload=5, current_workload=4)
    agent_light = Agent(id=u2.id, tier="tier_1", is_available=True, max_workload=5, current_workload=1)
    db_session.add_all([agent_heavy, agent_light])
    await db_session.flush()

    db_session.add(AgentSkill(agent_id=agent_heavy.id, skill_name="technical"))
    db_session.add(AgentSkill(agent_id=agent_light.id, skill_name="technical"))

    ticket = SupportTicket(
        customer_id=customer.id,
        title="Technical Error",
        description="App crashed",
        priority="HIGH",
        status="OPEN",
        required_skill="technical",
        created_at=Clock.now()
    )
    db_session.add(ticket)
    await db_session.commit()

    assigned, chosen_agent, _ = await skill_workload_matcher.match_and_assign(
        db=db_session,
        ticket=ticket,
        required_skill="technical",
        strict_skill=True
    )

    assert assigned is True
    assert chosen_agent.id == agent_light.id
    assert chosen_agent.current_workload == 2
    assert ticket.status == "ASSIGNED"


# -----------------------------------------------------------------------------
# Scenario 16 & 17: SLA Approaching 75% Warning & SLA Breach
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_16_and_17_sla_warning_and_breach(db_session):
    """
    Scenario 16: SLA approaching 75% triggers WARNING_75.
    Scenario 17: SLA breach automatically escalates to BREACHED.
    """
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    t0 = datetime(2026, 10, 5, 9, 0, tzinfo=tz)  # Monday 09:00 AM
    Clock.set_time(t0)

    customer = User(email="sla_test@example.com", hashed_password="pw", role="CUSTOMER", name="SLA Test")
    db_session.add(customer)
    await db_session.flush()

    ticket = SupportTicket(
        customer_id=customer.id,
        title="SLA Test Ticket",
        description="Testing SLA intervals",
        priority="HIGH",
        status="OPEN",
        created_at=t0
    )
    db_session.add(ticket)
    await db_session.flush()

    # Target: 4 business hours = 240 minutes
    sla_rec = SLARecord(
        ticket_id=ticket.id,
        target_resolution_minutes=240,
        business_minutes_elapsed=0,
        status="OK",
        created_at=t0
    )
    db_session.add(sla_rec)
    await db_session.commit()

    # 1. Advance to 180 business minutes (12:00 PM Monday) -> Exactly 75%
    t_75 = t0 + timedelta(minutes=180)
    Clock.set_time(t_75)
    updated_sla = await sla_tracker.update_ticket_sla(db_session, ticket)

    assert updated_sla.business_minutes_elapsed == 180
    assert updated_sla.status == "WARNING_75"
    assert updated_sla.warning_75_sent_at is not None

    # 2. Advance to 245 business minutes (13:05 PM Monday) -> > 240 minutes (BREACHED)
    t_breach = t0 + timedelta(minutes=245)
    Clock.set_time(t_breach)
    breached_sla = await sla_tracker.update_ticket_sla(db_session, ticket)

    assert breached_sla.business_minutes_elapsed == 245
    assert breached_sla.status == "BREACHED"
    assert breached_sla.breached_at is not None


# -----------------------------------------------------------------------------
# Scenario 18 & 19: Weekend & Holiday Exclusion from SLA
# -----------------------------------------------------------------------------
def test_scenario_18_and_19_weekend_holiday_exclusion():
    """
    Scenario 18: Weekend exclusion
    Scenario 19: Holiday exclusion
    Elapsed wall-clock time over weekends and holidays does not count as business time.
    """
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")

    # Friday 17:00 PM to Monday 10:00 AM (includes full Saturday & Sunday)
    fri_17 = datetime(2026, 10, 9, 17, 0, tzinfo=tz)
    mon_10 = datetime(2026, 10, 12, 10, 0, tzinfo=tz)
    biz_mins = calendar_engine.calculate_business_minutes(fri_17, mon_10)

    # 60 mins Friday (17:00 to 18:00) + 60 mins Monday (09:00 to 10:00) = 120 mins
    assert biz_mins == 120

    # Holiday: 2026-10-02 (Gandhi Jayanti) on Friday
    # Thursday 17:00 PM to Monday 10:00 AM across holiday Friday and weekend
    thu_17 = datetime(2026, 10, 1, 17, 0, tzinfo=tz)
    mon_after_holiday = datetime(2026, 10, 5, 10, 0, tzinfo=tz)
    biz_mins_holiday = calendar_engine.calculate_business_minutes(thu_17, mon_after_holiday)

    # 60 mins Thursday (17:00 to 18:00) + Friday (Holiday = 0) + Weekend (0) + 60 mins Monday = 120 mins
    assert biz_mins_holiday == 120


# -----------------------------------------------------------------------------
# Scenario 20: Runtime SLA Configuration Change
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_20_runtime_sla_configuration_change(db_session):
    """
    Scenario 20: Runtime SLA configuration change without modifying code or restarting.
    Warning percentage dynamically shifts from 75% to 85%.
    """
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    t0 = datetime(2026, 10, 5, 9, 0, tzinfo=tz)
    Clock.set_time(t0)

    customer = User(email="runtime_sla@example.com", hashed_password="pw", role="CUSTOMER", name="Runtime SLA")
    db_session.add(customer)
    await db_session.flush()

    ticket = SupportTicket(customer_id=customer.id, title="Config Change", description="Test", priority="MEDIUM", status="OPEN", created_at=t0)
    db_session.add(ticket)
    await db_session.flush()

    # Target: 100 minutes
    sla_rec = SLARecord(ticket_id=ticket.id, target_resolution_minutes=100, business_minutes_elapsed=0, status="OK", created_at=t0)
    db_session.add(sla_rec)
    await db_session.commit()

    # Dynamic runtime modification to 85%
    dynamic_config.update_config({"sla_config": {"sla_warning_percentage": 85}})

    # At 78 minutes elapsed (78%), with default 75% this would have been a warning,
    # but with new 85% threshold, status should remain OK!
    Clock.set_time(t0 + timedelta(minutes=78))
    res78 = await sla_tracker.update_ticket_sla(db_session, ticket)
    assert res78.business_minutes_elapsed == 78
    assert res78.status == "OK"

    # At 86 minutes elapsed (86%) -> reaches updated 85% threshold -> WARNING_75
    Clock.set_time(t0 + timedelta(minutes=86))
    res86 = await sla_tracker.update_ticket_sla(db_session, ticket)
    assert res86.status == "WARNING_75"


# -----------------------------------------------------------------------------
# Masked Handoff Summary
# -----------------------------------------------------------------------------
def test_masked_handoff_summary_generation():
    """
    Requirement 5.6: Concise structured handoff summary with sensitive details masked.
    """
    summary = handoff_generator.generate_summary(
        customer_name="Johnathan Doe",
        issue_title="Duplicate charge of ₹4,999 on Visa 4111 2222 3333 4444",
        order_id="ORD-9912",
        product_id="Laptop",
        sentiment="frustrated",
        risk_type="duplicate_payment",
        priority="HIGH"
    )

    formatted = summary["formatted_summary"]
    assert "Jo****" in summary["masked_customer"]
    assert "ORD-9912" in formatted
    assert "[CARD_MASKED]" in formatted
    assert "4111 2222 3333 4444" not in formatted
    assert "Frustrated" in formatted
    assert "duplicate_payment" in formatted
