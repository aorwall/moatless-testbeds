# Moatless Testbeds
Moatless Testbeds enables you to run testbeds as isolated pods in a Kubernetes cluster, orchestrated through a central API.

While initially tested with SWE-Bench's docker containerization solution, it supports any Docker image that meets the basic requirements:

- Contains a git repository in the `/testbeds` directory for applying patches
- Supports running tests with specific commands (e.g., `pytest [path to test file]`)

#### Usage Example

```python
from moatless_testbeds import TestbedSDK

sdk = TestbedSDK(
    base_url="http://<API-IP>",
    api_key="<API-KEY>"
)

testbed = sdk.create_client(instance_id="<INSTANCE-ID>")
testbed.wait_until_ready()

test_files = ["path/to/test_file.py"]
result = testbed.run_tests(test_files)

print(result.model_dump_json(indent=2))
```

## Installation

### Prerequisites

- Docker installed and configured
- kubectl configured with access to your Kubernetes cluster
- envsubst utility installed

### Installation Steps

The easiest way to install is using the provided install script:

```bash
# Clone the repository
git clone https://github.com/aorwall/moatless-testbeds.git
cd moatless-testbeds

# Install Testbeds SDK
pip install -e .

# Optional: Set environment variables only if using custom images
# If not set, will use default public images
# export KUBERNETES_NAMESPACE=testbeds  # default: testbeds
# export DOCKER_REGISTRY=your-registry  # default: aorwall

# Run the install script
./scripts/install.sh
```

The API will be available at `http://<EXTERNAL-IP>`.

## Run evaluation

The evaluation script allows you to test gold patches and verify that your setup is working correctly.

### Prerequisites

Make sure you have the following environment variables set:
- `TESTBED_API_IP`: The IP address of your API service
- `NAMESPACE`: The Kubernetes namespace where the API is deployed (default: testbeds)
- `TESTBED_API_KEY`: Your API key (if API key authentication is enabled)

You can source these from the installation:

```bash
source .env.testbed
```

### Running Evaluation

To run an evaluation:

```bash
python scripts/run_evaluation.py --instance-id <instance-id>
```

For example:
```bash
python scripts/run_evaluation.py --instance-id django__django-11333
```

The script will:
1. Create a new testbed instance
2. Run the evaluation using the specified instance ID with the gold patch
3. Output the evaluation results in JSON format
4. Clean up the testbed instance

A successful run will show "✅ Evaluation completed successfully!" in the logs. Any errors during execution will be logged with detailed information.

### Run tests

```bash
python scripts/run_tests.py --instance-id <instance-id>
```

For example:

```bash
python scripts/run_tests.py --instance-id django__django-11333
```

The script will:
1. Create a new testbed instance
2. Run the tests using the specified instance ID with the files specified in the instance's `test_patch`
3. Output the test results in JSON format
4. Clean up the testbed instance

## Architecture

The solution consists of three core components:

### 1. Orchestrating API

- Deployed as a central service in the Kubernetes cluster
- Manages testbed jobs and pods lifecycle
- Provides endpoints for command execution in testbeds
- Handles pod creation and deletion

### 2. Testbeds

Testbeds are composed of two parts:
- **Main Testbed Image**: Contains the test environment and code
- **Sidecar Container**: Exposes a simple HTTP API with four endpoints:
  - Command execution
  - File management (save/retrieve)
  - Status polling

The command execution flow is straightforward:
1. Send command via `POST /exec`
2. Poll status via `GET /exec` until completion

### 3. SDK

The SDK provides a simple interface to interact with the API. It handles:
- Testbed creation and management
- Command execution
- Test running and evaluation

#### Test Execution Flow
1. Start or reset testbed (recommended: new testbed for each test run)
2. Apply code changes as git patches
3. Run tests using specified commands
4. Parse test output into TestResult objects
5. Generate evaluation reports comparing against FAIL_TO_PASS and PASS_TO_PASS tests
