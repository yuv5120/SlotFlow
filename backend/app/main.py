from contextlib import asynccontextmanager
from datetime import date, datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from .auth import authenticate, require_staff
from .availability import available_slots
from .booking import _clinic, _patient, book, claim_offer, reschedule, update_status
from .db import connection, pool


@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open()
    pool.wait()
    yield
    pool.close()


app = FastAPI(title="SlotFlow API", version="1.0.0", lifespan=lifespan)


class LoginBody(BaseModel):
    password: str


class BookingBody(BaseModel):
    staffId: UUID
    serviceId: UUID
    startsAt: datetime
    patientName: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=30)
    email: str | None = None
    idempotencyKey: str = Field(min_length=8, max_length=200)


class WaitlistBody(BaseModel):
    patientName: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=30)
    serviceId: UUID
    staffId: UUID
    preferredDate: date


class AppointmentPatch(BaseModel):
    status: str | None = None
    startsAt: datetime | None = None


@app.get("/api/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/api/admin/login")
def login(body: LoginBody, response: Response):
    authenticate(body.password, response)
    return {"ok": True}


@app.get("/api/public")
def public():
    with connection() as conn:
        clinic = _clinic(conn)
        staff = conn.execute("""SELECT id,name,role,color FROM staff
            WHERE clinic_id=%s AND active ORDER BY name""", (clinic["id"],)).fetchall()
        services = conn.execute("""SELECT id,name,duration_minutes AS duration,
            price_cents/100.0 AS price FROM services WHERE clinic_id=%s AND active ORDER BY name""",
            (clinic["id"],)).fetchall()
    return {"clinic": {"name": clinic["name"], "timezone": clinic["timezone"]},
            "staff": staff, "services": services}


@app.get("/api/bootstrap", dependencies=[Depends(require_staff)])
def bootstrap():
    with connection() as conn:
        clinic = _clinic(conn)
        staff = conn.execute("""SELECT id,name,role,color FROM staff
            WHERE clinic_id=%s AND active ORDER BY name""", (clinic["id"],)).fetchall()
        services = conn.execute("""SELECT id,name,duration_minutes AS duration,
            price_cents/100.0 AS price FROM services WHERE clinic_id=%s AND active ORDER BY name""",
            (clinic["id"],)).fetchall()
        appointments = conn.execute("""SELECT a.id,p.name AS patient,p.phone,sv.name AS service,
            sv.id AS "serviceId",st.name AS staff,st.id AS "staffId",
            a.starts_at AS "startsAt",a.ends_at AS "endsAt",a.status,a.source,
            CASE WHEN (SELECT count(*) FROM appointments past
                WHERE past.patient_id=p.id AND past.status='no_show')>=2 THEN 'high'
              WHEN (SELECT count(*) FROM appointments past
                WHERE past.patient_id=p.id AND past.status='no_show')=1 THEN 'medium'
              ELSE 'low' END AS risk
            FROM appointments a JOIN patients p ON p.id=a.patient_id
            JOIN services sv ON sv.id=a.service_id JOIN staff st ON st.id=a.staff_id
            WHERE a.clinic_id=%s AND a.starts_at>=now()-interval '30 days'
            AND a.starts_at<now()+interval '90 days' ORDER BY a.starts_at""",
            (clinic["id"],)).fetchall()
        waitlist = conn.execute("""SELECT w.id,p.name AS patient,p.phone,sv.name AS service,
            sv.id AS "serviceId",w.staff_id AS "staffId",
            w.preferred_date AS "preferredDate",w.status,w.created_at AS "createdAt"
            FROM waitlist w JOIN patients p ON p.id=w.patient_id
            JOIN services sv ON sv.id=w.service_id WHERE w.clinic_id=%s ORDER BY w.created_at""",
            (clinic["id"],)).fetchall()
    return {"mode": "live", "clinic": clinic, "staff": staff, "services": services,
            "appointments": appointments, "waitlist": waitlist}


@app.get("/api/availability")
def availability(day: date | None = None, date: date | None = None,
                 staffId: UUID | None = None, serviceId: UUID | None = None):
    target = date or day
    if not target or not staffId or not serviceId:
        raise HTTPException(400, "Date, provider and service are required")
    with connection() as conn:
        slots = available_slots(conn, "haven-health", staffId, serviceId, target)
    return {"slots": slots}


@app.post("/api/appointments", status_code=201)
def create_appointment(body: BookingBody):
    return book(body.staffId, body.serviceId, body.startsAt, body.patientName.strip(),
                body.phone.strip(), body.email, body.idempotencyKey)


@app.patch("/api/appointments/{appointment_id}", dependencies=[Depends(require_staff)])
def patch_appointment(appointment_id: UUID, body: AppointmentPatch):
    if body.startsAt is not None:
        return {"appointment": reschedule(appointment_id, body.startsAt)}
    if body.status not in {"confirmed", "checked_in", "completed", "cancelled", "no_show"}:
        raise HTTPException(400, "Invalid status")
    return {"appointment": update_status(appointment_id, body.status)}


@app.post("/api/waitlist", status_code=201)
def join_waitlist(body: WaitlistBody):
    with connection() as conn, conn.transaction():
        clinic = _clinic(conn)
        valid = conn.execute("""SELECT 1 FROM staff st JOIN services sv ON sv.clinic_id=st.clinic_id
            WHERE st.id=%s AND sv.id=%s AND st.clinic_id=%s AND st.active AND sv.active""",
            (body.staffId, body.serviceId, clinic["id"])).fetchone()
        if not valid:
            raise HTTPException(400, "Service or provider unavailable")
        patient_id = _patient(conn, clinic["id"], body.patientName.strip(), body.phone.strip())
        entry = conn.execute("""INSERT INTO waitlist
            (clinic_id,staff_id,service_id,patient_id,preferred_date)
            VALUES(%s,%s,%s,%s,%s) RETURNING id""",
            (clinic["id"], body.staffId, body.serviceId, patient_id, body.preferredDate)).fetchone()
    return {"id": entry["id"]}


@app.post("/api/offer/{token}")
def confirm_offer(token: UUID):
    return claim_offer(token)
