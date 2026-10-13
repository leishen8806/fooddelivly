import { useCallback, useEffect, useMemo, useState, type ComponentProps } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { useAuthStore } from '../store/authStore';
import { AdminLayout } from './layout/AdminLayout';
import { DataTable } from './components/DataTable';

export type OrderListFilters = { q: string; from: string; to: string; paymentStatus: string; paymentMethod: string; statuses: string[]; page: number; sort: string };
export const parseDateRange = (from: string, to: string): string | null => {
  if (from && !/^\d{4}-\d{2}-\d{2}$/.test(from)) return 'invalid';
  if (to && !/^\d{4}-\d{2}-\d{2}$/.test(to)) return 'invalid';
  if (from && to && from > to) return 'reversed';
  return null;
};

type OrderSummary = {
  id: number; public_code: string; room_number: string; order_status: string; payment_status: string;
  payment_method?: string; currency: string; total_minor: number; received_minor: number;
  refund_pending_minor: number; coupon_name_snapshot?: string | null; coupon_discount_minor: number;
  customer?: { display_name?: string; username?: string; telegram_chat_url?: string | null } | null;
  items_preview: string[]; item_count: number; open_issue_count: number; created_at: string;
};
type Counts = { counts: Record<string, number>; pending_payment: number };
type OrderDetail = OrderSummary & { subtotal_minor: number; delivery_fee_minor: number; service_fee_minor: number; wallet_paid_minor: number; external_due_minor: number; paid_confirmed_minor: number; adjusted_total_minor?: number; removed_minor: number; items: Array<{ id: number; name: Record<string, string> | string; quantity: number; unit_price_minor: number; line_total_minor: number; options_json: Record<string, unknown> }>; proof?: { review_status: string; submitted_at: string; url: string } | null; events: Array<{ event: string; from_state?: string; to_state?: string; created_at: string }>; issues: Array<{ id: number; order_item_id: number; issue_type: string; status: string; resolution?: string | null; note?: string | null }>; refunds: Array<{ id: number; amount_minor: number; method: string; status: string; reference?: string | null }> };

const STATUS_KEYS = ['NEW', 'ACCEPTED', 'PREPARING', 'READY', 'DELIVERED', 'COMPLETED', 'CANCELLED'];
const minorDigits = (currency: string) => ['KHR', 'JPY', 'VND'].includes(currency.toUpperCase()) ? 0 : 2;
export const formatMinor = (minor: number, currency: string) => {
  const digits = minorDigits(currency);
  const negative = minor < 0;
  const raw = String(Math.abs(Math.trunc(minor))).padStart(digits + 1, '0');
  const amount = digits ? `${raw.slice(0, -digits)}.${raw.slice(-digits)}` : raw;
  return `${negative ? '-' : ''}${currency} ${amount}`;
};

function queryParams(filters: OrderListFilters) {
  const params = new URLSearchParams();
  if (filters.q) params.set('q', filters.q);
  if (filters.from) params.set('from', filters.from);
  if (filters.to) params.set('to', filters.to);
  if (filters.paymentStatus) params.set('payment_status', filters.paymentStatus);
  if (filters.paymentMethod) params.set('payment_method', filters.paymentMethod);
  filters.statuses.forEach((status) => params.append('status', status));
  params.set('limit', '25'); params.set('offset', String((filters.page - 1) * 25)); params.set('sort', filters.sort);
  return params;
}

const statusLabel = (t: (key: string, options?: Record<string, unknown>) => string, status: string) => t(`order.status.${status.toLowerCase()}`, { defaultValue: status });
const paymentLabel = (t: (key: string, options?: Record<string, unknown>) => string, status: string) => t(`order.payment.${status.toLowerCase()}`, { defaultValue: status });

export default function AdminOrdersPage() {
  const { code } = useParams<{ code?: string }>();
  return code ? <AdminOrderDetail code={code} /> : <AdminOrdersList />;
}

function AdminOrdersList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { role, storeId, logoutAdmin } = useAuthStore();
  const isHq = role === 'MANAGER' && storeId === null;
  const [filters, setFilters] = useState<OrderListFilters>({ q: '', from: '', to: '', paymentStatus: '', paymentMethod: '', statuses: [], page: 1, sort: '-created_at' });
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [counts, setCounts] = useState<Counts>({ counts: {}, pending_payment: 0 });
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dateError, setDateError] = useState<string | null>(null);

  const load = useCallback(async (activeFilters: OrderListFilters) => {
    const validation = parseDateRange(activeFilters.from, activeFilters.to);
    if (validation) { setDateError(validation); return; }
    setDateError(null); setLoading(true); setError(null);
    try {
      const [list, count] = await Promise.all([api.get('/api/v1/admin/orders/search', { params: queryParams(activeFilters) }), api.get('/api/v1/admin/orders/counts', { params: queryParams({ ...activeFilters, statuses: [], page: 1 }) })]);
      setOrders(list.data.items); setTotal(list.data.total); setCounts(count.data);
    } catch { setError(t('admin.ordersLoadFailed')); }
    finally { setLoading(false); }
  }, [t]);
  useEffect(() => { void load(filters); }, [filters, load]);

  const update = (patch: Partial<OrderListFilters>) => setFilters((current) => ({ ...current, ...patch, page: patch.page ?? 1 }));
  const navigateSection = (section: ComponentProps<typeof AdminLayout>['activeSection']) => navigate(`/admin/${section}`);
  const columns = useMemo(() => [
    { key: 'public_code', header: t('admin.orderNumber'), render: (row: OrderSummary) => <strong>{row.public_code}</strong> },
    { key: 'customer', header: t('admin.customer'), render: (row: OrderSummary) => row.customer?.telegram_chat_url ? <a href={row.customer.telegram_chat_url} onClick={(event) => event.stopPropagation()}>{row.customer.display_name || row.customer.username || '—'}</a> : (row.customer?.display_name || '—') },
    { key: 'room', header: t('admin.roomNumber'), render: (row: OrderSummary) => row.room_number || '—' },
    { key: 'items', header: t('admin.orderItems'), render: (row: OrderSummary) => <span title={row.items_preview.join(', ')}>{row.items_preview.join('、')}{row.item_count > row.items_preview.length ? ` +${row.item_count - row.items_preview.length}` : ''}</span> },
    { key: 'total_minor', header: t('admin.total'), render: (row: OrderSummary) => <strong>{formatMinor(row.total_minor, row.currency)}</strong> },
    { key: 'received', header: t('admin.receivedAmount'), render: (row: OrderSummary) => formatMinor(row.received_minor, row.currency) },
    { key: 'created_at', header: t('admin.createdAt'), render: (row: OrderSummary) => new Date(row.created_at).toLocaleString() },
    { key: 'payment_status', header: t('admin.paymentStatus'), render: (row: OrderSummary) => <span className={`status-pill payment-${row.payment_status.toLowerCase()}`}>{paymentLabel(t, row.payment_status)}</span> },
    { key: 'order_status', header: t('admin.orderStatus'), render: (row: OrderSummary) => <span className={`status-pill order-${row.order_status.toLowerCase()}`}>{statusLabel(t, row.order_status)}{row.open_issue_count ? ` · ${t('admin.openIssues', { count: row.open_issue_count })}` : ''}</span> },
  ], [t]);
  return <AdminLayout role={role || 'STAFF'} activeSection="orders" onNavigate={navigateSection} onSignOut={() => { logoutAdmin(); navigate('/admin/login'); }} isHq={isHq}>
    <section className="admin-page admin-orders-page">
      <div className="admin-page-heading"><div><span className="eyebrow">WORK QUEUE</span><h2>{t('admin.orders')}</h2></div><button type="button" className="admin-button secondary" onClick={() => void load(filters)}>{t('common.refresh')}</button></div>
      <div className="admin-toolbar" role="search">
        <input aria-label={t('admin.searchOrders')} value={filters.q} onChange={(event) => update({ q: event.target.value })} placeholder={t('admin.searchOrders')} />
        <input aria-label={t('admin.fromDate')} type="date" value={filters.from} onChange={(event) => update({ from: event.target.value })} />
        <input aria-label={t('admin.toDate')} type="date" value={filters.to} onChange={(event) => update({ to: event.target.value })} />
        <select aria-label={t('admin.paymentStatus')} value={filters.paymentStatus} onChange={(event) => update({ paymentStatus: event.target.value })}><option value="">{t('admin.allPaymentStatuses')}</option>{['UNPAID', 'PROOF_SUBMITTED', 'PAID_CONFIRMED', 'PARTIALLY_REFUNDED', 'REFUNDED', 'REJECTED'].map((status) => <option key={status} value={status}>{paymentLabel(t, status)}</option>)}</select>
        <select aria-label={t('admin.paymentMethod')} value={filters.paymentMethod} onChange={(event) => update({ paymentMethod: event.target.value })}><option value="">{t('admin.allPaymentMethods')}</option><option value="WALLET">{t('admin.wallet')}</option><option value="ABA">ABA</option><option value="MIXED">{t('admin.mixedPayment')}</option></select>
      </div>
      {dateError && <p className="admin-inline-error" role="alert">{t(dateError === 'reversed' ? 'admin.invalidDateRange' : 'admin.invalidDate')}</p>}
      <div className="admin-status-tabs" role="tablist"><button className={!filters.statuses.length && filters.paymentStatus !== 'PROOF_SUBMITTED' ? 'active' : ''} type="button" onClick={() => update({ statuses: [], paymentStatus: '' })}>{t('admin.allStatuses')} <b>{Object.values(counts.counts).reduce((sum, value) => sum + value, 0)}</b></button>{STATUS_KEYS.map((status) => <button className={filters.statuses.length === 1 && filters.statuses[0] === status ? 'active' : ''} type="button" key={status} onClick={() => update({ statuses: [status], paymentStatus: '' })}>{statusLabel(t, status)} <b>{counts.counts[status] || 0}</b></button>)}<button className={filters.paymentStatus === 'PROOF_SUBMITTED' ? 'active' : ''} type="button" onClick={() => update({ statuses: [], paymentStatus: 'PROOF_SUBMITTED' })}>{t('admin.pendingPayment')} <b>{counts.pending_payment}</b></button></div>
      <DataTable columns={columns} rows={orders} loading={loading} error={error} empty={t('admin.noOrders')} onRowClick={(row) => navigate(`/admin/orders/${row.public_code}`)} sortKey={filters.sort.replace('-', '')} sortDirection={filters.sort.startsWith('-') ? 'desc' : 'asc'} onSort={(key) => { if (key === 'created_at' || key === 'total_minor') update({ sort: filters.sort === `-${key}` ? key : `-${key}` }); }} getRowClassName={(row) => row.open_issue_count ? 'has-open-issue' : undefined} />
      <div className="admin-order-mobile-list" aria-label={t('admin.orders')}>
        {loading && <p>{t('common.loading')}</p>}{error && <p className="admin-inline-error" role="alert">{error}</p>}{!loading && !error && !orders.length && <p>{t('admin.noOrders')}</p>}
        {orders.map((order) => <button type="button" className="admin-order-mobile-card" key={order.id} onClick={() => navigate(`/admin/orders/${order.public_code}`)}><span><strong>{order.public_code}</strong><small>{order.customer?.display_name || '—'} · {t('admin.roomNumber')} {order.room_number || '—'}</small><small>{order.items_preview.join('、')}{order.item_count > order.items_preview.length ? ` +${order.item_count - order.items_preview.length}` : ''}</small></span><span><strong>{formatMinor(order.total_minor, order.currency)}</strong><small className={`status-pill order-${order.order_status.toLowerCase()}`}>{statusLabel(t, order.order_status)}</small></span></button>)}
      </div>
      <div className="admin-pagination"><span>{t('admin.totalRecords', { count: total })}</span><div><button type="button" disabled={filters.page <= 1} onClick={() => setFilters((current) => ({ ...current, page: current.page - 1 }))}>{t('admin.previousPage')}</button><span>{filters.page}</span><button type="button" disabled={filters.page * 25 >= total} onClick={() => setFilters((current) => ({ ...current, page: current.page + 1 }))}>{t('admin.nextPage')}</button></div></div>
    </section>
  </AdminLayout>;
}

function AdminOrderDetail({ code }: { code: string }) {
  const { t } = useTranslation(); const navigate = useNavigate(); const { role, storeId, logoutAdmin } = useAuthStore();
  const [order, setOrder] = useState<OrderDetail | null>(null); const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.get(`/api/v1/admin/orders/${encodeURIComponent(code)}`).then((response) => setOrder(response.data)).catch(() => setError(t('admin.orderNotFound'))); }, [code, t]);
  const reportShortage = async (itemId: number) => {
    if (!order) return;
    const note = globalThis.prompt(t('admin.shortageNotePrompt'), '') ?? '';
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/shortage`, { order_item_id: itemId, note });
      const response = await api.get(`/api/v1/admin/orders/${encodeURIComponent(code)}`);
      setOrder(response.data);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : t('admin.ordersLoadFailed'));
    }
  };
  const transition = async (status: string, reason?: string) => {
    if (!order) return;
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/status`, { status, reason });
      setOrder((await api.get(`/api/v1/admin/orders/${encodeURIComponent(code)}`)).data);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : t('admin.actionFailed')); }
  };
  const reviewPayment = async (decision: 'APPROVED' | 'REJECTED') => {
    if (!order) return;
    const reason = decision === 'REJECTED' ? (globalThis.prompt(t('payment.rejectReasonInput'), '') ?? '') : undefined;
    if (decision === 'REJECTED' && !(reason || '').trim()) return;
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/payment-review`, { decision, reason });
      setOrder((await api.get(`/api/v1/admin/orders/${encodeURIComponent(code)}`)).data);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : t('admin.actionFailed')); }
  };
  const navigateSection = (section: ComponentProps<typeof AdminLayout>['activeSection']) => navigate(`/admin/${section}`);
  return <AdminLayout role={role || 'STAFF'} activeSection="orders" onNavigate={navigateSection} onSignOut={() => { logoutAdmin(); navigate('/admin/login'); }} isHq={role === 'MANAGER' && storeId === null}>
    <section className="admin-page admin-order-detail"><Link className="admin-breadcrumb" to="/admin/orders">{t('admin.orders')}</Link>{error ? <p className="admin-inline-error" role="alert">{error}</p> : !order ? <p>{t('common.loading')}</p> : <><div className="admin-page-heading"><div><span className="eyebrow">{order.public_code}</span><h2>{t('admin.orderDetail')}</h2></div><span className={`status-pill order-${order.order_status.toLowerCase()}`}>{statusLabel(t, order.order_status)}</span></div><div className="admin-card admin-order-actions"><h3>{t('admin.actions')}</h3><div className="admin-action-row">{order.order_status === 'NEW' && <button type="button" className="admin-button primary" onClick={() => void transition('ACCEPTED')}>{t('order.accept')}</button>}{order.payment_status === 'PROOF_SUBMITTED' && order.order_status === 'ACCEPTED' && <><button type="button" className="admin-button primary" onClick={() => void reviewPayment('APPROVED')}>{t('admin.approvePayment')}</button><button type="button" className="admin-button secondary" onClick={() => void reviewPayment('REJECTED')}>{t('admin.rejectPayment')}</button></>}{order.order_status === 'PREPARING' && <button type="button" className="admin-button primary" onClick={() => void transition('READY')}>{t('order.markReady')}</button>}{order.order_status === 'READY' && <button type="button" className="admin-button primary" onClick={() => void transition('DELIVERED')}>{t('admin.markDelivered')}</button>}{order.order_status === 'DELIVERED' && <button type="button" className="admin-button primary" onClick={() => void transition('COMPLETED')}>{t('order.complete')}</button>}</div></div><div className="admin-detail-grid"><article className="admin-card"><h3>{t('admin.customer')}</h3><p>{order.customer?.display_name || '—'} {order.customer?.username ? `@${order.customer.username.replace(/^@/, '')}` : ''}</p><p>{t('admin.roomNumber')}: {order.room_number}</p>{order.customer?.telegram_chat_url && <a href={order.customer.telegram_chat_url}>{t('admin.openTelegram')}</a>}</article><article className="admin-card"><h3>{t('admin.paymentStatus')}</h3><p className={`status-pill payment-${order.payment_status.toLowerCase()}`}>{paymentLabel(t, order.payment_status)}</p><p>{t('admin.total')}: <strong>{formatMinor(order.total_minor, order.currency)}</strong></p><p>{t('admin.receivedAmount')}: {formatMinor(order.received_minor, order.currency)}</p>{order.refund_pending_minor > 0 && <p>{t('admin.pendingRefund')}: {formatMinor(order.refund_pending_minor, order.currency)}</p>}{order.proof && <img className="admin-payment-proof" src={order.proof.url} alt={t('admin.paymentImage')} />}</article></div><div className="admin-card"><h3>{t('admin.orderItems')}</h3><DataTable columns={[{ key: 'name', header: t('admin.product'), render: (item: OrderDetail['items'][number]) => <span>{typeof item.name === 'string' ? item.name : Object.values(item.name)[0]}</span> }, { key: 'quantity', header: t('admin.quantity'), render: (item: OrderDetail['items'][number]) => item.quantity }, { key: 'line_total_minor', header: t('admin.total'), render: (item: OrderDetail['items'][number]) => formatMinor(item.line_total_minor, order.currency) }, { key: 'shortage', header: t('admin.reportShortage'), render: (item: OrderDetail['items'][number]) => { const issue = order.issues.find((candidate) => candidate.order_item_id === item.id); const unpaid = order.order_status === 'NEW' && ['UNPAID', 'REJECTED'].includes(order.payment_status); const paid = ['ACCEPTED', 'PREPARING'].includes(order.order_status) && ['PAID_CONFIRMED', 'PARTIALLY_REFUNDED'].includes(order.payment_status); return issue?.status === 'OPEN' ? <span className="status-pill payment-proof_submitted">{t('admin.openIssues', { count: 1 })}</span> : (unpaid || paid) ? <button type="button" className="admin-button secondary" onClick={(event) => { event.stopPropagation(); void reportShortage(item.id); }}>{t('admin.reportShortage')}</button> : null; } }]} rows={order.items} empty={t('admin.noOrders')} /></div><div className="admin-card"><h3>{t('admin.activity')}</h3>{order.events.length ? <ul className="admin-event-list">{order.events.map((event) => <li key={`${event.event}-${event.created_at}`}><strong>{event.event}</strong><span>{event.created_at}</span></li>)}</ul> : <p>{t('admin.noActivity')}</p>}</div></>}</section>
  </AdminLayout>;
}
