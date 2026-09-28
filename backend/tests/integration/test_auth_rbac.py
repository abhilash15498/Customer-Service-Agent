import pytest


@pytest.mark.asyncio
async def test_auth_registration_and_login_flow(client):
    # 1. Register customer
    reg_resp = await client.post("/api/v1/auth/register", json={
        "email": "customer@example.com",
        "name": "Jane Customer",
        "password": "CustomerPassword123!",
        "role": "CUSTOMER"
    })
    assert reg_resp.status_code == 201
    user_data = reg_resp.json()
    assert user_data["email"] == "customer@example.com"
    assert user_data["role"] == "CUSTOMER"

    # 2. Duplicate registration should fail
    dup_resp = await client.post("/api/v1/auth/register", json={
        "email": "customer@example.com",
        "name": "Duplicate Jane",
        "password": "Password123!",
        "role": "CUSTOMER"
    })
    assert dup_resp.status_code == 400

    # 3. Login with wrong password
    bad_login = await client.post("/api/v1/auth/login", json={
        "email": "customer@example.com",
        "password": "WrongPassword!"
    })
    assert bad_login.status_code == 401

    # 4. Successful login
    login_resp = await client.post("/api/v1/auth/login", json={
        "email": "customer@example.com",
        "password": "CustomerPassword123!"
    })
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    assert "access_token" in token_data
    token = token_data["access_token"]

    # 5. Access /me profile
    headers = {"Authorization": f"Bearer {token}"}
    me_resp = await client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "customer@example.com"


@pytest.mark.asyncio
async def test_rbac_admin_restriction(client):
    # Customer registers
    await client.post("/api/v1/auth/register", json={
        "email": "regular@example.com",
        "name": "Regular User",
        "password": "Password123!",
        "role": "CUSTOMER"
    })

    login_resp = await client.post("/api/v1/auth/login", json={
        "email": "regular@example.com",
        "password": "Password123!"
    })
    customer_token = login_resp.json()["access_token"]
    cust_headers = {"Authorization": f"Bearer {customer_token}"}

    # Attempt to update admin config as customer -> Forbidden (403)
    admin_resp = await client.put(
        "/api/v1/admin/config",
        json={"updates": {"timezone": "UTC"}},
        headers=cust_headers
    )
    assert admin_resp.status_code == 403

    # Register admin
    await client.post("/api/v1/auth/register", json={
        "email": "admin@example.com",
        "name": "System Admin",
        "password": "AdminPassword123!",
        "role": "ADMIN"
    })
    admin_login = await client.post("/api/v1/auth/login", json={
        "email": "admin@example.com",
        "password": "AdminPassword123!"
    })
    admin_token = admin_login.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Admin accesses and updates config -> OK (200)
    admin_resp2 = await client.put(
        "/api/v1/admin/config",
        json={"updates": {"timezone": "UTC"}},
        headers=admin_headers
    )
    assert admin_resp2.status_code == 200
    assert admin_resp2.json()["configuration"]["timezone"] == "UTC"
