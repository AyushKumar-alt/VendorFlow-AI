-- ==============================================================================
-- VendorFlow AI: Production Role Provisioning & Least-Privilege Architecture
-- File: infra/scripts/01_provision_roles.sql
-- PostgreSQL Version: 17
-- ==============================================================================
--
-- CRITICAL SECURITY ARCHITECTURE & LIMITATIONS:
-- 1. DO NOT EXECUTE THIS SCRIPT IN LOCAL DEVELOPMENT ENVIRONMENTS.
--    In local development, the single 'vendorflow' user acts as database owner
--    and superuser to simplify development and testing.
--
-- 2. POSTGRESQL TABLE OWNER PRIVILEGE SEMANTICS:
--    In PostgreSQL, the owner of a table and superusers automatically bypass
--    standard table-level GRANT/REVOKE checks. Therefore, audit log immutability
--    CANNOT be enforced if the runtime application connects as the table owner.
--
-- 3. PRODUCTION SEPARATION OF ROLES:
--    - 'vendorflow_admin': Owns the schema and tables. Used exclusively by CI/CD
--      deployment pipelines to run Alembic schema migrations and DDL.
--    - 'vendorflow_app': Runtime user used by the FastAPI connection pool.
--      Has NO DDL privileges, cannot ALTER or DROP tables, cannot TRUNCATE any
--      table, and is strictly restricted to SELECT and INSERT on 'audit_events'.
--
-- 4. ENVIRONMENT-SPECIFIC CREDENTIALS:
--    Replace placeholder passwords with cryptographically secure credentials
--    managed via secret management systems (e.g. HashiCorp Vault, AWS Secrets
--    Manager, or GCP Secret Manager). Never commit production credentials.
-- ==============================================================================

\set ON_ERROR_STOP on

-- Ensure connection to target production database
-- \connect vendorflow

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. Create Roles (if not existing)
-- ------------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vendorflow_admin') THEN
        CREATE ROLE vendorflow_admin WITH LOGIN ENCRYPTED PASSWORD 'CHANGE_ME_ADMIN_STRONG_PASSWORD';
    END IF;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vendorflow_app') THEN
        CREATE ROLE vendorflow_app WITH LOGIN ENCRYPTED PASSWORD 'CHANGE_ME_APP_STRONG_PASSWORD';
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 2. Schema Authorization & Public Hardening
-- ------------------------------------------------------------------------------

-- Revoke default public schema privileges from the PUBLIC pseudo-role
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE vendorflow FROM PUBLIC;

-- Grant database connection
GRANT CONNECT ON DATABASE vendorflow TO vendorflow_admin;
GRANT CONNECT ON DATABASE vendorflow TO vendorflow_app;

-- Establish schema ownership and usage
ALTER SCHEMA public OWNER TO vendorflow_admin;
GRANT USAGE, CREATE ON SCHEMA public TO vendorflow_admin;
GRANT USAGE ON SCHEMA public TO vendorflow_app;

-- ------------------------------------------------------------------------------
-- 3. Admin Privileges (DDL & Migrations)
-- ------------------------------------------------------------------------------

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO vendorflow_admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO vendorflow_admin;
GRANT ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA public TO vendorflow_admin;

-- ------------------------------------------------------------------------------
-- 4. Runtime Application Role: Operational DML (Least Privilege)
-- ------------------------------------------------------------------------------

-- Standard operational tables: SELECT, INSERT, UPDATE, DELETE
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    organizations,
    users,
    memberships,
    suppliers,
    document_types,
    document_requirements,
    supplier_documents,
    document_extractions,
    document_reviews
TO vendorflow_app;

-- Sequences usage for ID/version generation where applicable
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vendorflow_app;

-- ------------------------------------------------------------------------------
-- 5. Audit Log Immutability Enforcement
-- ------------------------------------------------------------------------------

-- Grant SELECT and INSERT only to audit_events (NO UPDATE, NO DELETE, NO TRUNCATE)
GRANT SELECT, INSERT ON TABLE audit_events TO vendorflow_app;

REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_events FROM vendorflow_app;

-- Explicitly ensure vendorflow_app cannot truncate any table in the schema
REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM vendorflow_app;

-- ------------------------------------------------------------------------------
-- 6. Default Privileges for Future Migrations Executed by Admin
-- ------------------------------------------------------------------------------

-- When vendorflow_admin creates future tables, grant standard DML to vendorflow_app
ALTER DEFAULT PRIVILEGES FOR ROLE vendorflow_admin IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vendorflow_app;

ALTER DEFAULT PRIVILEGES FOR ROLE vendorflow_admin IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO vendorflow_app;

-- Maintain audit log immutability safeguard for future audit log partitions/tables
-- Note: If future audit partitions are created, explicitly revoke UPDATE/DELETE:
-- REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_events_YYYY_MM FROM vendorflow_app;

COMMIT;
