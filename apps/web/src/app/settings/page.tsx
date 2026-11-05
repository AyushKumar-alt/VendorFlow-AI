import React from "react";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { Settings } from "lucide-react";

export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Configure organization details, customize required document templates per supplier tier, and define approval authority limits for manufacturing units."
        badge="Workspace Configuration"
      />

      <EmptyState
        icon={Settings}
        title="Workspace Configuration Management"
        description="Plant facility profiles, compliance requirement policies, user roles, and webhook integrations will be managed from this panel in future phases."
        phaseBadge="Scheduled for Phase 4.2"
        technicalNote="Organization and plant configuration models will be connected to the FastAPI backend."
      />
    </div>
  );
}
