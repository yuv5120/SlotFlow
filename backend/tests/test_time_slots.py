from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from app.time_slots import expand_slots, local_instant


def test_spring_forward_skips_nonexistent_wall_times():
    day = date(2026, 3, 8)
    slots = expand_slots(day, "America/New_York", [(time(1), time(4))], [], 15, [],
                         now=datetime(2026, 3, 1, tzinfo=UTC))
    local_labels = [slot.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M")
                    for slot in slots]
    assert "01:45" in local_labels
    assert "03:00" in local_labels
    assert not any(label.startswith("02:") for label in local_labels)


def test_fall_back_uses_first_occurrence():
    instant = local_instant(date(2026, 11, 1), 90, ZoneInfo("America/New_York"))
    assert instant == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_break_and_occupied_range_remove_overlapping_starts():
    day = date(2026, 11, 3)
    busy = [(datetime(2026, 11, 3, 10, 30, tzinfo=UTC),
             datetime(2026, 11, 3, 11, 0, tzinfo=UTC))]
    slots = expand_slots(day, "UTC", [(time(9), time(14))], [(time(12), time(13))],
                         30, busy, now=datetime(2026, 11, 2, tzinfo=UTC))
    labels = [slot.strftime("%H:%M") for slot in slots]
    assert "09:00" in labels
    assert "10:15" not in labels
    assert "10:30" not in labels
    assert "11:45" not in labels
    assert "12:00" not in labels
    assert "13:00" in labels
