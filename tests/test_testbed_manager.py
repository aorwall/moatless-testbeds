import pytest
from unittest.mock import Mock, patch
from kubernetes import client
from testbed.client.manager import TestbedManager
from testbed.schema import (
    CreateTestbedResponse,
    TestbedDetailed,
    TestbedStatusDetailed,
    ContainerStatus,
)


@pytest.fixture
def mock_k8s_client():
    with patch("kubernetes.client.CoreV1Api") as mock_core_v1, patch(
        "kubernetes.client.BatchV1Api"
    ) as mock_batch_v1:
        yield mock_core_v1, mock_batch_v1


@pytest.fixture
def testbed_manager(mock_k8s_client):
    mock_core_v1, mock_batch_v1 = mock_k8s_client
    return TestbedManager(namespace="test-namespace")


def test_create_access_delete_testbed(testbed_manager, mock_k8s_client):
    mock_core_v1, mock_batch_v1 = mock_k8s_client

    # Mock the config map existence check
    mock_core_v1.return_value.read_namespaced_config_map.return_value = Mock()

    # Mock job and service creation
    mock_batch_v1.return_value.create_namespaced_job.return_value = Mock()
    mock_core_v1.return_value.create_namespaced_service.return_value = Mock()

    # Mock get_testbed responses
    running_status = TestbedStatusDetailed(
        pod_phase="Running",
        testbed=ContainerStatus(
            ready=True, started=True, restart_count=0, state="running"
        ),
        sidecar=ContainerStatus(
            ready=True, started=True, restart_count=0, state="running"
        ),
    )
    mock_get_testbed = Mock(
        return_value=TestbedDetailed(
            testbed_id="test-instance-testbed-abcde",
            instance_id="test__instance",
            status=running_status,
            external_ip="192.168.1.1",
        )
    )

    # Test create_testbed
    with patch.object(
        TestbedManager, "_generate_test_id", return_value="test-instance-testbed-abcde"
    ):
        response = testbed_manager.create_testbed("test__instance", "test_user")

    assert isinstance(response, CreateTestbedResponse)
    assert response.testbed_id == "test-instance-testbed-abcde"

    # Test create_client (which internally calls get_testbed)
    with patch.object(TestbedManager, "get_testbed", mock_get_testbed):
        client = testbed_manager.create_client("test-instance-testbed-abcde")

    assert client.testbed_id == "test-instance-testbed-abcde"
    assert client.pub_address == "tcp://192.168.1.1:5555"
    assert client.sub_address == "tcp://192.168.1.1:5556"

    # Test delete_testbed
    mock_batch_v1.return_value.delete_namespaced_job.return_value = client.V1Status(
        status="Success"
    )
    mock_core_v1.return_value.delete_namespaced_service.return_value = Mock()

    delete_response = testbed_manager.delete_testbed("test-instance-testbed-abcde")

    assert isinstance(delete_response, client.V1Status)
    assert delete_response.status == "Success"

    # Verify that the necessary methods were called
    mock_batch_v1.return_value.create_namespaced_job.assert_called_once()
    mock_core_v1.return_value.create_namespaced_service.assert_called_once()
    mock_batch_v1.return_value.delete_namespaced_job.assert_called_once()
    mock_core_v1.return_value.delete_namespaced_service.assert_called_once()
