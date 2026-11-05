import {
  LayoutDashboard,
  Building2,
  FileText,
  ClipboardCheck,
  ShieldCheck,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  title: string;
  href: string;
  icon: LucideIcon;
  description: string;
  badge?: string;
}

export const MAIN_NAV_ITEMS: NavItem[] = [
  {
    title: "Overview",
    href: "/",
    icon: LayoutDashboard,
    description: "System overview, onboarding pipeline status, and setup workflow",
  },
  {
    title: "Suppliers",
    href: "/suppliers",
    icon: Building2,
    description: "Manufacturing vendor directory, registration profiles, and tier classifications",
  },
  {
    title: "Documents",
    href: "/documents",
    icon: FileText,
    description: "GSTIN, PAN, MSME, ISO certificates, and factory compliance filings",
  },
  {
    title: "Reviews",
    href: "/reviews",
    icon: ClipboardCheck,
    description: "Verification queue, human review approvals, and audit discrepancy logs",
  },
  {
    title: "Compliance",
    href: "/compliance",
    icon: ShieldCheck,
    description: "Regulatory tracking, certificate expiry monitoring, and risk thresholds",
  },
];

export const BOTTOM_NAV_ITEMS: NavItem[] = [
  {
    title: "Settings",
    href: "/settings",
    icon: Settings,
    description: "Manufacturing workspace settings, document requirements, and organization defaults",
  },
];
