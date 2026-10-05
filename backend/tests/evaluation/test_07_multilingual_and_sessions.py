from datetime import timedelta, timezone
import pytest
from sqlalchemy.future import select

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.conversation import Conversation, ConversationSummary, Message
from app.models.ticket import SupportTicket
from app.services.sessions.correction import correction_detector
from app.services.sessions.intent import intent_classifier
from app.services.sessions.language import language_processor
from app.services.sessions.manager import session_manager


@pytest.fixture(autouse=True)
def reset_system_state():
    Clock.reset()
    dynamic_config.reset_to_defaults()
    yield
    Clock.reset()
    dynamic_config.reset_to_defaults()


async def get_customer_headers(client, email="customer@example.com", name="Test Customer"):
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": name,
        "password": "Password123!",
        "role": "CUSTOMER"
    })
    login_resp = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "Password123!"
    })
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# -----------------------------------------------------------------------------
# Scenario 54: Three Supported Additional Languages Beyond English
# -----------------------------------------------------------------------------
def test_scenario_54_supported_additional_languages():
    """
    Scenario 54: Supports at least 3 additional languages beyond English.
    Verifies Kannada, Hindi, Spanish, French, German detection with high confidence.
    """
    # 1. Kannada
    kn_res = language_processor.detect_language("ನನ್ನ ಆರ್ಡರ್ ಇನ್ನೂ ಬಂದಿಲ್ಲ, ದಯವಿಟ್ಟು ಸಹಾಯ ಮಾಡಿ.")
    assert kn_res.primary_language == "kn"
    assert kn_res.confidence >= 0.90
    assert kn_res.low_confidence is False

    # 2. Hindi
    hi_res = language_processor.detect_language("मेरा पार्सल अभी तक नहीं आया है, कृपया जांच करें।")
    assert hi_res.primary_language == "hi"
    assert hi_res.confidence >= 0.90
    assert hi_res.low_confidence is False

    # 3. Spanish
    es_res = language_processor.detect_language("Hola, necesito ayuda para cancelar mi pedido y solicitar un reembolso.")
    assert es_res.primary_language == "es"
    assert es_res.confidence >= 0.85
    assert es_res.low_confidence is False

    # 4. French
    fr_res = language_processor.detect_language("Bonjour, j'ai un problème avec ma commande et je souhaite un remboursement.")
    assert fr_res.primary_language == "fr"
    assert fr_res.confidence >= 0.85

    # 5. German
    de_res = language_processor.detect_language("Guten Tag, wo ist meine Lieferung? Ich brauche Hilfe mit der Rechnung.")
    assert de_res.primary_language == "de"
    assert de_res.confidence >= 0.85


# -----------------------------------------------------------------------------
# Scenario 55: Mixed-Language Message (Code-Switching)
# -----------------------------------------------------------------------------
def test_scenario_55_mixed_language_code_switching():
    """
    Scenario 55: Mixed-language input (Code-switching).
    'Nanna order #4521 innu bandilla, what should I do?'
    System detects mixed languages and locks critical entities (Order #4521).
    """
    mixed_msg = "Nanna order #4521 innu bandilla, what should I do?"
    res = language_processor.detect_language(mixed_msg)

    assert res.is_mixed_language is True
    assert "kn-Latn" in res.detected_languages or "kn" in res.detected_languages
    assert "en" in res.detected_languages

    # Verify Entity Locking (Req 9.3)
    order_locks = [l for l in res.protected_entities if l.entity_type == "ORDER_ID"]
    assert len(order_locks) >= 1
    assert "4521" in order_locks[0].raw_text

    # Unlock restores exact text
    unlocked = language_processor.unlock_entities(res.sanitized_text, res.protected_entities)
    assert "#4521" in unlocked


# -----------------------------------------------------------------------------
# Scenario 56: Language Switching Across Multiple Turns
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_56_language_switching_across_turns(client):
    """
    Scenario 56: Language switching across conversation turns.
    Turn 1 (English): Customer establishes order #4521.
    Turn 2 (Kannada): Customer asks about delivery.
    Turn 3 (Spanish): Customer asks where is the package.
    The system maintains entity context (order 4521) across all language switches.
    """
    headers = await get_customer_headers(client, "lang_switch@example.com")

    # Start conversation
    conv_res = await client.post("/api/v1/conversations", headers=headers)
    conv_id = conv_res.json()["id"]

    # Turn 1: English
    t1_res = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "I need help with my order #4521."},
        headers=headers
    )
    assert t1_res.status_code == 200
    d1 = t1_res.json()
    assert d1["confirmed_entities"].get("order_id") == "4521"
    assert d1["language_detected"] == "en"

    # Turn 2: Switch to Kannada script
    t2_res = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "ಇದು ಯಾವಾಗ ಡೆಲಿವರಿ ಆಗುತ್ತದೆ?"},
        headers=headers
    )
    assert t2_res.status_code == 200
    d2 = t2_res.json()
    assert d2["language_detected"] == "kn"
    # Entity memory persisted!
    assert d2["confirmed_entities"].get("order_id") == "4521"

    # Turn 3: Switch to Spanish
    t3_res = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "¿Dónde está mi paquete? Por favor ayuda."},
        headers=headers
    )
    assert t3_res.status_code == 200
    d3 = t3_res.json()
    assert d3["language_detected"] == "es"
    assert d3["confirmed_entities"].get("order_id") == "4521"


# -----------------------------------------------------------------------------
# Scenario 57: Transliteration
# -----------------------------------------------------------------------------
def test_scenario_57_transliterated_input():
    """
    Scenario 57: Transliterated input (Romanized Hindi/Kannada).
    Detects transliteration and maintains correct entity extraction.
    """
    hinglish_msg = "Mera refund abhi tak nahi aaya, order #8821 tha"
    res = language_processor.detect_language(hinglish_msg)

    assert res.is_transliterated is True
    assert res.primary_language == "hi-Latn"
    assert res.low_confidence is False
    assert any("8821" in l.raw_text for l in res.protected_entities)


# -----------------------------------------------------------------------------
# Scenario 58: Spelling Errors & Typos
# -----------------------------------------------------------------------------
def test_scenario_58_spelling_errors_and_typos():
    """
    Scenario 58: Spelling errors.
    Customer writes with typos: 'Plz process my refunnd for ordr #4521'.
    Entity extraction and intent detection remain resilient.
    """
    typo_msg = "Plz process my refunnd for ordr #4521, paymnt was debited."

    # Entity extraction tolerates 'ordr #4521'
    lang_res = language_processor.detect_language(typo_msg)
    order_locks = [l for l in lang_res.protected_entities if l.entity_type == "ORDER_ID"]
    assert len(order_locks) >= 1
    assert "4521" in order_locks[0].raw_text

    # Intent classification tolerates 'refunnd'
    intent_res = intent_classifier.analyze_intent(typo_msg)
    assert "refund_request" in intent_res.all_intents
    assert intent_res.low_confidence is False


# -----------------------------------------------------------------------------
# Scenario 59: Low Language Confidence
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_59_low_language_confidence(client):
    """
    Scenario 59: Ambiguous language / low confidence.
    Random gibberish triggers a polite clarification prompt rather than guessing.
    """
    headers = await get_customer_headers(client, "low_lang@example.com")
    conv_res = await client.post("/api/v1/conversations", headers=headers)
    conv_id = conv_res.json()["id"]

    gibberish = "qwprtz klmnbv xjkhyt 9921"
    msg_res = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": gibberish},
        headers=headers
    )
    assert msg_res.status_code == 200
    data = msg_res.json()
    assert data["language_detected"] == "unknown"
    # Assistant politely asks for clarification
    assert "clarify" in data["assistant_message"]["content"].lower() or "language" in data["assistant_message"]["content"].lower()


# -----------------------------------------------------------------------------
# Scenario 60: Low Intent Confidence
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_60_low_intent_confidence(client):
    """
    Scenario 60: Low intent confidence.
    Ambiguous or greeting-only input triggers clarification options instead of false actions.
    """
    headers = await get_customer_headers(client, "low_intent@example.com")
    conv_res = await client.post("/api/v1/conversations", headers=headers)
    conv_id = conv_res.json()["id"]

    vague_msg = "Hello, help me"
    msg_res = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": vague_msg},
        headers=headers
    )
    assert msg_res.status_code == 200
    data = msg_res.json()
    assert data["escalated"] is False
    assert "clarify" in data["assistant_message"]["content"].lower() or "assistance" in data["assistant_message"]["content"].lower()


# -----------------------------------------------------------------------------
# Scenario 61: Multiple Requests in One Message (Compound Intents)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_61_multiple_requests_compound_intent(client):
    """
    Scenario 61: Multiple requests in one message.
    'My order is late and I was charged twice.'
    Decomposes into:
    1. order delay
    2. duplicate payment (triggers high-risk escalation)
    """
    headers = await get_customer_headers(client, "multi_intent@example.com")
    conv_res = await client.post("/api/v1/conversations", headers=headers)
    conv_id = conv_res.json()["id"]

    compound_msg = "My order #4521 is late and I was charged twice for ₹2,499."
    msg_res = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": compound_msg},
        headers=headers
    )
    assert msg_res.status_code == 200
    data = msg_res.json()

    assert data["is_compound_intent"] is True
    assert "order_delay" in data["detected_intents"]
    assert "duplicate_payment" in data["detected_intents"]
    # Duplicate payment triggers auto-escalation!
    assert data["escalated"] is True
    assert data["escalation_reason"] == "duplicate_payment"
    assert data["ticket_id"] is not None


# -----------------------------------------------------------------------------
# Scenario 62: Corrected Information (Self-Correction)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_62_corrected_information(client):
    """
    Scenario 62: Self-correction mid-conversation.
    Turn 1: 'My order number is 4521.'
    Turn 2: 'Sorry, I meant 4251.'
    The system replaces 4521 with 4251 and confirms the update.
    """
    headers = await get_customer_headers(client, "correction_cust@example.com")
    conv_res = await client.post("/api/v1/conversations", headers=headers)
    conv_id = conv_res.json()["id"]

    # Turn 1
    t1_res = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "My order number is 4521."},
        headers=headers
    )
    assert t1_res.json()["confirmed_entities"].get("order_id") == "4521"

    # Turn 2: Correction
    t2_res = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Sorry, I meant 4251."},
        headers=headers
    )
    assert t2_res.status_code == 200
    d2 = t2_res.json()
    assert d2["confirmed_entities"].get("order_id") == "4251"
    assert "4251" in d2["assistant_message"]["content"]
    assert "updated" in d2["assistant_message"]["content"].lower() or "updating" in d2["assistant_message"]["content"].lower()


# -----------------------------------------------------------------------------
# Scenario 63: Session Inactivity Expiry (30 Minutes)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_63_session_inactivity_expiry(client, db_session):
    """
    Scenario 63: Session expiry after 30 minutes of inactivity.
    Advancing clock by 35 minutes marks session IDLE / RESTORED.
    """
    headers = await get_customer_headers(client, "idle_cust@example.com")
    start_time = Clock.now()

    conv_res = await client.post("/api/v1/conversations", headers=headers)
    conv_id = conv_res.json()["id"]

    # Send message at T0
    await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Hello, need help with my account."},
        headers=headers
    )

    # Advance clock by 35 minutes (> 30 minute timeout)
    Clock.set_time(start_time + timedelta(minutes=35))

    # Next interaction triggers inactivity check
    user_stmt = select(Conversation).where(Conversation.id == conv_id)
    conv_db = (await db_session.execute(user_stmt)).scalar_one()

    # Re-evaluate through session manager
    updated_conv = await session_manager.get_or_create_conversation(
        db_session,
        customer_id=conv_db.customer_id,
        conversation_id=conv_id
    )
    assert updated_conv.status in ["IDLE", "RESTORED"]


# -----------------------------------------------------------------------------
# Scenario 64: Session Restoration (Within 24h vs After 24h)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_64_session_restoration(client, db_session):
    """
    Scenario 64: Session restoration within 24 hours vs expiration after 24 hours.
    - Within 24h: Generates compact summary context rather than dumping raw messages.
    - After 24h: Closes stale session and starts new session.
    """
    headers = await get_customer_headers(client, "restore_cust@example.com")
    start_time = Clock.now()

    conv_res = await client.post("/api/v1/conversations", headers=headers)
    conv_id = conv_res.json()["id"]

    # Add message
    await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "My order #9901 is delayed. Please help."},
        headers=headers
    )

    # 1. Advance 2 hours (within 24 hours) -> Session restored with summary
    Clock.set_time(start_time + timedelta(hours=2))

    res_within_24 = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Are there any updates on my order?"},
        headers=headers
    )
    assert res_within_24.status_code == 200

    # Verify ConversationSummary was generated
    sum_stmt = select(ConversationSummary).where(ConversationSummary.conversation_id == conv_id)
    sum_db = (await db_session.execute(sum_stmt)).scalars().first()
    assert sum_db is not None
    assert "9901" in sum_db.summary_text or "delayed" in sum_db.summary_text.lower()

    # 2. Advance 26 hours (> 24 hours from initial start) -> Fresh session created
    Clock.set_time(start_time + timedelta(hours=28))

    conv_stmt = select(Conversation).where(Conversation.id == conv_id)
    conv_db = (await db_session.execute(conv_stmt)).scalar_one()

    conv_after_28 = await session_manager.get_or_create_conversation(
        db_session,
        customer_id=conv_db.customer_id,
        conversation_id=conv_id
    )
    # The old session is closed and a new session is returned
    assert conv_after_28.id != conv_id
    assert conv_after_28.status == "ACTIVE"


# -----------------------------------------------------------------------------
# Scenario 65: Simultaneous Customer Sessions & Tenant Isolation
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_65_simultaneous_customer_sessions_and_isolation(client):
    """
    Scenario 65: Simultaneous customer sessions & Tenant isolation.
    Customer A cannot access Customer B's sessions (HTTP 403).
    Customer A can maintain simultaneous independent active conversations.
    """
    headers_a = await get_customer_headers(client, "cust_a@example.com", "Customer A")
    headers_b = await get_customer_headers(client, "cust_b@example.com", "Customer B")

    # Customer A creates conversation 1
    c1_a = (await client.post("/api/v1/conversations", headers=headers_a)).json()
    # Customer A creates conversation 2 (simultaneous active session)
    c2_a = (await client.post("/api/v1/conversations", headers=headers_a)).json()

    assert c1_a["id"] != c2_a["id"]

    # Customer A posts to conversation 1
    await client.post(
        f"/api/v1/conversations/{c1_a['id']}/messages",
        json={"content": "Message in session 1 for order 1111."},
        headers=headers_a
    )
    # Customer A posts to conversation 2
    await client.post(
        f"/api/v1/conversations/{c2_a['id']}/messages",
        json={"content": "Message in session 2 for order 2222."},
        headers=headers_a
    )

    # Customer B attempts to access Customer A's conversation 1 -> 403 Forbidden!
    b_access_c1 = await client.get(f"/api/v1/conversations/{c1_a['id']}", headers=headers_b)
    assert b_access_c1.status_code == 403

    # Customer B attempts to post to Customer A's conversation 1 -> 403 Forbidden!
    b_post_c1 = await client.post(
        f"/api/v1/conversations/{c1_a['id']}/messages",
        json={"content": "Malicious intrusion message"},
        headers=headers_b
    )
    assert b_post_c1.status_code == 403
