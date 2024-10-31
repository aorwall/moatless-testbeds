class TestbedError(Exception):
    """Base exception for all testbed-related errors"""
    def __init__(self, message: str, error_code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}

class TestbedConnectionError(TestbedError):
    """Raised when there are connection issues with the testbed"""
    pass

class TestbedTimeoutError(TestbedError):
    """Raised when operations timeout"""
    pass

class TestbedAuthenticationError(TestbedError):
    """Raised when there are authentication issues"""
    pass

class TestbedValidationError(TestbedError):
    """Raised when there are validation issues with the input"""
    pass 