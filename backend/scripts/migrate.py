from pathlib import Path

import psycopg

from app.config import settings

directory = Path(__file__).resolve().parents[1] / "migrations"
with psycopg.connect(settings.database_url) as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    for path in sorted(directory.glob("*.sql")):
        version = path.stem
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (version,)).fetchone():
            continue
        conn.execute(path.read_text())
        conn.execute("INSERT INTO schema_migrations(version) VALUES(%s)", (version,))
        print(f"Applied {version}")
print("Schema ready")
