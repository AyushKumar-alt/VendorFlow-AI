import React from "react";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { FileText } from "lucide-react";

export default function DocumentsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Documents"
        description="Centralized document repository for mandatory manufacturing compliance: GSTIN certificates, PAN cards, MSME Udyam, bank mandates, and ISO quality accreditations."
        badge="Document Repository"
      />

      <EmptyState
        icon={FileText}
        title="No Compliance Documents Submitted"
        description="Document intake channels, file storage integration, and automated document extraction pipelines will be connected here. No documents have been uploaded yet."
        phaseBadge="Scheduled for Phase 3.1"
        technicalNote="Document intake and optical validation services will be implemented in Phase 3."
      />
    </div>
  );
}
