import React from "react";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { ShieldCheck } from "lucide-react";

export default function CompliancePage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Compliance"
        description="Monitor regulatory compliance standards, track certificate expiration deadlines, and manage risk score policies across all onboarded manufacturing suppliers."
        badge="Risk & Compliance"
      />

      <EmptyState
        icon={ShieldCheck}
        title="Compliance Engine Not Configured"
        description="Automated expiration tracking, compliance audit rules, and supplier risk scoring algorithms will be displayed here once active supplier contracts exist."
        phaseBadge="Scheduled for Phase 4.1"
        technicalNote="Automated expiry alerting and compliance status calculation engines will be implemented in Phase 4."
      />
    </div>
  );
}
