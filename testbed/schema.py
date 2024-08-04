from datetime import datetime
from typing import Optional, Literal, Dict, List, Any

from pydantic import BaseModel, Field


class Prediction(BaseModel):
    run_id: str
    instance_id: str
    patch: Optional[str] = Field(
        default=None,
        description="The patch to apply to the instance, will run gold patch if not provided",
    )


class TestResult(BaseModel):
    success: List[str] = Field(default_factory=list)
    failure: List[str] = Field(default_factory=list)


class TestsStatus(BaseModel):
    FAIL_TO_PASS: TestResult = Field(default_factory=TestResult)
    PASS_TO_PASS: TestResult = Field(default_factory=TestResult)
    FAIL_TO_FAIL: TestResult = Field(default_factory=TestResult)
    PASS_TO_FAIL: TestResult = Field(default_factory=TestResult)


class EvaluationResult(BaseModel):
    instance_id: str
    patch_applied: bool = False
    resolved: bool = False
    tests_status: TestsStatus = Field(default_factory=TestsStatus)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "patch_applied": self.patch_applied,
            "resolved": self.resolved,
            "tests_status": self.tests_status.model_dump(),
        }


class ContainerStatus(BaseModel):
    ready: bool
    started: bool
    restart_count: int
    state: Literal["running", "waiting", "terminated", "unknown"]
    reason: Optional[str] = None
    message: Optional[str] = None


class TestbedStatusSummary(BaseModel):
    pod_phase: str
    testbed_ready: bool
    sidecar_ready: bool


class TestbedStatusDetailed(BaseModel):
    pod_phase: str
    testbed: ContainerStatus
    sidecar: ContainerStatus


class TestbedSummary(BaseModel):
    testbed_id: str
    instance_id: str
    status: TestbedStatusSummary


class TestbedDetailed(BaseModel):
    testbed_id: str
    instance_id: str
    status: TestbedStatusDetailed
    external_ip: Optional[str] = None


class CreateTestbedRequest(BaseModel):
    instance_id: str


class CreateTestbedResponse(BaseModel):
    testbed_id: str


class GetTestbedResponse(BaseModel):
    testbed_id: str
    status: TestbedDetailed
