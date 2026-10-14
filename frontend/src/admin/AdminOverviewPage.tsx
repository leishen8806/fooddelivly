import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
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
  top_products: Array<{ name: string | Record<string, string>; quantity: number }>;
};

const chartColors = ['#2f7f83', '#d16b45', '#7c63a8', '#d4a72c', '#3f6fa6', '#a34e72', '#5b8e4b', '#6d747b'];
export const DEFAULT_CHART_WIDTH = 640;
export const normalizeChartWidth = (width: number, fallback = DEFAULT_CHART_WIDTH) => Number.isFinite(width) && width > 0 ? Math.round(width) : fallback;
export function useChartWidth<T extends HTMLElement>(fallback = DEFAULT_CHART_WIDTH) {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const update = () => setWidth(normalizeChartWidth(element.getBoundingClientRect().width, fallback));
    update();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [fallback]);
  return { ref, width };
}

export const getTickIndexes = (count: number, days: 7 | 30) => {
  if (count <= 0) return [];
  if (days === 7) return Array.from({ length: count }, (_, index) => index);
  return Array.from({ length: count }, (_, index) => index).filter((index) => index % 5 === 0 || index === count - 1);
};
export const formatDateTick = (value: string, locale = 'en') => {
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat(locale, { month: '2-digit', day: '2-digit', timeZone: 'UTC' }).formatToParts(date);
  return `${parts.find((part) => part.type === 'month')?.value || ''}-${parts.find((part) => part.type === 'day')?.value || ''}`;
};
const maxOf = (values: number[]) => Math.max(1, ...values);
const truncate = (value: string, length = 24) => value.length > length ? `${value.slice(0, length - 1)}…` : value;
const emptyChart = (label: string, emptyLabel: string) => <div className="dashboard-chart-empty" role="img" aria-label={label}><p className="state">{emptyLabel}</p></div>;
const numericTicks = (max: number, count = 4) => Array.from({ length: count + 1 }, (_, index) => Math.round((max * index) / count));
export const localizedProductName = (name: string | Record<string, string>, language: string) => {
  if (typeof name === 'string') return name;
  return name[language] || name[language.split('-')[0]] || name.en || Object.values(name)[0] || '';
};

const statusOrder = ['NEW', 'ACCEPTED', 'PREPARING', 'READY', 'DELIVERED', 'COMPLETED', 'CANCELLED'];
const statusFallbacks: Record<string, string> = { NEW: 'New order', ACCEPTED: 'Accepted', PREPARING: 'Preparing', READY: 'Ready', DELIVERED: 'Delivered', COMPLETED: 'Completed', CANCELLED: 'Cancelled' };
const paymentFallbacks: Record<string, string> = { WALLET: 'Wallet', ABA: 'ABA transfer', ABA_MANUAL: 'ABA transfer', MANUAL: 'ABA transfer', MIXED: 'Wallet + ABA', CASH: 'Cash' };
const translateStatus = (key: string, t: (key: string, options?: Record<string, unknown>) => string) => t(`order.status.${key.toLowerCase()}`, { defaultValue: statusFallbacks[key] || 'Status' });
const translatePayment = (key: string, t: (key: string, options?: Record<string, unknown>) => string) => t(`payment.method.${key.toLowerCase()}`, { defaultValue: paymentFallbacks[key] || 'Payment' });

export function OrderStatusChart({ values, label, valueLabel, emptyLabel, displayNames = {} }: { values: Record<string, number>; label: string; valueLabel: string; emptyLabel: string; displayNames?: Record<string, string> }) {
  const { ref, width } = useChartWidth<HTMLDivElement>();
  const ordered = statusOrder.filter((key) => values[key] !== undefined).map((key) => [key, values[key]] as const);
  const entries = (ordered.length ? ordered : Object.entries(values)).filter(([, value]) => value > 0);
  if (!entries.length) return emptyChart(label, emptyLabel);
  const max = maxOf(entries.map(([, value]) => value));
  const height = entries.length * 34 + 20;
  const left = Math.min(150, Math.max(100, width * 0.28));
  const right = 42;
  const barWidth = Math.max(1, width - left - right);
  return <div className="dashboard-chart-wrap" ref={ref}><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className="admin-chart dashboard-status-chart"><desc>{label}</desc>{entries.map(([name, value], index) => { const y = index * 34 + 24; const bar = (value / max) * barWidth; const displayName = displayNames[name] || name; return <g key={name}><text x="0" y={y} className="dashboard-chart-label">{displayName}</text><rect x={left} y={y - 16} width={bar} height="20" rx="4" fill={chartColors[index % chartColors.length]}><title>{`${displayName}: ${value}`}</title></rect><text x={width - 4} y={y} textAnchor="end" className="dashboard-chart-value">{value}</text></g>; })}</svg><table className="sr-only"><thead><tr><th>{label}</th><th>{valueLabel}</th></tr></thead><tbody>{entries.map(([name, value]) => <tr key={name}><td>{displayNames[name] || name}</td><td>{value}</td></tr>)}</tbody></table></div>;
}

export function PaymentMethodChart({ values, label, valueLabel, percentLabel, emptyLabel }: { values: Record<string, number>; label: string; valueLabel: string; percentLabel: string; emptyLabel: string }) {
  const { ref, width } = useChartWidth<HTMLDivElement>();
  const entries = Object.entries(values).filter(([, value]) => value > 0);
  if (!entries.length) return emptyChart(label, emptyLabel);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);
  const legend = <ul className="dashboard-chart-legend">{entries.map(([name, value], index) => <li key={name}><i style={{ backgroundColor: chartColors[index % chartColors.length] }} /><span>{name}</span><strong>{value} ({total ? ((value / total) * 100).toFixed(1) : '0.0'}%)</strong></li>)}</ul>;
  const table = <table className="sr-only"><thead><tr><th>{label}</th><th>{valueLabel}</th><th>{percentLabel}</th></tr></thead><tbody>{entries.map(([name, value]) => <tr key={name}><td>{name}</td><td>{value}</td><td>{total ? ((value / total) * 100).toFixed(1) : '0.0'}%</td></tr>)}</tbody></table>;
  if (entries.length === 1) return <div className="dashboard-chart-wrap dashboard-single-payment" ref={ref}><strong>{entries[0][0]} {entries[0][1]} ({total ? '100.0' : '0.0'}%)</strong>{legend}{table}</div>;
  let offset = 0;
  return <div className="dashboard-chart-wrap" ref={ref}><svg viewBox={`0 0 ${width} 54`} role="img" aria-label={label} className="admin-chart dashboard-payment-chart"><desc>{label}</desc>{entries.map(([name, value], index) => { const barWidth = total ? (value / total) * width : 0; const x = offset; offset += barWidth; return <rect key={name} x={x} y="10" width={barWidth} height="30" fill={chartColors[index % chartColors.length]}><title>{`${name}: ${value}`}</title></rect>; })}</svg>{legend}{table}</div>;
}

export function TopProductsChart({ values, label, quantityLabel, emptyLabel, language = 'en' }: { values: Array<{ name: string | Record<string, string>; quantity: number }>; label: string; quantityLabel: string; emptyLabel: string; language?: string }) {
  const { ref, width } = useChartWidth<HTMLDivElement>();
  if (!values.length) return emptyChart(label, emptyLabel);
  const max = maxOf(values.map((item) => item.quantity));
  const height = values.length * 34 + 20;
  const barStart = Math.min(250, Math.max(125, width * 0.42));
  const right = 42;
  const barWidth = Math.max(1, width - barStart - right);
  const maxLabelLength = Math.max(12, Math.floor((barStart - 8) / 7));
  return <div className="dashboard-chart-wrap" ref={ref}><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className="admin-chart dashboard-products-chart"><desc>{label}</desc>{values.map((item, index) => { const name = localizedProductName(item.name, language); const y = index * 34 + 24; const bar = (item.quantity / max) * barWidth; return <g key={`${name}-${index}`}><text x="0" y={y} className="dashboard-chart-label"><title>{name}</title>{truncate(name, maxLabelLength)}</text><rect x={barStart} y={y - 16} width={bar} height="20" rx="4" fill={chartColors[index % chartColors.length]}><title>{`${name}: ${item.quantity}`}</title></rect><text x={width - 4} y={y} textAnchor="end" className="dashboard-chart-value">{item.quantity}</text></g>; })}</svg><table className="sr-only"><thead><tr><th>{label}</th><th>{quantityLabel}</th></tr></thead><tbody>{values.map((item, index) => <tr key={`${localizedProductName(item.name, language)}-${index}`}><td>{localizedProductName(item.name, language)}</td><td>{item.quantity}</td></tr>)}</tbody></table></div>;
}

type TrendValue = Dashboard['series'][number];
export function OrderTrendChart({ values, currency, dateLabel, ordersLabel, salesLabel, emptyLabel, days = 7, locale = 'en', rightIsCurrency = true }: { values: TrendValue[]; currency: string; dateLabel: string; ordersLabel: string; salesLabel: string; emptyLabel: string; days?: 7 | 30; locale?: string; rightIsCurrency?: boolean }) {
  const { ref, width } = useChartWidth<HTMLDivElement>();
  if (!values.length || values.every((item) => item.orders === 0 && item.sales_minor === 0)) return emptyChart(ordersLabel, emptyLabel);
  const height = 250;
  const margin = { top: 26, right: 66, bottom: 44, left: 42 };
  const plotWidth = Math.max(1, width - margin.left - margin.right);
  const plotHeight = height - margin.top - margin.bottom;
  const orderMax = maxOf(values.map((item) => item.orders));
  const rightMax = maxOf(values.map((item) => item.sales_minor));
  const point = (index: number, value: number, max: number) => `${margin.left + (index / Math.max(1, values.length - 1)) * plotWidth},${margin.top + plotHeight - (value / max) * plotHeight}`;
  const orderPoints = values.map((item, index) => point(index, item.orders, orderMax)).join(' ');
  const salesPoints = values.map((item, index) => point(index, item.sales_minor, rightMax)).join(' ');
  const ticks = numericTicks(orderMax);
  const rightTicks = numericTicks(rightMax);
  const tickIndexes = getTickIndexes(values.length, days);
  const formatRight = (value: number) => rightIsCurrency ? `${formatMinor(value, currency)} ${currency}` : String(value);
  return <div className="dashboard-chart-wrap" ref={ref}><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${ordersLabel} / ${salesLabel}`} className="admin-chart dashboard-trend-chart"><desc>{`${ordersLabel} / ${salesLabel}`}</desc>{ticks.map((tick, index) => { const y = margin.top + plotHeight - (tick / orderMax) * plotHeight; return <g key={`left-${tick}`}><line x1={margin.left} x2={width - margin.right} y1={y} y2={y} className="dashboard-grid-line" /><text x={margin.left - 8} y={y + 4} textAnchor="end" className="dashboard-axis-label">{tick}</text><text x={width - margin.right + 8} y={y + 4} className="dashboard-axis-label">{formatRight(rightTicks[index])}</text></g>; })}<polyline points={orderPoints} fill="none" stroke={chartColors[0]} strokeWidth="3" /><polyline points={salesPoints} fill="none" stroke={chartColors[1]} strokeWidth="3" strokeDasharray="8 5" />{values.map((item, index) => { const orderPoint = point(index, item.orders, orderMax).split(','); const salesPoint = point(index, item.sales_minor, rightMax).split(','); return <g key={item.date} tabIndex={0} className="dashboard-data-point"><circle cx={orderPoint[0]} cy={orderPoint[1]} r="4" fill={chartColors[0]}><title>{`${item.date}: ${ordersLabel} ${item.orders}, ${salesLabel} ${formatRight(item.sales_minor)}`}</title></circle><polygon points={`${Number(salesPoint[0])},${Number(salesPoint[1]) - 5} ${Number(salesPoint[0]) + 5},${salesPoint[1]} ${Number(salesPoint[0])},${Number(salesPoint[1]) + 5} ${Number(salesPoint[0]) - 5},${salesPoint[1]}`} fill={chartColors[1]}><title>{`${item.date}: ${ordersLabel} ${item.orders}, ${salesLabel} ${formatRight(item.sales_minor)}`}</title></polygon></g>; })}{tickIndexes.map((index) => <text key={values[index].date} x={margin.left + (index / Math.max(1, values.length - 1)) * plotWidth} y={height - 12} textAnchor="middle" className="dashboard-axis-label">{formatDateTick(values[index].date, locale)}</text>)}</svg><div className="dashboard-chart-legend"><span><i className="dashboard-legend-line dashboard-legend-orders" />{ordersLabel}</span><span><i className="dashboard-legend-line dashboard-legend-sales dashboard-legend-dashed" />{salesLabel}</span></div><table className="sr-only"><thead><tr><th>{dateLabel}</th><th>{ordersLabel}</th><th>{salesLabel}</th></tr></thead><tbody>{values.map((item) => <tr key={item.date}><td>{item.date}</td><td>{item.orders}</td><td>{formatRight(item.sales_minor)}</td></tr>)}</tbody></table></div>;
}

export const hourlyPeak = (values: number[]) => values.reduce<{ index: number; value: number } | null>((peak, value, index) => !peak || value > peak.value ? { index, value } : peak, null);
export function HourlyChart({ values, label, valueLabel, hourLabel }: { values: number[]; label: string; valueLabel: string; hourLabel: string }) {
  const { ref, width } = useChartWidth<HTMLDivElement>();
  const max = maxOf(values);
  const height = 220;
  const margin = { top: 20, right: 12, bottom: 42, left: 36 };
  const plotWidth = Math.max(1, width - margin.left - margin.right);
  const plotHeight = height - margin.top - margin.bottom;
  const barWidth = Math.max(2, plotWidth / Math.max(1, values.length) - 3);
  const tickStep = width < 460 ? 6 : 3;
  const topIndexes = [...values.keys()].sort((a, b) => values[b] - values[a]).filter((index) => values[index] > 0).slice(0, 3);
  return <div className="dashboard-chart-wrap" ref={ref}><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className="admin-chart dashboard-hourly-chart"><desc>{label}</desc>{numericTicks(max).map((tick) => { const y = margin.top + plotHeight - (tick / max) * plotHeight; return <g key={tick}><line x1={margin.left} x2={width - margin.right} y1={y} y2={y} className="dashboard-grid-line" /><text x={margin.left - 7} y={y + 4} textAnchor="end" className="dashboard-axis-label">{tick}</text></g>; })}{values.map((value, index) => { const barHeight = (value / max) * plotHeight; const x = margin.left + index * (plotWidth / Math.max(1, values.length)) + 1; const y = margin.top + plotHeight - barHeight; return <g key={index}><rect x={x} y={y} width={barWidth} height={barHeight} fill={topIndexes.includes(index) ? chartColors[1] : chartColors[0]}><title>{`${index}: ${value}`}</title></rect>{topIndexes.includes(index) && <text x={x + barWidth / 2} y={Math.max(12, y - 5)} textAnchor="middle" className="dashboard-chart-value">{value}</text>}</g>; })}{values.map((_, index) => index % tickStep === 0 ? <text key={`hour-${index}`} x={margin.left + index * (plotWidth / Math.max(1, values.length)) + barWidth / 2} y={height - 12} textAnchor="middle" className="dashboard-axis-label">{index}</text> : null)}</svg><table className="sr-only"><thead><tr><th>{hourLabel}</th><th>{valueLabel}</th></tr></thead><tbody>{values.map((value, index) => <tr key={index}><td>{String(index).padStart(2, '0')}:00</td><td>{value}</td></tr>)}</tbody></table></div>;
}

export default function AdminOverviewPage() {
  const { t, i18n } = useTranslation();
  const [searchParams] = useSearchParams();
  const [days, setDays] = useState<7 | 30>(7);
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(async () => { setLoading(true); setError(''); try { setData((await api.get('/api/v1/admin/dashboard', { params: { days } })).data); } catch { setError(t('error.network')); } finally { setLoading(false); } }, [days, t]);
  useEffect(() => { void load(); }, [load]);
  const statusValues = Object.fromEntries(statusOrder.filter((key) => data?.status_breakdown?.[key] !== undefined).map((key) => [key, data?.status_breakdown[key] || 0]));
  const statusNames = Object.fromEntries(statusOrder.map((key) => [key, translateStatus(key, t)]));
  const paymentValues = Object.fromEntries(Object.entries(data?.payment_breakdown ?? {}).map(([key, value]) => [translatePayment(key, t), value]));
  const currency = data?.currency || 'USD';
  const period = t('admin.dashboard.period', { days });
  const hourly = data?.hourly.map((item) => item.orders) || [];
  const peak = hourlyPeak(hourly);
  const peakText = peak && peak.value > 0 ? t('admin.dashboard.peak', { time: `${String(peak.index).padStart(2, '0')}:00–${String((peak.index + 1) % 24).padStart(2, '0')}:00`, count: peak.value }) : t('admin.dashboard.noData');
  const pendingSubtitle = <small className="metric-subtitle">{t('admin.dashboard.allPending')}</small>;
  return <AdminSectionPage activeSection="overview"><section className="panel dashboard-page"><div className="panel-heading dashboard-toolbar"><div><span className="eyebrow">{t('nav.overview')}</span><p className="dashboard-period">{period}</p></div><div className="dashboard-period-switch" role="group" aria-label={t('admin.dashboard.days')}><button type="button" aria-pressed={days === 7} onClick={() => setDays(7)}>7</button><button type="button" aria-pressed={days === 30} onClick={() => setDays(30)}>30</button></div></div>{searchParams.get('access') === 'denied' && <p className="notice" role="alert">{t('admin.permissionDenied')}</p>}{error && <p className="error" role="alert">{error}</p>}{loading && <p className="state">{t('common.loading')}</p>}{data && <><div className="overview-grid"><Link className="metric metric-action" to="/admin/orders"><span>{t('admin.dashboard.todayOrders')}</span><strong>{data.kpis.today_orders}</strong></Link><Link className="metric metric-action" to="/admin/finance/stats"><span>{t('admin.dashboard.todaySales')}</span><strong>{formatMinor(data.kpis.today_sales_minor, currency)} {currency}</strong></Link><Link className="metric metric-action warning" to="/admin/orders"><span>{t('admin.dashboard.pendingOrders')}</span><strong>{data.kpis.pending_orders}</strong>{pendingSubtitle}</Link><Link className="metric metric-action warning" to="/admin/orders"><span>{t('admin.needsReview')}</span><strong>{data.kpis.pending_review}</strong>{pendingSubtitle}</Link><Link className="metric metric-action warning" to="/admin/refunds"><span>{t('admin.dashboard.pendingRefunds')}</span><strong>{data.kpis.pending_refunds}</strong>{pendingSubtitle}</Link><Link className="metric metric-action" to="/admin/customers"><span>{t('admin.dashboard.totalUsers')}</span><strong>{data.kpis.total_users}</strong></Link><div className="metric"><span>{t('admin.dashboard.newUsers')}</span><strong>{data.kpis.new_users}</strong></div><div className="metric"><span>{t('admin.dashboard.activeUsers')}</span><strong>{data.kpis.daily_active}</strong></div></div><div className="dashboard-grid"><article className="panel"><h3>{t('admin.dashboard.orders')} <small className="dashboard-period-tag">{period}</small></h3><OrderTrendChart values={data.series} currency={currency} days={days} locale={i18n.language} dateLabel={t('admin.dashboard.date')} ordersLabel={t('admin.dashboard.orders')} salesLabel={t('admin.orderSales')} emptyLabel={t('admin.dashboard.noData')} /></article><article className="panel"><h3>{t('admin.dashboard.users')} <small className="dashboard-period-tag">{period}</small></h3><OrderTrendChart values={data.series.map((item) => ({ ...item, orders: item.daily_active, sales_minor: item.new_users }))} currency={currency} rightIsCurrency={false} days={days} locale={i18n.language} dateLabel={t('admin.dashboard.date')} ordersLabel={t('admin.dashboard.activeUsers')} salesLabel={t('admin.dashboard.newUsers')} emptyLabel={t('admin.dashboard.noData')} /></article><article className="panel"><h3>{t('admin.dashboard.status')} <small className="dashboard-period-tag">{period}</small></h3><OrderStatusChart values={statusValues} displayNames={statusNames} label={t('admin.dashboard.status')} valueLabel={t('admin.dashboard.value')} emptyLabel={t('admin.dashboard.noData')} /></article><article className="panel"><h3>{t('admin.dashboard.payment')} <small className="dashboard-period-tag">{period}</small></h3><PaymentMethodChart values={paymentValues} label={t('admin.dashboard.payment')} valueLabel={t('admin.dashboard.value')} percentLabel={t('admin.dashboard.percent')} emptyLabel={t('admin.dashboard.noData')} /></article><article className="panel"><h3>{t('admin.dashboard.hourly')} <small className="dashboard-period-tag">{period}</small></h3><HourlyChart values={hourly} label={t('admin.dashboard.hourly')} hourLabel={t('admin.dashboard.hour')} valueLabel={t('admin.dashboard.value')} /><p className="dashboard-peak">{peakText}</p></article><article className="panel"><h3>{t('admin.dashboard.topProducts')} <small className="dashboard-period-tag">{period}</small></h3><TopProductsChart values={data.top_products} language={i18n.language} label={t('admin.dashboard.topProducts')} quantityLabel={t('admin.dashboard.quantity')} emptyLabel={t('admin.dashboard.noData')} /></article></div></>}</section></AdminSectionPage>;
}
