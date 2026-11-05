import React from "react";
import { PageHeader } from "@/components/ui/page-header";
import { GettingStarted } from "@/components/dashboard/getting-started";
import { EmptyState } from "@/components/ui/empty-state";
import { Layers } from "lucide-react";

export default function OverviewPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        description="Welcome to VendorFlow AI. Centralized supplier onboarding and document-first compliance monitoring for Indian manufacturing operations."
        badge="Workspace Overview"
      />

      {/* Getting Started Checklist */}
      <GettingStarted />

      {/* Honest Empty State Section */}
      <section aria-labelledby="live-records-heading" className="space-y-3">
        <div className="flex items-center justify-between">
          <h2
            id="live-records-heading"
            className="text-sm font-semibold uppercase tracking-wider text-slate-500"
          >
            Live Operational Summary
          </h2>
          <span className="text-xs text-slate-400">
            Awaiting supplier registration
          </span>
        </div>

        <EmptyState
          icon={Layers}
          title="No Supplier Records or Compliance Metrics"
          description="Operational statistics, audit pipelines, certificate renewals, and compliance scores will appear here once supplier records are created and document intake is initiated."
          phaseBadge="Phase 2.3+ Integration"
          technicalNote="Backend database connection ready on PostgreSQL 17. API client integration will connect vendor tables in subsequent milestones."
        />
      </section>
    </div>
  );
}
