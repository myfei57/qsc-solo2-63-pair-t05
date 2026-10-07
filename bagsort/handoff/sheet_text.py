"""Plain-text rendering of a handoff sheet.

The sheet is the document both terminals point at when a transfer is
disputed, so it states the ownership rule and the reason for choosing it,
not only the status fields.
"""

from __future__ import annotations

from .entities import HandoffEvent, HandoffSheet


def _line(title: str, value: object) -> str:
    return f"{title:<14}: {value}\n"


def _event_line(event: HandoffEvent) -> str:
    actor = f"{event.principal.terminal}/{event.principal.operator}"
    purpose = event.principal.purpose
    return f"  {event.sequence}. [{event.at}] {event.kind} by {actor} ({purpose})\n"


def render_sheet(sheet: HandoffSheet) -> str:
    lines = [
        "CROSS-TERMINAL BAGGAGE HANDOFF SHEET\n",
        _line("handoff", sheet.id),
        _line("bag", sheet.bag_id),
        _line("from", sheet.from_terminal),
        _line("to", sheet.to_terminal),
        _line("status", sheet.status.value),
        _line("opened_at", sheet.opened_at),
        _line("confirmed_at", sheet.confirmed_at or "-"),
        _line("interrupted_at", sheet.interrupted_at or "-"),
        _line("interrupt_reason", sheet.interrupt_reason or "-"),
        "\nOWNERSHIP RULE\n",
        _line("rule", sheet.ownership_rule.value),
        "reason        :\n",
    ]
    lines.extend(f"    {part}\n" for part in _wrap(sheet.rule_reason))
    lines.append("\nCONFIRMATIONS (both sides required)\n")
    for event in sheet.events:
        lines.append(_event_line(event))
    return "".join(lines)


def _wrap(text: str, width: int = 60) -> list[str]:
    words = (text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines
