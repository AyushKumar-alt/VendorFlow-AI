import React from "react";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { ClipboardCheck } from "lucide-react";

export default function ReviewsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Reviews"
        description="Verification queue for procurement officers and compliance leads to review submitted supplier credentials, resolve flagged discrepancies, and grant approvals."
        badge="Verification Queue"
      />

      <EmptyState
        icon={ClipboardCheck}
        title="Review Queue is Empty"
        description="Pending document verifications, validation tasks, and manual override workflows will appear here once supplier submissions are active."
        phaseBadge="Scheduled for Phase 3.2"
        technicalNote="Human-in-the-loop review interface and approval state machine will be integrated in Phase 3.2."
      />
    </div>
  );
}
