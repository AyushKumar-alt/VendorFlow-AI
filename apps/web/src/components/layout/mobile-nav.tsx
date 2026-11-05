"use client";

import React, { useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { MAIN_NAV_ITEMS, BOTTOM_NAV_ITEMS } from "@/lib/navigation";
import { X, Shield } from "lucide-react";

interface MobileNavProps {
  isOpen: boolean;
  onClose: () => void;
}

export function MobileNav({ isOpen, onClose }: MobileNavProps) {
  const pathname = usePathname();

  // Close drawer on escape key
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && isOpen) {
        onClose();
      }
    }

    if (isOpen) {
      document.body.style.overflow = "hidden";
      window.addEventListener("keydown", handleKeyDown);
    } else {
      document.body.style.overflow = "";
    }

    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Mobile Navigation"
      className="fixed inset-0 z-50 lg:hidden flex"
    >
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-slate-900/60 backdrop-blur-2xs transition-opacity"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <div className="relative flex flex-col w-72 max-w-[80vw] bg-slate-900 text-slate-300 shadow-xl z-10 border-r border-slate-800">
        {/* Drawer Header */}
        <div className="h-16 flex items-center justify-between px-4 border-b border-slate-800">
          <Link
            href="/"
            onClick={onClose}
            className="flex items-center gap-2.5 focus:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
          >
            <div className="h-8 w-8 rounded-md bg-blue-600 flex items-center justify-center text-white shrink-0 shadow-xs">
              <Shield className="h-5 w-5 stroke-[2.25]" aria-hidden="true" />
            </div>
            <div className="flex flex-col leading-tight">
              <span className="font-semibold text-sm tracking-tight text-white">
                VendorFlow AI
              </span>
              <span className="text-[11px] text-slate-400 font-normal">
                Supplier Compliance
              </span>
            </div>
          </Link>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-800 transition-colors focus:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500"
            aria-label="Close navigation menu"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        {/* Main Navigation */}
        <div className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
          <div className="px-2 pb-2 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
            Navigation
          </div>
          <nav aria-label="Mobile Main Navigation">
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
                      onClick={onClose}
                      className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors focus:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 ${
                        isActive
                          ? "bg-slate-800 text-white border-l-2 border-blue-500 font-semibold"
                          : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
                      }`}
                      aria-current={isActive ? "page" : undefined}
                    >
                      <Icon className="h-4.5 w-4.5 shrink-0 stroke-[1.75]" aria-hidden="true" />
                      <span className="truncate">{item.title}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </div>

        {/* Settings Navigation */}
        <div className="p-3 border-t border-slate-800">
          <nav aria-label="Mobile Settings Navigation">
            <ul className="space-y-1">
              {BOTTOM_NAV_ITEMS.map((item) => {
                const Icon = item.icon;
                const isActive = pathname === item.href;

                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onClose}
                      className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors focus:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 ${
                        isActive
                          ? "bg-slate-800 text-white border-l-2 border-blue-500 font-semibold"
                          : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
                      }`}
                      aria-current={isActive ? "page" : undefined}
                    >
                      <Icon className="h-4.5 w-4.5 shrink-0 stroke-[1.75]" aria-hidden="true" />
                      <span className="truncate">{item.title}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </div>
      </div>
    </div>
  );
}
