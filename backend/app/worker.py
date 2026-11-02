import logging
import time
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from .booking import offer_next
from .config import settings
from .db import connection, pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("slotflow.worker")


def claim_jobs() -> list[dict]:
    with connection() as conn, conn.transaction():
        return conn.execute("""WITH due AS (
            SELECT id FROM job_queue WHERE (status='pending' AND run_at<=now())
            OR (status='processing' AND locked_at<now()-interval '5 minutes')
            ORDER BY run_at FOR UPDATE SKIP LOCKED LIMIT 20)
            UPDATE job_queue j SET status='processing',attempts=j.attempts+1,locked_at=now()
            FROM due WHERE j.id=due.id RETURNING j.id,j.kind,j.payload,j.attempts""").fetchall()


def finish_job(job: dict, error: Exception | None = None) -> None:
    with connection() as conn:
        if error is None:
            conn.execute("UPDATE job_queue SET status='done',locked_at=NULL WHERE id=%s", (job["id"],))
        else:
            next_status = "dead" if job["attempts"] >= 5 else "pending"
            conn.execute("""UPDATE job_queue SET status=%s,locked_at=NULL,last_error=%s,
                run_at=now()+(%s::integer*interval '1 minute') WHERE id=%s""",
                (next_status, str(error)[:1000], min(2 ** job["attempts"], 30), job["id"]))


def reminder(payload: dict) -> None:
    appointment_id = UUID(payload["appointment_id"])
    with connection() as conn, conn.transaction():
        appointment = conn.execute("""SELECT a.id,a.patient_id,a.starts_at,a.status,
            p.name,c.name AS clinic,c.timezone FROM appointments a
            JOIN patients p ON p.id=a.patient_id JOIN clinics c ON c.id=a.clinic_id
            WHERE a.id=%s""", (appointment_id,)).fetchone()
        if not appointment or appointment["status"] != "confirmed":
            return
        if appointment["starts_at"].isoformat() != payload["starts_at"]:
            return
        if appointment["starts_at"] <= datetime.now(UTC):
            return
        local = appointment["starts_at"].astimezone(ZoneInfo(appointment["timezone"]))
        label = local.strftime("%a %d %b at %I:%M %p")
        body = f"Hi {appointment['name']}, your appointment at {appointment['clinic']} is {label}. Reply if you need to reschedule."
        key = f"reminder:{appointment_id}:{payload['starts_at']}:{payload['kind']}"
        conn.execute("""INSERT INTO message_outbox
            (appointment_id,patient_id,kind,idempotency_key,body)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(idempotency_key) DO NOTHING""",
            (appointment_id, appointment["patient_id"], f"reminder_{payload['kind']}", key, body))


def expire_offer(payload: dict) -> None:
    with connection() as conn, conn.transaction():
        row = conn.execute("""SELECT id,status,offer_token,offer_expires_at FROM waitlist
            WHERE id=%s FOR UPDATE""", (UUID(payload["waitlist_id"]),)).fetchone()
        if not row or row["status"] != "offered" or str(row["offer_token"]) != payload["token"]:
            return
        if row["offer_expires_at"] > datetime.now(UTC):
            return
        conn.execute("UPDATE waitlist SET status='expired' WHERE id=%s", (row["id"],))
        offer_next(conn, UUID(payload["appointment_id"]))


def google_token() -> str | None:
    if not all((settings.google_client_id, settings.google_client_secret, settings.google_refresh_token)):
        return None
    response = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "refresh_token": settings.google_refresh_token,
        "grant_type": "refresh_token",
    }, timeout=15)
    response.raise_for_status()
    return response.json()["access_token"]


def sync_calendar(payload: dict) -> None:
    token = google_token()
    if token is None:
        return
    appointment_id = UUID(payload["appointment_id"])
    with connection() as conn:
        row = conn.execute("""SELECT a.id,a.starts_at,a.ends_at,a.status,a.calendar_event_id,
            p.name AS patient,s.name AS service FROM appointments a
            JOIN patients p ON p.id=a.patient_id JOIN services s ON s.id=a.service_id
            WHERE a.id=%s""", (appointment_id,)).fetchone()
    if not row:
        return
    event_id = f"sf{appointment_id.hex}"
    url = f"https://www.googleapis.com/calendar/v3/calendars/{settings.google_calendar_id}/events"
    headers = {"Authorization": f"Bearer {token}"}
    if row["status"] == "cancelled":
        if row["calendar_event_id"]:
            response = httpx.delete(f"{url}/{event_id}", headers=headers, timeout=15)
            if response.status_code not in (200, 204, 404, 410):
                response.raise_for_status()
            with connection() as conn:
                conn.execute("UPDATE appointments SET calendar_event_id=NULL WHERE id=%s", (appointment_id,))
        return
    event = {"id": event_id, "summary": f"{row['service']} · {row['patient']}",
             "start": {"dateTime": row["starts_at"].isoformat()},
             "end": {"dateTime": row["ends_at"].isoformat()}}
    response = httpx.post(url, headers=headers, json=event, timeout=15)
    if response.status_code == 409:
        response = httpx.patch(f"{url}/{event_id}", headers=headers, json=event, timeout=15)
    response.raise_for_status()
    with connection() as conn:
        conn.execute("UPDATE appointments SET calendar_event_id=%s WHERE id=%s", (event_id, appointment_id))


def dispatch_outbox() -> None:
    with connection() as conn, conn.transaction():
        messages = conn.execute("""WITH due AS (
            SELECT id FROM message_outbox WHERE status='pending' AND next_attempt_at<=now()
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 20)
            UPDATE message_outbox m SET status='sending',attempts=m.attempts+1
            FROM due WHERE m.id=due.id RETURNING m.id,m.patient_id,m.kind,m.body""").fetchall()
    for message in messages:
        with connection() as conn:
            patient = conn.execute("SELECT phone FROM patients WHERE id=%s", (message["patient_id"],)).fetchone()
        if not patient:
            log.error("Outbox %s has no patient; requires review", message["id"])
            continue
        if not all((settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from_number)):
            log.info("[dry-run %s] %s: %s", message["kind"], patient["phone"], message["body"])
            provider_sid = "dry-run"
        else:
            sender = settings.twilio_whatsapp_from if message["kind"] == "waitlist_offer" and settings.twilio_whatsapp_from else settings.twilio_from_number
            recipient = f"whatsapp:{patient['phone']}" if sender.startswith("whatsapp:") else patient["phone"]
            try:
                response = httpx.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
                    auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                    data={"From": sender, "To": recipient, "Body": message["body"]}, timeout=15)
                response.raise_for_status()
                provider_sid = response.json()["sid"]
            except Exception:
                # The provider may have accepted a timed-out request. Leave 'sending'
                # for operator reconciliation; an automatic retry could double-text.
                log.exception("Outbox %s needs reconciliation", message["id"])
                continue
        with connection() as conn:
            conn.execute("""UPDATE message_outbox SET status='sent',sent_at=now(),provider_sid=%s
                WHERE id=%s""", (provider_sid, message["id"]))


def run_once() -> None:
    for job in claim_jobs():
        try:
            if job["kind"] == "reminder":
                reminder(job["payload"])
            elif job["kind"] == "offer_expiry":
                expire_offer(job["payload"])
            elif job["kind"] == "calendar_sync":
                sync_calendar(job["payload"])
            finish_job(job)
        except Exception as exc:
            log.exception("Job %s failed", job["id"])
            finish_job(job, exc)
    dispatch_outbox()


if __name__ == "__main__":
    pool.open()
    pool.wait()
    log.info("SlotFlow durable worker started")
    try:
        while True:
            try:
                run_once()
            except Exception:
                log.exception("Worker cycle failed; retrying")
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        pool.close()
