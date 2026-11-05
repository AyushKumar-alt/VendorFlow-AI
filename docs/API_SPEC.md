# VendorFlow AI: REST API Specification

## 1. Overview & Architectural Conventions

This document specifies the RESTful HTTP API contracts for VendorFlow AI.

### Base URL & Versioning
All domain and business endpoints are versioned under the `/api/v1` path prefix:
- Development Server: `http://127.0.0.1:8000/api/v1`

### Authentication & Tenant Scoping
In accordance with VendorFlow AI's multi-tenant isolation architecture:
- Every business request requires a verified tenant context (`OrganizationContext`).
- Client requests MUST NEVER supply `organization_id` in request payloads.
- **Development Headers**:
  - `X-Dev-User-Id` (or `X-User-Id`): UUID or email of the authenticated user.
  - `X-Dev-Organization-Id` (or `X-Organization-Id`): UUID of the active buyer organization.
  - `X-Organization-Slug`: URL-safe slug of the active buyer organization (alternative to organization ID).
- Requests lacking credentials return **HTTP 401 Unauthorized**.
- Requests for organizations where the user has no active membership return **HTTP 403 Forbidden**.
- Requests referencing resources belonging to another tenant return **HTTP 404 Not Found** (preventing enumeration attacks).

---

## 2. Health & Diagnostic Endpoints

### `GET /`
- **Description**: Service heartbeat and version metadata.
- **Authentication**: None.
- **Response**: `200 OK`
  ```json
  {
    "service": "vendorflow-api",
    "version": "0.1.0"
  }
  ```

### `GET /api/v1/health`
- **Description**: API liveness probe.
- **Authentication**: None.
- **Response**: `200 OK`
  ```json
  {
    "status": "healthy",
    "service": "vendorflow-api"
  }
  ```

### `GET /api/v1/health/ready`
- **Description**: API readiness probe verifying database connectivity.
- **Authentication**: None.
- **Response**: `200 OK` (or `503 Service Unavailable` if database disconnected)
  ```json
  {
    "status": "ready",
    "database": "connected"
  }
  ```

---

## 3. Organization & Tenant Context Endpoints

### `GET /api/v1/organization/context`
- **Description**: Resolves and returns the authenticated caller's tenant context.
- **Headers**: `X-Dev-User-Id`, `X-Dev-Organization-Id` (or `X-Organization-Slug`).
- **Response**: `200 OK`
  ```json
  {
    "user_id": "848bb24c-12f5-46ff-b6aa-5ee0ec3b1102",
    "organization_id": "4e749293-68d8-4fbe-a006-258079df0e3c",
    "membership_id": "c138f5f4-3d02-4fc9-b5d1-678516d2ca01",
    "role": "admin",
    "user_email": "rajesh@tataautocomp.com",
    "user_full_name": "Rajesh Sharma",
    "organization_name": "Tata AutoComp Pune",
    "organization_slug": "tata-autocomp-pune",
    "is_superuser": false
  }
  ```

### `GET /api/v1/organization/admin-only`
- **Description**: Role-restricted endpoint requiring `owner` or `admin` role in the active organization.
- **Response**: `200 OK` or `403 Forbidden` (`detail: "Insufficient role permissions..."`).

### `GET /api/v1/organization/reviewer-only`
- **Description**: Role-restricted endpoint requiring `owner`, `admin`, or `reviewer` role in the active organization.
- **Response**: `200 OK` or `403 Forbidden`.

---

## 4. Supplier Management Domain

All supplier endpoints are tenant-scoped: records belong strictly to the authenticated organization.

### `POST /api/v1/suppliers`
Create a new supplier within the authenticated organization.

- **Authentication**: Required (`OrganizationContext`).
- **Request Headers**: `X-Dev-User-Id`, `X-Dev-Organization-Id`.
- **Request Body (`application/json`)**:
  | Field | Type | Required | Description | Constraints |
  | :--- | :--- | :--- | :--- | :--- |
  | `legal_name` | string | Yes | Registered company name | Min 1, Max 255 chars |
  | `trade_name` | string | No | Commercial / DBA name | Max 255 chars |
  | `vendor_code` | string | No | Buyer internal ERP vendor code | Max 50 chars, unique per org |
  | `category` | string | No | Supplier category | Default `'raw_materials'` |
  | `tier` | string | No | Risk / supply tier | `'tier_1'`, `'tier_2'`, `'tier_3'` (default `'tier_2'`) |
  | `gstin` | string | No | 15-character Indian GSTIN | Format `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$` |
  | `pan` | string | No | 10-character Indian PAN | Format `^[A-Z]{5}[0-9]{4}[A-Z]{1}$` |
  | `primary_contact_name` | string | No | Contact person | Max 255 chars |
  | `primary_contact_email` | string | No | Contact email | Valid email format |
  | `primary_contact_phone` | string | No | Contact phone | Max 20 chars |
  | `city` | string | No | Operating city | Max 100 chars |
  | `state` | string | No | Operating state | Max 100 chars |
  | `pincode` | string | No | Postal code | Max 10 chars |
  | `notes` | string | No | Internal notes | Text |

- **Security & Constraints**:
  - `organization_id` cannot be supplied in the payload (`extra="forbid"` returns `422`).
  - Uniqueness of `vendor_code` and `gstin` within the organization is enforced. Duplicates return `409 Conflict`.
  - Transaction atomicity: Generates an `AuditEvent` with `action = "supplier.created"` committed in the same database transaction.

- **Response (`201 Created`)**:
  ```json
  {
    "id": "7f8b9e64-5390-48ee-a6a3-6eb8b778a5b2",
    "organization_id": "4e749293-68d8-4fbe-a006-258079df0e3c",
    "vendor_code": "V-1001",
    "legal_name": "Precision Fasteners Pvt Ltd",
    "trade_name": "Precision Fasteners",
    "category": "machined_components",
    "tier": "tier_1",
    "gstin": "27AABCU9603R1ZM",
    "pan": "AABCU9603R",
    "status": "draft",
    "compliance_status": "pending",
    "primary_contact_name": "Suresh Patil",
    "primary_contact_email": "suresh@precisionfasteners.com",
    "primary_contact_phone": "+91-9822012345",
    "city": "Pune",
    "state": "Maharashtra",
    "pincode": "411018",
    "notes": null,
    "created_at": "2026-11-05T09:40:00Z",
    "updated_at": "2026-11-05T09:40:00Z"
  }
  ```

---

### `GET /api/v1/suppliers`
List suppliers belonging to the caller's organization.

- **Authentication**: Required (`OrganizationContext`).
- **Query Parameters**:
  | Parameter | Type | Default | Description |
  | :--- | :--- | :--- | :--- |
  | `page` | integer | `1` | Page number ($\ge 1$) |
  | `page_size` | integer | `20` | Bounded page size ($1 \le \text{page\_size} \le 100$) |
  | `status` | string | null | Filter by lifecycle status (`draft`, `invited`, `onboarding`, `under_review`, `approved`, `rejected`, `archived`) |
  | `search` | string | null | Case-insensitive search across `legal_name`, `trade_name`, `vendor_code`, `gstin`, `pan` |

- **Response (`200 OK`)**:
  ```json
  {
    "items": [
      {
        "id": "7f8b9e64-5390-48ee-a6a3-6eb8b778a5b2",
        "organization_id": "4e749293-68d8-4fbe-a006-258079df0e3c",
        "vendor_code": "V-1001",
        "legal_name": "Precision Fasteners Pvt Ltd",
        "trade_name": "Precision Fasteners",
        "category": "machined_components",
        "tier": "tier_1",
        "gstin": "27AABCU9603R1ZM",
        "pan": "AABCU9603R",
        "status": "draft",
        "compliance_status": "pending",
        "primary_contact_name": "Suresh Patil",
        "primary_contact_email": "suresh@precisionfasteners.com",
        "primary_contact_phone": "+91-9822012345",
        "city": "Pune",
        "state": "Maharashtra",
        "pincode": "411018",
        "notes": null,
        "created_at": "2026-11-05T09:40:00Z",
        "updated_at": "2026-11-05T09:40:00Z"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 20,
    "total_pages": 1
  }
  ```

---

### `GET /api/v1/suppliers/{supplier_id}`
Retrieve detailed information for a single supplier.

- **Authentication**: Required (`OrganizationContext`).
- **Path Parameters**:
  - `supplier_id`: UUID of the supplier.
- **Tenant Security**: If the requested supplier belongs to another organization, returns `404 Not Found` (never leaks resource existence across tenant boundaries).
- **Response (`200 OK`)**: Returns `SupplierResponse` object.
- **Error Responses**:
  - `404 Not Found`: Supplier does not exist or belongs to another tenant.

---

### `PATCH /api/v1/suppliers/{supplier_id}`
Partially update an existing supplier within the caller's organization.

- **Authentication**: Required (`OrganizationContext`).
- **Path Parameters**:
  - `supplier_id`: UUID of the supplier.
- **Request Body (`application/json`)**:
  Permits partial updates to editable fields:
  - `legal_name`, `trade_name`, `vendor_code`, `category`, `tier`, `gstin`, `pan`, `primary_contact_name`, `primary_contact_email`, `primary_contact_phone`, `city`, `state`, `pincode`, `notes`.
  - Immutable fields (`id`, `organization_id`, `created_at`, `updated_at`, `status`) are rejected via `extra="forbid"` (`422`).
- **Business Rules**:
  - Updating an archived supplier returns `400 Bad Request` (`detail: "Cannot modify an archived supplier."`).
  - Unique constraint changes (`vendor_code`, `gstin`) are validated against other suppliers in the tenant.
  - Generates an `AuditEvent` with `action = "supplier.updated"`, storing `previous_state`, `new_state`, and `changed_fields`.
  - Database trigger `trg_suppliers_updated_at` automatically updates `updated_at`.
- **Response (`200 OK`)**: Returns updated `SupplierResponse`.

---

### `POST /api/v1/suppliers/{supplier_id}/archive`
Soft-archive a supplier.

- **Authentication**: Required (`OrganizationContext`).
- **Path Parameters**:
  - `supplier_id`: UUID of the supplier.
- **Semantics**:
  - Soft archival: sets `status = 'archived'`. The record, its associated documents, reviews, and audit events are permanently retained in PostgreSQL.
  - Never performs physical `DELETE`.
  - Repeated archival request returns `400 Bad Request` (`detail: "Supplier is already archived."`).
  - Generates an `AuditEvent` with `action = "supplier.archived"`.
- **Response (`200 OK`)**: Returns archived `SupplierResponse`.
- **Error Responses**:
  - `400 Bad Request`: If supplier is already archived.
  - `404 Not Found`: If supplier does not exist in caller's organization.

---

## 4. Document Types API

All endpoints require authentication via `OrganizationContext`. Operations are strictly tenant-scoped to the caller's organization.

### `POST /api/v1/document-types`
Create a new tenant-owned document type.

- **Authentication**: Required (`OrganizationContext`).
- **Request Body (`application/json`)**:
  - `code` (string, required): Alphanumeric code (2-50 chars, regex `^[a-z0-9_-]+$`). Unique within organization.
  - `name` (string, required): Display title (1-255 chars).
  - `description` (string, optional): Detailed explanation or issuing authority.
  - `category` (string, default `"statutory"`): One of `statutory`, `tax`, `quality`, `financial`, `legal`, `safety`.
  - `has_expiry` (boolean, default `false`): Whether documents of this type require expiry tracking.
  - `expected_fields` (array, default `[]`): Expected fields for extraction/validation (e.g. `["gstin", "legal_name"]`).
- **Response (`201 Created`)**: Returns created `DocumentTypeResponse`.
- **Error Responses**:
  - `409 Conflict`: If `code` already exists within caller's organization.
  - `422 Unprocessable Entity`: Validation failure on category, code format, or payload fields.

---

### `GET /api/v1/document-types`
List document types belonging to the caller's organization.

- **Authentication**: Required (`OrganizationContext`).
- **Query Parameters**:
  - `category` (optional): Filter by category.
  - `search` (optional): Case-insensitive search on `name`, `code`, or `description`.
  - `page` (optional, default `1`): 1-based page index.
  - `page_size` (optional, default `50`, max `100`): Items per page.
- **Response (`200 OK`)**: Returns `DocumentTypeListResponse` with pagination metadata (`total`, `page`, `page_size`, `total_pages`, `items`).

---

### `GET /api/v1/document-types/{document_type_id}`
Retrieve a single document type by ID.

- **Authentication**: Required (`OrganizationContext`).
- **Path Parameters**:
  - `document_type_id`: UUID of document type.
- **Response (`200 OK`)**: Returns `DocumentTypeResponse`.
- **Error Responses**:
  - `404 Not Found`: If document type does not exist or belongs to another organization.

---

### `PATCH /api/v1/document-types/{document_type_id}`
Partially update editable fields of a document type.

- **Authentication**: Required (`OrganizationContext`).
- **Path Parameters**:
  - `document_type_id`: UUID of document type.
- **Request Body (`application/json`)**:
  - `name`, `description`, `category`, `has_expiry`, `expected_fields`.
  - `code`, `id`, `organization_id`, and `is_system_preset` cannot be updated (`extra="forbid"` / immutable).
- **Response (`200 OK`)**: Returns updated `DocumentTypeResponse`.
- **Error Responses**:
  - `404 Not Found`: If document type does not exist or belongs to another organization.

---

## 5. Document Requirements API

All endpoints require authentication via `OrganizationContext`.

### `POST /api/v1/document-requirements`
Create an organization-specific document requirement policy.

- **Authentication**: Required (`OrganizationContext`).
- **Request Body (`application/json`)**:
  - `document_type_id` (UUID, required): ID of tenant-owned document type.
  - `name` (string, required): Requirement policy name (e.g. "Tier 1 Statutory GST Pack").
  - `supplier_category` (string, optional): Target category (e.g. `machined_components`) or `null` for all categories.
  - `supplier_tier` (string, optional): Target tier (e.g. `tier_1`) or `null` for all tiers.
  - `is_mandatory` (boolean, default `true`): Mandatory vs optional.
  - `validity_period_days` (integer, optional, `> 0`): Default validity duration.
  - `expiry_lead_notice_days` (integer, default `30`, `>= 0`): Advance warning threshold before expiry.
  - `is_active` (boolean, default `true`): Active policy status.
  - `validation_rules` (object, default `{}`): JSONB validation criteria.
- **Business Validations**:
  - `document_type_id` must belong to caller's organization (`400 Bad Request` if missing or cross-tenant).
  - Duplicate mappings `(document_type_id, supplier_category, supplier_tier)` within organization are rejected (`409 Conflict`).
- **Response (`201 Created`)**: Returns `DocumentRequirementResponse`.
- **Error Responses**:
  - `400 Bad Request`: Cross-tenant or invalid document type.
  - `409 Conflict`: Duplicate requirement configuration for this type/category/tier combination.

---

### `GET /api/v1/document-requirements`
List document requirements for the caller's organization.

- **Authentication**: Required (`OrganizationContext`).
- **Query Parameters**:
  - `document_type_id` (optional): Filter by document type UUID.
  - `supplier_category` (optional): Filter by supplier category.
  - `supplier_tier` (optional): Filter by supplier tier.
  - `is_active` (optional): Filter by active (`true`) or disabled (`false`).
  - `is_mandatory` (optional): Filter by mandatory (`true`) or optional (`false`).
  - `page` (optional, default `1`): 1-based page number.
  - `page_size` (optional, default `50`, max `100`): Items per page.
- **Response (`200 OK`)**: Returns `DocumentRequirementListResponse`.

---

### `GET /api/v1/document-requirements/{requirement_id}`
Retrieve a single document requirement by ID.

- **Authentication**: Required (`OrganizationContext`).
- **Path Parameters**:
  - `requirement_id`: UUID of requirement.
- **Response (`200 OK`)**: Returns `DocumentRequirementResponse` with populated `document_type` metadata.
- **Error Responses**:
  - `404 Not Found`: If requirement does not exist or belongs to another organization.

---

### `PATCH /api/v1/document-requirements/{requirement_id}`
Partially update an existing document requirement.

- **Authentication**: Required (`OrganizationContext`).
- **Path Parameters**:
  - `requirement_id`: UUID of requirement.
- **Request Body (`application/json`)**:
  - `name`, `supplier_category`, `supplier_tier`, `is_mandatory`, `validity_period_days`, `expiry_lead_notice_days`, `is_active`, `validation_rules`.
- **Response (`200 OK`)**: Returns updated `DocumentRequirementResponse`.
- **Error Responses**:
  - `404 Not Found`: If requirement does not exist in caller's organization.
  - `409 Conflict`: If changing category/tier results in a duplicate mapping.

---

### `POST /api/v1/document-requirements/{requirement_id}/enable`
Enable a document requirement.

- **Authentication**: Required (`OrganizationContext`).
- **Path Parameters**:
  - `requirement_id`: UUID of requirement.
- **Semantics**: Sets `is_active = true` and records `document_requirement.enabled` audit event.
- **Response (`200 OK`)**: Returns `DocumentRequirementResponse`.

---

### `POST /api/v1/document-requirements/{requirement_id}/disable`
Disable a document requirement.

- **Authentication**: Required (`OrganizationContext`).
- **Path Parameters**:
  - `requirement_id`: UUID of requirement.
- **Semantics**: Sets `is_active = false` and records `document_requirement.disabled` audit event. Disabled requirements are excluded from active supplier evaluation.
- **Response (`200 OK`)**: Returns `DocumentRequirementResponse`.

---

## 6. Supplier Requirements Evaluation API

### `GET /api/v1/suppliers/{supplier_id}/document-requirements`
Evaluate the applicable document requirements for a specific supplier.

- **Authentication**: Required (`OrganizationContext`).
- **Path Parameters**:
  - `supplier_id`: UUID of supplier.
- **Evaluation Rules**:
  1. Supplier must belong to caller's organization (`404 Not Found` for cross-tenant access).
  2. Only active requirements (`is_active = true`) are evaluated (disabled requirements are excluded).
  3. A requirement applies to the supplier if:
     - `supplier_category IS NULL OR supplier_category == supplier.category`
     - `supplier_tier IS NULL OR supplier_tier == supplier.tier`
  4. Applicable requirements are partitioned into:
     - `mandatory_requirements`: Applicable requirements where `is_mandatory = true`.
     - `optional_requirements`: Applicable requirements where `is_mandatory = false`.
- **Response (`200 OK`)**: Returns `SupplierRequirementsEvaluationResponse`:
  ```json
  {
    "supplier_id": "c1a938d8-...",
    "supplier_name": "Bharat Precision Machining Ltd",
    "supplier_category": "machined_components",
    "supplier_tier": "tier_1",
    "total_applicable": 3,
    "total_mandatory": 2,
    "total_optional": 1,
    "mandatory_requirements": [
      {
        "requirement_id": "...",
        "document_type_id": "...",
        "name": "Mandatory Statutory GST",
        "is_mandatory": true,
        "validity_period_days": null,
        "expiry_lead_notice_days": 30,
        "supplier_category": null,
        "supplier_tier": null,
        "validation_rules": {},
        "document_type_code": "gst_certificate",
        "document_type_name": "GST Certificate",
        "document_type_category": "statutory",
        "has_expiry": false,
        "expected_fields": ["gstin", "legal_name"]
      }
    ],
    "optional_requirements": [...]
  }
  ```
- **Error Responses**:
  - `404 Not Found`: Supplier does not exist or belongs to another tenant.
