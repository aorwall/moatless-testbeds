# Moatless Testbeds
Moatless Testbeds allows you to create isolated testbed environments in a Kubernetes cluster where you can apply code changes through git patches and run tests or SWE-Bench evaluations. 

While initially tested with SWE-Bench's docker containerization solution, it supports any Docker image that meets the basic requirements:

- Contains a git repository in the `/testbeds` directory for applying patches
- Supports running tests with specific commands (e.g., `pytest [path to test file]`)


#### Usage Example

```python
from testbeds.sdk import TestbedSDK

sdk = TestbedSDK(
    base_url="https://testbeds.moatless.ai", # Replace with your API URL
    api_key="<API-KEY>"
)

with sdk.create_client(instance_id="<INSTANCE-ID>") as testbed:
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
pip install moatless-testbeds

# Set the Kubernetes namespace if not default
# export KUBERNETES_NAMESPACE=testbeds  # default: testbeds

# Optional: Set environment variables only if using custom images
# If not set, will use default public images
# export DOCKER_REGISTRY=your-registry  # default: aorwall

# Optional: Enable direct command execution in testbeds
# Warning: This allows arbitrary command execution and should be used with caution
# export ENABLE_EXEC=true  # default: false

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
python scripts/run_tests.py --instance-id <instance-id> [--test-files test1.py test2.py ...]
```

For example:

```bash
# Run with test_patch files
python scripts/run_tests.py --instance-id django__django-11333

# Run specific test files
python scripts/run_tests.py --instance-id django__django-11333 --test-files tests/test_forms.py tests/test_models.py
```

The script will:
1. Create a new testbed instance
2. Run the specified tests or fall back to the test_patch files if no tests are specified
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
