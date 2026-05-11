from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_users_me_without_cookie_returns_401() -> None:
    response = client.get("/api/v1/users/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"
