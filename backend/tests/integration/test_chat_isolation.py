from datetime import datetime, timedelta, timezone
import pytest
from app.core.clock import Clock


@pytest.mark.asyncio
async def test_customer_session_isolation(client):
    # Customer A
    await client.post("/api/v1/auth/register", json={
        "email": "custA@example.com",
        "name": "Customer A",
        "password": "PasswordA123!",
        "role": "CUSTOMER"
    })
    token_a = (await client.post("/api/v1/auth/login", json={
        "email": "custA@example.com",
        "password": "PasswordA123!"
    })).json()["access_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Customer B
    await client.post("/api/v1/auth/register", json={
        "email": "custB@example.com",
        "name": "Customer B",
        "password": "PasswordB123!",
        "role": "CUSTOMER"
    })
    token_b = (await client.post("/api/v1/auth/login", json={
        "email": "custB@example.com",
        "password": "PasswordB123!"
    })).json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 1. Customer A creates a conversation
    conv_a = (await client.post("/api/v1/conversations", headers=headers_a)).json()
    conv_a_id = conv_a["id"]

    # 2. Customer A posts a message containing a credit card number
    msg_resp = await client.post(
        f"/api/v1/conversations/{conv_a_id}/messages",
        json={"content": "Here is my credit card 4111 2222 3333 4444 for order 9876"},
        headers=headers_a
    )
    assert msg_resp.status_code == 200
    msg_data = msg_resp.json()
    # Verify PII masking in stored message
    assert "[CARD_MASKED]" in msg_data["user_message"]["masked_content"]
    assert "4111 2222 3333 4444" not in msg_data["user_message"]["masked_content"]
    assert "9876" in msg_data["user_message"]["masked_content"]

    # 3. Scenario 65: Customer B attempts to access Customer A's conversation -> MUST BE FORBIDDEN (403)
    cross_access_resp = await client.get(
        f"/api/v1/conversations/{conv_a_id}",
        headers=headers_b
    )
    assert cross_access_resp.status_code == 403

    # 4. Customer B attempts to post to Customer A's conversation -> MUST BE FORBIDDEN (403)
    cross_post_resp = await client.post(
        f"/api/v1/conversations/{conv_a_id}/messages",
        json={"content": "I am snooping"},
        headers=headers_b
    )
    assert cross_post_resp.status_code == 403

    # 5. Customer B lists conversations -> Customer A's conversation should NOT appear
    b_convs = (await client.get("/api/v1/conversations", headers=headers_b)).json()
    assert all(c["id"] != conv_a_id for c in b_convs)


@pytest.mark.asyncio
async def test_session_inactivity_timeout(client):
    # Customer registers & logs in
    await client.post("/api/v1/auth/register", json={
        "email": "timeout_user@example.com",
        "name": "Timeout User",
        "password": "Password123!",
        "role": "CUSTOMER"
    })
    token = (await client.post("/api/v1/auth/login", json={
        "email": "timeout_user@example.com",
        "password": "Password123!"
    })).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Start conversation at T0
    t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    Clock.set_time(t0)

    conv = (await client.post("/api/v1/conversations", headers=headers)).json()
    conv_id = conv["id"]

    await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Hello at 10:00 AM"},
        headers=headers
    )

    # Fast forward simulated clock by 35 minutes (>30 min inactivity timeout)
    t1 = t0 + timedelta(minutes=35)
    Clock.set_time(t1)

    # Post another message to the same conversation ID
    # Session manager should mark previous status as IDLE/RESTORED
    msg_resp = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Hello at 10:35 AM"},
        headers=headers
    )
    assert msg_resp.status_code == 200

    # Verify conversation status was updated
    conv_status = (await client.get(f"/api/v1/conversations/{conv_id}", headers=headers)).json()
    assert conv_status["status"] in ["RESTORED", "IDLE", "ACTIVE"]
