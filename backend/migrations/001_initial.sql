CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS clinics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text NOT NULL UNIQUE,
  name text NOT NULL,
  timezone text NOT NULL DEFAULT 'Asia/Kolkata',
  phone text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staff (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id uuid NOT NULL REFERENCES clinics(id),
  name text NOT NULL,
  role text NOT NULL DEFAULT 'Provider',
  color text NOT NULL DEFAULT '#8d76de',
  active boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS services (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id uuid NOT NULL REFERENCES clinics(id),
  name text NOT NULL,
  duration_minutes integer NOT NULL CHECK (duration_minutes BETWEEN 5 AND 480),
  price_cents integer NOT NULL DEFAULT 0,
  active boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS availability_rules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_id uuid NOT NULL REFERENCES staff(id),
  weekday integer NOT NULL CHECK (weekday BETWEEN 1 AND 7),
  start_local time NOT NULL,
  end_local time NOT NULL,
  effective_from date,
  effective_until date,
  CHECK (start_local < end_local)
);

CREATE TABLE IF NOT EXISTS availability_exceptions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_id uuid NOT NULL REFERENCES staff(id),
  local_date date NOT NULL,
  start_local time,
  end_local time,
  kind text NOT NULL CHECK (kind IN ('closed','open')),
  CHECK ((kind = 'closed' AND start_local IS NULL AND end_local IS NULL) OR
         (kind = 'open' AND start_local IS NOT NULL AND end_local IS NOT NULL AND start_local < end_local)),
  UNIQUE (staff_id, local_date, kind)
);

CREATE TABLE IF NOT EXISTS availability_breaks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_id uuid NOT NULL REFERENCES staff(id),
  weekday integer NOT NULL CHECK (weekday BETWEEN 1 AND 7),
  start_local time NOT NULL,
  end_local time NOT NULL,
  CHECK (start_local < end_local)
);

CREATE TABLE IF NOT EXISTS patients (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id uuid NOT NULL REFERENCES clinics(id),
  name text NOT NULL,
  phone text NOT NULL,
  email text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (clinic_id, phone)
);

CREATE TABLE IF NOT EXISTS appointments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id uuid NOT NULL REFERENCES clinics(id),
  staff_id uuid NOT NULL REFERENCES staff(id),
  service_id uuid NOT NULL REFERENCES services(id),
  patient_id uuid NOT NULL REFERENCES patients(id),
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed','checked_in','completed','cancelled','no_show')),
  source text NOT NULL DEFAULT 'online',
  calendar_event_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (starts_at < ends_at)
);

DO $$ BEGIN
  ALTER TABLE appointments ADD CONSTRAINT no_staff_overlap
    EXCLUDE USING gist (staff_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)
    WHERE (status IN ('confirmed','checked_in'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS appointment_clinic_start ON appointments(clinic_id, starts_at);

CREATE TABLE IF NOT EXISTS waitlist (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id uuid NOT NULL REFERENCES clinics(id),
  staff_id uuid REFERENCES staff(id),
  service_id uuid NOT NULL REFERENCES services(id),
  patient_id uuid NOT NULL REFERENCES patients(id),
  preferred_date date NOT NULL,
  status text NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting','offered','booked','expired','cancelled')),
  offered_start timestamptz,
  offer_expires_at timestamptz,
  offer_token uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS waitlist_next ON waitlist(clinic_id, preferred_date, created_at) WHERE status = 'waiting';
CREATE UNIQUE INDEX IF NOT EXISTS one_open_offer_per_slot ON waitlist(staff_id, offered_start) WHERE status='offered';

CREATE TABLE IF NOT EXISTS message_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  appointment_id uuid REFERENCES appointments(id),
  patient_id uuid NOT NULL REFERENCES patients(id),
  kind text NOT NULL,
  idempotency_key text NOT NULL UNIQUE,
  body text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','sent','failed')),
  provider_sid text,
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS booking_requests (
  idempotency_key text PRIMARY KEY,
  appointment_id uuid NOT NULL REFERENCES appointments(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_queue (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  unique_key text NOT NULL UNIQUE,
  kind text NOT NULL CHECK (kind IN ('reminder','offer_expiry','calendar_sync')),
  payload jsonb NOT NULL,
  run_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','done','dead')),
  attempts integer NOT NULL DEFAULT 0,
  locked_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS job_queue_due ON job_queue(run_at) WHERE status='pending';
