import json
import re
from datetime import datetime
from math import ceil, isfinite
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReviewDecisionLiteral = Literal["approved", "rejected", "revision_requested"]
RejectionReasonLiteral = Literal[
    "illegible",
    "mismatched_gstin",
    "expired",
    "name_mismatch",
    "incomplete_pages",
    "invalid_document_type",
    "other",
]

MAX_VERIFIED_DATA_BYTES = 65_536  # 64 KB
MAX_ROOT_KEYS = 50
MAX_NESTING_DEPTH = 5  # Root dictionary counts as depth 1
MAX_TOTAL_NODES = 200
MAX_DICT_KEYS = 50
MAX_LIST_ITEMS = 100
VALID_KEY_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_verified_data_payload(data: Any) -> dict[str, Any]:
    """Recursively validate verified_data structure, size, nesting, and value types."""
    if not isinstance(data, dict):
        raise TypeError("verified_data root must be a JSON object (dictionary).")

    # 1. Byte-size and JSON validity using explicit UTF-8 serialization
    try:
        serialized = json.dumps(data, allow_nan=False, ensure_ascii=False)
        byte_length = len(serialized.encode("utf-8"))
    except TypeError as exc:
        raise ValueError(f"Unsupported value type in verified_data: {exc}") from exc
    except ValueError as exc:
        err_msg = str(exc)
        if (
            "Out of range float values" in err_msg
            or "nan" in err_msg.lower()
            or "inf" in err_msg.lower()
        ):
            raise ValueError(
                "Non-finite float values (NaN, Infinity) are forbidden in verified_data."
            ) from exc
        raise ValueError(f"verified_data serialization failed: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"verified_data serialization error: {exc}") from exc

    if byte_length > MAX_VERIFIED_DATA_BYTES:
        raise ValueError(
            f"verified_data serialized size ({byte_length} bytes) exceeds "
            f"maximum limit of {MAX_VERIFIED_DATA_BYTES} bytes."
        )

    # 2. Root keys limit
    if len(data) > MAX_ROOT_KEYS:
        raise ValueError(
            f"verified_data root dictionary cannot exceed {MAX_ROOT_KEYS} keys."
        )

    # 3. Recursive inspection for depth, total nodes, valid keys, and primitive leaf values
    node_count = 0

    def _traverse(node: Any, current_depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > MAX_TOTAL_NODES:
            raise ValueError(
                f"verified_data exceeds maximum total node count of {MAX_TOTAL_NODES}."
            )

        if current_depth > MAX_NESTING_DEPTH:
            raise ValueError(
                f"verified_data exceeds maximum nesting depth of {MAX_NESTING_DEPTH}."
            )

        if isinstance(node, dict):
            if len(node) > MAX_DICT_KEYS:
                raise ValueError(
                    f"Dictionary in verified_data exceeds maximum allowed keys ({MAX_DICT_KEYS})."
                )
            for k, v in node.items():
                if not isinstance(k, str) or not VALID_KEY_REGEX.match(k):
                    raise ValueError(
                        f"Invalid dictionary key '{k}'. Keys must be 1-64 alphanumeric, dash, or underscore characters."
                    )
                _traverse(v, current_depth + 1)
        elif isinstance(node, list):
            if len(node) > MAX_LIST_ITEMS:
                raise ValueError(
                    f"List in verified_data exceeds maximum allowed items ({MAX_LIST_ITEMS})."
                )
            for item in node:
                _traverse(item, current_depth + 1)
        elif isinstance(node, bool):
            pass  # Valid boolean (must be checked before int as bool subclasses int)
        elif isinstance(node, int):
            pass  # Valid integer
        elif isinstance(node, float):
            if not isfinite(node):
                raise ValueError(
                    "Non-finite float values (NaN, Infinity) are forbidden in verified_data."
                )
        elif isinstance(node, str):
            pass  # Valid string
        elif node is None:
            pass  # Valid JSON null
        else:
            raise ValueError(
                f"Unsupported value type '{type(node).__name__}' in verified_data. "
                "Only primitives (str, int, float, bool, None) and containers (dict, list) are permitted."
            )

    try:
        _traverse(data, current_depth=1)
    except RecursionError as exc:
        raise ValueError("verified_data exceeded recursion limit.") from exc

    return data


class DocumentReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecisionLiteral
    comments: Annotated[str | None, Field(default=None, max_length=2000)] = None
    rejection_reason_code: RejectionReasonLiteral | None = None
    verified_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("verified_data", mode="after")
    @classmethod
    def validate_verified_data_field(cls, v: Any) -> dict[str, Any]:
        return validate_verified_data_payload(v)

    @field_validator("comments", mode="after")
    @classmethod
    def validate_comments_whitespace(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def validate_decision_policy(self) -> "DocumentReviewCreate":
        if (
            self.decision in ("rejected", "revision_requested")
            and not self.rejection_reason_code
        ):
            raise ValueError(
                f"rejection_reason_code is mandatory when decision is '{self.decision}'."
            )
        if self.decision == "approved" and self.rejection_reason_code is not None:
            raise ValueError(
                "rejection_reason_code must be null when decision is 'approved'."
            )
        return self


class DocumentReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    document_id: UUID
    reviewer_id: UUID
    decision: str
    comments: str | None
    rejection_reason_code: str | None
    verified_data: dict[str, Any]
    created_at: datetime


class DocumentReviewListResponse(BaseModel):
    items: list[DocumentReviewResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(
        cls,
        items: list[DocumentReviewResponse],
        total: int,
        page: int,
        page_size: int,
    ) -> "DocumentReviewListResponse":
        total_pages = ceil(total / page_size) if total > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
