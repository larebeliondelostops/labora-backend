from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_cors_allows_local_frontend_with_credentials() -> None:
    response = client.options(
        "/api/v1/legal-documents/current",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_allows_production_frontend_with_credentials() -> None:
    response = client.options(
        "/api/v1/legal-documents/current",
        headers={
            "Origin": "https://labora.centralspike.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "https://labora.centralspike.com"
    )
    assert response.headers["access-control-allow-credentials"] == "true"
