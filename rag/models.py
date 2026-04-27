from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RagDocument:
    chunk_id: str
    source_kind: str
    chunk_type: str
    schema_name: str
    object_name: str
    object_type: str = ""
    subprogram: str = ""
    title: str = ""
    summary_text: str = ""
    content_text: str = ""
    code_text: str = ""
    parent_chunk_id: Optional[str] = None
    node_id: str = ""
    run_id: str = ""
    start_line: int = 0
    end_line: int = 0
    source_hash: str = ""
    prompt_version: str = ""
    metadata_json: str = "{}"
