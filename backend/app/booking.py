from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from psycopg import errors

from . import cache
from .availability import available_slots
from .config import settings
from .db import connection
from .jobs import cancel_reminders, enqueue, enqueue_confirmation, schedule_appointment


def _clinic(conn):
    row = conn.execute("SELECT id,name,slug,timezone FROM clinics WHERE slug='haven-health'").fetchone()
    if not row:
        raise HTTPException(503, "Run the seed command first")
    return row


def _patient(conn, clinic_id: UUID, name: str, phone: str, email: str | None = None) -> UUID:
    return conn.execute("""INSERT INTO patients(clinic_id,name,phone,email) VALUES(%s,%s,%s,%s)
        ON CONFLICT(clinic_id,phone) DO UPDATE SET name=EXCLUDED.name,
        email=COALESCE(EXCLUDED.email,patients.email) RETURNING id""",
        (clinic_id, name, phone, email)).fetchone()["id"]


def book(staff_id: UUID, service_id: UUID, starts_at: datetime, name: str,
         phone: str, email: str | None, idempotency_key: str) -> dict:
    if starts_at.tzinfo is None:
        raise HTTPException(422, "startsAt must include a time zone offset")
    start = starts_at.astimezone(UTC)
    with connection() as conn:
        existing = conn.execute("SELECT appointment_id FROM booking_requests WHERE idempotency_key=%s",
                                (idempotency_key,)).fetchone()
        if existing:
            return {"id": existing["appointment_id"], "repeated": True}
    with cache.slot_lock(staff_id, start.isoformat()) as acquired:
        if not acquired:
            raise HTTPException(409, "That time is being booked. Please try again")
        try:
            with connection() as conn, conn.transaction():
                existing = conn.execute("SELECT appointment_id FROM booking_requests WHERE idempotency_key=%s",
                                        (idempotency_key,)).fetchone()
                if existing:
                    return {"id": existing["appointment_id"], "repeated": True}
                clinic = _clinic(conn)
                local_day = start.astimezone(ZoneInfo(clinic["timezone"])).date()
                slots = available_slots(conn, clinic["slug"], staff_id, service_id,
                                        local_day, use_cache=False)
                if start not in slots:
                    raise HTTPException(409, "That time is no longer available")
                service = conn.execute("SELECT duration_minutes FROM services WHERE id=%s AND clinic_id=%s",
                                       (service_id, clinic["id"])).fetchone()
                patient_id = _patient(conn, clinic["id"], name, phone, email)
                appointment = conn.execute("""INSERT INTO appointments
                    (clinic_id,staff_id,service_id,patient_id,starts_at,ends_at)
                    VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (clinic["id"], staff_id, service_id, patient_id, start,
                     start + timedelta(minutes=service["duration_minutes"]))).fetchone()
                appointment_id = appointment["id"]
                conn.execute("INSERT INTO booking_requests(idempotency_key,appointment_id) VALUES(%s,%s)",
                             (idempotency_key, appointment_id))
                enqueue_confirmation(conn, appointment_id)
                schedule_appointment(conn, appointment_id, start)
            cache.invalidate(staff_id, local_day)
            return {"id": appointment_id, "repeated": False}
        except errors.ExclusionViolation:
            raise HTTPException(409, "That time was just taken") from None
        except errors.UniqueViolation:
            with connection() as conn:
                prior = conn.execute("SELECT appointment_id FROM booking_requests WHERE idempotency_key=%s",
                                     (idempotency_key,)).fetchone()
            if prior:
                return {"id": prior["appointment_id"], "repeated": True}
            raise HTTPException(409, "Booking conflict") from None


def offer_next(conn, cancelled_appointment_id: UUID) -> None:
    slot = conn.execute("""SELECT a.clinic_id,a.staff_id,a.service_id,a.starts_at,
        (a.starts_at AT TIME ZONE c.timezone)::date AS local_day
        FROM appointments a JOIN clinics c ON c.id=a.clinic_id WHERE a.id=%s AND a.status='cancelled'""",
        (cancelled_appointment_id,)).fetchone()
    if not slot:
        return
    if slot["starts_at"] <= datetime.now(UTC):
        return
    existing = conn.execute("""SELECT 1 FROM waitlist WHERE staff_id=%s AND offered_start=%s
        AND status='offered' LIMIT 1""", (slot["staff_id"], slot["starts_at"])).fetchone()
    if existing:
        return
    candidate = conn.execute("""SELECT w.id,w.patient_id FROM waitlist w
        WHERE w.clinic_id=%s AND w.service_id=%s AND (w.staff_id IS NULL OR w.staff_id=%s)
        AND w.preferred_date=%s AND w.status='waiting'
        ORDER BY w.created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
        (slot["clinic_id"], slot["service_id"], slot["staff_id"], slot["local_day"])).fetchone()
    if not candidate:
        return
    token = uuid4()
    expires = datetime.now(UTC) + timedelta(minutes=15)
    conn.execute("""UPDATE waitlist SET status='offered',staff_id=%s,offered_start=%s,
        offer_expires_at=%s,offer_token=%s WHERE id=%s""",
        (slot["staff_id"], slot["starts_at"], expires, token, candidate["id"]))
    body = f"A time opened at Haven Health. Confirm within 15 minutes: {settings.app_url}/offer/{token}"
    conn.execute("""INSERT INTO message_outbox(patient_id,kind,idempotency_key,body)
        VALUES(%s,'waitlist_offer',%s,%s) ON CONFLICT(idempotency_key) DO NOTHING""",
        (candidate["patient_id"], f"waitlist:{candidate['id']}:{cancelled_appointment_id}", body))
    enqueue(conn, f"offer-expiry:{candidate['id']}:{token}", "offer_expiry",
            {"waitlist_id": str(candidate["id"]), "appointment_id": str(cancelled_appointment_id),
             "token": str(token)}, expires)


def update_status(appointment_id: UUID, status: str) -> dict:
    with connection() as conn, conn.transaction():
        prior = conn.execute("SELECT id,status,staff_id,starts_at FROM appointments WHERE id=%s FOR UPDATE",
                             (appointment_id,)).fetchone()
        if not prior:
            raise HTTPException(404, "Appointment not found")
        row = conn.execute("UPDATE appointments SET status=%s WHERE id=%s RETURNING id,status",
                           (status, appointment_id)).fetchone()
        if status == "cancelled" and prior["status"] != "cancelled":
            cancel_reminders(conn, appointment_id)
            offer_next(conn, appointment_id)
            enqueue(conn, f"calendar-cancel:{appointment_id}", "calendar_sync",
                    {"appointment_id": str(appointment_id)}, datetime.now(UTC))
        clinic = _clinic(conn)
        local_day = prior["starts_at"].astimezone(ZoneInfo(clinic["timezone"])).date()
    cache.invalidate(prior["staff_id"], local_day)
    return row


def reschedule(appointment_id: UUID, starts_at: datetime) -> dict:
    if starts_at.tzinfo is None:
        raise HTTPException(422, "startsAt must include a time zone offset")
    start = starts_at.astimezone(UTC)
    try:
        with connection() as conn, conn.transaction():
            prior = conn.execute("""SELECT a.id,a.staff_id,a.service_id,a.starts_at,a.status,
                s.duration_minutes,c.slug,c.timezone FROM appointments a
                JOIN services s ON s.id=a.service_id JOIN clinics c ON c.id=a.clinic_id
                WHERE a.id=%s FOR UPDATE OF a""", (appointment_id,)).fetchone()
            if not prior:
                raise HTTPException(404, "Appointment not found")
            if prior["status"] not in ("confirmed", "checked_in"):
                raise HTTPException(409, "Only active appointments can be moved")
            local_day = start.astimezone(ZoneInfo(prior["timezone"])).date()
            slots = available_slots(conn, prior["slug"], prior["staff_id"], prior["service_id"],
                                    local_day, ignore_appointment=appointment_id, use_cache=False)
            if start not in slots:
                raise HTTPException(409, "That time is unavailable")
            row = conn.execute("""UPDATE appointments SET starts_at=%s,ends_at=%s,
                calendar_event_id=NULL WHERE id=%s RETURNING id,starts_at,ends_at""",
                (start, start + timedelta(minutes=prior["duration_minutes"]), appointment_id)).fetchone()
            cancel_reminders(conn, appointment_id)
            schedule_appointment(conn, appointment_id, start)
            old_day = prior["starts_at"].astimezone(ZoneInfo(prior["timezone"])).date()
        cache.invalidate(prior["staff_id"], old_day)
        cache.invalidate(prior["staff_id"], local_day)
        return row
    except errors.ExclusionViolation:
        raise HTTPException(409, "That time was just taken") from None


def claim_offer(token: UUID) -> dict:
    try:
        with connection() as conn, conn.transaction():
            offer = conn.execute("""SELECT w.*,s.duration_minutes FROM waitlist w
                JOIN services s ON s.id=w.service_id WHERE w.offer_token=%s
                AND w.status='offered' AND w.offer_expires_at>now() FOR UPDATE OF w""",
                (token,)).fetchone()
            if not offer:
                raise HTTPException(410, "This offer has expired")
            start = offer["offered_start"]
            appointment = conn.execute("""INSERT INTO appointments
                (clinic_id,staff_id,service_id,patient_id,starts_at,ends_at,source)
                VALUES(%s,%s,%s,%s,%s,%s,'waitlist') RETURNING id""",
                (offer["clinic_id"], offer["staff_id"], offer["service_id"], offer["patient_id"],
                 start, start + timedelta(minutes=offer["duration_minutes"]))).fetchone()
            conn.execute("UPDATE waitlist SET status='booked' WHERE id=%s", (offer["id"],))
            enqueue_confirmation(conn, appointment["id"])
            schedule_appointment(conn, appointment["id"], start)
            clinic = _clinic(conn)
            local_day = start.astimezone(ZoneInfo(clinic["timezone"])).date()
        cache.invalidate(offer["staff_id"], local_day)
        return {"id": appointment["id"]}
    except errors.ExclusionViolation:
        raise HTTPException(409, "This time was already taken") from None
