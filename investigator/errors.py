"""Structured errors for run.py (SPEC.md section 4.4).

An InvestigatorError carries a code, the pipeline stage, a message, a retryable flag and free-form details.
The runner turns it into the error envelope {run_id, status: "error", error: {...}} after flushing the logs.
Uncaught exceptions inside an agent become E_INTERNAL (never "partial"): see runner.py.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

ErrorCode = Literal["E_INPUT", "E_TIMEOUT", "E_NO_SOURCES", "E_GENERATOR", "E_ZETARIS_REQUIRED", "E_INTERNAL"]
ERROR_CODES: tuple[str, ...] = ("E_INPUT", "E_TIMEOUT", "E_NO_SOURCES", "E_GENERATOR", "E_ZETARIS_REQUIRED", "E_INTERNAL")


class InvestigatorError(Exception):
    """Error with a stable code and a stage name; serialisable with to_json()."""

    def __init__(self, code: ErrorCode, stage: str, message: str, retryable: bool = False,
                 details: Optional[dict[str, Any]] = None) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(f"{code} at {stage}: {message}")
        self.code: ErrorCode = code
        self.stage = stage
        self.message = message
        self.retryable = retryable
        self.details: dict[str, Any] = dict(details or {})

    def to_json(self) -> dict[str, Any]:
        return {"code": self.code, "stage": self.stage, "message": self.message,
                "retryable": self.retryable, "details": self.details}
