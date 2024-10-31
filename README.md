# Moatless Testbeds
Moatless Testbeds allows you to create isolated testbed environments in a Kubernetes cluster where you can apply code changes through git patches and run tests or SWE-Bench evaluations. 

While initially tested with SWE-Bench's docker containerization solution, it supports any Docker image that meets the basic requirements:

- Contains a git repository in the `/testbeds` directory for applying patches
- Supports running tests with specific commands (e.g., `pytest [path to test file]`)

***Fill out [this form](https://forms.gle/t375zSfy9D88qDJG7) if you’re interested in testing the hosted version of Moatless Testbeds.***

## Getting Started

### Initialize the SDK
```bash
pip install moatless-testbeds
```

### Run tests

```python
from testbeds.sdk import TestbedSDK

# Initialize the SDK with your credentials
sdk = TestbedSDK(
    base_url="https://testbeds.moatless.ai",  # Replace with your API URL
    api_key="<API-KEY>"
)

# Create a testbed instance and automatically handle cleanup
with sdk.create_client(instance_id="django__django-11333") as testbed:
    # Define test files to run
    test_files = [
        "tests/test_forms.py",
        "tests/test_models.py"
    ]

    # Example patch fixing a bug
    patch = """
diff --git a/django/forms/models.py b/django/forms/models.py
index abc123..def456 100644
--- a/django/forms/models.py
+++ b/django/forms/models.py
@@ -245,7 +245,7 @@ class BaseModelForm(BaseForm):
-        if self.instance and not self.instance._state.adding:
+        if self.instance and not self.instance._state.adding and not self._meta.fields:
             self._meta.fields = None
    """

    # Run the tests and get results
    result = testbed.run_tests(
        test_files=test_files,
        patch=patch
    )
    print(f"Test Status: {result.get_summary()}")
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
