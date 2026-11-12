-- Role and database bootstrap for Azure Database for PostgreSQL (research R3, R16).
--
-- Run by infra/scripts/db-bootstrap.sh on the host, signed in as the Entra administrator group
-- maa-db-admins, in two connections:
--   1. database postgres,             with -v phase_db=postgres
--   2. database market_anomaly_agent, without phase_db
-- Both with -v ON_ERROR_STOP=1 -v job_role=id-maa-job -v admin_role=maa-db-admins.
-- Idempotent: every step checks or tolerates its own result, so a rerun changes nothing.

\set ON_ERROR_STOP on

\if :{?phase_db}
    -- Step 1: the job's managed identity as a regular (non-admin, non-MFA) role. Must run in
    -- database postgres.
    SELECT count(*) = 0 AS need_principal FROM pg_roles WHERE rolname = :'job_role' \gset
    \if :need_principal
        SELECT * FROM pgaadauth_create_principal(:'job_role', false, false);
    \endif

    -- Step 2: the database, created (and so owned) by the admin group, which can then grant on
    -- it and on its public schema.
    SELECT 'CREATE DATABASE market_anomaly_agent'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'market_anomaly_agent') \gexec

    -- Step 3: only the job role may connect, besides the owner.
    GRANT CONNECT, TEMPORARY ON DATABASE market_anomaly_agent TO :"job_role";
    REVOKE ALL ON DATABASE market_anomaly_agent FROM PUBLIC;

    -- Step 4: the admin group can read what the job owns (queries, pg_dump) and restore as it.
    GRANT :"job_role" TO :"admin_role" WITH INHERIT TRUE, SET TRUE;
\else
    -- Step 3, in market_anomaly_agent: the job role runs the migrations, so it creates (and
    -- owns) the tables.
    GRANT USAGE, CREATE ON SCHEMA public TO :"job_role";

    -- Step 5: fail loudly unless both privileges are in place.
    SELECT has_schema_privilege(:'job_role', 'public', 'CREATE')
       AND pg_has_role(current_user, :'job_role', 'USAGE') AS bootstrap_ok \gset
    \if :bootstrap_ok
        \echo 'bootstrap privileges verified'
    \else
        DO $$ BEGIN RAISE EXCEPTION 'bootstrap check failed: the job role lacks CREATE on schema public, or the admin group is not a member of it (research R3)'; END $$;
    \endif
\endif
