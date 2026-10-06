import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_frontend_index_html_serving():
    """Verify that root URL serves the single-page frontend application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        content = response.text
        assert "NexusAI Enterprise Support" in content
        assert "Customer Portal" in content
        assert "Agent Dashboard" in content
        assert "Admin & DevOps" in content
        assert "file-dropzone" in content


@pytest.mark.asyncio
async def test_frontend_static_assets_serving():
    """Verify CSS and JavaScript assets are accessible."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Check index.css
        css_res = await client.get("/index.css")
        assert css_res.status_code == 200
        assert "text/css" in css_res.headers.get("content-type", "")

        # Check app.js
        js_res = await client.get("/app.js")
        assert js_res.status_code == 200
        content_type = js_res.headers.get("content-type", "")
        assert "javascript" in content_type or "text/" in content_type
        assert "switchView" in js_res.text


@pytest.mark.asyncio
async def test_backend_api_and_health_routes_preserved():
    """Verify API endpoints are not shadowed by static files."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health_res = await client.get("/health")
        assert health_res.status_code == 200
        data = health_res.json()
        assert data["status"] == "ok"
