import pytest
import time
import threading
import zmq
import logging
from testbed.client.comm.zeromq_communicator import ZeroMQCommunicator
from testbed.client.comm.communicator import Message
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_server(server_communicator, event):
    logger.info("Server started")

    def request_handler(request):
        logger.info(f"Server received request: {request.type}")
        if request.type == "ping":
            return Message("pong", {})
        elif request.type == "run_prediction":
            # Simulate running a prediction
            time.sleep(1)
            return Message("evaluation_finished", {"result": "success"})
        else:
            return Message("error", {"error": "Unknown request type"})

    with server_communicator:
        start_time = time.time()
        while not event.is_set():
            server_communicator.handle_requests(request_handler, timeout=100)
            # Publish a message every 0.5 seconds
            current_time = time.time()
            if current_time - start_time > 0.5:
                server_communicator.send_message(
                    "server_update", {"timestamp": current_time}
                )
                start_time = current_time
                logger.info("Server sent update message")

    logger.info("Server stopped")


def test_zeromq_communicator():
    testbed_id = "test_testbed"

    # Server setup
    server_pubsub_port = 5556
    server_req_port = 5557
    server_pubsub_address = f"tcp://*:{server_pubsub_port}"
    server_req_address = f"tcp://*:{server_req_port}"
    server_communicator = ZeroMQCommunicator(
        testbed_id, server_pubsub_address, server_req_address, is_server=True
    )

    # Client setup
    client_pubsub_address = (
        f"tcp://localhost:{server_pubsub_port}"  # Connect to server's SUB
    )
    client_req_address = f"tcp://localhost:{server_req_port}"  # Connect to server's REQ
    client_communicator = ZeroMQCommunicator(
        testbed_id, client_pubsub_address, client_req_address, is_server=False
    )

    # Start server in a separate thread
    event = threading.Event()
    server_thread = threading.Thread(
        target=run_server, args=(server_communicator, event)
    )
    server_thread.start()

    time.sleep(0.5)
    logger.info("Starting client communication")

    with client_communicator:
        logger.info("Verifying connection")
        assert client_communicator.verify_connection(), "Failed to verify connection"
        logger.info("Connection verified")

        # Test ping-pong (REQ-REP)
        logger.info("Testing ping-pong")
        try:
            response = client_communicator.send_request(Message("ping", {}))
            assert response.type == "pong", f"Expected pong, got {response.type}"
            logger.info("Ping-pong test passed")
        except Exception as e:
            logger.error(f"Ping-pong test failed: {str(e)}")
            raise

        # Test run_prediction (REQ-REP)
        logger.info("Testing run_prediction")
        response = client_communicator.send_request(
            Message("run_prediction", {"run_id": "test_run"})
        )
        assert (
            response.type == "evaluation_finished"
        ), f"Expected evaluation_finished, got {response.type}"
        assert (
            response.body["result"] == "success"
        ), f"Expected success, got {response.body['result']}"
        logger.info("Run prediction test passed")

        # Test PUB-SUB
        logger.info("Testing PUB-SUB")
        received_messages = []
        start_time = time.time()
        while time.time() - start_time < 0.5:
            messages = client_communicator.receive_messages()
            received_messages.extend(messages)
            if messages:
                logger.info(f"Received {len(messages)} messages")
            time.sleep(0.1)

        assert (
            len(received_messages) > 0
        ), f"No messages received from server in {time.time() - start_time} seconds"
        for message in received_messages:
            assert (
                message.type == "server_update"
            ), f"Expected server_update, got {message.type}"
            assert "timestamp" in message.body, "Timestamp not found in message body"
        logger.info(f"Received {len(received_messages)} server updates")
        logger.info("PUB-SUB test passed")

    # Stop the server thread
    logger.info("Stopping server")
    event.set()
    server_thread.join()

    logger.info("Test completed successfully")


if __name__ == "__main__":
    pytest.main([__file__])
