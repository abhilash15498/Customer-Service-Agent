from datetime import datetime, time, timedelta
import zoneinfo
import pytest
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.audit import AuditLog
from app.models.conversation import Conversation
from app.models.escalation import Escalation
from app.models.sentiment import SentimentAnalysis
from app.models.sla import SLARecord
from app.models.ticket import SupportTicket, TicketAssignment, TicketEvent
from app.models.user import Agent, AgentSkill, User
from app.services.escalation.engine import escalation_engine
from app.services.routing.dispatcher import dispatch_router
from app.services.sentiment.analyzer import SentimentResult, sentiment_analyzer
from app.services.sla.calendar import calendar_engine
from app.services.sla.tracker import sla_tracker


@pytest.fixture(autouse=True)
def reset_system_state():
    """Reset clock and dynamic config to defaults before and after each test."""
    Clock.reset()
    dynamic_config.reset_to_defaults()
    yield
    Clock.reset()
    dynamic_config.reset_to_defaults()


# -----------------------------------------------------------------------------
# 1. Calendar Engine: Business Hours, Holidays, and Weekend Exclusion
# -----------------------------------------------------------------------------
def test_calendar_engine_business_hours_and_holidays():
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")

    # Regular Monday 10:30 AM -> Within business hours (09:00 - 18:00)
    monday_open = datetime(2026, 10, 5, 10, 30, tzinfo=tz)
    assert calendar_engine.is_business_hour(monday_open) is True

    # Monday 20:30 PM -> Outside business hours
    monday_closed = datetime(2026, 10, 5, 20, 30, tzinfo=tz)
    assert calendar_engine.is_business_hour(monday_closed) is False

    # Saturday 14:00 PM -> Weekend (Closed)
    saturday_closed = datetime(2026, 10, 10, 14, 0, tzinfo=tz)
    assert calendar_engine.is_business_hour(saturday_closed) is False

    # Sunday 11:00 AM -> Weekend (Closed)
    sunday_closed = datetime(2026, 10, 11, 11, 0, tzinfo=tz)
    assert calendar_engine.is_business_hour(sunday_closed) is False

    # Holiday: 2026-10-02 (Gandhi Jayanti) on Friday 11:00 AM -> Closed
    holiday_closed = datetime(2026, 10, 2, 11, 0, tzinfo=tz)
    assert calendar_engine.is_business_hour(holiday_closed) is False


def test_calendar_engine_next_business_time_and_weekend_skip():
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")

    # Friday 19:00 PM -> Next business time should be Monday 09:00 AM
    friday_night = datetime(2026, 10, 9, 19, 0, tzinfo=tz)
    next_open = calendar_engine.get_next_business_time(friday_night)
    assert next_open.year == 2026
    assert next_open.month == 10
    assert next_open.day == 12  # Monday
    assert next_open.hour == 9
    assert next_open.minute == 0


def test_calendar_engine_business_minutes_calculation():
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")

    # Monday 09:00 to Monday 12:00 -> Exactly 180 business minutes
    start = datetime(2026, 10, 5, 9, 0, tzinfo=tz)
    end = datetime(2026, 10, 5, 12, 0, tzinfo=tz)
    elapsed = calendar_engine.calculate_business_minutes(start, end)
    assert elapsed == 180

    # Friday 17:00 to Monday 10:00 -> 60 min on Friday (17:00-18:00) + 60 min on Monday (09:00-10:00) = 120 min
    start_friday = datetime(2026, 10, 9, 17, 0, tzinfo=tz)
    end_monday = datetime(2026, 10, 12, 10, 0, tzinfo=tz)
    elapsed_over_weekend = calendar_engine.calculate_business_minutes(start_friday, end_monday)
    assert elapsed_over_weekend == 120


def test_calendar_engine_add_business_minutes_over_weekend():
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")

    # Start Friday 17:00, add 120 business minutes (2 business hours)
    # Expected: 60 min Friday to 18:00, then weekend skipped, 60 min Monday 09:00 to 10:00
    start_friday = datetime(2026, 10, 9, 17, 0, tzinfo=tz)
    deadline = calendar_engine.add_business_minutes(start_friday, 120)
    assert deadline.year == 2026
    assert deadline.month == 10
    assert deadline.day == 12  # Monday
    assert deadline.hour == 10
    assert deadline.minute == 0


# -----------------------------------------------------------------------------
# 2. Dispatch Routing: Queues, After-Hours On-Call, and Agent Balancing
# -----------------------------------------------------------------------------
def test_dispatch_routing_queues_and_on_call():
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")

    # 1. During business hours -> direct queue assignment
    open_time = datetime(2026, 10, 5, 11, 0, tzinfo=tz)
    route_pay = dispatch_router.determine_queue("duplicate_payment", "CRITICAL", now=open_time)
    assert route_pay["queue"] == "payments"
    assert route_pay["routing_mode"] == "IMMEDIATE_BUSINESS_HOURS"

    route_sec = dispatch_router.determine_queue("account_compromise", "CRITICAL", now=open_time)
    assert route_sec["queue"] == "security"

    route_leg = dispatch_router.determine_queue("legal_threat", "HIGH", now=open_time)
    assert route_leg["queue"] == "legal"

    # 2. After-hours with urgent/critical issue -> routed to on_call queue
    closed_time = datetime(2026, 10, 5, 22, 0, tzinfo=tz)
    route_after_hours_crit = dispatch_router.determine_queue("account_compromise", "CRITICAL", now=closed_time)
    assert route_after_hours_crit["queue"] == "on_call"
    assert route_after_hours_crit["routing_mode"] == "AFTER_HOURS_ON_CALL"

    # 3. After-hours non-urgent -> queued for next business day
    route_after_hours_low = dispatch_router.determine_queue("general_inquiry", "LOW", now=closed_time)
    assert route_after_hours_low["queue"] == "general_support"
    assert route_after_hours_low["routing_mode"] == "SCHEDULED_NEXT_BUSINESS_DAY"
    assert route_after_hours_low["next_business_time"] is not None


@pytest.mark.asyncio
async def test_agent_skill_and_workload_matching(db_session):
    now = datetime(2026, 10, 5, 11, 0, tzinfo=zoneinfo.ZoneInfo("Asia/Kolkata"))
    Clock.set_time(now)

    # Create customer
    customer = User(email="cust_assign@example.com", hashed_password="pw", role="CUSTOMER", name="Cust Assign")
    db_session.add(customer)
    await db_session.flush()

    # Create 2 agents: Agent A (payments, workload=2), Agent B (payments, workload=0)
    user_a = User(email="agent_a@example.com", hashed_password="pw", role="AGENT", name="Agent A")
    user_b = User(email="agent_b@example.com", hashed_password="pw", role="AGENT", name="Agent B")
    db_session.add_all([user_a, user_b])
    await db_session.flush()

    agent_a = Agent(id=user_a.id, tier="tier_1", is_available=True, max_workload=5, current_workload=2)
    agent_b = Agent(id=user_b.id, tier="tier_1", is_available=True, max_workload=5, current_workload=0)
    db_session.add_all([agent_a, agent_b])
    await db_session.flush()

    skill_a = AgentSkill(agent_id=agent_a.id, skill_name="payments")
    skill_b = AgentSkill(agent_id=agent_b.id, skill_name="payments")
    db_session.add_all([skill_a, skill_b])

    # Create ticket requiring 'payments'
    ticket = SupportTicket(
        customer_id=customer.id,
        title="Double Charge Issue",
        description="Duplicate charge ₹5000",
        priority="CRITICAL",
        status="OPEN",
        required_skill="payments",
        created_at=now
    )
    db_session.add(ticket)
    await db_session.commit()

    # Run agent assignment: Agent B should be selected because workload=0 < 2
    assigned_agent = await dispatch_router.assign_agent(db_session, ticket, required_skill="payments")
    assert assigned_agent is not None
    assert assigned_agent.id == agent_b.id
    assert assigned_agent.current_workload == 1
    assert ticket.status == "ASSIGNED"


# -----------------------------------------------------------------------------
# 3. Deterministic Escalation Engine Triggers & Audit Logging
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_escalation_high_risk_account_compromise(db_session):
    now = datetime(2026, 10, 5, 11, 0, tzinfo=zoneinfo.ZoneInfo("Asia/Kolkata"))
    Clock.set_time(now)

    customer = User(email="risk_user@example.com", hashed_password="pw", role="CUSTOMER", name="Risk User")
    db_session.add(customer)
    await db_session.flush()

    conv = Conversation(customer_id=customer.id, status="ACTIVE", last_message_at=now)
    db_session.add(conv)
    await db_session.flush()

    # Trigger evaluation for account compromise
    sentiment_res = SentimentResult(
        sentiment="neutral",
        confidence=0.92,
        urgency="critical",
        risk_type="account_compromise"
    )

    decision = escalation_engine.evaluate_triggers(sentiment_res, "Someone logged into my account unauthorized.")
    assert decision.should_escalate is True
    assert decision.reason == "account_compromise"
    assert decision.priority == "CRITICAL"

    esc_res = await escalation_engine.execute_escalation(
        db=db_session,
        conversation=conv,
        decision=decision,
        summary_text="Someone logged into my account unauthorized."
    )

    assert esc_res.escalated is True
    assert esc_res.queue == "security"
    assert esc_res.ticket_id is not None

    # Verify Escalation DB row
    esc_stmt = select(Escalation).where(Escalation.conversation_id == conv.id)
    esc_row = (await db_session.execute(esc_stmt)).scalar_one()
    assert esc_row.reason == "account_compromise"
    assert esc_row.queue == "security"
    assert esc_row.is_handled is False

    # Verify SupportTicket DB row & SLA record
    t_stmt = select(SupportTicket).where(SupportTicket.id == esc_res.ticket_id).options(selectinload(SupportTicket.sla_record))
    ticket = (await db_session.execute(t_stmt)).scalar_one()
    assert ticket.priority == "CRITICAL"
    assert ticket.sla_record is not None
    assert ticket.sla_record.target_resolution_minutes == 360  # 24h * 0.25 multiplier = 6 hours = 360 min

    # Verify AuditLog DB row
    audit_stmt = select(AuditLog).where(AuditLog.entity_id == conv.id)
    audit = (await db_session.execute(audit_stmt)).scalar_one()
    assert audit.event_type == "ESCALATION"
    assert audit.details["queue"] == "security"
    assert audit.details["priority"] == "CRITICAL"


@pytest.mark.asyncio
async def test_escalation_negative_streak_threshold(db_session):
    now = datetime(2026, 10, 5, 11, 0, tzinfo=zoneinfo.ZoneInfo("Asia/Kolkata"))
    Clock.set_time(now)

    customer = User(email="streak_user@example.com", hashed_password="pw", role="CUSTOMER", name="Streak User")
    db_session.add(customer)
    await db_session.flush()

    conv = Conversation(customer_id=customer.id, status="ACTIVE", last_message_at=now)
    db_session.add(conv)
    await db_session.flush()

    # Streak of 3 frustrated messages
    sentiment_res = SentimentResult(
        sentiment="frustrated",
        confidence=0.88,
        urgency="high",
        negative_streak_count=3
    )

    decision = escalation_engine.evaluate_triggers(sentiment_res, "Still no resolution, this is awful!")
    assert decision.should_escalate is True
    assert decision.reason == "repeated_frustration"
    assert decision.priority == "HIGH"

    esc_res = await escalation_engine.execute_escalation(
        db=db_session,
        conversation=conv,
        decision=decision,
        summary_text="Still no resolution, this is awful!"
    )

    assert esc_res.escalated is True
    assert esc_res.queue == "general_support"


# -----------------------------------------------------------------------------
# 4. 15-Minute Unhandled Negative Conversation Timer
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_15_minute_unhandled_negative_timeout(db_session):
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    start_time = datetime(2026, 10, 5, 10, 0, tzinfo=tz)
    Clock.set_time(start_time)

    customer = User(email="unhandled_cust@example.com", hashed_password="pw", role="CUSTOMER", name="Unhandled Cust")
    db_session.add(customer)
    await db_session.flush()

    conv = Conversation(customer_id=customer.id, status="ACTIVE", last_message_at=start_time)
    db_session.add(conv)
    await db_session.flush()

    # Customer sent a negative message at T0
    from app.models.conversation import Message
    msg = Message(
        conversation_id=conv.id,
        sender_role="CUSTOMER",
        content="This order is completely broken!",
        masked_content="This order is completely broken!",
        created_at=start_time
    )
    db_session.add(msg)
    await db_session.flush()

    sent = SentimentAnalysis(
        message_id=msg.id,
        sentiment="frustrated",
        confidence=0.90,
        urgency="high",
        created_at=start_time
    )
    db_session.add(sent)
    await db_session.commit()

    # At T0 + 10 minutes: Check should NOT trigger escalation yet (< 15 min)
    Clock.set_time(start_time + timedelta(minutes=10))
    res_10m = await escalation_engine.check_unhandled_negative_conversations(db_session)
    assert len(res_10m) == 0

    # At T0 + 16 minutes: Clock advances past 15 min threshold -> Trigger Escalation!
    Clock.set_time(start_time + timedelta(minutes=16))
    res_16m = await escalation_engine.check_unhandled_negative_conversations(db_session)
    assert len(res_16m) == 1
    assert res_16m[0].reason == "15m_unhandled_negative"
    assert res_16m[0].activated_condition == "sla_timeout"

    # Verify Escalation record in DB
    esc_stmt = select(Escalation).where(Escalation.conversation_id == conv.id)
    esc = (await db_session.execute(esc_stmt)).scalar_one()
    assert esc.reason == "15m_unhandled_negative"
    assert esc.activated_condition == "sla_timeout"


# -----------------------------------------------------------------------------
# 5. SLA 75% Warning & Breach State Machine
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sla_warning_and_breach_tracking(db_session):
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    start_time = datetime(2026, 10, 5, 9, 0, tzinfo=tz)  # Monday 09:00 AM
    Clock.set_time(start_time)

    customer = User(email="sla_cust@example.com", hashed_password="pw", role="CUSTOMER", name="SLA Cust")
    db_session.add(customer)
    await db_session.flush()

    ticket = SupportTicket(
        customer_id=customer.id,
        title="Payment Inquiry",
        description="Check status",
        priority="MEDIUM",
        status="OPEN",
        created_at=start_time
    )
    db_session.add(ticket)
    await db_session.flush()

    # Target: 100 business minutes
    sla_rec = SLARecord(
        ticket_id=ticket.id,
        target_response_minutes=20,
        target_resolution_minutes=100,
        business_minutes_elapsed=0,
        status="OK",
        created_at=start_time
    )
    db_session.add(sla_rec)
    await db_session.commit()

    # 1. At Monday 10:00 (60 business minutes elapsed, < 75%) -> Status OK
    Clock.set_time(start_time + timedelta(minutes=60))
    updated_sla = await sla_tracker.update_ticket_sla(db_session, ticket)
    assert updated_sla.status == "OK"
    assert updated_sla.business_minutes_elapsed == 60

    # 2. At Monday 10:20 (80 business minutes elapsed, >= 75%) -> WARNING_75
    Clock.set_time(start_time + timedelta(minutes=80))
    updated_sla = await sla_tracker.update_ticket_sla(db_session, ticket)
    assert updated_sla.status == "WARNING_75"
    assert updated_sla.warning_75_sent_at is not None

    # Check AuditLog for SLA_WARNING
    audit_warn = (await db_session.execute(
        select(AuditLog).where(AuditLog.event_type == "SLA_WARNING").where(AuditLog.entity_id == ticket.id)
    )).scalar_one_or_none()
    assert audit_warn is not None

    # 3. At Monday 10:50 (110 business minutes elapsed, >= 100%) -> BREACHED
    Clock.set_time(start_time + timedelta(minutes=110))
    updated_sla = await sla_tracker.update_ticket_sla(db_session, ticket)
    assert updated_sla.status == "BREACHED"
    assert updated_sla.breached_at is not None

    # Check AuditLog for SLA_BREACH
    audit_breach = (await db_session.execute(
        select(AuditLog).where(AuditLog.event_type == "SLA_BREACH").where(AuditLog.entity_id == ticket.id)
    )).scalar_one_or_none()
    assert audit_breach is not None


# -----------------------------------------------------------------------------
# 6. End-to-End Chat API Escalation and Ticket Verification
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_end_to_end_chat_auto_escalation_flow(client, db_session):
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 10, 5, 11, 0, tzinfo=tz)
    Clock.set_time(now)

    # Register & Login
    await client.post("/api/v1/auth/register", json={
        "email": "auto_esc_user@example.com",
        "name": "Auto Esc Customer",
        "password": "Password123!",
        "role": "CUSTOMER"
    })
    token = (await client.post("/api/v1/auth/login", json={
        "email": "auto_esc_user@example.com",
        "password": "Password123!"
    })).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create conversation
    conv = (await client.post("/api/v1/conversations", headers=headers)).json()
    conv_id = conv["id"]

    # Customer sends duplicate payment message
    msg_resp = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Payment was deducted twice for order #88412."},
        headers=headers
    )
    assert msg_resp.status_code == 200
    data = msg_resp.json()

    assert data["escalated"] is True
    assert data["escalation_reason"] == "duplicate_payment"
    ticket_id = data["ticket_id"]
    assert ticket_id is not None

    # Query the generated ticket via Ticket API
    ticket_resp = await client.get(f"/api/v1/tickets/{ticket_id}", headers=headers)
    assert ticket_resp.status_code == 200
    ticket_data = ticket_resp.json()

    assert ticket_data["priority"] == "CRITICAL"
    assert ticket_data["status"] in ["OPEN", "ASSIGNED"]
    assert ticket_data["sla_record"] is not None
    assert ticket_data["sla_record"]["target_resolution_minutes"] == 360  # 6h for CRITICAL
