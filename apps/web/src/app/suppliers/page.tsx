import React from "react";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { Building2 } from "lucide-react";

export default function SuppliersPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Suppliers"
        description="Maintain the active manufacturing supplier directory, track self-service onboarding invitations, and manage vendor profile records."
        badge="Vendor Directory"
      />

      <EmptyState
        icon={Building2}
        title="Supplier Directory Not Initialized"
        description="Supplier registration records, plant capability classifications, and self-service onboarding pipelines will be established here. No supplier records exist in the workspace yet."
        phaseBadge="Scheduled for Phase 2.3"
        technicalNote="Database schema for supplier profiles and contacts will be connected via FastAPI in Phase 2.3."
      />
    </div>
  );
}
