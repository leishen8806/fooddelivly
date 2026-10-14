import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { formatMinor } from './components/MoneyInput';
import { AdminSectionPage } from './layout/AdminSectionPage';

type Dashboard = {
  currency?: string;
  kpis: { today_orders: number; today_sales_minor: number; pending_orders: number; pending_review: number; pending_refunds: number; total_users: number; new_users: number; daily_active: number };
  series: Array<{ date: string; orders: number; sales_minor: number; new_users: number; daily_active: number }>;
  status_breakdown: Record<string, number>;
  payment_breakdown: Record<string, number>;
  hourly: Array<{ hour: number; orders: number }>;
  top_products: Array<{ name: string; quantity: number }>;
};

const chartColors = ['#2f7f83', '#d16b45', '#7c63a8', '#d4a72c', '#3f6fa6', '#a34e72', '#5b8e4b', '#6d747b'];
const maxOf = (values: number[]) => Math.max(1, ...values);
const truncate = (value: string, length = 24) => value.length > length ? `${value.slice(0, length - 1)}…` : value;
const emptyChart = (label: string, emptyLabel: string) => <div className="dashboard-chart-empty" role="img" aria-label={label}><p className="state">{emptyLabel}</p></div>;

export function OrderStatusChart({ values, label, valueLabel, emptyLabel }: { values: Record<string, number>; label: string; valueLabel: string; emptyLabel: string }) {
  const entries = Object.entries(values);
  if (!entries.length) return emptyChart(label, emptyLabel);
  const max = maxOf(entries.map(([, value]) => value));
  const height = entries.length * 34 + 16;
  return <div className="dashboard-chart-wrap">
    <svg viewBox={`0 0 420 ${height}`} role="img" aria-label={label} className="admin-chart dashboard-status-chart">
      <desc>{label}</desc>
      {entries.map(([name, value], index) => { const y = index * 34 + 24; const width = (value / max) * 260; return <g key={name}>
        <text x="0" y={y} className="dashboard-chart-label">{name}</text>
        <rect x="132" y={y - 16} width={width} height="20" rx="4" fill={chartColors[index % chartColors.length]}><title>{`${name}: ${value}`}</title></rect>
        <text x="404" y={y} textAnchor="end" className="dashboard-chart-value">{value}</text>
      </g>; })}
    </svg>
    <table className="sr-only"><thead><tr><th>{label}</th><th>{valueLabel}</th></tr></thead><tbody>{entries.map(([name, value]) => <tr key={name}><td>{name}</td><td>{value}</td></tr>)}</tbody></table>
  </div>;
}

export function PaymentMethodChart({ values, label, valueLabel, percentLabel, emptyLabel }: { values: Record<string, number>; label: string; valueLabel: string; percentLabel: string; emptyLabel: string }) {
  const entries = Object.entries(values);
  if (!entries.length) return emptyChart(label, emptyLabel);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);
  let offset = 0;
  return <div className="dashboard-chart-wrap">
    <svg viewBox="0 0 420 48" role="img" aria-label={label} className="admin-chart dashboard-payment-chart">
      <desc>{label}</desc>
      {entries.map(([name, value], index) => { const width = total ? (value / total) * 420 : 0; const x = offset; offset += width; return <rect key={name} x={x} y="8" width={width} height="28" fill={chartColors[index % chartColors.length]}><title>{`${name}: ${value}`}</title></rect>; })}
    </svg>
    <ul className="dashboard-chart-legend">{entries.map(([name, value], index) => <li key={name}><i style={{ backgroundColor: chartColors[index % chartColors.length] }} /><span>{name}</span><strong>{value} ({total ? ((value / total) * 100).toFixed(1) : '0.0'}%)</strong></li>)}</ul>
    <table className="sr-only"><thead><tr><th>{label}</th><th>{valueLabel}</th><th>{percentLabel}</th></tr></thead><tbody>{entries.map(([name, value]) => <tr key={name}><td>{name}</td><td>{value}</td><td>{total ? ((value / total) * 100).toFixed(1) : '0.0'}%</td></tr>)}</tbody></table>
  </div>;
}

export function TopProductsChart({ values, label, quantityLabel, emptyLabel }: { values: Array<{ name: string; quantity: number }>; label: string; quantityLabel: string; emptyLabel: string }) {
  if (!values.length) return emptyChart(label, emptyLabel);
  const max = maxOf(values.map((item) => item.quantity));
  const height = values.length * 30 + 16;
  return <div className="dashboard-chart-wrap">
    <svg viewBox={`0 0 420 ${height}`} role="img" aria-label={label} className="admin-chart dashboard-products-chart">
      <desc>{label}</desc>
      {values.map((item, index) => { const y = index * 30 + 22; const width = (item.quantity / max) * 255; return <g key={`${item.name}-${index}`}>
        <text x="0" y={y} className="dashboard-chart-label"><title>{item.name}</title>{truncate(item.name)}</text>
        <rect x="150" y={y - 14} width={width} height="18" rx="4" fill={chartColors[index % chartColors.length]}><title>{`${item.name}: ${item.quantity}`}</title></rect>
        <text x="404" y={y} textAnchor="end" className="dashboard-chart-value">{item.quantity}</text>
      </g>; })}
    </svg>
    <table className="sr-only"><thead><tr><th>{label}</th><th>{quantityLabel}</th></tr></thead><tbody>{values.map((item) => <tr key={item.name}><td>{item.name}</td><td>{item.quantity}</td></tr>)}</tbody></table>
  </div>;
}

export function OrderTrendChart({ values, currency, dateLabel, ordersLabel, salesLabel, emptyLabel }: { values: Dashboard['series']; currency: string; dateLabel: string; ordersLabel: string; salesLabel: string; emptyLabel: string }) {
  if (!values.length) return emptyChart(ordersLabel, emptyLabel);
  const width = 320;
  const height = 160;
  const plot = (items: number[]) => { const max = maxOf(items); return items.map((value, index) => `${(index / Math.max(1, items.length - 1)) * width},${height - (value / max) * 132 - 12}`).join(' '); };
  return <div className="dashboard-chart-wrap">
    <svg viewBox={`0 0 ${width} ${height + 24}`} role="img" aria-label={`${ordersLabel} / ${salesLabel}`} className="admin-chart dashboard-trend-chart">
      <desc>{`${ordersLabel} / ${salesLabel}`}</desc>
      <polyline points={plot(values.map((item) => item.orders))} fill="none" stroke="#2f7f83" strokeWidth="3" />
      <polyline points={plot(values.map((item) => item.sales_minor))} fill="none" stroke="#d16b45" strokeWidth="3" strokeDasharray="8 5" />
      {values.map((item) => <title key={item.date}>{`${item.date}: ${ordersLabel} ${item.orders}, ${salesLabel} ${formatMinor(item.sales_minor, currency)} ${currency}`}</title>)}
    </svg>
    <div className="dashboard-chart-legend"><span><i className="dashboard-legend-line dashboard-legend-orders" />{ordersLabel}</span><span><i className="dashboard-legend-line dashboard-legend-sales" />{salesLabel}</span></div>
    <table className="sr-only"><thead><tr><th>{dateLabel}</th><th>{ordersLabel}</th><th>{salesLabel}</th></tr></thead><tbody>{values.map((item) => <tr key={item.date}><td>{item.date}</td><td>{item.orders}</td><td>{formatMinor(item.sales_minor, currency)} {currency}</td></tr>)}</tbody></table>
  </div>;
}

function Bars({ values, label }: { values: number[]; label: string }) {
  const max = maxOf(values);
  return <svg viewBox={`0 0 ${Math.max(240, values.length * 8)} 100`} role="img" aria-label={label} className="admin-chart"><desc>{label}</desc>{values.map((value, index) => <rect key={index} x={index * 8 + 1} y={100 - (value / max) * 90} width="6" height={(value / max) * 90} fill="currentColor"><title>{`${index}: ${value}`}</title></rect>)}</svg>;
}

export default function AdminOverviewPage() {
  const { t } = useTranslation();
  const [days, setDays] = useState<7 | 30>(7);
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(async () => { setLoading(true); setError(''); try { setData((await api.get('/api/v1/admin/dashboard', { params: { days } })).data); } catch { setError(t('error.network')); } finally { setLoading(false); } }, [days, t]);
  useEffect(() => { void load(); }, [load]);
  const statusValues = Object.fromEntries(Object.entries(data?.status_breakdown ?? {}).map(([key, value]) => [t(`order.status.${key.toLowerCase()}`, { defaultValue: key }), value]));
  const paymentValues = Object.fromEntries(Object.entries(data?.payment_breakdown ?? {}).map(([key, value]) => [t(`payment.method.${key.toLowerCase()}`, { defaultValue: key }), value]));
  const currency = data?.currency || 'USD';
  return <AdminSectionPage activeSection="overview"><section className="panel dashboard-page"><div className="panel-heading"><div><span className="eyebrow">{t('nav.overview')}</span><h2>{t('nav.overview')}</h2></div><label>{t('admin.dashboard.days')}<select value={days} onChange={(event) => setDays(Number(event.target.value) as 7 | 30)}><option value="7">7</option><option value="30">30</option></select></label></div>{error && <p className="error" role="alert">{error}</p>}{loading && <p className="state">{t('common.loading')}</p>}{data && <><div className="overview-grid"><Link className="metric metric-action" to="/admin/orders"><span>{t('admin.dashboard.todayOrders')}</span><strong>{data.kpis.today_orders}</strong></Link><Link className="metric metric-action" to="/admin/finance/stats"><span>{t('admin.dashboard.todaySales')}</span><strong>{formatMinor(data.kpis.today_sales_minor, currency)} {currency}</strong></Link><Link className="metric metric-action warning" to="/admin/orders"><span>{t('admin.dashboard.pendingOrders')}</span><strong>{data.kpis.pending_orders}</strong></Link><Link className="metric metric-action warning" to="/admin/orders"><span>{t('admin.needsReview')}</span><strong>{data.kpis.pending_review}</strong></Link><Link className="metric metric-action warning" to="/admin/refunds"><span>{t('admin.dashboard.pendingRefunds')}</span><strong>{data.kpis.pending_refunds}</strong></Link><Link className="metric metric-action" to="/admin/customers"><span>{t('admin.dashboard.totalUsers')}</span><strong>{data.kpis.total_users}</strong></Link><div className="metric"><span>{t('admin.dashboard.newUsers')}</span><strong>{data.kpis.new_users}</strong></div><div className="metric"><span>{t('admin.dashboard.activeUsers')}</span><strong>{data.kpis.daily_active}</strong></div></div><div className="dashboard-grid"><article className="panel"><h3>{t('admin.dashboard.orders')}</h3><OrderTrendChart values={data.series} currency={currency} dateLabel={t('admin.dashboard.date')} ordersLabel={t('admin.dashboard.orders')} salesLabel={t('admin.orderSales')} emptyLabel={t('admin.dashboard.noData')} /></article><article className="panel"><h3>{t('admin.dashboard.users')}</h3><OrderTrendChart values={data.series.map((item) => ({ ...item, orders: item.daily_active, sales_minor: item.new_users }))} currency={currency} dateLabel={t('admin.dashboard.date')} ordersLabel={t('admin.dashboard.activeUsers')} salesLabel={t('admin.dashboard.newUsers')} emptyLabel={t('admin.dashboard.noData')} /></article><article className="panel"><h3>{t('admin.dashboard.status')}</h3><OrderStatusChart values={statusValues} label={t('admin.dashboard.status')} valueLabel={t('admin.dashboard.value')} emptyLabel={t('admin.dashboard.noData')} /></article><article className="panel"><h3>{t('admin.dashboard.payment')}</h3><PaymentMethodChart values={paymentValues} label={t('admin.dashboard.payment')} valueLabel={t('admin.dashboard.value')} percentLabel={t('admin.dashboard.percent')} emptyLabel={t('admin.dashboard.noData')} /></article><article className="panel"><h3>{t('admin.dashboard.hourly')}</h3><Bars values={data.hourly.map((item) => item.orders)} label={t('admin.dashboard.hourly')} /></article><article className="panel"><h3>{t('admin.dashboard.topProducts')}</h3><TopProductsChart values={data.top_products} label={t('admin.dashboard.topProducts')} quantityLabel={t('admin.dashboard.quantity')} emptyLabel={t('admin.dashboard.noData')} /></article></div></>}</section></AdminSectionPage>;
}
