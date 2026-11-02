from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def local_instant(day: date, minute: int, zone: ZoneInfo) -> datetime | None:
    """Return the earlier occurrence of a wall time, or None for a DST gap."""
    hour, minute_of_hour = divmod(minute, 60)
    naive = datetime.combine(day, time(hour, minute_of_hour))
    candidate = naive.replace(tzinfo=zone, fold=0)
    round_trip = candidate.astimezone(UTC).astimezone(zone)
    if round_trip.replace(tzinfo=None) != naive:
        return None
    return candidate.astimezone(UTC)


def expand_slots(
    day: date,
    zone_name: str,
    windows: list[tuple[time, time]],
    breaks: list[tuple[time, time]],
    duration_minutes: int,
    busy: list[tuple[datetime, datetime]],
    now: datetime | None = None,
) -> list[datetime]:
    zone = ZoneInfo(zone_name)
    current = now or datetime.now(UTC)
    to_minute = lambda value: value.hour * 60 + value.minute
    result: list[datetime] = []
    for opening, closing in windows:
        for minute in range(to_minute(opening), to_minute(closing) - duration_minutes + 1, 15):
            if any(minute < to_minute(end) and minute + duration_minutes > to_minute(start)
                   for start, end in breaks):
                continue
            start = local_instant(day, minute, zone)
            if start is None or start <= current:
                continue
            end = start + timedelta(minutes=duration_minutes)
            if any(occupied_start < end and occupied_end > start
                   for occupied_start, occupied_end in busy):
                continue
            result.append(start)
    return result
