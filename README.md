# Client-Testbed Architecture with Kubernetes and ZeroMQ

## Overview

This document describes the architecture of the Client-Testbed system, which utilizes Kubernetes for orchestration and ZeroMQ for communication. The system is designed to create isolated evaluation environments (testbeds) and facilitate communication between clients and these testbeds.

## Key Components

1. TestbedManager
2. TestbedClient
3. Server
4. ZeroMQCommunicator
5. Testbed

## Kubernetes Integration

The system leverages Kubernetes for managing and orchestrating testbed environments:

- **TestbedManager**: Interacts with the Kubernetes API to create and manage testbed instances.
- **Kubernetes Job**: Each testbed is created as a Kubernetes Job, ensuring the testbed runs to completion.
- **Kubernetes Service**: A LoadBalancer service is created for each testbed to expose the ZeroMQ ports to external clients.
- **Containers**:
  - Testbed Container: Runs the actual evaluation code.
  - Sidecar Container: Handles communication and manages the testbed lifecycle.

## ZeroMQ Communication

ZeroMQ is used for efficient, asynchronous communication between the client and the testbed. The system uses two patterns:

1. **REQ-REP Pattern**: Used for ping-pong communication to check connectivity.
   - **REQ (Client)**: Sends ping requests.
   - **REP (Server)**: Responds with pong messages.

2. **PUB-SUB Pattern**: Used for run_evaluation and result communication.
   - **Publisher (PUB)**: The server (testbed) publishes evaluation results.
   - **Subscriber (SUB)**: The client subscribes to messages from a specific testbed.

## Workflow

### 1. Testbed Creation

```python
def create_testbed(self, instance_id: str, user_id: str | None = None) -> CreateTestbedResponse:
    # ... (code to create Kubernetes Job and Service)
    return CreateTestbedResponse(testbed_id=testbed_id)
```

- The TestbedManager creates a Kubernetes Job and Service for a new testbed.
- The Job runs two containers: the testbed and the sidecar.
- The Service exposes the ZeroMQ ports (5555 for PUB, 5556 for SUB).

### 2. Client Connection

```python
def create_client(self, testbed_id: str, timeout: float = 30) -> TestbedClient:
    # ... (code to create TestbedClient)
    return TestbedClient(testbed_id=testbed_id, pub_address=pub_address, sub_address=sub_address)
```

- The TestbedManager creates a TestbedClient with the external IP and ports of the testbed's Service.
- The TestbedClient initializes a ZeroMQCommunicator to handle messaging.

### 3. Communication Flow
The client and server communicate using ZeroMQ's PUB-SUB pattern, with an additional ping-pong mechanism for checking connectivity.

#### Client to Server (Ping):

```python
def ping(self, timeout=30):
    self.communicator.send_message(message_type="ping", data={})
    start_time = time.time()
    while time.time() - start_time < timeout:
        messages = self.communicator.receive_messages()
        for message in messages:
            if message.type == "pong":
                return True
        time.sleep(1)
    return False
```

1. The client sends a "ping" message to the server.
2. It then waits for a "pong" response, with a specified timeout.

#### Server to Client (Pong):

```python
def process_message(self, message):
    if message.type == "ping":
        self.communicator.send_message(Message(type="pong", body={}))
        logger.info("Sent pong response.")
    # ... handle other message types
``` 

1. The server receives the "ping" message.
2. It immediately responds with a "pong" message.

This ping-pong mechanism allows the client to check if the server is responsive and the communication channel is working correctly.

### ZeroMQ Communicator
Both client and server use the same ZeroMQCommunicator class for sending and receiving messages:

```python
class ZeroMQCommunicator(Communicator):
    def __init__(self, testbed_id: str, pub_address: str, sub_address: str):
        # ... initialization code ...

    def send_message(self, message_type: str, data: dict[str, Any]) -> None:
        self.pub_socket.send_multipart([
            self.testbed_id.encode(),
            message_type.encode(),
            json.dumps(data).encode()
        ])

    def receive_messages(self) -> Iterable[Message]:
        messages = []
        while self.sub_socket.poll(timeout=100):
            testbed_id, message_type, data = self.sub_socket.recv_multipart()
            if testbed_id.decode() == self.testbed_id:
                messages.append(Message(
                    message_type.decode(),
                    json.loads(data.decode())
                ))
        return messages
```

This implementation allows for bidirectional communication:
- The client's PUB socket connects to the server's SUB socket.
- The server's PUB socket connects to the client's SUB socket.

By using the same ZeroMQCommunicator class for both client and server, we ensure consistent message formatting and handling. The testbed_id is used to filter messages, ensuring that each client only receives messages intended for it.

This approach demonstrates how the client and server can communicate effectively using ZeroMQ, with the ping-pong mechanism serving as a concrete example of the message exchange process.
