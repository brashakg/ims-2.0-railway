"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Loader2,
  Mail,
  Phone,
  MapPin,
  CheckCircle2,
  XCircle,
  ShieldCheck,
  Tag,
  StickyNote,
  Calendar,
} from "lucide-react";

interface Order {
  id: string;
  orderNumber: string | null;
  name: string | null;
  totalPrice: number;
  currency: string;
  financialStatus: string | null;
  fulfillmentStatus: string | null;
  orderStatus: string;
  processedAt: string | null;
  createdAt: string;
}

interface Customer {
  id: string;
  shopifyCustomerId: string | null;
  email: string | null;
  phone: string | null;
  firstName: string | null;
  lastName: string | null;
  address1: string | null;
  address2: string | null;
  city: string | null;
  state: string | null;
  zip: string | null;
  country: string | null;
  ordersCount: number;
  totalSpent: number;
  tags: string | null;
  note: string | null;
  acceptsMarketing: boolean;
  taxExempt: boolean;
  verified: boolean;
  createdAt: string;
  orders: Order[];
}

export default function CustomerDetailPage() {
  const params = useParams<{ id: string }>();
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!params?.id) return;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(`/api/customers/${params.id}`);
        const data = await res.json();
        if (!res.ok || !data.success) {
          throw new Error(data.error || "Failed to load customer");
        }
        setCustomer(data.data);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load customer");
      } finally {
        setLoading(false);
      }
    })();
  }, [params?.id]);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }

  if (error || !customer) {
    return (
      <div className="p-6">
        <Link
          href="/dashboard/customers"
          className="text-sm text-blue-600 hover:underline inline-flex items-center gap-1"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to customers
        </Link>
        <p className="mt-4 text-red-700">{error || "Customer not found"}</p>
      </div>
    );
  }

  const displayName =
    [customer.firstName, customer.lastName].filter(Boolean).join(" ") ||
    customer.email ||
    customer.phone ||
    "Unnamed customer";

  const addressLines = [
    customer.address1,
    customer.address2,
    [customer.city, customer.state, customer.zip].filter(Boolean).join(", "),
    customer.country,
  ].filter(Boolean);

  const tagsList = (customer.tags || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);

  const avgOrderValue =
    customer.ordersCount > 0
      ? customer.totalSpent / customer.ordersCount
      : 0;

  return (
    <div className="min-h-screen bg-slate-50 p-4 sm:p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <Link
          href="/dashboard/customers"
          className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900 mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          Customers
        </Link>

        <div className="flex items-start justify-between mb-6 gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold text-slate-900">
              {displayName}
            </h1>
            <p className="text-sm text-slate-500 mt-1">
              Customer since{" "}
              {new Date(customer.createdAt).toLocaleDateString(undefined, {
                year: "numeric",
                month: "long",
                day: "numeric",
              })}
            </p>
          </div>
          {customer.shopifyCustomerId && (
            <a
              href={`https://${(process.env.NEXT_PUBLIC_SHOPIFY_STORE_URL || "bokaro-better-vision.myshopify.com")}/admin/customers/${customer.shopifyCustomerId.split("/").pop()}`}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-blue-600 hover:underline"
            >
              Open in Shopify ↗
            </a>
          )}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {/* Left column: Orders + Tags + Notes */}
          <div className="lg:col-span-2 space-y-5">
            {/* Stats row */}
            <div className="grid grid-cols-3 gap-3">
              <StatCard label="Orders" value={customer.ordersCount} />
              <StatCard
                label="Total spent"
                value={`₹${customer.totalSpent.toFixed(2)}`}
              />
              <StatCard
                label="Avg order value"
                value={`₹${avgOrderValue.toFixed(2)}`}
              />
            </div>

            {/* Orders list */}
            <Section title={`Last ${Math.min(customer.orders.length, 50)} order(s)`}>
              {customer.orders.length === 0 ? (
                <p className="text-sm text-slate-500 px-4 py-6">
                  No orders yet.
                </p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium text-slate-600">
                        Order
                      </th>
                      <th className="px-4 py-2 text-left font-medium text-slate-600">
                        Date
                      </th>
                      <th className="px-4 py-2 text-left font-medium text-slate-600">
                        Payment
                      </th>
                      <th className="px-4 py-2 text-left font-medium text-slate-600">
                        Fulfillment
                      </th>
                      <th className="px-4 py-2 text-right font-medium text-slate-600">
                        Total
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {customer.orders.map((o) => (
                      <tr
                        key={o.id}
                        className="border-b border-slate-100 last:border-0 hover:bg-slate-50"
                      >
                        <td className="px-4 py-2 font-medium text-slate-900">
                          {o.name || `#${o.orderNumber || "—"}`}
                        </td>
                        <td className="px-4 py-2 text-slate-600">
                          {new Date(
                            o.processedAt || o.createdAt
                          ).toLocaleDateString()}
                        </td>
                        <td className="px-4 py-2">
                          <StatusPill
                            value={o.financialStatus || o.orderStatus}
                            tone={paymentTone(o.financialStatus, o.orderStatus)}
                          />
                        </td>
                        <td className="px-4 py-2">
                          <StatusPill
                            value={o.fulfillmentStatus || "unfulfilled"}
                            tone={fulfillmentTone(o.fulfillmentStatus)}
                          />
                        </td>
                        <td className="px-4 py-2 text-right font-medium text-slate-900">
                          {o.currency || "₹"} {o.totalPrice.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>

            {/* Tags */}
            <Section title="Tags">
              {tagsList.length === 0 ? (
                <p className="text-sm text-slate-500 px-4 py-4">No tags.</p>
              ) : (
                <div className="flex flex-wrap gap-2 p-4">
                  {tagsList.map((t) => (
                    <span
                      key={t}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 text-xs border border-slate-200"
                    >
                      <Tag className="w-3 h-3" />
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </Section>

            {/* Notes */}
            <Section title="Notes">
              {customer.note ? (
                <div className="flex items-start gap-2 px-4 py-3 text-sm text-slate-700">
                  <StickyNote className="w-4 h-4 text-slate-400 flex-shrink-0 mt-0.5" />
                  <p className="whitespace-pre-wrap">{customer.note}</p>
                </div>
              ) : (
                <p className="text-sm text-slate-500 px-4 py-4">No notes.</p>
              )}
            </Section>
          </div>

          {/* Right column: contact, address, marketing, flags */}
          <div className="space-y-5">
            <Section title="Contact">
              <dl className="p-4 space-y-2 text-sm">
                <DetailRow
                  icon={<Mail className="w-4 h-4 text-slate-400" />}
                  label="Email"
                  value={customer.email || "—"}
                  trailing={
                    customer.verified ? (
                      <span className="text-xs text-emerald-700 flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" /> verified
                      </span>
                    ) : null
                  }
                />
                <DetailRow
                  icon={<Phone className="w-4 h-4 text-slate-400" />}
                  label="Phone"
                  value={customer.phone || "—"}
                />
              </dl>
            </Section>

            <Section title="Default address">
              {addressLines.length === 0 ? (
                <p className="text-sm text-slate-500 px-4 py-4">
                  No address on file.
                </p>
              ) : (
                <div className="flex items-start gap-2 px-4 py-3 text-sm text-slate-700">
                  <MapPin className="w-4 h-4 text-slate-400 flex-shrink-0 mt-0.5" />
                  <div>
                    {addressLines.map((line, i) => (
                      <div key={i}>{line}</div>
                    ))}
                  </div>
                </div>
              )}
            </Section>

            <Section title="Marketing">
              <div className="p-4 text-sm">
                {customer.acceptsMarketing ? (
                  <div className="flex items-center gap-2 text-emerald-700">
                    <CheckCircle2 className="w-4 h-4" />
                    Subscribed to email marketing
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-slate-500">
                    <XCircle className="w-4 h-4" />
                    Not subscribed
                  </div>
                )}
              </div>
            </Section>

            <Section title="Flags">
              <div className="p-4 text-sm space-y-2">
                <div className="flex items-center gap-2">
                  <ShieldCheck
                    className={`w-4 h-4 ${
                      customer.taxExempt
                        ? "text-emerald-600"
                        : "text-slate-400"
                    }`}
                  />
                  {customer.taxExempt ? "Tax exempt" : "Not tax exempt"}
                </div>
                <div className="flex items-center gap-2">
                  <Calendar className="w-4 h-4 text-slate-400" />
                  Created{" "}
                  {new Date(customer.createdAt).toLocaleDateString()}
                </div>
              </div>
            </Section>
          </div>
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <div className="px-4 py-2.5 border-b border-slate-200 bg-slate-50">
        <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
      </div>
      <div>{children}</div>
    </div>
  );
}

function StatCard({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-3">
      <div className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">
        {label}
      </div>
      <div className="text-xl font-bold text-slate-900 mt-0.5">{value}</div>
    </div>
  );
}

function DetailRow({
  icon,
  label,
  value,
  trailing,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  trailing?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      {icon}
      <div className="flex-1">
        <div className="text-[11px] uppercase tracking-wide text-slate-500">
          {label}
        </div>
        <div className="text-sm text-slate-800 break-all">{value}</div>
      </div>
      {trailing}
    </div>
  );
}

function StatusPill({
  value,
  tone,
}: {
  value: string;
  tone: "good" | "warn" | "bad" | "neutral";
}) {
  const classes =
    tone === "good"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : tone === "warn"
        ? "bg-amber-50 text-amber-700 border-amber-200"
        : tone === "bad"
          ? "bg-red-50 text-red-700 border-red-200"
          : "bg-slate-50 text-slate-700 border-slate-200";
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded-full border text-[11px] ${classes}`}
    >
      {value}
    </span>
  );
}

function paymentTone(
  financialStatus: string | null,
  orderStatus: string
): "good" | "warn" | "bad" | "neutral" {
  if (orderStatus === "CANCELLED") return "bad";
  const s = (financialStatus || "").toLowerCase();
  if (s === "paid" || s === "partially_paid") return "good";
  if (s === "pending" || s === "authorized") return "warn";
  if (s === "refunded" || s === "partially_refunded") return "bad";
  return "neutral";
}

function fulfillmentTone(
  status: string | null
): "good" | "warn" | "bad" | "neutral" {
  const s = (status || "").toLowerCase();
  if (s === "fulfilled") return "good";
  if (s === "partial") return "warn";
  if (s === "unfulfilled" || !s) return "neutral";
  return "neutral";
}
