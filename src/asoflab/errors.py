"""Errors raised when input breaks the temporal contract."""

from collections.abc import Mapping, Sequence
from typing import Any


class ContractViolation(Exception):
    """Input contradicts the temporal contract and the run must stop.

    `code` is one of the E_ codes in docs/temporal-contract.md. `examples`
    holds a few offending records so the message is actionable.

    It deliberately does not subclass ValueError: Pydantic converts ValueError
    into its own ValidationError, which would hide the code.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        examples: Sequence[Mapping[str, Any]] = (),
        remediation: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.examples = [dict(example) for example in examples]
        self.remediation = remediation
        super().__init__(self._render())

    def _render(self) -> str:
        parts = [f"[{self.code}] {self.message}"]
        if self.examples:
            shown = "; ".join(str(example) for example in self.examples[:3])
            parts.append(f"Examples: {shown}")
        if self.remediation:
            parts.append(f"What to do: {self.remediation}")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "examples": self.examples,
            "remediation": self.remediation,
        }


class NotEstimable(Exception):
    """A statistic cannot be computed from the sample; `reason` says why."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
