import dataclasses
import json
import os
from typing import Any, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class ConcurrentStats:
    """Statistics for concurrent operations"""
    _active_setups: int = 0
    _active_tests: int = 0
    _active_evals: int = 0
    _max_concurrent_setups: int = 0
    _max_concurrent_tests: int = 0
    _max_concurrent_evals: int = 0
    _active_requests: int = 0
    _max_concurrent_requests: int = 0
    
    def increment_setup(self):
        self._active_setups += 1
        self._max_concurrent_setups = max(self._max_concurrent_setups, self._active_setups)
    
    def decrement_setup(self):
        self._active_setups = max(0, self._active_setups - 1)
    
    def increment_test(self):
        self._active_tests += 1
        self._max_concurrent_tests = max(self._max_concurrent_tests, self._active_tests)
    
    def decrement_test(self):
        self._active_tests = max(0, self._active_tests - 1)
    
    def increment_eval(self):
        self._active_evals += 1
        self._max_concurrent_evals = max(self._max_concurrent_evals, self._active_evals)
    
    def decrement_eval(self):
        self._active_evals = max(0, self._active_evals - 1)

    def increment_request(self):
        self._active_requests += 1
        self._max_concurrent_requests = max(self._max_concurrent_requests, self._active_requests)
    
    def decrement_request(self):
        self._active_requests = max(0, self._active_requests - 1)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "current": {
                "setups": self._active_setups,
                "tests": self._active_tests,
                "evals": self._active_evals,
                "requests": self._active_requests,
            },
            "max": {
                "setups": self._max_concurrent_setups,
                "tests": self._max_concurrent_tests,
                "evals": self._max_concurrent_evals,
                "requests": self._max_concurrent_requests,
            }
        }

@dataclasses.dataclass
class EvalMetrics:
    """Metrics for evaluation operations"""
    setup_start_time: float = 0.0
    setup_end_time: float = 0.0
    ready_start_time: float = 0.0
    ready_end_time: float = 0.0
    test_metrics: Dict[str, Dict[str, float]] = dataclasses.field(default_factory=dict)
    eval_metrics: Dict[str, Dict[str, float]] = dataclasses.field(default_factory=dict)
    
    @property
    def setup_duration(self) -> float:
        """Time taken for testbed setup in seconds"""
        if not (self.setup_start_time and self.setup_end_time):
            return 0.0
        return self.setup_end_time - self.setup_start_time
    
    @property
    def ready_duration(self) -> float:
        """Time taken for testbed to become ready in seconds"""
        if not (self.ready_start_time and self.ready_end_time):
            return 0.0
        return self.ready_end_time - self.ready_start_time
    
    @property
    def total_setup_duration(self) -> float:
        """Total time from setup start to ready in seconds"""
        if not (self.setup_start_time and self.ready_end_time):
            return 0.0
        return self.ready_end_time - self.setup_start_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "setup_duration": self.setup_duration,
            "ready_duration": self.ready_duration,
            "total_setup_duration": self.total_setup_duration,
            "test_metrics": self.test_metrics,
            "eval_metrics": self.eval_metrics,
            "timestamps": {
                "setup_start": datetime.fromtimestamp(self.setup_start_time).isoformat() if self.setup_start_time else None,
                "setup_end": datetime.fromtimestamp(self.setup_end_time).isoformat() if self.setup_end_time else None,
                "ready_start": datetime.fromtimestamp(self.ready_start_time).isoformat() if self.ready_start_time else None,
                "ready_end": datetime.fromtimestamp(self.ready_end_time).isoformat() if self.ready_end_time else None,
            }
        }

@dataclasses.dataclass
class TestbedMetrics:
    """Metrics for testbed operations"""
    instance_id: str | None = None  # Instance ID associated with these metrics
    testbed_id: str | None = None  # Testbed ID associated with these metrics
    eval_metrics: EvalMetrics = dataclasses.field(default_factory=EvalMetrics)
    concurrent_stats: ConcurrentStats = dataclasses.field(default_factory=ConcurrentStats)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to a dictionary"""
        return {
            "instance_id": self.instance_id,
            "testbed_id": self.testbed_id,
            **self.eval_metrics.to_dict(),
            "concurrent_stats": self.concurrent_stats.to_dict()
        }

    def save_to_file(self, log_dir: str | None = None) -> str | None:
        """Save metrics to a JSON file in the log directory if one is set"""
        if not log_dir:
            return None
            
        # Generate filename with timestamp and IDs
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        instance_part = f"_{self.instance_id}" if self.instance_id else ""
        testbed_part = f"_{self.testbed_id}" if self.testbed_id else ""
        filename = f"metrics_{timestamp}{instance_part}{testbed_part}.json"
        
        filepath = os.path.join(log_dir, filename)
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Saved metrics to {filepath}")
        return filepath

    # Delegate concurrent operation methods
    def increment_setup(self): self.concurrent_stats.increment_setup()
    def decrement_setup(self): self.concurrent_stats.decrement_setup()
    def increment_test(self): self.concurrent_stats.increment_test()
    def decrement_test(self): self.concurrent_stats.decrement_test()
    def increment_eval(self): self.concurrent_stats.increment_eval()
    def decrement_eval(self): self.concurrent_stats.decrement_eval()
    def increment_request(self): self.concurrent_stats.increment_request()
    def decrement_request(self): self.concurrent_stats.decrement_request()