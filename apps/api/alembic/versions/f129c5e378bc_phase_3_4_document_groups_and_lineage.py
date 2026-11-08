"""phase_3_4_document_groups_and_lineage

Revision ID: f129c5e378bc
Revises: 9b7d3318e727
Create Date: 2026-11-05 20:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f129c5e378bc"
down_revision: str | Sequence[str] | None = "9b7d3318e727"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply Phase 3.4 migration: lineage preflight, document groups, sites, requirement links, and backfill."""
    # -------------------------------------------------------------------------
    # 1. Lineage Preflight Assertions (Single DO $$ ... $$ block)
    # -------------------------------------------------------------------------
    op.execute(
        """
        DO $$
        DECLARE
            v_dangling_count INTEGER;
            v_self_ref_count INTEGER;
            v_mismatch_count INTEGER;
            v_fork_count INTEGER;
            v_cycle_count INTEGER;
            v_total_docs INTEGER;
            v_mapped_docs INTEGER;
            v_multi_root_count INTEGER;
        BEGIN
            -- 1. Dangling predecessor check
            SELECT COUNT(*) INTO v_dangling_count
            FROM supplier_documents c
            WHERE c.previous_version_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM supplier_documents p WHERE p.id = c.previous_version_id
              );
            IF v_dangling_count > 0 THEN
                RAISE EXCEPTION 'Lineage preflight validation failed: % documents have dangling previous_version_id', v_dangling_count;
            END IF;

            -- 2. Direct self-reference check
            SELECT COUNT(*) INTO v_self_ref_count
            FROM supplier_documents
            WHERE previous_version_id IS NOT NULL AND previous_version_id = id;
            IF v_self_ref_count > 0 THEN
                RAISE EXCEPTION 'Lineage preflight validation failed: % documents have self-referencing previous_version_id', v_self_ref_count;
            END IF;

            -- 3. Predecessor consistency: organization_id, supplier_id, document_type_id must match
            SELECT COUNT(*) INTO v_mismatch_count
            FROM supplier_documents c
            JOIN supplier_documents p ON c.previous_version_id = p.id
            WHERE c.organization_id != p.organization_id
               OR c.supplier_id != p.supplier_id
               OR c.document_type_id != p.document_type_id;
            IF v_mismatch_count > 0 THEN
                RAISE EXCEPTION 'Lineage preflight validation failed: % documents have predecessor organization/supplier/document_type mismatch', v_mismatch_count;
            END IF;

            -- 4. Branching fork check (multiple children for same predecessor)
            SELECT COUNT(*) INTO v_fork_count
            FROM (
                SELECT previous_version_id
                FROM supplier_documents
                WHERE previous_version_id IS NOT NULL
                GROUP BY previous_version_id
                HAVING COUNT(*) > 1
            ) forks;
            IF v_fork_count > 0 THEN
                RAISE EXCEPTION 'Lineage preflight validation failed: % predecessors have multiple child versions (branching detected)', v_fork_count;
            END IF;

            -- 5. Cycle detection (including rootless cycles and lassos with tails)
            WITH RECURSIVE back_traverse AS (
                SELECT 
                    id AS start_id,
                    previous_version_id AS current_prev,
                    ARRAY[id] AS visited,
                    1 AS depth,
                    FALSE AS is_cycle
                FROM supplier_documents
                WHERE previous_version_id IS NOT NULL

                UNION ALL

                SELECT
                    bt.start_id,
                    p.previous_version_id AS current_prev,
                    bt.visited || p.id AS visited,
                    bt.depth + 1,
                    (p.previous_version_id = ANY(bt.visited)) AS is_cycle
                FROM back_traverse bt
                JOIN supplier_documents p ON bt.current_prev = p.id
                WHERE NOT bt.is_cycle AND p.previous_version_id IS NOT NULL
            )
            SELECT COUNT(DISTINCT start_id) INTO v_cycle_count
            FROM back_traverse
            WHERE is_cycle = TRUE;
            IF v_cycle_count > 0 THEN
                RAISE EXCEPTION 'Lineage preflight validation failed: Lineage cycle detected involving % documents', v_cycle_count;
            END IF;

            -- 6. Reachability & coverage check
            SELECT COUNT(*) INTO v_total_docs FROM supplier_documents;
            
            WITH RECURSIVE root_finder AS (
                SELECT id AS doc_id, id AS root_id
                FROM supplier_documents
                WHERE previous_version_id IS NULL

                UNION ALL

                SELECT c.id AS doc_id, rf.root_id
                FROM supplier_documents c
                JOIN root_finder rf ON c.previous_version_id = rf.doc_id
            )
            SELECT COUNT(*) INTO v_mapped_docs FROM root_finder;

            IF v_total_docs != v_mapped_docs THEN
                RAISE EXCEPTION 'Lineage preflight validation failed: Total documents (%) does not match reachable documents (%)', v_total_docs, v_mapped_docs;
            END IF;

            -- 7. Document reachable from multiple roots
            WITH RECURSIVE root_finder AS (
                SELECT id AS doc_id, id AS root_id
                FROM supplier_documents
                WHERE previous_version_id IS NULL

                UNION ALL

                SELECT c.id AS doc_id, rf.root_id
                FROM supplier_documents c
                JOIN root_finder rf ON c.previous_version_id = rf.doc_id
            )
            SELECT COUNT(*) INTO v_multi_root_count
            FROM (
                SELECT doc_id FROM root_finder GROUP BY doc_id HAVING COUNT(*) > 1
            ) mr;

            IF v_multi_root_count > 0 THEN
                RAISE EXCEPTION 'Lineage preflight validation failed: Documents reachable from multiple roots';
            END IF;
        END $$;
        """
    )

    # -------------------------------------------------------------------------
    # 2. Create supplier_sites Table
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE supplier_sites (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            supplier_id UUID NOT NULL,
            site_code VARCHAR(50) NOT NULL,
            site_name VARCHAR(255) NOT NULL,
            address_line1 VARCHAR(255) NULL,
            address_line2 VARCHAR(255) NULL,
            city VARCHAR(100) NULL,
            state VARCHAR(100) NULL,
            postal_code VARCHAR(20) NULL,
            country_code VARCHAR(2) NOT NULL DEFAULT 'IN',
            gstin VARCHAR(15) NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_supplier_sites_supplier FOREIGN KEY (supplier_id, organization_id)
                REFERENCES suppliers(id, organization_id) ON DELETE RESTRICT,
            CONSTRAINT uq_supplier_sites_org_code UNIQUE (organization_id, supplier_id, site_code),
            CONSTRAINT uq_supplier_sites_id_org UNIQUE (id, organization_id),
            CONSTRAINT uq_supplier_sites_id_supp_org UNIQUE (id, supplier_id, organization_id),
            CONSTRAINT ck_supplier_sites_gstin_format CHECK (gstin IS NULL OR gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'),
            CONSTRAINT ck_supplier_sites_country_code CHECK (country_code ~ '^[A-Z]{2}$')
        );
        """
    )
    op.execute(
        """
        CREATE INDEX idx_supplier_sites_lookup
            ON supplier_sites (organization_id, supplier_id, is_active);
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_supplier_sites_updated_at
            BEFORE UPDATE ON supplier_sites
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_column();
        """
    )

    # -------------------------------------------------------------------------
    # 3. Create supplier_document_groups Table
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE supplier_document_groups (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            supplier_id UUID NOT NULL,
            document_type_id UUID NOT NULL,
            site_id UUID NULL,
            display_name VARCHAR(255) NOT NULL,
            coverage_scope VARCHAR(50) NOT NULL DEFAULT 'supplier',
            document_reference VARCHAR(255) NULL,
            issuing_authority VARCHAR(255) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_doc_groups_supplier FOREIGN KEY (supplier_id, organization_id)
                REFERENCES suppliers(id, organization_id) ON DELETE RESTRICT,
            CONSTRAINT fk_doc_groups_doc_type FOREIGN KEY (document_type_id, organization_id)
                REFERENCES document_types(id, organization_id) ON DELETE RESTRICT,
            CONSTRAINT fk_doc_groups_site FOREIGN KEY (site_id, supplier_id, organization_id)
                REFERENCES supplier_sites(id, supplier_id, organization_id) ON DELETE RESTRICT,
            CONSTRAINT ck_supplier_doc_groups_scope CHECK (coverage_scope IN ('supplier', 'site')),
            CONSTRAINT ck_supplier_doc_groups_site_scope CHECK (
                (coverage_scope = 'site' AND site_id IS NOT NULL) OR
                (coverage_scope = 'supplier' AND site_id IS NULL)
            ),
            CONSTRAINT uq_supplier_doc_groups_id_org UNIQUE (id, organization_id),
            CONSTRAINT uq_supplier_doc_groups_id_supp_org UNIQUE (id, supplier_id, organization_id),
            CONSTRAINT uq_supplier_doc_groups_id_lineage UNIQUE (id, supplier_id, document_type_id, organization_id)
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_supplier_doc_groups_site_ref
            ON supplier_document_groups (organization_id, supplier_id, document_type_id, site_id, document_reference)
            WHERE site_id IS NOT NULL AND document_reference IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_supplier_doc_groups_supplier_ref
            ON supplier_document_groups (organization_id, supplier_id, document_type_id, document_reference)
            WHERE site_id IS NULL AND document_reference IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX idx_supplier_doc_groups_lookup
            ON supplier_document_groups (organization_id, supplier_id, document_type_id);
        """
    )
    op.execute(
        """
        CREATE INDEX idx_supplier_doc_groups_site
            ON supplier_document_groups (organization_id, site_id)
            WHERE site_id IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_supplier_document_groups_updated_at
            BEFORE UPDATE ON supplier_document_groups
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_column();
        """
    )

    # -------------------------------------------------------------------------
    # 4. Create document_group_requirement_links Table
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE document_group_requirement_links (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            document_group_id UUID NOT NULL,
            requirement_id UUID NOT NULL,
            link_origin VARCHAR(50) NOT NULL DEFAULT 'legacy_backfill',
            linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_group_req_links_group FOREIGN KEY (document_group_id, organization_id)
                REFERENCES supplier_document_groups(id, organization_id) ON DELETE RESTRICT,
            CONSTRAINT fk_group_req_links_requirement FOREIGN KEY (requirement_id, organization_id)
                REFERENCES document_requirements(id, organization_id) ON DELETE RESTRICT,
            CONSTRAINT ck_group_req_links_origin CHECK (link_origin IN ('legacy_backfill', 'manual', 'policy_engine')),
            CONSTRAINT uq_group_req_links_group_req UNIQUE (document_group_id, requirement_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX idx_group_req_links_req
            ON document_group_requirement_links (organization_id, requirement_id);
        """
    )
    op.execute(
        """
        CREATE INDEX idx_group_req_links_group
            ON document_group_requirement_links (organization_id, document_group_id);
        """
    )

    # -------------------------------------------------------------------------
    # 5. Create supplier_document_migration_discrepancies Table & Triggers
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE supplier_document_migration_discrepancies (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            document_id UUID NOT NULL,
            discrepancy_type VARCHAR(100) NOT NULL,
            legacy_status VARCHAR(50) NULL,
            latest_review_decision VARCHAR(50) NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_doc_discrepancies_doc FOREIGN KEY (document_id, organization_id)
                REFERENCES supplier_documents(id, organization_id) ON DELETE RESTRICT,
            CONSTRAINT ck_discrepancies_type CHECK (
                discrepancy_type IN (
                    'status_review_mismatch',
                    'unlinked_requirement',
                    'orphan_extracted',
                    'expired_effective',
                    'rootless_cycle_repaired',
                    'competing_approval'
                )
            )
        );
        """
    )
    op.execute(
        """
        CREATE INDEX idx_doc_discrepancies_lookup
            ON supplier_document_migration_discrepancies (organization_id, discrepancy_type);
        """
    )
    op.execute(
        """
        CREATE INDEX idx_doc_discrepancies_doc_type
            ON supplier_document_migration_discrepancies (document_id, discrepancy_type);
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_protect_migration_discrepancies_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'supplier_document_migration_discrepancies is append-only and cannot be updated or deleted';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_discrepancies_mutation
            BEFORE UPDATE OR DELETE ON supplier_document_migration_discrepancies
            FOR EACH ROW
            EXECUTE FUNCTION fn_protect_migration_discrepancies_mutation();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_protect_migration_discrepancies_truncate()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'supplier_document_migration_discrepancies cannot be truncated';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_discrepancies_truncate
            BEFORE TRUNCATE ON supplier_document_migration_discrepancies
            FOR EACH STATEMENT
            EXECUTE FUNCTION fn_protect_migration_discrepancies_truncate();
        """
    )

    # -------------------------------------------------------------------------
    # 6. Alter document_requirements Table (supporting 'supplier', 'site', 'either')
    # -------------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE document_requirements
            ADD COLUMN applicability_scope VARCHAR(50) NOT NULL DEFAULT 'supplier',
            ADD COLUMN precedence_order INTEGER NOT NULL DEFAULT 100,
            ADD CONSTRAINT ck_doc_requirements_applicability_scope CHECK (applicability_scope IN ('supplier', 'site', 'either')),
            ADD CONSTRAINT ck_doc_requirements_precedence_order CHECK (precedence_order >= 0);
        """
    )

    # -------------------------------------------------------------------------
    # 7. Add Temporary Columns on supplier_documents for Backfill
    # -------------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE supplier_documents
            ADD COLUMN document_group_id UUID NULL,
            ADD COLUMN processing_status VARCHAR(50) NULL,
            ADD COLUMN legacy_status VARCHAR(50) NULL,
            ADD COLUMN approval_provenance VARCHAR(50) NULL,
            ADD COLUMN legacy_requirement_id UUID NULL,
            ADD COLUMN is_effective_version BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )

    # -------------------------------------------------------------------------
    # 8. Data Backfill (executed in a single DO $$ ... $$ block for asyncpg)
    # -------------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            -- 1. Create temporary table mapping roots to new group IDs
            CREATE TEMP TABLE _migration_group_map AS
            SELECT 
                d.id AS root_id,
                gen_random_uuid() AS group_id,
                d.organization_id,
                d.supplier_id,
                d.document_type_id,
                dt.name AS display_name,
                d.created_at,
                d.updated_at
            FROM supplier_documents d
            JOIN document_types dt ON dt.id = d.document_type_id AND dt.organization_id = d.organization_id
            WHERE d.previous_version_id IS NULL;

            -- 2. Insert into supplier_document_groups
            INSERT INTO supplier_document_groups (
                id, organization_id, supplier_id, document_type_id, site_id,
                display_name, coverage_scope, document_reference, issuing_authority,
                created_at, updated_at
            )
            SELECT 
                group_id, organization_id, supplier_id, document_type_id, NULL,
                display_name, 'supplier', NULL, NULL,
                created_at, updated_at
            FROM _migration_group_map;

            -- 3. Map all documents via recursive CTE to their group and calculate lineage_depth
            CREATE TEMP TABLE _migration_doc_lineage AS
            WITH RECURSIVE lineage_tree AS (
                SELECT 
                    d.id AS doc_id,
                    m.group_id,
                    d.organization_id,
                    d.supplier_id,
                    d.document_type_id,
                    d.requirement_id,
                    d.status AS legacy_status,
                    d.expiry_date,
                    1 AS lineage_depth
                FROM supplier_documents d
                JOIN _migration_group_map m ON d.id = m.root_id
                WHERE d.previous_version_id IS NULL

                UNION ALL

                SELECT 
                    c.id AS doc_id,
                    lt.group_id,
                    c.organization_id,
                    c.supplier_id,
                    c.document_type_id,
                    c.requirement_id,
                    c.status AS legacy_status,
                    c.expiry_date,
                    lt.lineage_depth + 1
                FROM supplier_documents c
                JOIN lineage_tree lt ON c.previous_version_id = lt.doc_id
            )
            SELECT * FROM lineage_tree;

            -- 4. Calculate latest review for every document
            CREATE TEMP TABLE _migration_latest_reviews AS
            WITH ranked_reviews AS (
                SELECT 
                    r.document_id,
                    r.decision,
                    ROW_NUMBER() OVER (
                        PARTITION BY r.document_id 
                        ORDER BY r.created_at DESC, r.id DESC
                    ) as rn
                FROM document_reviews r
            )
            SELECT document_id, decision AS latest_decision
            FROM ranked_reviews
            WHERE rn = 1;

            -- 5. Calculate document attributes: processing_status, approval_provenance, is_approved, is_effective_version
            -- Provenance describes historical approval origin ('review', 'legacy_verified', or NULL).
            -- Approval eligibility (is_approved) strictly evaluates whether the document is currently approved.
            -- All qualifying approved versions are ranked together by lineage_depth DESC with deterministic doc_id tie-breaker.
            CREATE TEMP TABLE _migration_doc_computed AS
            WITH doc_inter AS (
                SELECT 
                    dl.doc_id,
                    dl.group_id,
                    dl.organization_id,
                    dl.requirement_id,
                    dl.legacy_status,
                    dl.expiry_date,
                    dl.lineage_depth,
                    lr.latest_decision,
                    -- processing_status: 'extracted' is terminal extraction success state
                    CASE 
                        WHEN dl.legacy_status = 'uploaded' THEN 'uploaded'
                        WHEN dl.legacy_status = 'processing' THEN 'processing'
                        ELSE 'extracted'
                    END AS processing_status,
                    -- approval_provenance: historical source of approval origin
                    CASE 
                        WHEN lr.latest_decision = 'approved' THEN 'review'
                        WHEN dl.legacy_status IN ('verified', 'expired') THEN 'legacy_verified'
                        ELSE NULL
                    END AS approval_provenance,
                    -- is_approved: current approval eligibility (distinct from historical provenance)
                    CASE
                        WHEN lr.latest_decision = 'approved' THEN TRUE
                        WHEN lr.latest_decision IS NULL AND dl.legacy_status IN ('verified', 'expired') THEN TRUE
                        ELSE FALSE
                    END AS is_approved
                FROM _migration_doc_lineage dl
                LEFT JOIN _migration_latest_reviews lr ON dl.doc_id = lr.document_id
            ),
            ranked_effective AS (
                SELECT 
                    di.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY di.group_id
                        ORDER BY 
                            di.is_approved DESC,
                            di.lineage_depth DESC,
                            di.doc_id DESC
                    ) AS effective_rank
                FROM doc_inter di
            )
            SELECT 
                doc_id,
                group_id,
                organization_id,
                requirement_id,
                legacy_status,
                expiry_date,
                lineage_depth,
                latest_decision,
                processing_status,
                approval_provenance,
                (di.is_approved = TRUE AND di.effective_rank = 1) AS is_effective_version
            FROM ranked_effective di;

            -- 6. Update supplier_documents
            UPDATE supplier_documents sd
            SET 
                document_group_id = mc.group_id,
                processing_status = mc.processing_status,
                legacy_status = mc.legacy_status,
                approval_provenance = mc.approval_provenance,
                legacy_requirement_id = mc.requirement_id,
                is_effective_version = mc.is_effective_version
            FROM _migration_doc_computed mc
            WHERE sd.id = mc.doc_id;

            -- 7. Insert requirement links for groups
            INSERT INTO document_group_requirement_links (
                id, organization_id, document_group_id, requirement_id, link_origin, linked_at
            )
            SELECT 
                gen_random_uuid(),
                dl.organization_id,
                dl.group_id,
                dl.requirement_id,
                'legacy_backfill',
                now()
            FROM _migration_doc_lineage dl
            WHERE dl.requirement_id IS NOT NULL
            GROUP BY dl.organization_id, dl.group_id, dl.requirement_id;

            -- 8. Discrepancies generation:
            -- (a) status_review_mismatch:
            INSERT INTO supplier_document_migration_discrepancies (
                id, organization_id, document_id, discrepancy_type, legacy_status, latest_review_decision, details
            )
            SELECT 
                gen_random_uuid(),
                mc.organization_id,
                mc.doc_id,
                'status_review_mismatch',
                mc.legacy_status,
                mc.latest_decision,
                jsonb_build_object(
                    'reason', 'Mismatch between legacy status and latest review decision',
                    'legacy_status', mc.legacy_status,
                    'latest_decision', mc.latest_decision
                )
            FROM _migration_doc_computed mc
            WHERE (mc.legacy_status = 'verified' AND (mc.latest_decision IS NULL OR mc.latest_decision != 'approved'))
               OR (mc.legacy_status IN ('rejected', 'action_required') AND mc.latest_decision = 'approved');

            -- (b) competing_approval:
            INSERT INTO supplier_document_migration_discrepancies (
                id, organization_id, document_id, discrepancy_type, legacy_status, latest_review_decision, details
            )
            SELECT 
                gen_random_uuid(),
                mc.organization_id,
                mc.doc_id,
                'competing_approval',
                mc.legacy_status,
                mc.latest_decision,
                jsonb_build_object(
                    'group_id', mc.group_id,
                    'lineage_depth', mc.lineage_depth,
                    'reason', 'Older approved version superseded by later approved version'
                )
            FROM _migration_doc_computed mc
            WHERE mc.approval_provenance IS NOT NULL
              AND mc.is_effective_version = FALSE
              AND EXISTS (
                  SELECT 1 FROM _migration_doc_computed other 
                  WHERE other.group_id = mc.group_id AND other.is_effective_version = TRUE
              );

            -- (c) expired_effective:
            INSERT INTO supplier_document_migration_discrepancies (
                id, organization_id, document_id, discrepancy_type, legacy_status, latest_review_decision, details
            )
            SELECT 
                gen_random_uuid(),
                mc.organization_id,
                mc.doc_id,
                'expired_effective',
                mc.legacy_status,
                mc.latest_decision,
                jsonb_build_object(
                    'expiry_date', mc.expiry_date,
                    'reason', 'Effective document version is expired according to expiry_date'
                )
            FROM _migration_doc_computed mc
            WHERE mc.is_effective_version = TRUE
              AND mc.expiry_date IS NOT NULL
              AND mc.expiry_date < CURRENT_DATE;

            -- (d) unlinked_requirement:
            INSERT INTO supplier_document_migration_discrepancies (
                id, organization_id, document_id, discrepancy_type, legacy_status, latest_review_decision, details
            )
            SELECT 
                gen_random_uuid(),
                mc.organization_id,
                mc.doc_id,
                'unlinked_requirement',
                mc.legacy_status,
                mc.latest_decision,
                jsonb_build_object(
                    'reason', 'Document had no requirement assigned at migration time'
                )
            FROM _migration_doc_computed mc
            WHERE mc.requirement_id IS NULL;

            -- (e) orphan_extracted:
            INSERT INTO supplier_document_migration_discrepancies (
                id, organization_id, document_id, discrepancy_type, legacy_status, latest_review_decision, details
            )
            SELECT 
                gen_random_uuid(),
                mc.organization_id,
                mc.doc_id,
                'orphan_extracted',
                mc.legacy_status,
                mc.latest_decision,
                jsonb_build_object(
                    'reason', 'Document has extracted status with no reviews'
                )
            FROM _migration_doc_computed mc
            WHERE mc.legacy_status = 'extracted'
              AND mc.latest_decision IS NULL;

            -- Clean up temporary tables
            DROP TABLE _migration_doc_computed;
            DROP TABLE _migration_latest_reviews;
            DROP TABLE _migration_doc_lineage;
            DROP TABLE _migration_group_map;
        END $$;
        """
    )

    # -------------------------------------------------------------------------
    # 9. Verify Backfill Assertions (Single DO $$ ... $$ block)
    # -------------------------------------------------------------------------
    op.execute(
        """
        DO $$
        DECLARE
            v_unmapped_docs INTEGER;
            v_unmapped_processing INTEGER;
            v_multi_effective INTEGER;
        BEGIN
            SELECT COUNT(*) INTO v_unmapped_docs FROM supplier_documents WHERE document_group_id IS NULL;
            IF v_unmapped_docs > 0 THEN
                RAISE EXCEPTION 'Backfill assertion failed: % documents were not mapped to a group', v_unmapped_docs;
            END IF;

            SELECT COUNT(*) INTO v_unmapped_processing FROM supplier_documents WHERE processing_status IS NULL;
            IF v_unmapped_processing > 0 THEN
                RAISE EXCEPTION 'Backfill assertion failed: % documents have null processing_status', v_unmapped_processing;
            END IF;

            SELECT COUNT(*) INTO v_multi_effective
            FROM (
                SELECT document_group_id FROM supplier_documents WHERE is_effective_version = TRUE GROUP BY document_group_id HAVING COUNT(*) > 1
            ) me;
            IF v_multi_effective > 0 THEN
                RAISE EXCEPTION 'Backfill assertion failed: % groups have multiple effective versions', v_multi_effective;
            END IF;
        END $$;
        """
    )

    # -------------------------------------------------------------------------
    # 10. Drop Legacy Columns & Add Final Schema Constraints/Indexes
    # -------------------------------------------------------------------------
    op.execute(
        "ALTER TABLE supplier_documents ALTER COLUMN document_group_id SET NOT NULL;"
    )
    op.execute(
        "ALTER TABLE supplier_documents ALTER COLUMN processing_status SET NOT NULL;"
    )
    op.execute("DROP INDEX IF EXISTS uq_supplier_docs_active_version;")
    op.execute("DROP INDEX IF EXISTS idx_supplier_docs_expiry;")
    op.execute("DROP INDEX IF EXISTS idx_supplier_docs_supplier;")
    op.execute(
        "ALTER TABLE supplier_documents DROP CONSTRAINT IF EXISTS fk_supplier_documents_requirement;"
    )
    op.execute("ALTER TABLE supplier_documents DROP COLUMN IF EXISTS requirement_id;")
    op.execute(
        "ALTER TABLE supplier_documents DROP COLUMN IF EXISTS is_active_version;"
    )
    op.execute(
        "ALTER TABLE supplier_documents DROP CONSTRAINT IF EXISTS ck_supplier_documents_chk_supplier_docs_status;"
    )
    op.execute("ALTER TABLE supplier_documents DROP COLUMN IF EXISTS status;")
    op.execute(
        "ALTER TABLE supplier_documents DROP CONSTRAINT IF EXISTS uq_supplier_documents_version;"
    )

    op.execute(
        """
        ALTER TABLE supplier_documents
            ADD CONSTRAINT ck_supplier_docs_processing_status
                CHECK (processing_status IN ('uploaded', 'processing', 'extracted', 'failed')),
            ADD CONSTRAINT ck_supplier_docs_legacy_status
                CHECK (legacy_status IS NULL OR legacy_status IN ('uploaded', 'processing', 'extracted', 'verified', 'rejected', 'action_required', 'expired', 'superseded')),
            ADD CONSTRAINT ck_supplier_docs_approval_provenance
                CHECK (approval_provenance IS NULL OR approval_provenance IN ('review', 'legacy_verified')),
            ADD CONSTRAINT fk_supplier_documents_group
                FOREIGN KEY (document_group_id, supplier_id, document_type_id, organization_id)
                REFERENCES supplier_document_groups(id, supplier_id, document_type_id, organization_id)
                ON DELETE RESTRICT,
            ADD CONSTRAINT fk_supplier_documents_legacy_req
                FOREIGN KEY (legacy_requirement_id, organization_id)
                REFERENCES document_requirements(id, organization_id)
                ON DELETE RESTRICT,
            ADD CONSTRAINT uq_supplier_documents_group_version
                UNIQUE (document_group_id, version);
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_supplier_docs_effective_group
            ON supplier_documents (document_group_id)
            WHERE is_effective_version = TRUE;
        """
    )
    op.execute(
        """
        CREATE INDEX idx_supplier_docs_group
            ON supplier_documents (organization_id, document_group_id);
        """
    )
    op.execute(
        """
        CREATE INDEX idx_supplier_docs_effective_expiry
            ON supplier_documents (organization_id, expiry_date)
            WHERE is_effective_version = TRUE AND expiry_date IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX idx_supplier_docs_processing
            ON supplier_documents (organization_id, processing_status);
        """
    )

    # -------------------------------------------------------------------------
    # 11. Database Triggers for Lineage, Group Identity & Concurrency Protection
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_lock_group_on_document_insert()
        RETURNS TRIGGER AS $$
        BEGIN
            PERFORM 1 FROM supplier_document_groups
            WHERE id = NEW.document_group_id
            FOR UPDATE;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_lock_group_on_document_insert
            BEFORE INSERT ON supplier_documents
            FOR EACH ROW
            EXECUTE FUNCTION fn_lock_group_on_document_insert();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_protect_supplier_document_lineage()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.organization_id != NEW.organization_id THEN
                RAISE EXCEPTION 'organization_id on supplier_documents is immutable';
            END IF;
            IF OLD.supplier_id != NEW.supplier_id THEN
                RAISE EXCEPTION 'supplier_id on supplier_documents is immutable';
            END IF;
            IF OLD.document_type_id != NEW.document_type_id THEN
                RAISE EXCEPTION 'document_type_id on supplier_documents is immutable';
            END IF;
            IF OLD.document_group_id != NEW.document_group_id THEN
                RAISE EXCEPTION 'document_group_id on supplier_documents is immutable';
            END IF;
            IF OLD.previous_version_id IS DISTINCT FROM NEW.previous_version_id THEN
                RAISE EXCEPTION 'previous_version_id on supplier_documents is immutable';
            END IF;
            IF OLD.version != NEW.version THEN
                RAISE EXCEPTION 'version on supplier_documents is immutable';
            END IF;
            IF OLD.approval_provenance IS DISTINCT FROM NEW.approval_provenance THEN
                RAISE EXCEPTION 'approval_provenance on supplier_documents is immutable';
            END IF;
            IF OLD.legacy_status IS DISTINCT FROM NEW.legacy_status THEN
                RAISE EXCEPTION 'legacy_status on supplier_documents is immutable';
            END IF;
            IF OLD.legacy_requirement_id IS DISTINCT FROM NEW.legacy_requirement_id THEN
                RAISE EXCEPTION 'legacy_requirement_id on supplier_documents is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_supplier_document_lineage
            BEFORE UPDATE ON supplier_documents
            FOR EACH ROW
            EXECUTE FUNCTION fn_protect_supplier_document_lineage();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_freeze_supplier_document_group_identity()
        RETURNS TRIGGER AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM supplier_documents WHERE document_group_id = OLD.id) THEN
                IF OLD.organization_id != NEW.organization_id THEN
                    RAISE EXCEPTION 'Cannot modify organization_id of supplier_document_groups with existing documents';
                END IF;
                IF OLD.supplier_id != NEW.supplier_id THEN
                    RAISE EXCEPTION 'Cannot modify supplier_id of supplier_document_groups with existing documents';
                END IF;
                IF OLD.document_type_id != NEW.document_type_id THEN
                    RAISE EXCEPTION 'Cannot modify document_type_id of supplier_document_groups with existing documents';
                END IF;
                IF OLD.site_id IS DISTINCT FROM NEW.site_id THEN
                    RAISE EXCEPTION 'Cannot modify site_id of supplier_document_groups with existing documents';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_freeze_supplier_document_group_identity
            BEFORE UPDATE ON supplier_document_groups
            FOR EACH ROW
            EXECUTE FUNCTION fn_freeze_supplier_document_group_identity();
        """
    )


def downgrade() -> None:
    """Revert Phase 3.4 migration after verifying strict preflight downgrade conditions."""
    # -------------------------------------------------------------------------
    # 1. Downgrade Preflight Validations (Single DO $$ ... $$ block)
    # -------------------------------------------------------------------------
    op.execute(
        """
        DO $$
        DECLARE
            v_sites_count INTEGER;
            v_post_mig_docs INTEGER;
            v_failed_docs INTEGER;
            v_custom_req_count INTEGER;
            v_custom_group_metadata INTEGER;
            v_custom_group_name INTEGER;
            v_multi_effective INTEGER;
            v_revoked_effective INTEGER;
            v_missing_req INTEGER;
            v_version_collisions INTEGER;
            v_added_link_group UUID;
            v_added_link_req UUID;
            v_removed_link_group UUID;
            v_removed_link_req UUID;
        BEGIN
            -- 1. Check if supplier_sites has any rows
            SELECT COUNT(*) INTO v_sites_count FROM supplier_sites;
            IF v_sites_count > 0 THEN
                RAISE EXCEPTION 'Downgrade preflight failed: supplier_sites table is not empty (contains % records). Cannot downgrade with site data.', v_sites_count;
            END IF;

            -- 2. Check for post-migration documents (legacy_status IS NULL)
            SELECT COUNT(*) INTO v_post_mig_docs FROM supplier_documents WHERE legacy_status IS NULL;
            IF v_post_mig_docs > 0 THEN
                RAISE EXCEPTION 'Downgrade preflight failed: % post-migration documents exist (legacy_status IS NULL). Cannot downgrade without data loss.', v_post_mig_docs;
            END IF;

            -- 3. Check for documents with processing_status = 'failed'
            SELECT COUNT(*) INTO v_failed_docs FROM supplier_documents WHERE processing_status = 'failed';
            IF v_failed_docs > 0 THEN
                RAISE EXCEPTION 'Downgrade preflight failed: % documents have processing_status = ''failed''. Cannot map to legacy status.', v_failed_docs;
            END IF;

            -- 4. Check for custom document_requirements attributes (scope != 'supplier' or precedence != 100)
            SELECT COUNT(*) INTO v_custom_req_count
            FROM document_requirements
            WHERE applicability_scope != 'supplier' OR precedence_order != 100;
            IF v_custom_req_count > 0 THEN
                RAISE EXCEPTION 'Downgrade preflight failed: % requirements have non-default applicability_scope or precedence_order.', v_custom_req_count;
            END IF;

            -- 5d. Check for requirement links added post-migration
            -- Detects every group link without a corresponding non-null legacy requirement ID
            -- among that group's documents, and rejects empty groups with links.
            -- Note: Former Check 5 (rejecting >1 link), Check 5b (rejecting multi-requirement groups),
            -- and Check 5c (cross-product join mismatch) are omitted: Checks 5d and 5e provide
            -- complete set-equality validation for multi-requirement groups.
            SELECT l.document_group_id, l.requirement_id
            INTO v_added_link_group, v_added_link_req
            FROM document_group_requirement_links l
            WHERE NOT EXISTS (
                SELECT 1 FROM supplier_documents d
                WHERE d.document_group_id = l.document_group_id
                  AND d.legacy_requirement_id = l.requirement_id
            )
            LIMIT 1;
            IF v_added_link_group IS NOT NULL THEN
                RAISE EXCEPTION 'Downgrade preflight failed: Document group % has requirement link % added post-migration (not present in legacy document requirements).', v_added_link_group, v_added_link_req;
            END IF;

            -- 5e. Check for legacy requirement links removed post-migration
            -- Detects every non-null legacy requirement ID without a corresponding group link.
            SELECT d.document_group_id, d.legacy_requirement_id
            INTO v_removed_link_group, v_removed_link_req
            FROM supplier_documents d
            WHERE d.legacy_requirement_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM document_group_requirement_links l
                  WHERE l.document_group_id = d.document_group_id
                    AND l.requirement_id = d.legacy_requirement_id
              )
            LIMIT 1;
            IF v_removed_link_group IS NOT NULL THEN
                RAISE EXCEPTION 'Downgrade preflight failed: Document group % has legacy requirement % which is no longer linked in document_group_requirement_links (requirement association was removed post-migration).', v_removed_link_group, v_removed_link_req;
            END IF;

            -- 6. Check for custom group metadata
            SELECT COUNT(*) INTO v_custom_group_metadata
            FROM supplier_document_groups
            WHERE coverage_scope != 'supplier' OR site_id IS NOT NULL OR document_reference IS NOT NULL OR issuing_authority IS NOT NULL;
            IF v_custom_group_metadata > 0 THEN
                RAISE EXCEPTION 'Downgrade preflight failed: % document groups have custom metadata (site_id, coverage_scope, document_reference, issuing_authority).', v_custom_group_metadata;
            END IF;

            -- 7. Check for custom group display_name
            SELECT COUNT(*) INTO v_custom_group_name
            FROM supplier_document_groups g
            JOIN document_types dt ON g.document_type_id = dt.id AND g.organization_id = dt.organization_id
            WHERE g.display_name != dt.name;
            IF v_custom_group_name > 0 THEN
                RAISE EXCEPTION 'Downgrade preflight failed: % document groups have custom display_name.', v_custom_group_name;
            END IF;

            -- 8. Check for multiple effective versions per (supplier_id, document_type_id)
            SELECT COUNT(*) INTO v_multi_effective
            FROM (
                SELECT supplier_id, document_type_id
                FROM supplier_documents
                WHERE is_effective_version = TRUE
                GROUP BY supplier_id, document_type_id
                HAVING COUNT(*) > 1
            ) me;
            IF v_multi_effective > 0 THEN
                RAISE EXCEPTION 'Downgrade preflight failed: % (supplier_id, document_type_id) pairs have multiple effective versions.', v_multi_effective;
            END IF;

            -- 9. Check for explicitly revoked effective documents (effective version with latest review rejected or revision_requested)
            WITH ranked_reviews AS (
                SELECT 
                    r.document_id,
                    r.decision,
                    ROW_NUMBER() OVER (
                        PARTITION BY r.document_id 
                        ORDER BY r.created_at DESC, r.id DESC
                    ) as rn
                FROM document_reviews r
            )
            SELECT COUNT(*) INTO v_revoked_effective
            FROM supplier_documents d
            JOIN ranked_reviews rr ON d.id = rr.document_id AND rr.rn = 1
            WHERE d.is_effective_version = TRUE AND rr.decision IN ('rejected', 'revision_requested');
            IF v_revoked_effective > 0 THEN
                RAISE EXCEPTION 'Downgrade preflight failed: % effective documents have been explicitly revoked by latest review.', v_revoked_effective;
            END IF;

            -- 10. Check for ambiguous requirement reconstruction
            SELECT COUNT(*) INTO v_missing_req
            FROM supplier_documents d
            WHERE d.legacy_requirement_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM document_requirements r
                  WHERE r.id = d.legacy_requirement_id AND r.organization_id = d.organization_id
              );
            IF v_missing_req > 0 THEN
                RAISE EXCEPTION 'Downgrade preflight failed: % documents reference missing requirements.', v_missing_req;
            END IF;

            -- 11. Check for legacy version collisions on (supplier_id, document_type_id, version)
            SELECT COUNT(*) INTO v_version_collisions
            FROM (
                SELECT supplier_id, document_type_id, version
                FROM supplier_documents
                GROUP BY supplier_id, document_type_id, version
                HAVING COUNT(*) > 1
            ) collisions;
            IF v_version_collisions > 0 THEN
                RAISE EXCEPTION 'Downgrade preflight failed: % (supplier_id, document_type_id, version) tuples have duplicate records. Cannot restore legacy unique constraint uq_supplier_documents_version.', v_version_collisions;
            END IF;
        END $$;
        """
    )

    # -------------------------------------------------------------------------
    # 2. Drop Phase 3.4 Triggers & Trigger Functions
    # -------------------------------------------------------------------------
    op.execute(
        "DROP TRIGGER IF EXISTS trg_lock_group_on_document_insert ON supplier_documents;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_protect_supplier_document_lineage ON supplier_documents;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_freeze_supplier_document_group_identity ON supplier_document_groups;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_protect_discrepancies_mutation ON supplier_document_migration_discrepancies;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_protect_discrepancies_truncate ON supplier_document_migration_discrepancies;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_supplier_sites_updated_at ON supplier_sites;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_supplier_document_groups_updated_at ON supplier_document_groups;"
    )

    op.execute("DROP FUNCTION IF EXISTS fn_lock_group_on_document_insert();")
    op.execute("DROP FUNCTION IF EXISTS fn_protect_supplier_document_lineage();")
    op.execute("DROP FUNCTION IF EXISTS fn_freeze_supplier_document_group_identity();")
    op.execute("DROP FUNCTION IF EXISTS fn_protect_migration_discrepancies_mutation();")
    op.execute("DROP FUNCTION IF EXISTS fn_protect_migration_discrepancies_truncate();")

    # -------------------------------------------------------------------------
    # 3. Add Back Legacy Columns on supplier_documents
    # -------------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE supplier_documents
            ADD COLUMN requirement_id UUID NULL,
            ADD COLUMN is_active_version BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN status VARCHAR(50) NULL;
        """
    )

    # -------------------------------------------------------------------------
    # 4. Restore Legacy Data on supplier_documents
    # -------------------------------------------------------------------------
    op.execute(
        """
        UPDATE supplier_documents
        SET
            requirement_id = legacy_requirement_id,
            is_active_version = is_effective_version,
            status = legacy_status;
        """
    )
    op.execute("ALTER TABLE supplier_documents ALTER COLUMN status SET NOT NULL;")
    op.execute(
        """
        ALTER TABLE supplier_documents
            ADD CONSTRAINT ck_supplier_documents_chk_supplier_docs_status
            CHECK (status IN ('uploaded', 'processing', 'extracted', 'verified', 'rejected', 'action_required', 'expired', 'superseded'));
        """
    )

    # -------------------------------------------------------------------------
    # 5. Restore Legacy Foreign Keys & Indexes
    # -------------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE supplier_documents
            ADD CONSTRAINT fk_supplier_documents_requirement
            FOREIGN KEY (requirement_id, document_type_id, organization_id)
            REFERENCES document_requirements(id, document_type_id, organization_id)
            ON DELETE RESTRICT;
        """
    )
    op.execute(
        """
        ALTER TABLE supplier_documents
            ADD CONSTRAINT uq_supplier_documents_version
            UNIQUE (supplier_id, document_type_id, version);
        """
    )
    op.execute(
        """
        CREATE INDEX idx_supplier_docs_expiry
            ON supplier_documents (organization_id, expiry_date)
            WHERE is_active_version = TRUE AND expiry_date IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX idx_supplier_docs_supplier
            ON supplier_documents (organization_id, supplier_id, is_active_version);
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_supplier_docs_active_version
            ON supplier_documents (supplier_id, document_type_id)
            WHERE is_active_version = TRUE;
        """
    )

    # -------------------------------------------------------------------------
    # 6. Drop Phase 3.4 Constraints, Indexes & Columns on supplier_documents
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS idx_supplier_docs_processing;")
    op.execute("DROP INDEX IF EXISTS idx_supplier_docs_effective_expiry;")
    op.execute("DROP INDEX IF EXISTS idx_supplier_docs_group;")
    op.execute("DROP INDEX IF EXISTS uq_supplier_docs_effective_group;")

    op.execute(
        """
        ALTER TABLE supplier_documents
            DROP CONSTRAINT IF EXISTS uq_supplier_documents_group_version,
            DROP CONSTRAINT IF EXISTS fk_supplier_documents_legacy_req,
            DROP CONSTRAINT IF EXISTS fk_supplier_documents_group,
            DROP CONSTRAINT IF EXISTS ck_supplier_docs_approval_provenance,
            DROP CONSTRAINT IF EXISTS ck_supplier_docs_legacy_status,
            DROP CONSTRAINT IF EXISTS ck_supplier_docs_processing_status,
            DROP COLUMN IF EXISTS is_effective_version,
            DROP COLUMN IF EXISTS legacy_requirement_id,
            DROP COLUMN IF EXISTS approval_provenance,
            DROP COLUMN IF EXISTS legacy_status,
            DROP COLUMN IF EXISTS processing_status,
            DROP COLUMN IF EXISTS document_group_id;
        """
    )

    # -------------------------------------------------------------------------
    # 7. Revert document_requirements Columns
    # -------------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE document_requirements
            DROP CONSTRAINT IF EXISTS ck_doc_requirements_precedence_order,
            DROP CONSTRAINT IF EXISTS ck_doc_requirements_applicability_scope,
            DROP COLUMN IF EXISTS precedence_order,
            DROP COLUMN IF EXISTS applicability_scope;
        """
    )

    # -------------------------------------------------------------------------
    # 8. Drop Phase 3.4 Tables
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS idx_doc_discrepancies_doc_type;")
    op.execute("DROP INDEX IF EXISTS idx_doc_discrepancies_lookup;")
    op.execute("DROP TABLE IF EXISTS supplier_document_migration_discrepancies;")
    op.execute("DROP TABLE IF EXISTS document_group_requirement_links;")
    op.execute("DROP TABLE IF EXISTS supplier_document_groups;")
    op.execute("DROP TABLE IF EXISTS supplier_sites;")
