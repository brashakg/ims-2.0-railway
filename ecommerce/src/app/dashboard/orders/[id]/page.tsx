"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Loader2,
  User,
  MapPin,
  StickyNote,
  Tag,
  Calendar,
  CreditCard,
  Package,
} from "lucide-react";

interface LineItem {
  id: string;
  title: string;
  variantTitle: string | null;
  sku: string | null;
  quantity: number;
  price: number;
  totalDiscount: number;
}

interface Customer {
  id: string;
  firstName: string | null;
  lastName: string | null;
  email: string | null;
  phone: string | null;
  ordersCount: number;
  totalSpent: number;
}

interface Order {
  id: string;
  shopifyOrderId: string | null;
  orderNumber: string | null;
  name: string | null;
  email: string | null;
  phone: string | null;
  totalPrice: number;
  subtotalPrice: number;
  totalTax: number;
  totalDiscount: number;
  currency: string;
  financialStatus: string | null;
  fulfillmentStatus: string | null;
  orderStatus: string;
  shippingAddress: string | null;
  billingAddress: string | null;
  note: string | null;
  tags: string | null;
  source: string | null;
  cancelReason: string | null;
  cancelledAt: string | null;
  closedAt: string | null;
  processedAt: string | null;
  createdAt: string;
  customer: Customer | null;
  lineItems: LineItem[];
}

export default function OrderDetailPage() {
  const params = useParams<{ id: string }>();
  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!params?.id) return;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(`/api/orders/${params.id}`);
        const data = await res.json();
        if (!res.ok || !data.success) {
          throw new Error(data.error || "Failed to load order");
        }
        setOrder(data.data);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load order");
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

  if (error || !order) {
    return (
      <div className="p-6">
        <Link
          href="/dashboard/orders"
          className="text-sm text-blue-600 hover:underline inline-flex items-center gap-1"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to orders
        </Link>
        <p className="mt-4 text-red-700">{error || "Order not found"}</p>
      </div>
    );
  }

  const shipping = parseAddress(order.shippingAddress);
  const billing = parseAddress(order.billingAddress);
  const tagsList = (order.tags || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const orderName = order.name || `#${order.orderNumber || order.id.slice(-6)}`;
  const processedDate = order.processedAt || order.createdAt;

  return (
    <div className="min-h-screen bg-slate-50 p-4 sm:p-6">
      <div className="max-w-6xl mx-auto">
        <Link
          href="/dashboard/orders"
          className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900 mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          Orders
        </Link>

        <div className="flex items-start justify-between mb-6 gap-4 flex-wrap">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl sm:text-3xl font-bold text-slate-900">
                {orderName}
              </h1>
              <StatusPill
                value={order.financialStatus || order.orderStatus}
                tone={paymentTone(order.financialStatus, order.orderStatus)}
              />
              <StatusPill
                value={order.fulfillmentStatus || "unfulfilled"}
                tone={fulfillmentTone(order.fulfillmentStatus)}
              />
            </div>
            <p className="text-sm text-slate-500 mt-1">
              {new Date(processedDate).toLocaleString()} · {order.source || "unknown"} source
            </p>
          </div>
          {order.shopifyOrderId && (
            <a
              href={`https://${(process.env.NEXT_PUBLIC_SHOPIFY_STORE_URL || "bokaro-better-vision.myshopify.com")}/admin/orders/${order.shopifyOrderId.split("/").pop()}`}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-blue-600 hover:underline"
            >
              Open in Shopify ↗
            </a>
          )}
        </div>

        {order.orderStatus === "CANCELLED" && (
          <div className="mb-5 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800">
            Cancelled{order.cancelledAt ? ` on ${new Date(order.cancelledAt).toLocaleString()}` : ""}
            {order.cancelReason ? ` — reason: ${order.cancelReason}` : ""}.
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {/* Left: Line items, tags, notes */}
          <div className="lg:col-span-2 space-y-5">
            <Section
              title={`${order.lineItems.length} item(s) · ${order.fulfillmentStatus || "unfulfilled"}`}
            >
              {order.lineItems.length === 0 ? (
                <p className="text-sm text-slate-500 px-4 py-6">No line items.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium text-slate-600">
                        Product
                      </th>
                      <th className="px-4 py-2 text-left font-medium text-slate-600">
                        SKU
                      </th>
                      <th className="px-4 py-2 text-right font-medium text-slate-600">
                        Qty
                      </th>
                      <th className="px-4 py-2 text-right font-medium text-slate-600">
                        Unit price
                      </th>
                      <th className="px-4 py-2 text-right font-medium text-slate-600">
                        Total
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {order.lineItems.map((li) => (
                      <tr
                        key={li.id}
                        className="border-b border-slate-100 last:border-0"
                      >
                        <td className="px-4 py-2 text-slate-900">
                          <div className="font-medium">{li.title}</div>
                          {li.variantTitle && (
                            <div className="text-xs text-slate-500">
                              {li.variantTitle}
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-2 text-xs text-slate-600">
                          {li.sku || "—"}
                        </td>
                        <td className="px-4 py-2 text-right text-slate-700">
                          {li.quantity}
                        </td>
                        <td className="px-4 py-2 text-right text-slate-700">
                          {order.currency} {li.price.toFixed(2)}
                        </td>
                        <td className="px-4 py-2 text-right font-medium text-slate-900">
                          {order.currency}{" "}
                          {(li.price * li.quantity - (li.totalDiscount || 0)).toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>

            <Section title="Payment summary">
              <dl className="p-4 space-y-1.5 text-sm">
                <MoneyRow label="Subtotal" amount={order.subtotalPrice} currency={order.currency} />
                <MoneyRow label="Discount" amount={-Math.abs(order.totalDiscount)} currency={order.currency} muted />
                <MoneyRow label="Tax" amount={order.totalTax} currency={order.currency} muted />
                <div className="border-t border-slate-200 pt-2 mt-2" />
                <MoneyRow label="Total" amount={order.totalPrice} currency={order.currency} bold />
                <div className="text-xs text-slate-500 mt-1 flex items-center gap-1">
                  <CreditCard className="w-3 h-3" />
                  {order.financialStatus || "unknown status"}
                </div>
              </dl>
            </Section>

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

            <Section title="Notes">
              {order.note ? (
                <div className="flex items-start gap-2 px-4 py-3 text-sm text-slate-700">
                  <StickyNote className="w-4 h-4 text-slate-400 flex-shrink-0 mt-0.5" />
                  <p className="whitespace-pre-wrap">{order.note}</p>
                </div>
              ) : (
                <p className="text-sm text-slate-500 px-4 py-4">No notes.</p>
              )}
            </Section>
          </div>

          {/* Right: Customer, Addresses */}
          <div className="space-y-5">
            <Section title="Customer">
              {order.customer ? (
                <div className="p-4 text-sm space-y-2">
                  <Link
                    href={`/dashboard/customers/${order.customer.id}`}
                    className="flex items-center gap-2 font-medium text-blue-700 hover:underline"
                  >
                    <User className="w-4 h-4 text-slate-400" />
                    {[order.customer.firstName, order.customer.lastName]
                      .filter(Boolean)
                      .join(" ") ||
                      order.customer.email ||
                      "Unnamed"}
                  </Link>
                  <div className="text-slate-600 text-xs">
                    {order.customer.ordersCount} order(s) · ₹
                    {order.customer.totalSpent.toFixed(2)} lifetime
                  </div>
                  {order.customer.email && (
                    <div className="text-slate-600 text-xs break-all">
                      {order.customer.email}
                    </div>
                  )}
                  {order.customer.phone && (
                    <div className="text-slate-600 text-xs">
                      {order.customer.phone}
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-sm text-slate-500 px-4 py-4">
                  Guest order (no linked customer).
                </p>
              )}
            </Section>

            <Section title="Shipping address">
              <AddressBlock addr={shipping} />
            </Section>

            <Section title="Billing address">
              <AddressBlock addr={billing} />
            </Section>

            <Section title="Timeline">
              <div className="p-4 text-sm space-y-2">
                <TimeRow
                  label="Created"
                  value={new Date(order.createdAt).toLocaleString()}
                />
                {order.processedAt && (
                  <TimeRow
                    label="Processed"
                    value={new Date(order.processedAt).toLocaleString()}
                  />
                )}
                {order.closedAt && (
                  <TimeRow
                    label="Closed"
                    value={new Date(order.closedAt).toLocaleString()}
                  />
                )}
                {order.cancelledAt && (
                  <TimeRow
                    label="Cancelled"
                    value={new Date(order.cancelledAt).toLocaleString()}
                  />
                )}
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

type ParsedAddress = {
  address1?: string;
  address2?: string;
  city?: string;
  province?: string;
  zip?: string;
  country?: string;
  name?: string;
  phone?: string;
} | null;

function parseAddress(raw: string | null): ParsedAddress {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function AddressBlock({ addr }: { addr: ParsedAddress }) {
  if (!addr) {
    return <p className="text-sm text-slate-500 px-4 py-4">None on file.</p>;
  }
  const lines = [
    addr.name,
    addr.address1,
    addr.address2,
    [addr.city, addr.province, addr.zip].filter(Boolean).join(", "),
    addr.country,
    addr.phone,
  ].filter(Boolean);
  if (lines.length === 0) {
    return <p className="text-sm text-slate-500 px-4 py-4">None on file.</p>;
  }
  return (
    <div className="flex items-start gap-2 px-4 py-3 text-sm text-slate-700">
      <MapPin className="w-4 h-4 text-slate-400 flex-shrink-0 mt-0.5" />
      <div>
        {lines.map((l, i) => (
          <div key={i}>{l}</div>
        ))}
      </div>
    </div>
  );
}

function TimeRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 text-slate-700">
      <Calendar className="w-4 h-4 text-slate-400" />
      <span className="text-xs uppercase tracking-wide text-slate-500 w-20">
        {label}
      </span>
      <span>{value}</span>
    </div>
  );
}

function MoneyRow({
  label,
  amount,
  currency,
  bold,
  muted,
}: {
  label: string;
  amount: number;
  currency: string;
  bold?: boolean;
  muted?: boolean;
}) {
  return (
    <div
      className={`flex items-center justify-between ${bold ? "font-bold text-slate-900" : muted ? "text-slate-600" : "text-slate-800"}`}
    >
      <span>{label}</span>
      <span>
        {currency} {amount.toFixed(2)}
      </span>
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
  return "neutral";
}

// Package icon import guard — unused but keeps lucide happy if added later.
void Package;
