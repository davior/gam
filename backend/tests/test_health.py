def test_health_is_public(client):
    """The container healthcheck calls this without credentials."""
    response = client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert isinstance(body["ffmpeg"], bool)
