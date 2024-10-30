from enum import Enum
from typing import Optional, Literal, Dict, List, Any

from pydantic import BaseModel, Field, model_validator

from testbeds.swebench.constants import ResolvedStatus


class SWEbenchInstance(BaseModel):
    repo: str = Field(..., description="Repository")
    instance_id: str = Field(..., description="Unique identifier for the instance")
    base_commit: str = Field(..., description="Base commit hash")
    patch: str | None = Field(None, description="Patch to be applied")
    test_patch: str = Field(..., description="Test patch to be applied")
    problem_statement: str = Field(..., description="Description of the problem")
    hints_text: Optional[str] = Field(
        None, description="Optional hints for solving the problem"
    )
    created_at: str = Field(..., description="Timestamp of instance creation")
    version: str = Field(..., description="Version of the instance")
    fail_to_pass: list[str] = Field(
        ..., description="List of tests expected to change from fail to pass"
    )
    pass_to_pass: list[str] = Field(
        ..., description="List of tests expected to remain passing"
    )
    environment_setup_commit: str = Field(
        ..., description="Commit hash for environment setup"
    )

    @classmethod
    @model_validator(mode="before")
    def validate_test_lists(cls, obj: Any):
        print("validate_test_lists")
        print(obj)
        if isinstance(obj, dict):
            if "FAIL_TO_PASS" in obj and isinstance(obj["FAIL_TO_PASS"], str):
                obj["fail_to_pass"] = eval(obj["FAIL_TO_PASS"])
            if "PASS_TO_PASS" in obj and isinstance(obj["PASS_TO_PASS"], str):
                obj["pass_to_pass"] = eval(obj["PASS_TO_PASS"])
        return obj


class Prediction(BaseModel):
    run_id: str = Field(..., description="Unique identifier for the prediction run")
    instance_id: str = Field(..., description="ID of the SWE-bench instance")
    patch: Optional[str] = Field(
        default=None,
        description="The patch to apply to the instance, will run gold patch if not provided",
    )


class RunEvaluationRequest(BaseModel):
    run_id: Optional[str] = Field(default=None, description="The run ID to evaluate")
    instance: SWEbenchInstance = Field(
        ..., description="The SWE-bench instance to evaluate"
    )
    patch: Optional[str] = Field(
        default=None,
        description="The patch to apply to the instance, will run gold patch if not provided",
    )
    timeout: int = Field(
        default=1800, description="Timeout for the evaluation in seconds"
    )


class RunCommandsRequest(BaseModel):
    commands: list[str] = Field(..., description="List of commands to run")
    timeout: int = Field(
        default=60,
        description="The maximum time in seconds to wait for the API response. Commands may continue running in the container after this timeout.",
    )


class CommandExecutionResponse(BaseModel):
    status: Literal["running", "completed", "idle"] = Field(
        ..., description="Status of the command execution"
    )
    output: Optional[str] = Field(None, description="Output of the command execution")


class TestStatus(str, Enum):
    FAILED = "FAILED"
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"

    def __str__(self):
        return self.value


class TraceItem(BaseModel):
    file_path: str
    method: Optional[str] = None
    line_number: Optional[int] = None
    output: str = ""


class TestResult(BaseModel):
    status: TestStatus = Field(..., description="Status of the test")
    name: str
    file_path: Optional[str] = None
    method: Optional[str] = None
    failure_output: Optional[str] = None
    stacktrace: List[TraceItem] = Field(
        default_factory=list, description="List of stack trace items"
    )

    @model_validator(mode="before")
    def convert_status_to_enum(cls, values):
        if isinstance(values.get("status"), str):
            values["status"] = TestStatus(values["status"])
        return values


class TestRunResponse(BaseModel):
    test_results: List[TestResult] = Field(..., description="List of test results")
    output: Optional[str] = Field(default=None, description="Output of the test run")

    def get_summary(self) -> str:
        passed = sum(1 for t in self.test_results if t.status == TestStatus.PASSED)
        failed = sum(1 for t in self.test_results if t.status == TestStatus.FAILED)
        skipped = sum(1 for t in self.test_results if t.status == TestStatus.SKIPPED)
        errors = sum(1 for t in self.test_results if t.status == TestStatus.ERROR)
        
        return f"Test Results: {passed} passed, {failed} failed, {skipped} skipped, {errors} errors"


class EvalTestResult(BaseModel):
    success: List[str] = Field(
        default_factory=list, description="List of successful tests"
    )
    failure: List[str] = Field(default_factory=list, description="List of failed tests")


class TestsStatus(BaseModel):
    status: ResolvedStatus = Field(..., description="Whether the problem was resolved")
    fail_to_pass: EvalTestResult = Field(
        default_factory=TestResult, description="Tests that changed from fail to pass"
    )
    pass_to_pass: EvalTestResult = Field(
        default_factory=TestResult, description="Tests that remained passing"
    )


class EvaluationResult(BaseModel):
    run_id: Optional[str] = Field(None, description="Unique identifier for the evaluation run")
    instance_id: str = Field(..., description="ID of the SWE-bench instance")
    patch_applied: bool = Field(
        default=False, description="Whether the patch was successfully applied"
    )
    resolved: bool = Field(
        default=False, description="Whether the problem was resolved"
    )
    tests_status: TestsStatus = Field(
        default_factory=TestsStatus, description="Status of all tests"
    )
    output: Optional[str] = Field(
        default=None, description="Output of the evaluation run"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "patch_applied": self.patch_applied,
            "resolved": self.resolved,
            "tests_status": self.tests_status.model_dump(),
            "output": self.output,
            "git_diff_output_before": self.git_diff_output_before,
        }


class ContainerStatus(BaseModel):
    ready: bool = Field(..., description="Whether the container is ready")
    started: bool = Field(..., description="Whether the container has started")
    restart_count: int = Field(
        ..., description="Number of times the container has been restarted"
    )
    state: Literal["running", "waiting", "terminated", "unknown"] = Field(
        ..., description="Current state of the container"
    )
    reason: Optional[str] = Field(None, description="Reason for the current state")
    message: Optional[str] = Field(
        None, description="Additional message about the container state"
    )


class TestbedStatusSummary(BaseModel):
    pod_phase: str = Field(..., description="Current phase of the pod")
    testbed_ready: bool = Field(
        ..., description="Whether the testbed container is ready"
    )
    sidecar_ready: bool = Field(
        ..., description="Whether the sidecar container is ready"
    )


class TestbedStatusDetailed(BaseModel):
    pod_phase: str = Field(..., description="Current phase of the pod")
    testbed: ContainerStatus = Field(
        ..., description="Detailed status of the testbed container"
    )
    sidecar: ContainerStatus = Field(
        ..., description="Detailed status of the sidecar container"
    )


class TestbedSummary(BaseModel):
    testbed_id: str = Field(..., description="Unique identifier for the testbed")
    instance_id: str = Field(..., description="ID of the SWE-bench instance")
    status: str = Field(..., description="Current status of the testbed")


class TestbedDetailed(BaseModel):
    testbed_id: str = Field(..., description="Unique identifier for the testbed")
    instance_id: str = Field(..., description="ID of the SWE-bench instance")
    status: TestbedStatusDetailed = Field(
        ..., description="Detailed status of the testbed"
    )
    external_ip: Optional[str] = Field(
        None, description="External IP address of the testbed, if available"
    )


class CreateTestbedRequest(BaseModel):
    instance_id: str = Field(
        ..., description="ID of the SWE-bench instance to create a testbed for"
    )


class CreateTestbedResponse(BaseModel):
    testbed_id: str = Field(
        ..., description="Unique identifier for the created testbed"
    )


class GetTestbedResponse(BaseModel):
    testbed_id: str = Field(..., description="Unique identifier for the testbed")
    status: TestbedDetailed = Field(..., description="Detailed status of the testbed")


class CommandExecutionSummary(BaseModel):
    execution_id: str = Field(
        ..., description="Unique identifier for the command execution"
    )
    status: Literal["running", "completed"] = Field(
        ..., description="Status of the command execution"
    )
    commands: List[str] = Field(..., description="List of commands executed")
    output_file: str = Field(
        ..., description="Path to the file containing the command output"
    )
