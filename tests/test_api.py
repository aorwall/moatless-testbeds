import json
import pytest
from unittest.mock import patch, MagicMock
from testbed.api.main import create_app
import os


@pytest.fixture
def app():
    api_keys = {"valid-key": "test-user", "another-key": "another-user"}
    with open("test_api_keys.json", "w") as f:
        json.dump(api_keys, f)

    os.environ["API_KEYS_PATH"] = "test_api_keys.json"
    app = create_app()
    app.config["TESTING"] = True
    yield app

    # Cleanup
    os.remove("test_api_keys.json")
    os.environ.pop("API_KEYS_PATH", None)


@pytest.fixture
def client(app):
    return app.test_client()


def test_http_exception_handler(client):
    """Test handling of HTTP exceptions (like 404)"""
    response = client.get("/nonexistent-endpoint", headers={"X-API-Key": "valid-key", "accept": "application/json"})
    print(response.data)
    assert response.status_code == 404

    data = json.loads(response.data)
    assert "error_id" in data
    assert data["code"] == 404
    assert data["name"] == "Not Found"


def test_unknown_exception_handler(client):
    """Test handling of unexpected exceptions"""
    with patch("testbed.api.manager.TestbedManager.get_testbed") as mock_get_testbed:
        mock_get_testbed.side_effect = Exception("Unexpected database error")

        response = client.get("/testbeds/test-id", headers={"X-API-Key": "valid-key"})
        assert response.status_code == 500

        data = json.loads(response.data)
        assert "reference_code" in data
        assert data["error"] == "An unexpected error occurred"


def test_cleanup_exception_handler(client):
    """Test handling of exceptions during cleanup"""
    with patch(
        "testbed.api.manager.TestbedManager.cleanup_user_resources"
    ) as mock_cleanup:
        mock_cleanup.side_effect = Exception("Database connection failed")

        response = client.post("/cleanup", headers={"X-API-Key": "valid-key"})
        assert response.status_code == 500

        data = json.loads(response.data)
        assert data["error"] == "Database connection failed"


def test_api_key_validation(client):
    """Test API key validation"""
    # Test with valid API key
    response = client.get("/testbeds", headers={"X-API-Key": "valid-key"})
    assert response.status_code == 200

    # Test with invalid API key
    response = client.get("/testbeds", headers={"X-API-Key": "invalid-key"})
    assert response.status_code == 401
    data = json.loads(response.data)
    assert data["error"] == "Invalid API key"

    # Test with missing API key
    response = client.get("/testbeds")
    assert response.status_code == 401
