-- Runs once, on an empty volume, as the postgres superuser.
-- One role per consumer, so each gets only what it needs (and so a leaked
-- Grafana password cannot read raw shipment data).
-- Passwords are dev defaults, matching data/docker-compose.yml.

CREATE ROLE aicc     LOGIN PASSWORD 'aicc';                -- the services (owner of business data)
CREATE ROLE airflow  LOGIN PASSWORD 'airflow';             -- Airflow metadata DB
CREATE ROLE debezium LOGIN REPLICATION PASSWORD 'debezium'; -- reads the WAL, never writes
CREATE ROLE grafana  LOGIN PASSWORD 'grafana';             -- read-only, analytics schema only

CREATE DATABASE aicc    OWNER aicc;
CREATE DATABASE airflow OWNER airflow;
