import json
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from fastapi import HTTPException
from psycopg import Connection

from . import cache
from .time_slots import expand_slots


def available_slots(
    conn: Connection,
    clinic_slug: str,
    staff_id: UUID,
    service_id: UUID,
    day: date,
    ignore_appointment: UUID | None = None,
    use_cache: bool = True,
) -> list[datetime]:
    context = conn.execute("""SELECT c.id AS clinic_id,c.timezone,s.duration_minutes
        FROM clinics c JOIN staff st ON st.clinic_id=c.id AND st.id=%s AND st.active
        JOIN services s ON s.clinic_id=c.id AND s.id=%s AND s.active
        WHERE c.slug=%s""", (staff_id, service_id, clinic_slug)).fetchone()
    if not context:
        raise HTTPException(404, "Service or provider unavailable")

    key = cache.availability_key(staff_id, service_id, day)
    if use_cache and ignore_appointment is None:
        cached = cache.get(key)
        if cached:
            return [datetime.fromisoformat(value) for value in json.loads(cached)]

    exceptions = conn.execute("""SELECT kind,start_local,end_local FROM availability_exceptions
        WHERE staff_id=%s AND local_date=%s""", (staff_id, day)).fetchall()
    if any(row["kind"] == "closed" for row in exceptions):
        return []
    openings = [row for row in exceptions if row["kind"] == "open"]
    if not openings:
        openings = conn.execute("""SELECT start_local,end_local FROM availability_rules
            WHERE staff_id=%s AND weekday=%s
            AND (effective_from IS NULL OR effective_from<=%s)
            AND (effective_until IS NULL OR effective_until>=%s)""",
            (staff_id, day.isoweekday(), day, day)).fetchall()
    breaks = conn.execute("""SELECT start_local,end_local FROM availability_breaks
        WHERE staff_id=%s AND weekday=%s""", (staff_id, day.isoweekday())).fetchall()
    lower = datetime.combine(day - timedelta(days=1), time.min, UTC)
    upper = datetime.combine(day + timedelta(days=2), time.min, UTC)
    busy = conn.execute("""SELECT starts_at,ends_at FROM appointments
        WHERE staff_id=%s AND status IN ('confirmed','checked_in')
        AND (%s::uuid IS NULL OR id<>%s::uuid) AND starts_at<%s AND ends_at>%s""",
        (staff_id, ignore_appointment, ignore_appointment, upper, lower)).fetchall()
    slots = expand_slots(
        day, context["timezone"],
        [(row["start_local"], row["end_local"]) for row in openings],
        [(row["start_local"], row["end_local"]) for row in breaks],
        context["duration_minutes"],
        [(row["starts_at"], row["ends_at"]) for row in busy],
    )
    if use_cache and ignore_appointment is None:
        cache.set_short(key, json.dumps([slot.isoformat() for slot in slots]))
    return slots
