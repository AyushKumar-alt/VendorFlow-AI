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
