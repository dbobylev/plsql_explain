from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CallEdge:
    caller_subprogram: Optional[str]
    callee_schema: Optional[str]
    callee_object: str
    callee_subprogram: Optional[str]


@dataclass
class TableAccess:
    subprogram: Optional[str]
    table_schema: Optional[str]
    table_name: str
    operation: str  # 'SELECT' | 'INSERT' | 'UPDATE' | 'DELETE' | 'MERGE'


@dataclass
class SubprogramInfo:
    name: str
    subprogram_type: str  # 'PROCEDURE' | 'FUNCTION'
    start_line: int
    end_line: int
    source_text: str


@dataclass
class SubstatementInfo:
    subprogram: Optional[str]  # None for package-level init block
    seq: int
    parent_seq: Optional[int]
    position: int
    statement_type: str
    start_line: int
    end_line: int
    source_text: str


@dataclass
class ParseOutput:
    schema_name: str
    object_name: str
    object_type: str
    status: str  # 'ok' | 'wrapped' | 'error'
    error_message: Optional[str]
    call_edges: list[CallEdge] = field(default_factory=list)
    table_accesses: list[TableAccess] = field(default_factory=list)
    subprograms: list[SubprogramInfo] = field(default_factory=list)
    substatements: list[SubstatementInfo] = field(default_factory=list)
