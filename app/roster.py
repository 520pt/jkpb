from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from enum import Enum


class Shift(str, Enum):
    EARLY = "early"
    MIDDLE = "middle"
    NIGHT = "night"

    @property
    def start_time(self) -> time:
        return {
            Shift.EARLY: time(0, 0),
            Shift.MIDDLE: time(8, 0),
            Shift.NIGHT: time(16, 0),
        }[self]

    @property
    def end_time(self) -> time:
        return {
            Shift.EARLY: time(8, 0),
            Shift.MIDDLE: time(16, 0),
            Shift.NIGHT: time(0, 0),
        }[self]

    @property
    def label(self) -> str:
        return {
            Shift.EARLY: "早班",
            Shift.MIDDLE: "中班",
            Shift.NIGHT: "夜班",
        }[self]


def normalize_shift_code(value: str | None) -> Shift | None:
    code = (value or "").strip()
    return {
        "早": Shift.EARLY,
        "中": Shift.MIDDLE,
        "晚": Shift.NIGHT,
        "夜": Shift.NIGHT,
    }.get(code)


@dataclass(frozen=True)
class ShiftAssignment:
    person_name: str
    work_date: date
    shift: Shift

    @property
    def time_range_text(self) -> str:
        return f"{self.shift.start_time:%H:%M}至{self.shift.end_time:%H:%M}"

    def message(self, mention_text: str = "") -> str:
        suffix = f"\n{mention_text}" if mention_text else ""
        return (
            f"{self.person_name} {self.work_date:%Y-%m-%d}"
            f"（{self.time_range_text})是你的{self.shift.label}"
            f"{suffix}"
        )
