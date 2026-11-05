"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { MAIN_NAV_ITEMS, BOTTOM_NAV_ITEMS } from "@/lib/navigation";
import { ChevronLeft, ChevronRight, Shield } from "lucide-react";

interface AppSidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
}

export function AppSidebar({ collapsed, onToggleCollapse }: AppSidebarProps) {
  const pathname = usePathname();

  return (
    <aside
      aria-label="Sidebar navigation"
      className={`hidden lg:flex flex-col bg-slate-900 text-slate-300 border-r border-slate-800 transition-all duration-200 ease-in-out shrink-0 ${
        collapsed ? "w-18" : "w-64"
      }`}
    >
      {/* Brand Header */}
      <div className="h-16 flex items-center px-4 border-b border-slate-800/80 justify-between">
        <Link
          href="/"
          className="flex items-center gap-2.5 focus:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
        >
          <div className="h-8 w-8 rounded-md bg-blue-600 flex items-center justify-center text-white shrink-0 shadow-xs">
            <Shield className="h-5 w-5 stroke-[2.25]" aria-hidden="true" />
          </div>
          {!collapsed && (
            <div className="flex flex-col leading-tight min-w-0">
              <span className="font-semibold text-sm tracking-tight text-white truncate">
                VendorFlow AI
              </span>
              <span className="text-[11px] text-slate-400 font-normal truncate">
                Supplier Compliance
              </span>
            </div>
          )}
        </Link>
      </div>

      {/* Main Navigation */}
      <div className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
        {!collapsed && (
          <div className="px-2 pb-2 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
            Navigation
          </div>
        )}
        <nav aria-label="Main Navigation">
          <ul className="space-y-1">
            {MAIN_NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const isActive =
                item.href === "/"
                  ? pathname === "/"
                  : pathname === item.href || pathname.startsWith(`${item.href}/`);

              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    title={collapsed ? item.title : undefined}
                    className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors focus:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 ${
                      isActive
                        ? "bg-slate-800 text-white border-l-2 border-blue-500 font-semibold"
                        : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
                    } ${collapsed ? "justify-center px-2" : ""}`}
                    aria-current={isActive ? "page" : undefined}
                  >
                    <Icon className="h-4.5 w-4.5 shrink-0 stroke-[1.75]" aria-hidden="true" />
                    {!collapsed && <span className="truncate">{item.title}</span>}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>

      {/* Bottom Section: Settings & Collapse Toggle */}
      <div className="p-3 border-t border-slate-800 space-y-1">
        <nav aria-label="Settings Navigation">
          <ul className="space-y-1">
            {BOTTOM_NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const isActive = pathname === item.href;

              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    title={collapsed ? item.title : undefined}
                    className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors focus:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 ${
                      isActive
                        ? "bg-slate-800 text-white border-l-2 border-blue-500 font-semibold"
                        : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
                    } ${collapsed ? "justify-center px-2" : ""}`}
                    aria-current={isActive ? "page" : undefined}
                  >
                    <Icon className="h-4.5 w-4.5 shrink-0 stroke-[1.75]" aria-hidden="true" />
                    {!collapsed && <span className="truncate">{item.title}</span>}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        {/* Collapse Button */}
        <button
          type="button"
          onClick={onToggleCollapse}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={`w-full flex items-center gap-3 px-3 py-2 mt-2 rounded-md text-xs font-medium text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 transition-colors focus:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 ${
            collapsed ? "justify-center px-2" : ""
          }`}
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" />
          ) : (
            <>
              <ChevronLeft className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="truncate">Collapse Sidebar</span>
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
