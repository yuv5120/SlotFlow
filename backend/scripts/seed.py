from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.jobs import schedule_appointment

zone = ZoneInfo("Asia/Kolkata")
staff = [
    ("Dr. Maya Chen", "General practitioner", "#8768d6"),
    ("Dr. James Wilson", "Dermatologist", "#4f9ea4"),
    ("Dr. Sarah Kim", "Physiotherapist", "#dc9668"),
]
services = [
    ("General consultation", 30, 6500),
    ("Follow-up visit", 30, 4500),
    ("Skin consultation", 45, 9000),
    ("Physiotherapy", 60, 8000),
]

with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
    clinic = conn.execute("""INSERT INTO clinics(slug,name,timezone,phone)
        VALUES('haven-health','Haven Health','Asia/Kolkata','+15550100000')
        ON CONFLICT(slug) DO UPDATE SET name=EXCLUDED.name RETURNING id""").fetchone()["id"]
    staff_ids = []
    for name, role, color in staff:
        row = conn.execute("SELECT id FROM staff WHERE clinic_id=%s AND name=%s", (clinic, name)).fetchone()
        staff_id = row["id"] if row else conn.execute("""INSERT INTO staff(clinic_id,name,role,color)
            VALUES(%s,%s,%s,%s) RETURNING id""", (clinic, name, role, color)).fetchone()["id"]
        staff_ids.append(staff_id)
        if not conn.execute("SELECT 1 FROM availability_rules WHERE staff_id=%s", (staff_id,)).fetchone():
            for weekday in range(1, 7):
                conn.execute("INSERT INTO availability_rules(staff_id,weekday,start_local,end_local) VALUES(%s,%s,'09:00','17:00')", (staff_id, weekday))
                conn.execute("INSERT INTO availability_breaks(staff_id,weekday,start_local,end_local) VALUES(%s,%s,'12:00','13:00')", (staff_id, weekday))
    service_ids = []
    for name, duration, price in services:
        row = conn.execute("SELECT id FROM services WHERE clinic_id=%s AND name=%s", (clinic, name)).fetchone()
        service_ids.append(row["id"] if row else conn.execute("""INSERT INTO services(clinic_id,name,duration_minutes,price_cents)
            VALUES(%s,%s,%s,%s) RETURNING id""", (clinic, name, duration, price)).fetchone()["id"])
    if not conn.execute("SELECT 1 FROM appointments WHERE clinic_id=%s LIMIT 1", (clinic,)).fetchone():
        names = ["Olivia Rhye", "Phoenix Baker", "Lana Steiner", "Demi Wilkinson", "Candice Wu", "Natali Craig", "Drew Cano", "Andi Lane", "Riley Torres"]
        specs = [(0,0,9,0,"completed"),(1,1,9,30,"checked_in"),(1,2,10,30,"confirmed"),(2,3,11,0,"confirmed"),(0,0,11,30,"confirmed"),(0,1,13,30,"confirmed"),(1,2,14,0,"confirmed"),(2,3,15,0,"confirmed"),(0,0,16,0,"confirmed")]
        today = datetime.now(zone).date()
        for index, ((provider, service, hour, minute, status), name) in enumerate(zip(specs, names), start=1):
            patient = conn.execute("INSERT INTO patients(clinic_id,name,phone) VALUES(%s,%s,%s) RETURNING id", (clinic, name, f"+155501010{index:02d}")).fetchone()["id"]
            start = datetime(today.year, today.month, today.day, hour, minute, tzinfo=zone).astimezone(UTC)
            appointment = conn.execute("""INSERT INTO appointments(clinic_id,staff_id,service_id,patient_id,starts_at,ends_at,status)
                VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""", (clinic, staff_ids[provider], service_ids[service], patient, start, start + timedelta(minutes=services[service][1]), status)).fetchone()
            if status == "confirmed":
                schedule_appointment(conn, appointment["id"], start)
        for index, name, service, provider in [(10,"Sofia Martinez",0,0),(11,"Ethan Reed",2,1),(12,"Ava Patel",1,0)]:
            patient = conn.execute("INSERT INTO patients(clinic_id,name,phone) VALUES(%s,%s,%s) RETURNING id", (clinic, name, f"+155501010{index:02d}")).fetchone()["id"]
            conn.execute("""INSERT INTO waitlist(clinic_id,staff_id,service_id,patient_id,preferred_date)
                VALUES(%s,%s,%s,%s,%s)""", (clinic, staff_ids[provider], service_ids[service], patient, today))
print("Haven Health seed ready")
