import os
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from psycopg import errors

pytestmark = pytest.mark.skipif(
    os.getenv("SLOTFLOW_INTEGRATION") != "1", reason="Needs the temporary PostgreSQL test database"
)


def test_booking_contention_and_waitlist_offer():
    from app.db import pool
    from app.main import app
    from app.worker import run_once

    with TestClient(app) as client:
        public = client.get("/api/public")
        assert public.status_code == 200
        staff_id = public.json()["staff"][0]["id"]
        service_id = public.json()["services"][0]["id"]
        day = datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=1)
        while day.isoweekday() == 7:
            day += timedelta(days=1)
        slots = client.get("/api/availability", params={"date": day.isoformat(),
            "staffId": staff_id, "serviceId": service_id})
        assert slots.status_code == 200
        starts_at = slots.json()["slots"][0]

        key = str(uuid4())
        booking = {"staffId": staff_id, "serviceId": service_id, "startsAt": starts_at,
                   "patientName": "Integration Patient", "phone": "+15559990001",
                   "idempotencyKey": key}
        first = client.post("/api/appointments", json=booking)
        assert first.status_code == 201, first.text
        repeated = client.post("/api/appointments", json=booking)
        assert repeated.status_code == 201
        assert repeated.json()["id"] == first.json()["id"]
        assert repeated.json()["repeated"] is True
        competing = client.post("/api/appointments", json={**booking,
            "idempotencyKey": str(uuid4()), "phone": "+15559990002"})
        assert competing.status_code == 409

        wait = client.post("/api/waitlist", json={"staffId": staff_id, "serviceId": service_id,
            "preferredDate": day.isoformat(), "patientName": "Waitlist Patient",
            "phone": "+15559990003"})
        assert wait.status_code == 201, wait.text
        login = client.post("/api/admin/login", json={"password": "test-password"})
        assert login.status_code == 200, login.text
        cancelled = client.patch(f"/api/appointments/{first.json()['id']}",
                                 json={"status": "cancelled"})
        assert cancelled.status_code == 200, cancelled.text
        with pool.connection() as conn:
            offer = conn.execute("SELECT offer_token,status FROM waitlist WHERE id=%s",
                                 (wait.json()["id"],)).fetchone()
            message = conn.execute("""SELECT id FROM message_outbox
                WHERE kind='waitlist_offer' AND patient_id=(SELECT patient_id FROM waitlist WHERE id=%s)""",
                (wait.json()["id"],)).fetchone()
        assert offer["status"] == "offered"
        assert message is not None
        confirmed = client.post(f"/api/offer/{offer['offer_token']}")
        assert confirmed.status_code == 200, confirmed.text
        again = client.post(f"/api/offer/{offer['offer_token']}")
        assert again.status_code == 410
        with pool.connection() as conn:
            booked = conn.execute("SELECT * FROM appointments WHERE id=%s",
                                  (confirmed.json()["id"],)).fetchone()
            with pytest.raises(errors.ExclusionViolation), conn.transaction():
                conn.execute("""INSERT INTO appointments
                    (clinic_id,staff_id,service_id,patient_id,starts_at,ends_at)
                    VALUES(%s,%s,%s,%s,%s,%s)""",
                    (booked["clinic_id"], booked["staff_id"], booked["service_id"],
                     booked["patient_id"], booked["starts_at"], booked["ends_at"]))
        with pool.connection() as conn:
            patient = conn.execute("SELECT id FROM patients ORDER BY created_at LIMIT 1").fetchone()
            row = conn.execute("""INSERT INTO message_outbox
                (patient_id,kind,idempotency_key,body)
                VALUES(%s,'test',%s,'Worker smoke test') RETURNING id""",
                (patient["id"], f"worker-test:{uuid4()}")).fetchone()
        run_once()
        with pool.connection() as conn:
            sent = conn.execute("SELECT status,provider_sid FROM message_outbox WHERE id=%s",
                                (row["id"],)).fetchone()
        assert sent == {"status": "sent", "provider_sid": "dry-run"}
