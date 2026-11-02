import json
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import Connection


def enqueue(conn: Connection, unique_key: str, kind: str, payload: dict, run_at: datetime) -> None:
    conn.execute("""INSERT INTO job_queue(unique_key,kind,payload,run_at)
        VALUES(%s,%s,%s::jsonb,%s) ON CONFLICT(unique_key) DO NOTHING""",
        (unique_key, kind, json.dumps(payload), run_at))


def schedule_appointment(conn: Connection, appointment_id: UUID, starts_at: datetime) -> None:
    now = datetime.now(UTC)
    occurrence = starts_at.isoformat()
    history = conn.execute("""SELECT count(*) AS misses FROM appointments past
        WHERE past.patient_id=(SELECT patient_id FROM appointments WHERE id=%s)
        AND past.status='no_show'""", (appointment_id,)).fetchone()
    reminders = [("24h", 24), ("2h", 2)]
    if history["misses"]:
        reminders.insert(1, ("4h", 4))
    for label, hours in reminders:
        due = starts_at - timedelta(hours=hours)
        if due > now:
            enqueue(conn, f"reminder:{appointment_id}:{occurrence}:{label}", "reminder",
                    {"appointment_id": str(appointment_id), "starts_at": occurrence, "kind": label}, due)
    enqueue(conn, f"calendar:{appointment_id}:{occurrence}", "calendar_sync",
            {"appointment_id": str(appointment_id)}, now)


def enqueue_confirmation(conn: Connection, appointment_id: UUID) -> None:
    row = conn.execute("""SELECT a.patient_id,a.starts_at,p.name,c.name AS clinic,c.timezone
        FROM appointments a JOIN patients p ON p.id=a.patient_id
        JOIN clinics c ON c.id=a.clinic_id WHERE a.id=%s""", (appointment_id,)).fetchone()
    local = row["starts_at"].astimezone(ZoneInfo(row["timezone"]))
    body = f"Hi {row['name']}, your appointment at {row['clinic']} is confirmed for {local.strftime('%a %d %b at %I:%M %p')}."
    conn.execute("""INSERT INTO message_outbox
        (appointment_id,patient_id,kind,idempotency_key,body)
        VALUES(%s,%s,'confirmation',%s,%s) ON CONFLICT(idempotency_key) DO NOTHING""",
        (appointment_id, row["patient_id"], f"confirmation:{appointment_id}", body))


def cancel_reminders(conn: Connection, appointment_id: UUID) -> None:
    conn.execute("""UPDATE job_queue SET status='done' WHERE status='pending'
        AND kind='reminder' AND payload->>'appointment_id'=%s""", (str(appointment_id),))
