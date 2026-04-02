from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TableRef:
    schema_name: str
    table_name: str


@dataclass
class TableMetadataRecord:
    schema_name: str
    table_name: str
    object_type: Optional[str]
    table_comment: Optional[str]


@dataclass
class ColumnMetadataRecord:
    schema_name: str
    table_name: str
    column_name: str
    column_id: Optional[int]
    data_type: Optional[str]
    nullable: Optional[bool]
    column_comment: Optional[str]

