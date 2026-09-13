"""Every error comes back as {"detail": {"code", "message"}}.

FastAPI's own handler passes a plain-string detail through unchanged, which would give
two different error shapes depending on how a route raised. The frontend has one error
handler, so the backend needs one shape.
"""


def test_unauthorized_uses_the_envelope(client):
    response = client.get("/api/me")
    assert response.status_code == 401

    detail = response.json()["detail"]
    assert detail["code"] == "unauthorized"
    assert detail["message"]


def test_not_found_uses_the_envelope(client):
    """A route FastAPI itself rejects, raised with a bare string, still normalises."""
    response = client.get("/api/no-such-route")
    assert response.status_code == 404

    detail = response.json()["detail"]
    assert detail["code"] == "not_found"
    assert detail["message"]
