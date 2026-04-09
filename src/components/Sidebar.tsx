"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession, signOut } from "next-auth/react";
import { useState } from "react";
import {
  Menu,
  X,
  LayoutDashboard,
  Package,
  FolderOpen,
  Image,
  ShoppingCart,
  Users,
  ArrowLeftRight,
  BarChart3,
  Megaphone,
  Globe,
  ClipboardCheck,
  Settings,
  MapPin,
  Tag,
  UserCog,
  LogOut,
  ChevronLeft,
  ChevronRight,
  ScrollText,
  HardDriveDownload,
  Percent,
} from "lucide-react";

interface NavItem {
  href: string;
  label: string;
  icon: React.ReactNode;
  exact?: boolean; // true = only match exact path
}

export default function Sidebar() {
  const pathname = usePathname();
  const { data: session } = useSession();
  const [isOpen, setIsOpen] = useState(true);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const mainLinks: NavItem[] = [
    { href: "/dashboard", label: "Dashboard", icon: <LayoutDashboard className="w-5 h-5 flex-shrink-0" />, exact: true },
    { href: "/dashboard/products", label: "Products", icon: <Package className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/orders", label: "Orders", icon: <ShoppingCart className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/customers", label: "Customers", icon: <Users className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/collections", label: "Collections", icon: <FolderOpen className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/stock-transfers", label: "Stock Transfers", icon: <ArrowLeftRight className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/stock-tally", label: "Stock Tally", icon: <ClipboardCheck className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/stock-import", label: "Backup & Restore", icon: <HardDriveDownload className="w-5 h-5 flex-shrink-0" /> },
  ];

  const insightLinks: NavItem[] = [
    { href: "/dashboard/reports", label: "Insights & Reports", icon: <BarChart3 className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/marketing", label: "Marketing", icon: <Megaphone className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/store-health", label: "Store Health", icon: <Globe className="w-5 h-5 flex-shrink-0" /> },
  ];

  const adminLinks: NavItem[] = [
    { href: "/dashboard/attributes", label: "Attributes", icon: <Tag className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/users", label: "Users", icon: <UserCog className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/locations", label: "Locations", icon: <MapPin className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/activity-logs", label: "Activity Logs", icon: <ScrollText className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/images", label: "Images", icon: <Image className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/shopify", label: "Shopify Sync", icon: <Settings className="w-5 h-5 flex-shrink-0" /> },
    { href: "/dashboard/admin/discount-rules", label: "Discount Rules", icon: <Percent className="w-5 h-5 flex-shrink-0" /> },
  ];

  const isActive = (href: string, exact?: boolean) => {
    if (exact) return pathname === href;
    return pathname === href || pathname.startsWith(href + "/");
  };

  const userRole = (session?.user as any)?.role || "USER";
  const isAdmin = userRole === "ADMIN";

  const closeMobile = () => setIsMobileMenuOpen(false);

  const renderLink = (link: NavItem) => (
    <Link
      key={link.href}
      href={link.href}
      className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm ${
        isActive(link.href, link.exact)
          ? "bg-blue-600 text-white font-medium"
          : "text-slate-300 hover:bg-slate-800 hover:text-white"
      }`}
      onClick={closeMobile}
      title={!isOpen ? link.label : undefined}
    >
      {link.icon}
      {isOpen && <span className="truncate">{link.label}</span>}
    </Link>
  );

  const renderSection = (title: string, links: NavItem[]) => (
    <div className="mt-5">
      {isOpen && (
        <p className="px-3 mb-2 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
          {title}
        </p>
      )}
      {!isOpen && <div className="border-t border-slate-700 mx-3 mb-2" />}
      <div className="space-y-0.5">{links.map(renderLink)}</div>
    </div>
  );

  return (
    <>
      {/* Mobile hamburger - fixed top-left, above everything */}
      <button
        onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
        className="sm:hidden fixed top-3 left-3 z-[60] p-2 bg-slate-900 text-white rounded-lg shadow-lg"
        aria-label="Toggle menu"
      >
        {isMobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
      </button>

      {/* Mobile overlay backdrop */}
      {isMobileMenuOpen && (
        <div
          className="sm:hidden fixed inset-0 bg-black/50 z-[45]"
          onClick={closeMobile}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`
          fixed left-0 top-0 h-full bg-slate-900 text-white flex flex-col
          transition-all duration-200 ease-in-out
          ${isMobileMenuOpen ? "z-[50] w-64 translate-x-0" : "max-sm:-translate-x-full max-sm:w-64"}
          ${isOpen ? "sm:w-64" : "sm:w-[68px]"}
          sm:translate-x-0 sm:z-30
        `}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-4 border-b border-slate-700/50 min-h-[64px]">
          <div className="w-9 h-9 bg-blue-600 rounded-lg flex items-center justify-center font-bold text-sm flex-shrink-0">
            BV
          </div>
          {isOpen && (
            <div className="overflow-hidden">
              <h1 className="font-bold text-base leading-tight">Better Vision</h1>
              <p className="text-[10px] text-slate-400">Inventory Manager</p>
            </div>
          )}
        </div>

        {/* Navigation - scrollable */}
        <nav className="flex-1 px-3 py-3 overflow-y-auto overflow-x-hidden scrollbar-thin">
          {/* Main */}
          <div className="space-y-0.5">{mainLinks.map(renderLink)}</div>

          {/* Insights */}
          {renderSection("Insights", insightLinks)}

          {/* Admin */}
          {isAdmin && renderSection("Admin", adminLinks)}
        </nav>

        {/* User footer */}
        <div className="border-t border-slate-700/50 p-3">
          {isOpen && (
            <div className="px-3 mb-2">
              <p className="text-xs font-medium text-slate-300 truncate">
                {session?.user?.email}
              </p>
              <span className="inline-block bg-blue-600/20 text-blue-300 text-[10px] px-2 py-0.5 rounded mt-1 font-medium">
                {userRole}
              </span>
            </div>
          )}
          <button
            onClick={() => {
              signOut({ callbackUrl: "/login" });
              closeMobile();
            }}
            className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white transition-colors text-sm"
          >
            <LogOut className="w-5 h-5 flex-shrink-0" />
            {isOpen && <span>Logout</span>}
          </button>
        </div>

        {/* Desktop collapse toggle */}
        <button
          onClick={() => setIsOpen(!isOpen)}
          className="hidden sm:flex absolute -right-3 top-[72px] w-6 h-6 bg-slate-700 hover:bg-slate-600 text-white rounded-full items-center justify-center border-2 border-slate-900 shadow transition-colors"
          aria-label={isOpen ? "Collapse sidebar" : "Expand sidebar"}
        >
          {isOpen ? <ChevronLeft className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </button>
      </aside>
    </>
  );
}
