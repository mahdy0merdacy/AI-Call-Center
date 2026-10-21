-- The operational schema: one Postgres schema per tenant backend.
-- Seeded from tenants/fixtures/*.json, same cases including the failure ones.
-- The MCP servers still read the JSON today; roadmap step 6 points them here.

\connect aicc

CREATE SCHEMA acme      AUTHORIZATION aicc;
CREATE SCHEMA swiftnet  AUTHORIZATION aicc;
CREATE SCHEMA analytics AUTHORIZATION aicc;   -- derived tables (step 7). Rebuildable.

SET ROLE aicc;

CREATE FUNCTION public.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

-- ── Acme Delivery ──────────────────────────────────────────────────────────

CREATE TABLE acme.shipments (
    shipment_id        text PRIMARY KEY CHECK (shipment_id ~ '^[A-Z0-9]{10}$'),
    status             text NOT NULL
                       CHECK (status IN ('in_transit', 'out_for_delivery', 'delayed', 'delivered')),
    location           text,
    estimated_delivery date,
    original_estimate  date,
    delay_reason       text,
    delivered_at       timestamptz,
    signed_by          text,                 -- personal data: never leaves via the tool (DDIA.md §17)
    recipient_initial  text,
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER shipments_touch BEFORE UPDATE ON acme.shipments
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

INSERT INTO acme.shipments
    (shipment_id, status, location, estimated_delivery, original_estimate,
     delay_reason, delivered_at, signed_by, recipient_initial) VALUES
    ('AC4471X209', 'in_transit', 'Lyon sorting centre', '2026-09-30', NULL, NULL, NULL, NULL, 'M.'),
    ('AC8823B110', 'delivered', NULL, NULL, NULL, NULL, '2026-09-26 14:12:00+02', 'R. Dupont', NULL),
    ('AC1199Z044', 'delayed', NULL, '2026-10-02', '2026-09-27',
     'weather disruption on the Marseille route', NULL, NULL, NULL),
    -- The JSON models this as in_transit + location "out for delivery" + reschedulable=false.
    -- A real status is what the conditional UPDATE in DDIA.md §12 checks against.
    ('AC0000A000', 'out_for_delivery', 'out for delivery', '2026-09-28', NULL, NULL, NULL, NULL, NULL);

-- ── SwiftNet ISP ───────────────────────────────────────────────────────────

CREATE TABLE swiftnet.lines (
    phone_number      text PRIMARY KEY CHECK (phone_number ~ '^[0-9]{10}$'),
    status            text NOT NULL CHECK (status IN ('line_ok', 'area_outage', 'account_suspended')),
    sync_speed_mbps   integer,
    last_drop         timestamptz,
    area              text,
    outage_eta        timestamptz,
    ticket            text,
    suspension_reason text,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER lines_touch BEFORE UPDATE ON swiftnet.lines
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

INSERT INTO swiftnet.lines
    (phone_number, status, sync_speed_mbps, last_drop, area, outage_eta, ticket, suspension_reason) VALUES
    ('0142887711', 'line_ok', 68, NULL, NULL, NULL, NULL, NULL),
    ('0298445102', 'area_outage', NULL, NULL, 'Nantes centre', '2026-09-28 18:00:00+02', 'OUT-4471', NULL),
    ('0155093388', 'account_suspended', NULL, NULL, NULL, NULL, NULL, 'billing'),
    ('0100000000', 'line_ok', 12, '2026-09-27 22:40:00+02', NULL, NULL, NULL, NULL);

-- FULL: updates and deletes carry the whole OLD row in the WAL, so a CDC event
-- shows before AND after. Default (primary key only) gives "after" and a key.
-- Costs more WAL per change; worth it to see what changed (DDIA ch.11).
ALTER TABLE acme.shipments REPLICA IDENTITY FULL;
ALTER TABLE swiftnet.lines REPLICA IDENTITY FULL;

RESET ROLE;

-- ── change data capture ────────────────────────────────────────────────────
-- Publication = which tables' changes are decoded out of the WAL. Created
-- here, by the superuser, so Debezium needs no ownership of anything.
CREATE PUBLICATION dbz_aicc FOR TABLES IN SCHEMA acme, swiftnet;

GRANT CONNECT ON DATABASE aicc TO debezium;
GRANT USAGE ON SCHEMA acme, swiftnet TO debezium;
GRANT SELECT ON ALL TABLES IN SCHEMA acme, swiftnet TO debezium;   -- initial snapshot

-- ── dashboards ─────────────────────────────────────────────────────────────
GRANT CONNECT ON DATABASE aicc TO grafana;
GRANT USAGE ON SCHEMA analytics TO grafana;
ALTER DEFAULT PRIVILEGES FOR ROLE aicc IN SCHEMA analytics GRANT SELECT ON TABLES TO grafana;
