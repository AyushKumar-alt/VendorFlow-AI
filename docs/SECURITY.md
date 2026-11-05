# VendorFlow AI: Security Architecture & Tenant Isolation Guarantees

## 1. Security Context & Threat Model

VendorFlow AI is a document-first B2B SaaS platform for Indian manufacturing enterprises. The platform manages confidential supplier onboarding records, statutory tax identifiers (GSTIN, PAN), banking mandates, quality certifications (ISO), and internal compliance review decisions.

### 1.1 Protected Assets & Data Classification
- **Supplier Proprietary Data**: Vendor codes, contact lists, operational categories, and internal risk tiers.
- **Statutory & Financial Documents**: Government certificates (GST, Udyam MSME, PAN), bilateral bank mandates, and audited financial statements.
- **Sensitive Extraction Data & OCR Transcripts**: Raw OCR transcripts (`raw_text`) and extracted JSON fields (`extracted_data`) contain sensitive business and personally identifiable information (PII), including promoter PANs, personal mobile numbers, bank account numbers, IFSC codes, and director signatures. These are classified as **Confidential Business Data** and must never be exposed in public API responses or unauthenticated endpoints.
- **Review Decisions & Comments**: Internal reviewer deliberations, rejection reasons, and human-verified data overrides.
- **Audit Trails**: Historical records of who uploaded, reviewed, approved, or modified compliance evidence.

### 1.2 Threat Vectors & Mitigations
- **Cross-Tenant Data Leakage**: Mitigated by Option A (tenant-scoped supplier records), application-level tenant context injection, and database-level composite foreign keys.
- **Competitor Intelligence Harvesting**: Mitigated by isolating supplier identities per buyer. No buyer can query or infer another buyer's supplier relationships.
- **Rogue Document Review / Approval**: Mitigated by database-enforced membership integrity linking reviews directly to organization memberships, backed by application-level active status and role verification.
- **Requirement-Type Confusion Attacks**: Mitigated by composite foreign key `(requirement_id, document_type_id, organization_id)` on supplier documents, preventing an uploaded document from fulfilling an incompatible requirement type.
- **MIME Spoofing & Malicious File Ingestion**: Mitigated by magic byte / binary file signature inspection, file size bounds, and cryptographic SHA-256 validation.
- **Audit Trail Tampering**: Mitigated by application-level immutability, database privilege separation, and restrictive foreign keys preventing cascading deletes.

---

## 2. Multi-Tenant Isolation Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Client Request Layer                              │
│   Incoming HTTPS Request with Bearer Token + X-Organization-Slug Header     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 Layer 1: Application-Level Tenant Scoping                   │
│   FastAPI Dependency Injection (`get_current_tenant_membership`)             │
│   - Validates user identity via JWT / session                               │
│   - Verifies active user account: `users.is_active = TRUE`                  │
│   - Verifies active membership: `memberships.is_active = TRUE`              │
│   - Enforces role-based permissions (`owner`, `admin`, `reviewer`, `viewer`)│
│   - Restricts all repository queries: `WHERE organization_id = :tenant_id`  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 Layer 2: Database Structural Defense-in-Depth               │
│   PostgreSQL 17 Composite Foreign Keys & Tenant-Owned Models                │
│   - Parent tables declare `UNIQUE (id, organization_id)`                    │
│   - Child tables enforce `FOREIGN KEY (parent_id, organization_id)`         │
│   - Structural guarantee: Cross-tenant foreign key references are rejected   │
│     by the database engine even if application code contains a bug          │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 The Tenant Boundary
The root tenant boundary is the **Organization** (`organizations`). Every operational table contains a non-nullable `organization_id` foreign key referencing `organizations(id)`:
- `suppliers`
- `document_types`
- `document_requirements`
- `supplier_documents`
- `document_extractions`
- `document_reviews`
- `audit_events`
- `memberships`

### 2.2 Relational Tenant Integrity vs Application Authorization
It is vital to recognize the exact boundaries of database constraints:
- **What Composite FKs Provide**: Structural referential tenant integrity. They guarantee that relational links between parent and child tables (e.g. Supplier -> Document -> Extraction -> Review) cannot cross tenant boundaries. A document of Tenant A cannot reference a supplier of Tenant B.
- **What Composite FKs Do NOT Provide**: They do NOT prevent an authenticated user of Tenant A from executing an un-scoped `SELECT * FROM suppliers` if application code omits `WHERE organization_id = :tenant_id`. Application-level query scoping middleware is mandatory.
- **Role-Level Security (RLS)**: Row-Level Security provides query-level enforcement at the database engine level. It is deferred for the MVP due to `asyncpg` connection pooling session leakage risks, but preserved as an architectural option for future infrastructure hardening.

### 2.3 Supplier Data Model Isolation (Option A)
VendorFlow AI rejects globally shared supplier identities (Option B) in favor of **Tenant-Scoped Supplier Records (Option A)**:
- Supplier records belong strictly to the buyer organization that created them.
- If Supplier X is contracted by Buyer 1 and Buyer 2, two completely independent records exist.
- **Security Impact**: Eliminates competitor intelligence leaks (e.g. discovering shared parts vendors or order volumes) and prevents cross-tenant document disclosures.

### 2.4 Tenant-Owned Document Types
To eliminate nullable foreign key ambiguities and cross-tenant leakage:
- System preset document types (`gst_certificate`, `pan_card`, `msme_udyam`, `bank_mandate`, `iso_9001`, `factory_license`) are initialized as **tenant-owned copies** (`organization_id NOT NULL`, `is_system_preset = TRUE`) upon organization creation.
- A tenant cannot reference another tenant's custom or preset document types. Composite foreign keys `(document_type_id, organization_id)` enforce this at the database level.

---

## 3. Membership Integrity & Role-Based Access Control (RBAC)

### 3.1 Scoped Roles
User permissions are scoped strictly per organization via the `memberships` table:
- **`owner`**: Full administrative control, billing, member invites, organization settings.
- **`admin`**: Policy configuration, document requirement management, supplier lifecycle management.
- **`reviewer`**: Document inspection, extraction verification, approval/rejection workflows.
- **`viewer`**: Read-only access to supplier compliance status and reports.

### 3.2 Database Enforcement of Membership Existence vs Active Status
- **Database Enforcement**: `document_reviews` and `audit_events` enforce that reviewers and user actors must hold an existing membership in that organization:
  ```sql
  CONSTRAINT fk_doc_reviews_reviewer_membership
      FOREIGN KEY (reviewer_id, organization_id)
      REFERENCES memberships(user_id, organization_id)
      ON DELETE RESTRICT;

  CONSTRAINT fk_audit_events_actor_membership
      FOREIGN KEY (actor_id, organization_id)
      REFERENCES memberships(user_id, organization_id)
      ON DELETE RESTRICT;
  ```
- **Application Responsibilities**:
  1. **Active Account Check**: The application verifies that `users.is_active = TRUE`.
  2. **Active Membership Check**: The application verifies that `memberships.is_active = TRUE`. (A suspended employee whose membership is marked inactive is blocked from conducting reviews, even though their historical membership row exists).
  3. **Role Authorization**: The application verifies that the user holds the requisite role (`reviewer`, `admin`, or `owner`) before executing review or approval actions.

### 3.3 Organization Context & Tenant Authorization Foundation
VendorFlow AI provides an immutable request-scoped authorization abstraction, `OrganizationContext`, resolved via FastAPI dependency injection:
- **`OrganizationContext` Attributes**:
  - `user_id`: UUID of verified User.
  - `organization_id`: UUID of verified active Organization (tenant).
  - `membership_id`: UUID of active Membership joining user to tenant.
  - `role`: Membership role (`'owner'`, `'admin'`, `'reviewer'`, `'viewer'`).
  - `user_email`, `user_full_name`, `is_superuser`, `organization_name`, `organization_slug`.
- **Authorization Dependencies**:
  - `get_organization_context`: Resolves, authenticates, and validates active membership.
  - `require_roles(*allowed_roles)`: Enforces role-based permissions (HTTP 403 if unauthorized).
  - Shortcuts: `require_owner`, `require_admin_or_owner`, `require_reviewer`.

### 3.4 Development Identity Provider & Production Authentication Boundary
- **Development Identity Mechanism (`DevIdentityProvider`)**:
  - Designed strictly for local development and test automation.
  - Inspects development headers: `X-Dev-User-Id` (or `X-User-Id`) and `X-Dev-Organization-Id` (or `X-Organization-Slug`).
  - **No Implicit Trust**: Client-supplied headers are never accepted as proof of membership.
  - **Database Verification**: User, organization, and membership records are resolved directly from PostgreSQL.
  - **Rejection Semantics**:
    - Missing user identity: HTTP 401 Unauthorized.
    - Nonexistent or inactive user: HTTP 401 Unauthorized.
    - Missing organization header: HTTP 400 Bad Request.
    - Nonexistent organization: HTTP 404 Not Found.
    - Inactive organization (`status != 'active'`): HTTP 403 Forbidden.
    - User has no membership in organization: HTTP 403 Forbidden.
    - Membership is inactive (`is_active = FALSE`): HTTP 403 Forbidden.
    - Insufficient role privileges: HTTP 403 Forbidden.
  - **Zero Implicit Creation / Fallback**: Does not create users or organizations from headers; never silently falls back to a default organization.
- **Production Integration Boundary (`IdentityProvider` Protocol)**:
  - The identity provider adheres to the `IdentityProvider` protocol:
    ```python
    class IdentityProvider(Protocol):
        async def resolve_context(self, request: Request, db: AsyncSession) -> OrganizationContext: ...
    ```
  - In production, a cryptographic provider (e.g. JWT Bearer / OIDC / OAuth2) will replace `DevIdentityProvider` by overriding `get_identity_provider`. Business routes and services interact solely with `OrganizationContext`, requiring zero refactoring.

---

## 4. Document Storage & Upload Security

### 4.1 File Storage Pattern
- **Filesystem Pathing**: Files are stored under server-generated UUID paths:
  `storage/documents/<organization_id>/<supplier_id>/<document_id>.<extension>`
- **No Path Traversal**: Storage paths are generated programmatically using server UUIDs. User-supplied filenames (`file_name`) are stored purely as metadata and are never used in filesystem path construction.
- **No Database Blobs**: Binary data is never stored inside PostgreSQL, protecting database backup performance and memory usage.

### 4.2 File Signature (Magic Bytes) Validation
Client-supplied `Content-Type` headers are untrusted. Ingestion pipelines inspect the initial binary magic bytes before accepting files:
- **PDF**: `%PDF-` (`0x25 0x50 0x44 0x46 0x2D`)
- **JPEG**: `0xFF 0xD8 0xFF`
- **PNG**: `\x89PNG\r\n\x1a\n` (`0x89 0x50 0x4E 0x47 0x0D 0x0A 0x1A 0x0A`)

Any file whose magic bytes do not match the expected whitelist is immediately rejected.

### 4.3 Storage Lifecycle & Integrity Checks
- **Upload Staging**: Incoming upload streams are buffered to a temporary quarantine staging directory (`storage/tmp/...`).
- **Validation**: Stream is hashed (SHA-256), byte count is validated against bounds (`CHECK (file_size_bytes > 0 AND file_size_bytes <= 26214400)`), and magic bytes are verified.
- **SHA-256 Role**: Used strictly as an **integrity verification and duplicate-detection mechanism**, not as content-addressed storage. The database validates hash format: `CHECK (file_hash_sha256 ~ '^[a-f0-9]{64}$')`.
- **Atomic Move & Commit**: Validated files are moved to permanent storage paths and committed to PostgreSQL in an atomic database transaction.
- **Cleanup & Orphan Pruning**: If validation fails or the DB transaction aborts, temporary staged files are unlinked immediately. A periodic background job reconciles filesystem files against database records to prune uncommitted orphan files.

---

## 5. Audit Integrity & Permissions Architecture

### 5.1 Honest Audit Posture
VendorFlow AI does **not** claim cryptographic tamper-evidence or blockchain immutability for standard relational database tables. A database superuser or direct SQL command could theoretically alter table contents. Instead, VendorFlow AI implements a realistic, multi-tier operational defense:

1. **Application-Level Immutability**:
   - The SQLAlchemy models and API service expose **only** `INSERT` and `SELECT` on `audit_events`.
   - No `UPDATE` or `DELETE` endpoints, schemas, or repository queries exist in the codebase.
2. **Database-Level Privilege Separation (Production Architecture)**:
   - Reference provisioning script: `infra/scripts/01_provision_roles.sql`.
   - Distinct database roles:
     - `vendorflow_admin`: Schema owner and migration executor holding full DDL privileges.
     - `vendorflow_app`: Least-privilege runtime application user used by FastAPI connection pools.
   - The operational application user (`vendorflow_app`) is granted strictly:
     ```sql
     GRANT SELECT, INSERT ON TABLE audit_events TO vendorflow_app;
     REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_events FROM vendorflow_app;
     REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM vendorflow_app;
     ```
   - **PostgreSQL Owner Bypass Semantics**: In PostgreSQL, table owners and superusers automatically bypass table-level `GRANT`/`REVOKE` permission checks. Therefore, engine-enforced audit immutability strictly requires the runtime application to connect as a non-owner (`vendorflow_app`). In local development, the single `vendorflow` user is the database owner/superuser, so immutability in development is maintained via application-level invariant enforcement.
3. **Actor Attribution & Attribution Integrity**:
   - Every action records:
     - `actor_type`: `'user'`, `'supplier'`, or `'system'`.
     - `actor_id`: References `memberships(user_id, organization_id)` for authenticated internal users.
     - `ip_address` & `user_agent`: Captured from HTTP request headers.
     - `previous_state` & `new_state`: JSONB snapshots of the entity before and after modification.
   - Enforced by database constraint:
     `CHECK ((actor_type = 'user' AND actor_id IS NOT NULL) OR (actor_type IN ('supplier', 'system') AND actor_id IS NULL))`
4. **Polymorphic Target Validation**:
   - `entity_id` references multiple entity tables (`suppliers`, `supplier_documents`, `document_requirements`, etc.). The application service layer must validate that the target entity belongs to `organization_id` prior to inserting the event.
5. **Test Environment Isolation**:
   - All tests execute strictly against `vendorflow_test`.
   - Code-level guard in `Settings.get_test_database_url()` verifies that the parsed database is strictly `vendorflow_test`, preventing accidental mutation or deletion of operational development records.

---

## 6. Retention & Archival Policies (Compliance Lifecycle)

Retention requirements must be aligned with governing statutes, contractual obligations, and legal hold procedures rather than blanket universal claims.

### 6.1 Governing Indian Statutory Requirements
- **Section 128(5) of the Companies Act, 2013**: Requires books of account and related vouchers/papers to be preserved in good order for not less than **eight financial years** immediately preceding the current financial year.
- **Section 36 of the CGST Act, 2017**: Requires books of account and other records to be retained until the expiry of **72 months (6 years)** from the due date of furnishing the annual return for the year pertaining to such accounts.
- **Transient Records**: Non-statutory vendor notes, temporary onboarding drafts, and ephemeral OCR execution transcripts are subject to configurable enterprise retention schedules.

### 6.2 Legal Validation & Legal Holds
- **Legal Validation Required**: Any automated retention purge or archival schedule must be reviewed and validated by enterprise legal and tax counsel.
- **Legal Hold Capability**: Organizations can flag suppliers or documents under legal hold, suspending automated archival or retention purges during audits or disputes.
- **Prevention of Destructive Cascades**: Foreign keys across compliance tables (`audit_events`, `supplier_documents`, `document_reviews`, `document_extractions`, `suppliers`) use `ON DELETE RESTRICT`. Entities transition to `status = 'archived'` rather than being deleted.
