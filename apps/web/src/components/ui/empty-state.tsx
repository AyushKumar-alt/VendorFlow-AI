import React from "react";
import { type LucideIcon, Clock } from "lucide-react";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description: string;
  phaseBadge?: string;
  technicalNote?: string;
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  phaseBadge,
  technicalNote,
}: EmptyStateProps) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 sm:p-12 text-center shadow-xs">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-lg bg-slate-100 text-slate-600 border border-slate-200">
        <Icon className="h-6 w-6 stroke-[1.75]" aria-hidden="true" />
      </div>
      <h3 className="mt-4 text-base font-semibold text-slate-900">{title}</h3>
      <p className="mx-auto mt-2 max-w-md text-sm text-slate-500 leading-relaxed">
        {description}
      </p>
      {(phaseBadge || technicalNote) && (
        <div className="mt-6 flex flex-col items-center justify-center gap-2">
          {phaseBadge && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700 border border-slate-200">
              <Clock className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
              {phaseBadge}
            </span>
          )}
          {technicalNote && (
            <p className="text-xs text-slate-400 max-w-md">
              {technicalNote}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
