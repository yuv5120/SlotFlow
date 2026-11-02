from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.jobs import schedule_appointment


class FakeConnection:
    def __init__(self, misses: int):
        self.misses = misses
        self.keys: list[str] = []

    def execute(self, sql: str, params: tuple):
        if "SELECT count(*) AS misses" in sql:
            return self
        self.keys.append(params[0])
        return self

    def fetchone(self):
        return {"misses": self.misses}


def test_short_notice_booking_does_not_queue_overdue_reminders():
    conn = FakeConnection(misses=0)
    schedule_appointment(conn, uuid4(), datetime.now(UTC) + timedelta(minutes=90))
    assert len(conn.keys) == 1
    assert conn.keys[0].startswith("calendar:")


def test_prior_no_show_adds_four_hour_reminder():
    conn = FakeConnection(misses=1)
    schedule_appointment(conn, uuid4(), datetime.now(UTC) + timedelta(hours=5))
    assert any(key.endswith(":4h") for key in conn.keys)
    assert any(key.endswith(":2h") for key in conn.keys)
    assert not any(key.endswith(":24h") for key in conn.keys)
