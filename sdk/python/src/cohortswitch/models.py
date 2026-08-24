from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    flag_key: str
    value: Any
    variation: str
    reason: str
    matched_rule: str | None
    config_version: int
    evaluated_at: datetime

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "EvaluationResult":
        return cls(
            flag_key=str(payload["flag_key"]),
            value=payload["value"],
            variation=str(payload["variation"]),
            reason=str(payload["reason"]),
            matched_rule=payload.get("matched_rule"),
            config_version=int(payload["config_version"]),
            evaluated_at=datetime.fromisoformat(
                str(payload["evaluated_at"]).replace("Z", "+00:00")
            ),
        )
