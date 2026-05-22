'use client';

import { useState, useEffect } from 'react';
import { LineChart, BarChart3, Package, ShoppingCart, Users, IndianRupee } from 'lucide-react';
import Topbar from '@/components/Topbar';

interface StatCardProps {
  label: string;
  value: string;
  icon: React.ReactNode;
  color: string;
}

interface ReportData {
  overview?: {
    totalProducts: number;
    totalOrders: number;
    totalCustomers: number;
    totalRevenue: number;
    productsByCategory: { category: string; count: number }[];
    productsByStatus: { status: string; count: number }[];
    topBrands: { brand: string; count: number }[];
    recentOrders: any[];
  };
  sales?: {
    revenueByMonth: { month: string; revenue: number }[];
    topSellingProducts: { productTitle: string; quantitySold: number; totalRevenue: number }[];
    avgOrderValue: number;
    ordersByPaymentStatus: { status: string; count: number }[];
  };
  inventory?: {
    totalStockUnits: number;
    outOfStockCount: number;
    productsByCategory: { category: string; totalStock: number }[];
    lowStockProducts: { productName: string; brand: string; category: string; stock: number }[];
  };
}

export default function ReportsPage() {
  const [activeTab, setActiveTab] = useState<'overview' | 'sales' | 'inventory'>('overview');
  const [data, setData] = useState<ReportData>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        const [overviewRes, salesRes, inventoryRes] = await Promise.all([
          fetch('/api/reports?type=overview'),
          fetch('/api/reports?type=sales'),
          fetch('/api/reports?type=inventory'),
        ]);

        const overviewJson = overviewRes.ok ? await overviewRes.json() : {};
        const salesJson = salesRes.ok ? await salesRes.json() : {};
        const inventoryJson = inventoryRes.ok ? await inventoryRes.json() : {};

        setData({
          overview: overviewJson.data || overviewJson,
          sales: salesJson.data || salesJson,
          inventory: inventoryJson.data || inventoryJson,
        });
      } catch (error) {
        console.error('Error fetching reports:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const StatCard: React.FC<StatCardProps> = ({ label, value, icon, color }) => (
    <div className={`bg-gradient-to-br ${color} rounded-lg shadow-lg p-6 text-white`}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium opacity-90">{label}</p>
          <p className="text-3xl font-bold mt-2">{value}</p>
        </div>
        <div className="opacity-20">{icon}</div>
      </div>
    </div>
  );

  const renderOverviewTab = () => (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Total Products"
          value={data.overview?.totalProducts?.toLocaleString() || '0'}
          icon={<Package size={32} />}
          color="from-blue-400 to-blue-600"
        />
        <StatCard
          label="Total Orders"
          value={data.overview?.totalOrders?.toLocaleString() || '0'}
          icon={<ShoppingCart size={32} />}
          color="from-green-400 to-green-600"
        />
        <StatCard
          label="Total Customers"
          value={data.overview?.totalCustomers?.toLocaleString() || '0'}
          icon={<Users size={32} />}
          color="from-purple-400 to-purple-600"
        />
        <StatCard
          label="Total Revenue"
          value={`₹${(data.overview?.totalRevenue || 0).toLocaleString('en-IN')}`}
          icon={<IndianRupee size={32} />}
          color="from-amber-400 to-amber-600"
        />
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h3 className="text-lg font-semibold mb-4">Products by Category</h3>
        <div className="space-y-3">
          {data.overview?.productsByCategory?.map((item, idx) => {
            const maxCount = Math.max(...(data.overview?.productsByCategory?.map((c) => c.count) || [1]));
            const percentage = (item.count / maxCount) * 100;
            const colors = ['bg-blue-500', 'bg-green-500', 'bg-purple-500', 'bg-amber-500', 'bg-red-500', 'bg-pink-500'];
            return (
              <div key={idx}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="font-medium">{item.category}</span>
                  <span className="text-gray-600">{item.count} products</span>
                </div>
                <div className="bg-gray-200 rounded-full h-6 overflow-hidden">
                  <div
                    className={`h-full ${colors[idx % colors.length]} transition-all duration-500`}
                    style={{ width: `${percentage}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h3 className="text-lg font-semibold mb-4">Products by Status</h3>
        <div className="space-y-3">
          {data.overview?.productsByStatus?.map((item, idx) => {
            const maxCount = Math.max(...(data.overview?.productsByStatus?.map((s) => s.count) || [1]));
            const percentage = (item.count / maxCount) * 100;
            const statusColors = {
              active: 'bg-green-500',
              inactive: 'bg-gray-500',
              draft: 'bg-yellow-500',
            };
            return (
              <div key={idx}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="font-medium capitalize">{item.status}</span>
                  <span className="text-gray-600">{item.count}</span>
                </div>
                <div className="bg-gray-200 rounded-full h-6 overflow-hidden">
                  <div
                    className={`h-full ${statusColors[item.status as keyof typeof statusColors] || 'bg-blue-500'}`}
                    style={{ width: `${percentage}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h3 className="text-lg font-semibold mb-4">Top 10 Brands</h3>
        <div className="space-y-2">
          {data.overview?.topBrands?.slice(0, 10).map((item, idx) => {
            const maxCount = Math.max(...(data.overview?.topBrands?.map((b) => b.count) || [1]));
            const percentage = (item.count / maxCount) * 100;
            return (
              <div key={idx} className="flex items-center gap-4">
                <span className="w-24 text-sm font-medium truncate">{item.brand}</span>
                <div className="flex-1 bg-gray-200 rounded h-5 overflow-hidden">
                  <div
                    className="bg-gradient-to-r from-indigo-500 to-purple-500 h-full transition-all"
                    style={{ width: `${percentage}%` }}
                  />
                </div>
                <span className="text-sm text-gray-600 w-12 text-right">{item.count}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h3 className="text-lg font-semibold mb-4">Recent Orders</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b">
                <th className="text-left py-3 px-4 font-semibold text-gray-700">Order ID</th>
                <th className="text-left py-3 px-4 font-semibold text-gray-700">Customer</th>
                <th className="text-left py-3 px-4 font-semibold text-gray-700">Amount</th>
                <th className="text-left py-3 px-4 font-semibold text-gray-700">Status</th>
                <th className="text-left py-3 px-4 font-semibold text-gray-700">Date</th>
              </tr>
            </thead>
            <tbody>
              {data.overview?.recentOrders?.map((order, idx) => (
                <tr key={idx} className="border-b hover:bg-gray-50">
                  <td className="py-3 px-4">{order.id}</td>
                  <td className="py-3 px-4">{order.customerName}</td>
                  <td className="py-3 px-4 font-semibold">₹{order.totalAmount?.toLocaleString('en-IN')}</td>
                  <td className="py-3 px-4">
                    <span
                      className={`px-3 py-1 rounded-full text-xs font-semibold ${
                        order.status === 'completed'
                          ? 'bg-green-100 text-green-800'
                          : order.status === 'pending'
                            ? 'bg-yellow-100 text-yellow-800'
                            : 'bg-gray-100 text-gray-800'
                      }`}
                    >
                      {order.status}
                    </span>
                  </td>
                  <td className="py-3 px-4">{new Date(order.createdAt).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );

  const renderSalesTab = () => (
    <div className="space-y-6">
      <div className="bg-white rounded-lg shadow-md p-6">
        <h3 className="text-lg font-semibold mb-4">Revenue by Month</h3>
        <div className="flex items-end justify-around gap-2 h-64">
          {data.sales?.revenueByMonth?.map((item, idx) => {
            const maxRevenue = Math.max(...(data.sales?.revenueByMonth?.map((m) => m.revenue) || [1]));
            const percentage = (item.revenue / maxRevenue) * 100;
            return (
              <div key={idx} className="flex flex-col items-center gap-2 flex-1">
                <div
                  className="w-full bg-gradient-to-t from-blue-500 to-blue-400 rounded-t transition-all duration-500"
                  style={{ height: `${percentage}%` }}
                  title={`₹${item.revenue.toLocaleString('en-IN')}`}
                />
                <span className="text-xs font-semibold text-gray-700">{item.month}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <StatCard
          label="Average Order Value"
          value={`₹${(data.sales?.avgOrderValue || 0).toLocaleString('en-IN')}`}
          icon={<IndianRupee size={32} />}
          color="from-cyan-400 to-cyan-600"
        />
        <div className="bg-white rounded-lg shadow-md p-6">
          <h3 className="text-lg font-semibold mb-4">Orders by Payment Status</h3>
          <div className="space-y-3">
            {data.sales?.ordersByPaymentStatus?.map((item, idx) => {
              const maxCount = Math.max(...(data.sales?.ordersByPaymentStatus?.map((s) => s.count) || [1]));
              const percentage = (item.count / maxCount) * 100;
              const paymentColors = {
                paid: 'bg-green-500',
                pending: 'bg-yellow-500',
                failed: 'bg-red-500',
              };
              return (
                <div key={idx}>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="font-medium capitalize">{item.status}</span>
                    <span className="text-gray-600">{item.count}</span>
                  </div>
                  <div className="bg-gray-200 rounded-full h-6">
                    <div
                      className={`h-full rounded-full ${paymentColors[item.status as keyof typeof paymentColors] || 'bg-blue-500'}`}
                      style={{ width: `${percentage}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h3 className="text-lg font-semibold mb-4">Top Selling Products</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b">
                <th className="text-left py-3 px-4 font-semibold text-gray-700">Product</th>
                <th className="text-right py-3 px-4 font-semibold text-gray-700">Qty Sold</th>
                <th className="text-right py-3 px-4 font-semibold text-gray-700">Revenue</th>
              </tr>
            </thead>
            <tbody>
              {data.sales?.topSellingProducts?.map((product, idx) => (
                <tr key={idx} className="border-b hover:bg-gray-50">
                  <td className="py-3 px-4">{product.productTitle}</td>
                  <td className="py-3 px-4 text-right font-semibold">{product.quantitySold}</td>
                  <td className="py-3 px-4 text-right">₹{product.totalRevenue.toLocaleString('en-IN')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );

  const renderInventoryTab = () => (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <StatCard
          label="Total Stock Units"
          value={(data.inventory?.totalStockUnits || 0).toLocaleString()}
          icon={<Package size={32} />}
          color="from-emerald-400 to-emerald-600"
        />
        <StatCard
          label="Out of Stock"
          value={(data.inventory?.outOfStockCount || 0).toLocaleString()}
          icon={<Package size={32} />}
          color="from-red-400 to-red-600"
        />
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h3 className="text-lg font-semibold mb-4">Stock by Category</h3>
        <div className="space-y-3">
          {data.inventory?.productsByCategory?.map((item, idx) => {
            const maxStock = Math.max(...(data.inventory?.productsByCategory?.map((c) => c.totalStock) || [1]));
            const percentage = (item.totalStock / maxStock) * 100;
            const colors = ['bg-blue-500', 'bg-green-500', 'bg-purple-500', 'bg-amber-500', 'bg-red-500'];
            return (
              <div key={idx}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="font-medium">{item.category}</span>
                  <span className="text-gray-600">{item.totalStock} units</span>
                </div>
                <div className="bg-gray-200 rounded-full h-6 overflow-hidden">
                  <div
                    className={`h-full ${colors[idx % colors.length]} transition-all`}
                    style={{ width: `${percentage}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h3 className="text-lg font-semibold mb-4">Low Stock Products</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b">
                <th className="text-left py-3 px-4 font-semibold text-gray-700">Product</th>
                <th className="text-left py-3 px-4 font-semibold text-gray-700">Brand</th>
                <th className="text-left py-3 px-4 font-semibold text-gray-700">Category</th>
                <th className="text-right py-3 px-4 font-semibold text-gray-700">Stock</th>
              </tr>
            </thead>
            <tbody>
              {data.inventory?.lowStockProducts?.map((product, idx) => (
                <tr key={idx} className="border-b hover:bg-gray-50">
                  <td className="py-3 px-4 font-medium">{product.productName}</td>
                  <td className="py-3 px-4">{product.brand}</td>
                  <td className="py-3 px-4">{product.category}</td>
                  <td className="py-3 px-4 text-right">
                    <span className="px-3 py-1 rounded-full text-xs font-bold bg-red-100 text-red-800">
                      {product.stock}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );

  return (
    <>
      <Topbar
        title="Reports"
        subtitle="Real-time inventory and sales analytics"
        breadcrumb={[{ label: 'Home', href: '/dashboard' }, { label: 'Reports' }]}
        primaryAction={null}
      />
      <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>

        <div className="flex gap-2 mb-6 border-b">
          {(['overview', 'sales', 'inventory'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-6 py-3 font-semibold transition-all capitalize ${
                activeTab === tab
                  ? 'text-blue-600 border-b-2 border-blue-600'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="flex items-center justify-center min-h-96">
            <div className="text-center">
              <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4" />
              <p className="text-gray-600">Loading reports...</p>
            </div>
          </div>
        ) : (
          <div>
            {activeTab === 'overview' && renderOverviewTab()}
            {activeTab === 'sales' && renderSalesTab()}
            {activeTab === 'inventory' && renderInventoryTab()}
          </div>
        )}
      </div>
    </>
  );
}
