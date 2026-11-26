-- The analytics contract this service reads, as the data pipeline publishes it.
-- Test copy only: the real tables are created by the pipeline in DB aicc.

CREATE SCHEMA analytics;

CREATE TABLE analytics.tenant_daily (
    tenant_id text, day date, synthetic bool,
    calls int, ended_calls int, escalated_calls int,
    self_service_rate real, escalation_rate real, avg_duration_s real, avg_turns real,
    tool_calls int, tool_success_rate real, turn_p50_ms int, turn_p95_ms int,
    barge_ins int, errors int, slots_rejected int,
    PRIMARY KEY (tenant_id, day, synthetic)
);

CREATE TABLE analytics.sentiment_daily (
    tenant_id text, day date, synthetic bool, emotion text, utterances int,
    PRIMARY KEY (tenant_id, day, synthetic, emotion)
);

CREATE TABLE analytics.call_sentiment_daily (
    tenant_id text, day date, synthetic bool,
    calls int, escalation_risk_calls int, improving int, stable int, worsening int,
    ended_negative int,
    PRIMARY KEY (tenant_id, day, synthetic)
);

CREATE TABLE analytics.tool_daily (
    tenant_id text, day date, synthetic bool, tool text,
    calls int, ok int, errors int, timeouts int, avg_latency_ms real,
    PRIMARY KEY (tenant_id, day, synthetic, tool)
);

CREATE TABLE analytics.knowledge_daily (
    tenant_id text, day date, synthetic bool,
    searches int, covered int, uncovered int, failed int, avg_latency_ms real,
    PRIMARY KEY (tenant_id, day, synthetic)
);

CREATE TABLE analytics.document_usage (
    tenant_id text, day date, synthetic bool, document_title text, uses int,
    PRIMARY KEY (tenant_id, day, synthetic, document_title)
);

CREATE TABLE analytics.knowledge_gaps (
    tenant_id text, day date, synthetic bool, question text, asked int, best_similarity real,
    PRIMARY KEY (tenant_id, day, synthetic, question)
);

CREATE TABLE analytics.hourly (
    tenant_id text, day date, synthetic bool, hour int, calls int,
    PRIMARY KEY (tenant_id, day, synthetic, hour)
);

CREATE TABLE analytics.calls_to_review (
    tenant_id text, call_id text, started_at timestamptz, synthetic bool,
    reason text, last_emotion text NULL, duration_s real, turns int, transcript jsonb,
    PRIMARY KEY (tenant_id, call_id)
);

CREATE TABLE analytics.tenant_links (
    portal_tenant_id uuid, data_tenant_id text,
    PRIMARY KEY (portal_tenant_id, data_tenant_id)
);

CREATE TABLE analytics.publish_runs (
    run_id text PRIMARY KEY, published_at timestamptz, tables jsonb
);

-- The service's role reads the schema and nothing else.
GRANT USAGE ON SCHEMA analytics TO insights_test;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO insights_test;
