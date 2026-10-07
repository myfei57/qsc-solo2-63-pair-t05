"""Cross-terminal baggage handoff.

Two terminals share one baggage namespace. A bag only changes owner when the
receiving terminal confirms the handoff sheet; an interrupted handoff rolls
back to the terminal that opened it. Every call carries the acting terminal,
operator and stated purpose, all of which land in the tamper-evident audit
chain.
"""

from __future__ import annotations

OWNERSHIP_RULE = "on_confirmation"
OWNERSHIP_REASON = (
    "归属在接收终端确认接收时转移：交接须双方确认才算完成，"
    "对方未确认不算交接成功；交接中断时行李仍归发起终端负责。"
    "未采用进入流转区即转移归属的做法，否则交接中断会由尚未确认接收的"
    "对方终端承担责任，与双方确认规则相反。"
)

from .desk import HandoffDesk
from .entities import (
    CarouselRecord,
    HandoffEvent,
    HandoffSheet,
    OwnershipDecision,
    Principal,
)
from .sheet_text import render_sheet

__all__ = [
    "CarouselRecord",
    "HandoffDesk",
    "HandoffEvent",
    "HandoffSheet",
    "OWNERSHIP_REASON",
    "OWNERSHIP_RULE",
    "OwnershipDecision",
    "Principal",
    "render_sheet",
]
