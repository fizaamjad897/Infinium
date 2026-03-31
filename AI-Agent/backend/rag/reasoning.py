"""
Reasoning Trace Module
- Captures step-by-step reasoning for transparency
- Records timing, models used, and intermediate outputs
- Enables the "View Reasoning Trace" UI feature (FR-007)
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReasoningStep:
    """A single step in the reasoning process."""
    step_name: str                    # e.g., "retrieval", "code_reasoning", "synthesis"
    model_used: str = ""              # e.g., "kimi-k2.5:cloud"
    duration_ms: int = 0              # how long this step took
    input_preview: str = ""           # first 200 chars of input
    output_preview: str = ""          # first 500 chars of output
    chunks_count: int = 0             # number of chunks involved
    status: str = "pending"           # pending, running, complete, error

    def to_dict(self) -> dict:
        return {
            "step_name": self.step_name,
            "model_used": self.model_used,
            "duration_ms": self.duration_ms,
            "input_preview": self.input_preview,
            "output_preview": self.output_preview,
            "chunks_count": self.chunks_count,
            "status": self.status,
        }


@dataclass
class ReasoningTrace:
    """Complete reasoning trace for a query."""
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    query: str = ""
    steps: list = field(default_factory=list)
    total_duration_ms: int = 0
    models_used: list = field(default_factory=list)
    _start_time: float = field(default=0, repr=False)

    def start(self, query: str):
        """Start timing the trace."""
        self.query = query
        self._start_time = time.time()

    def add_step(
        self,
        step_name: str,
        model_used: str = "",
        input_text: str = "",
        output_text: str = "",
        chunks_count: int = 0,
        duration_ms: int = 0,
        status: str = "complete",
    ) -> ReasoningStep:
        """Add a completed step to the trace."""
        step = ReasoningStep(
            step_name=step_name,
            model_used=model_used,
            duration_ms=duration_ms,
            input_preview=input_text[:200] if input_text else "",
            output_preview=output_text[:500] if output_text else "",
            chunks_count=chunks_count,
            status=status,
        )
        self.steps.append(step)
        if model_used and model_used not in self.models_used:
            self.models_used.append(model_used)
        return step

    def finish(self):
        """Finalize the trace with total duration."""
        if self._start_time:
            self.total_duration_ms = int((time.time() - self._start_time) * 1000)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "query": self.query,
            "steps": [s.to_dict() for s in self.steps],
            "total_duration_ms": self.total_duration_ms,
            "models_used": self.models_used,
        }


class StepTimer:
    """Context manager for timing a reasoning step."""

    def __init__(self, trace: ReasoningTrace, step_name: str, model_used: str = ""):
        self.trace = trace
        self.step_name = step_name
        self.model_used = model_used
        self._start = 0
        self.output_text = ""
        self.input_text = ""
        self.chunks_count = 0
        self.status = "complete"

    def __enter__(self):
        self._start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = int((time.time() - self._start) * 1000)
        if exc_type:
            self.status = "error"
            self.output_text = str(exc_val)[:500] if exc_val else "Unknown error"
        self.trace.add_step(
            step_name=self.step_name,
            model_used=self.model_used,
            input_text=self.input_text,
            output_text=self.output_text,
            chunks_count=self.chunks_count,
            duration_ms=duration_ms,
            status=self.status,
        )
        return False  # Don't suppress exceptions
