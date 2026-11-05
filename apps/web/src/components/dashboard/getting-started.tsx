import React from "react";
import {
  FileCheck2,
  UserPlus,
  Inbox,
  ClipboardList,
  CircleDashed,
} from "lucide-react";

interface StepItem {
  id: number;
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  phase: string;
}

const ONBOARDING_STEPS: StepItem[] = [
  {
    id: 1,
    title: "1. Set up supplier requirements",
    description:
      "Define standard document checklists including GSTIN, PAN, Bank Mandate, MSME registration, and ISO 9001/14001 certificates for manufacturing compliance.",
    icon: FileCheck2,
    phase: "Phase 2.3",
  },
  {
    id: 2,
    title: "2. Add your first supplier",
    description:
      "Register manufacturing suppliers, specify supply category (raw materials, components, tooling, MRO), and issue self-service onboarding invitations.",
    icon: UserPlus,
    phase: "Phase 2.3",
  },
  {
    id: 3,
    title: "3. Collect supplier documents",
    description:
      "Enable secure vendor portal uploads for mandatory regulatory certifications, tax registrations, and factory inspection filings.",
    icon: Inbox,
    phase: "Phase 3.1",
  },
  {
    id: 4,
    title: "4. Review submitted documents",
    description:
      "Review extracted data, verify certificate validity periods, and approve verified vendors into the active manufacturing supplier roster.",
    icon: ClipboardList,
    phase: "Phase 3.2",
  },
];

export function GettingStarted() {
  return (
    <section
      aria-labelledby="getting-started-heading"
      className="rounded-lg border border-slate-200 bg-white p-6 shadow-xs"
    >
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-4 border-b border-slate-100">
        <div>
          <h2
            id="getting-started-heading"
            className="text-base font-semibold text-slate-900"
          >
            Setup & Onboarding Workflow
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Core stages of the document-first supplier compliance lifecycle. Modules will be enabled in subsequent milestones.
          </p>
        </div>
        <span className="inline-flex items-center gap-1 text-xs font-medium text-slate-500 bg-slate-50 border border-slate-200 px-2.5 py-1 rounded">
          <CircleDashed className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
          Foundation Phase
        </span>
      </div>

      <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-4">
        {ONBOARDING_STEPS.map((step) => {
          const StepIcon = step.icon;
          return (
            <div
              key={step.id}
              className="flex items-start gap-3.5 p-4 rounded-lg border border-slate-200/80 bg-slate-50/60 hover:bg-slate-50 transition-colors"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-white border border-slate-200 text-slate-700 shadow-2xs">
                <StepIcon className="h-4.5 w-4.5 stroke-[1.75]" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-medium text-slate-900 truncate">
                    {step.title}
                  </h3>
                  <span className="shrink-0 text-[11px] font-medium text-slate-600 bg-white border border-slate-200 px-1.5 py-0.5 rounded">
                    {step.phase}
                  </span>
                </div>
                <p className="mt-1 text-xs text-slate-500 leading-relaxed">
                  {step.description}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
