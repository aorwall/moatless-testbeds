from testbed.schema import TestResult, TestStatus, TestRunResponse


def test_deserialize_test_result():
    result = TestResult(name="test", status=TestStatus.FAILED)

    response = TestRunResponse(test_results=[result])
    print(response.model_dump_json(indent=2))

    serialized = response.model_dump()
    deserialized = TestRunResponse.model_validate(serialized)
    assert deserialized == response
