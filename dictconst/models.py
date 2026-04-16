from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DictConstantRef:
    const_name: str


@dataclass
class DictConstantRecord:
    const_name: str
    shortname: Optional[str]
    fullname: Optional[str]


@dataclass(frozen=True)
class DictConstantUsage:
    const_name: str
    shortname: Optional[str]
    fullname: Optional[str]
    resolved_text: Optional[str]

