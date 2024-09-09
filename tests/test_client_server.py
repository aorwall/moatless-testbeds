import pytest
from testbed.testbed.server import create_app
from testbed.client.client import TestbedClient
import threading


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def server(app):
    def run_server():
        app.run(host="localhost", port=5556)

    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    yield
    # The server will automatically shut down when the test process exits


@pytest.fixture(scope="module")
def client(server):
    return TestbedClient(testbed_id="testbed", host="localhost", port=5556)


def test_ping(client):
    assert client.ping()


def test_run_evaluation(client):
    response = client.run_evaluation("test_run")
    assert response.status_code == 200
    # Add more assertions based on the expected structure of the result


def test_ping_with_api_client(client):
    assert client.ping()


def test_run_evaluation_with_api_client(client):
    result = client.run_evaluation("test_run")
    assert result is not None
    # Add more assertions based on the expected structure of the result
