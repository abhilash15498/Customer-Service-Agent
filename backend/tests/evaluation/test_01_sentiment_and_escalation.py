import pytest
from app.services.sentiment.analyzer import sentiment_analyzer
from app.services.sentiment.sarcasm import sarcasm_detector
from app.services.sentiment.tone import tone_adapter
from app.services.sessions.language import language_processor


# -----------------------------------------------------------------------------
# Scenario 1: Sarcastic Customer Message
# -----------------------------------------------------------------------------
def test_scenario_1_context_aware_sarcasm():
    """
    Scenario 1: Sarcastic customer message
    Message 1: 'Hello, I need help with my order.'
    Message 2: 'I have already contacted support.'
    Message 3: 'Still nothing has happened.'
    Message 4: 'Great service.'
    In context, 'Great service' must be classified as SARCASTIC, not genuine positive.
    """
    history = [
        "Hello, I need help with my order.",
        "I have already contacted support.",
        "Still nothing has happened."
    ]

    # Without history, "Great service." might look positive
    isolated_res = sentiment_analyzer.analyze_message("Great service.")
    assert isolated_res.sentiment == "positive"

    # In context of unresolved complaints, it is SARCASTIC
    contextual_res = sentiment_analyzer.analyze_message("Great service.", recent_history=history)
    assert contextual_res.sarcasm is True
    assert contextual_res.sentiment == "sarcastic"
    assert contextual_res.confidence >= 0.85

    # Check intra-sentence sarcasm: "Thanks for nothing"
    intra_res = sentiment_analyzer.analyze_message("Thanks for nothing, you charged me twice!")
    assert intra_res.sarcasm is True


# -----------------------------------------------------------------------------
# Scenario 2: Calm Account-Compromise Complaint
# -----------------------------------------------------------------------------
def test_scenario_2_calm_account_compromise():
    """
    Scenario 2: Calm account-compromise complaint
    'I believe someone has accessed my account.'
    Must be detected as HIGH RISK ('account_compromise') even though the language is calm/neutral.
    Risk detection and sentiment detection are strictly separate concepts.
    """
    msg = "I believe someone has accessed my account."
    res = sentiment_analyzer.analyze_message(msg)

    # Risk detection must be triggered
    assert res.risk_type == "account_compromise"
    # Urgency must be elevated
    assert res.urgency in ["high", "critical"]
    # Sentiment may remain neutral/calm
    assert res.sentiment == "neutral"


# -----------------------------------------------------------------------------
# Scenario 3: Duplicate Payment
# -----------------------------------------------------------------------------
def test_scenario_3_duplicate_payment_risk():
    """
    Scenario 3: Duplicate payment
    'Payment deducted twice for order 123.' -> Trigger high-risk 'duplicate_payment'.
    """
    msg = "Payment was deducted twice for order 123."
    res = sentiment_analyzer.analyze_message(msg)

    assert res.risk_type == "duplicate_payment"
    assert res.urgency in ["high", "critical"]


# -----------------------------------------------------------------------------
# Scenario 4: Legal Threat
# -----------------------------------------------------------------------------
def test_scenario_4_legal_threat_risk():
    """
    Scenario 4: Legal threat
    'I will be contacting my lawyer and taking legal action if this is not resolved.'
    Must trigger high-risk 'legal_threat'.
    """
    msg = "I will be contacting my lawyer and filing a consumer court case against you."
    res = sentiment_analyzer.analyze_message(msg)

    assert res.risk_type == "legal_threat"
    assert res.urgency in ["high", "critical"]


# -----------------------------------------------------------------------------
# Scenario 5: Repeated Negative Messages (Streak Tracking)
# -----------------------------------------------------------------------------
def test_scenario_5_repeated_negative_streak():
    """
    Scenario 5: Repeated negative messages
    Tracks consecutive negative/frustrated messages across history.
    """
    history_streak_2 = [
        "This is terrible service.",
        "Nobody has replied and I am frustrated."
    ]
    current_msg = "Still waiting, completely unacceptable!"

    res = sentiment_analyzer.analyze_message(current_msg, recent_history=history_streak_2)
    assert res.sentiment == "frustrated"
    # Current message (1) + 2 past negative messages = streak of 3
    assert res.negative_streak_count == 3


# -----------------------------------------------------------------------------
# Multilingual Detection & Strict Entity Preservation
# -----------------------------------------------------------------------------
def test_multilingual_code_switching_and_entity_preservation():
    """
    Validates Kannada script detection, transliteration, and entity preservation.
    Translation/processing must NEVER corrupt order IDs, amounts, or phone numbers.
    """
    # Mixed English + Kannada
    mixed_msg = "Nanna order #4521 innu bandilla, amount ₹24,999 was debited. What should I do?"
    lang_res = language_processor.detect_language(mixed_msg)

    # Detects Kannada transliteration and/or English
    assert lang_res.is_mixed_language is True

    # Entity preservation: order #4521 and ₹24,999 are locked
    locked_text, locks = language_processor.lock_entities(mixed_msg)
    assert "#4521" not in locked_text
    assert "₹24,999" not in locked_text
    assert "__ENTITY_LOCK_ORDER_ID_0__" in locked_text
    assert "__ENTITY_LOCK_AMOUNT_1__" in locked_text

    # Unlock restores exact text
    restored = language_processor.unlock_entities(locked_text, locks)
    assert "#4521" in restored
    assert "₹24,999" in restored


# -----------------------------------------------------------------------------
# Tone Adaptation
# -----------------------------------------------------------------------------
def test_tone_adaptation_directives():
    """
    Requirement 16: Tone Adaptation
    - Sarcastic: Do NOT respond sarcastically. Remain professional and address underlying issue.
    - Frustrated: Deeply empathetic, calm, do NOT invent unapproved discounts.
    """
    sarcastic_directive = tone_adapter.get_system_directive("sarcastic")
    assert "Do NOT mirror their sarcasm" in sarcastic_directive
    assert "address the underlying issue" in sarcastic_directive

    frustrated_directive = tone_adapter.get_system_directive("frustrated")
    assert "empathetic" in frustrated_directive.lower()
    assert "Do NOT invent unapproved refunds" in frustrated_directive


# -----------------------------------------------------------------------------
# End-to-End Chat API Sentiment & Risk Response
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chat_api_sentiment_and_escalation_flag(client, db_session):
    """
    Tests that POST /conversations/{id}/messages executes sentiment analysis,
    stores sentiment record in DB, and sets escalation flag on high-risk input.
    """
    # Register & Login
    await client.post("/api/v1/auth/register", json={
        "email": "sentiment_cust@example.com",
        "name": "Sentiment Customer",
        "password": "Password123!",
        "role": "CUSTOMER"
    })
    token = (await client.post("/api/v1/auth/login", json={
        "email": "sentiment_cust@example.com",
        "password": "Password123!"
    })).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    conv = (await client.post("/api/v1/conversations", headers=headers)).json()

    # Customer sends calm account compromise message
    msg_resp = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"content": "I believe someone has accessed my account unauthorized."},
        headers=headers
    )
    assert msg_resp.status_code == 200
    data = msg_resp.json()

    # Verify SentimentRead payload in user message
    user_sent = data["user_message"]["sentiment"]
    assert user_sent is not None
    assert user_sent["risk_type"] == "account_compromise"
    assert data["escalated"] is True
    assert data["escalation_reason"] == "account_compromise"
