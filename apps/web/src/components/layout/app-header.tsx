"use client";

import React from "react";
import { usePathname } from "next/navigation";
import { Menu, Building } from "lucide-react";
import { MAIN_NAV_ITEMS, BOTTOM_NAV_ITEMS } from "@/lib/navigation";

interface AppHeaderProps {
  onOpenMobileNav: () => void;
}

export function AppHeader({ onOpenMobileNav }: AppHeaderProps) {
  const pathname = usePathname();

  // Find current route title for breadcrumb
  const allItems = [...MAIN_NAV_ITEMS, ...BOTTOM_NAV_ITEMS];
  const activeItem = allItems.find((item) =>
    item.href === "/"
      ? pathname === "/"
      : pathname === item.href || pathname.startsWith(`${item.href}/`)
  );

  const currentPageTitle = activeItem ? activeItem.title : "Overview";

  return (
    <header className="h-16 bg-white border-b border-slate-200 px-4 sm:px-6 flex items-center justify-between shrink-0">
      <div className="flex items-center gap-3">
        {/* Mobile menu trigger */}
        <button
          type="button"
          onClick={onOpenMobileNav}
          className="lg:hidden p-2 rounded-md text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition-colors focus:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500"
          aria-label="Open navigation menu"
        >
          <Menu className="h-5 w-5" aria-hidden="true" />
        </button>

        {/* Breadcrumb Navigation */}
        <nav aria-label="Breadcrumb" className="flex items-center text-sm font-medium">
          <ol className="flex items-center gap-1.5 text-slate-500">
            <li>
              <span className="text-slate-500">VendorFlow AI</span>
            </li>
            <li aria-hidden="true" className="text-slate-400">
              /
            </li>
            <li>
              <span className="text-slate-900 font-semibold" aria-current="page">
                {currentPageTitle}
              </span>
            </li>
          </ol>
        </nav>
      </div>

      {/* Workspace label */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-50 border border-slate-200 text-xs text-slate-700">
          <Building className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
          <span className="font-semibold text-slate-800">My Workspace</span>
        </div>
      </div>
    </header>
  );
}
