from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TableAccessInfo:
    table_schema: Optional[str]
    table_name: str
    operation: str  # SELECT | INSERT | UPDATE | DELETE | MERGE


@dataclass
class DependencyNode:
    schema_name: str
    object_name: str
    object_type: Optional[str]          # None when object not in object_source
    subprogram: Optional[str]           # None = standalone or package-level
    status: str                         # ok | wrapped | error | missing | cycle
    error_message: Optional[str]
    table_accesses: list[TableAccessInfo] = field(default_factory=list)
    children: list[DependencyNode] = field(default_factory=list)
