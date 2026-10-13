import { useCallback, useEffect, useState, type ChangeEvent, type ComponentProps } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import axios from 'axios';
import api from '../api';
import { useAuthStore } from '../store/authStore';
import { AdminLayout } from './layout/AdminLayout';
import { DataTable } from './components/DataTable';
import { ConfirmDialog } from './components/ConfirmDialog';

export type OrderListFilters = { q: string; from: string; to: string; paymentStatus: string; paymentMethod: string; statuses: string[]; page: number; sort: string };
export const parseDateRange = (from: string, to: string): string | null => {
  if (from && !/^\d{4}-\d{2}-\d{2}$/.test(from)) return 'invalid';
  if (to && !/^\d{4}-\d{2}-\d{2}$/.test(to)) return 'invalid';
  if (from && to && from > to) return 'reversed';
  return null;
};

type OrderSummary = {
  id: number; public_code: string; room_number: string; order_status: string; payment_status: string;
  payment_method?: string; currency: string; total_minor: number; subtotal_minor?: number; received_minor: number;
  refund_pending_minor: number; coupon_name_snapshot?: string | null; coupon_discount_minor: number;
  customer?: { display_name?: string; username?: string; telegram_user_id?: string; telegram_chat_url?: string | null } | null;
  items_preview: string[]; item_count: number; open_issue_count: number; created_at: string;
};
type Counts = { counts: Record<string, number>; pending_payment: number };
type OrderDetail = OrderSummary & { subtotal_minor: number; delivery_fee_minor: number; service_fee_minor: number; wallet_paid_minor: number; external_due_minor: number; paid_confirmed_minor: number; adjusted_total_minor?: number; removed_minor: number; refunded_minor: number; items: Array<{ id: number; name: Record<string, string> | string; quantity: number; unit_price_minor: number; line_total_minor: number; options_json: Record<string, unknown> }>; proof?: { review_status: string; submitted_at: string; url: string } | null; events: Array<{ event: string; from_state?: string; to_state?: string; created_at: string }>; issues: Array<{ id: number; order_item_id: number; issue_type: string; status: string; resolution?: string | null; note?: string | null }>; refunds: Array<{ id: number; amount_minor: number; method: string; status: string; requires_approval?: boolean; reference?: string | null }> };
type DetailDialog = { type: 'cancel' } | { type: 'rejectPayment' } | { type: 'shortage'; itemId: number } | { type: 'refund' } | null;

const backendErrorKeys: Record<string, string> = {
  'Order not found': 'admin.orderNotFound',
  'Order status transition is not allowed': 'admin.transitionNotAllowed',
  'A cancellation reason is required': 'order.cancelReason',
  'Reason required for rejection': 'admin.reasonRequired',
  'This order has an unresolved shortage; wait for the customer': 'order.openShortage',
  'No pending payment proof for this order': 'admin.noPendingPaymentProof',
  'Order is not awaiting payment review': 'admin.paymentReviewNotReady',
  'Order must be accepted before payment review': 'admin.paymentReviewAcceptFirst',
  'This order does not need a payment proof': 'admin.paymentProofNotNeeded',
  'This order already has a pending payment proof': 'admin.paymentProofPending',
  'A refund reason is required': 'admin.reasonRequired',
  'Order has no confirmed wallet payment': 'admin.noWalletPayment',
  'This refund is not awaiting manager approval': 'admin.refundApprovalNotReady',
  'This refund is not awaiting manual completion': 'admin.refundCompletionNotReady',
};

const localizeBackendError = (requestError: unknown, t: (key: string, options?: Record<string, unknown>) => string) => {
  const detail = axios.isAxiosError(requestError) && typeof requestError.response?.data?.detail === 'string'
    ? requestError.response.data.detail
    : null;
  if (!detail) return t('admin.actionFailed');
  return t(backendErrorKeys[detail] || 'admin.actionFailedWithReason', { reason: detail });
};

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
const paymentLabel = (t: (key: string, options?: Record<string, unknown>) => string, status: string) => {
  const key = status === 'PAID_CONFIRMED' ? 'payment.confirmed' : status === 'PROOF_SUBMITTED' ? 'payment.pending' : status === 'REJECTED' ? 'payment.rejected' : status === 'PARTIALLY_REFUNDED' ? 'payment.partiallyRefunded' : status === 'REFUNDED' ? 'order.status.refunded' : 'order.status.unpaid';
  return t(key, { defaultValue: status });
};

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
  const copyTelegramId = async (telegramUserId?: string) => {
    if (telegramUserId) await navigator.clipboard.writeText(telegramUserId);
  };
  const runListAction = async (row: OrderSummary, status: string) => {
    try {
      await api.post(`/api/v1/admin/orders/${row.id}/status`, { status });
      await load(filters);
    } catch { setError(t('admin.actionFailed')); }
  };
  const actionFor = (row: OrderSummary) => {
    if (row.open_issue_count > 0) return null;
    if (row.order_status === 'NEW') return { status: 'ACCEPTED', label: t('order.accept') };
    if (row.order_status === 'PREPARING' && row.payment_status === 'PAID_CONFIRMED') return { status: 'READY', label: t('order.markReady') };
    if (row.order_status === 'READY') return { status: 'DELIVERED', label: t('admin.markDelivered') };
    if (row.order_status === 'DELIVERED') return { status: 'COMPLETED', label: t('order.complete') };
    return null;
  };
  const columns = [
    { key: 'public_code', header: t('admin.orderNumber'), render: (row: OrderSummary) => <strong>{row.public_code}</strong> },
    { key: 'customer', header: t('admin.orderUser'), render: (row: OrderSummary) => <span>{row.customer?.display_name || '—'}{row.customer?.username ? ` @${row.customer.username.replace(/^@/, '')}` : ''}</span> },
    { key: 'chat', header: t('admin.openChat'), render: (row: OrderSummary) => row.customer?.telegram_chat_url ? <span className="admin-chat-actions"><a href={row.customer.telegram_chat_url} onClick={(event) => event.stopPropagation()}>{t('admin.openTelegram')}</a>{!row.customer.username && row.customer.telegram_user_id && <button type="button" className="admin-link-button" onClick={(event) => { event.stopPropagation(); void copyTelegramId(row.customer?.telegram_user_id); }}>{t('admin.copyTelegramId')}</button>}</span> : '—' },
    { key: 'room', header: t('admin.roomNumber'), render: (row: OrderSummary) => row.room_number || '—' },
    { key: 'items', header: t('admin.orderItems'), render: (row: OrderSummary) => <span title={row.items_preview.join(', ')}>{row.items_preview.join('、')}{row.item_count > row.items_preview.length ? ` +${row.item_count - row.items_preview.length}` : ''}</span> },
    { key: 'subtotal_minor', header: t('common.subtotal'), render: (row: OrderSummary) => formatMinor(row.subtotal_minor ?? row.total_minor, row.currency) },
    { key: 'coupon_discount_minor', header: t('coupon.couponDiscount'), render: (row: OrderSummary) => row.coupon_discount_minor ? `-${formatMinor(row.coupon_discount_minor, row.currency)}` : '—' },
    { key: 'total_minor', header: t('admin.payableAmount'), render: (row: OrderSummary) => <strong>{formatMinor(row.total_minor, row.currency)}</strong> },
    { key: 'received', header: t('admin.receivedAmount'), render: (row: OrderSummary) => <span>{formatMinor(row.received_minor, row.currency)}{row.refund_pending_minor > 0 && <small className="pending-refund-note">{t('admin.pendingShort', { amount: formatMinor(row.refund_pending_minor, row.currency) })}</small>}</span> },
    { key: 'created_at', header: t('admin.createdAt'), render: (row: OrderSummary) => new Date(row.created_at).toLocaleString() },
    { key: 'payment_status', header: t('admin.paymentStatus'), render: (row: OrderSummary) => <span className={`status-pill payment-${row.payment_status.toLowerCase()}`}>{paymentLabel(t, row.payment_status)}</span> },
    { key: 'order_status', header: t('admin.orderStatus'), render: (row: OrderSummary) => <span className={`status-pill order-${row.order_status.toLowerCase()}`}>{statusLabel(t, row.order_status)}{row.open_issue_count ? ` · ${t('admin.openIssues', { count: row.open_issue_count })}` : ''}</span> },
    { key: 'actions', header: t('admin.actions'), render: (row: OrderSummary) => { const action = actionFor(row); return action ? <button type="button" className="admin-button primary table-action" onClick={(event) => { event.stopPropagation(); void runListAction(row, action.status); }}>{action.label}</button> : row.payment_status === 'PROOF_SUBMITTED' ? <button type="button" className="admin-link-button" onClick={(event) => { event.stopPropagation(); navigate(`/admin/orders/${row.public_code}`); }}>{t('admin.reviewPayment')}</button> : '—'; } },
  ];
  return <AdminLayout role={role || 'STAFF'} activeSection="orders" onNavigate={navigateSection} onSignOut={() => { logoutAdmin(); navigate('/admin/login'); }} isHq={isHq}>
    <section className="admin-page admin-orders-page">
      <div className="admin-page-heading"><div><span className="eyebrow">WORK QUEUE</span><h2>{t('admin.orders')}</h2></div><button type="button" className="admin-button secondary" onClick={() => void load(filters)}>{t('common.retry')}</button></div>
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
        {orders.map((order) => { const action = actionFor(order); return <article className="admin-order-mobile-card" key={order.id} role="button" tabIndex={0} onClick={() => navigate(`/admin/orders/${order.public_code}`)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') navigate(`/admin/orders/${order.public_code}`); }}><span><strong>{order.public_code}</strong><small>{order.customer?.display_name || '—'}{order.customer?.username ? ` @${order.customer.username.replace(/^@/, '')}` : ''}</small><small>{t('admin.roomNumber')}: {order.room_number || '—'}</small><small>{t('common.subtotal')}: {formatMinor(order.subtotal_minor ?? order.total_minor, order.currency)} · {t('admin.receivedAmount')}: {formatMinor(order.received_minor, order.currency)}{order.refund_pending_minor > 0 && <em className="pending-refund-note">{t('admin.pendingShort', { amount: formatMinor(order.refund_pending_minor, order.currency) })}</em>}</small><small>{t('coupon.couponDiscount')}: {order.coupon_discount_minor ? `-${formatMinor(order.coupon_discount_minor, order.currency)}` : '—'}</small>{order.customer?.telegram_chat_url && <span className="admin-chat-actions"><a href={order.customer.telegram_chat_url} onClick={(event) => event.stopPropagation()}>{t('admin.openTelegram')}</a>{!order.customer.username && order.customer.telegram_user_id && <button type="button" className="admin-link-button" onClick={(event) => { event.stopPropagation(); void copyTelegramId(order.customer?.telegram_user_id); }}>{t('admin.copyTelegramId')}</button>}</span>}</span><span><strong>{t('admin.payableAmount')}: {formatMinor(order.total_minor, order.currency)}</strong><small className={`status-pill order-${order.order_status.toLowerCase()}`}>{statusLabel(t, order.order_status)}</small>{action && <button type="button" className="admin-button primary table-action" onClick={(event) => { event.stopPropagation(); void runListAction(order, action.status); }}>{action.label}</button>}</span></article>; })}
      </div>
      <div className="admin-pagination"><span>{t('admin.totalRecords', { count: total })}</span><div><button type="button" disabled={filters.page <= 1} onClick={() => setFilters((current) => ({ ...current, page: current.page - 1 }))}>{t('admin.previousPage')}</button><span>{filters.page}</span><button type="button" disabled={filters.page * 25 >= total} onClick={() => setFilters((current) => ({ ...current, page: current.page + 1 }))}>{t('admin.nextPage')}</button></div></div>
    </section>
  </AdminLayout>;
}

export function AdminOrderDetail({ code, initialOrder, roleOverride }: { code: string; initialOrder?: OrderDetail; roleOverride?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const auth = useAuthStore();
  const role = roleOverride ?? auth.role;
  const storeId = auth.storeId;
  const logoutAdmin = auth.logoutAdmin;
  const [order, setOrder] = useState<OrderDetail | null>(initialOrder ?? null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DetailDialog>(null);
  const [proofUploading, setProofUploading] = useState(false);

  const refreshOrder = async () => {
    const response = await api.get(`/api/v1/admin/orders/${encodeURIComponent(code)}`);
    setOrder(response.data);
  };

  useEffect(() => {
    api.get(`/api/v1/admin/orders/${encodeURIComponent(code)}`)
      .then((response) => setOrder(response.data))
      .catch(() => { if (!initialOrder) setLoadError(t('admin.orderNotFound')); });
  }, [code, initialOrder, t]);

  const submitShortage = async (itemId: number, note: string) => {
    if (!order) return;
    setActionError(null);
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/shortage`, { order_item_id: itemId, note });
      await refreshOrder();
      setDialog(null);
    } catch (requestError) {
      setActionError(localizeBackendError(requestError, t));
    }
  };

  const transition = async (status: string, reason?: string) => {
    if (!order) return;
    setActionError(null);
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/status`, { status, reason });
      await refreshOrder();
    } catch (requestError) {
      setActionError(localizeBackendError(requestError, t));
    }
  };

  const reviewPayment = async (decision: 'APPROVED' | 'REJECTED') => {
    if (!order) return;
    setActionError(null);
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/payment-review`, { decision });
      await refreshOrder();
      setDialog(null);
    } catch (requestError) {
      setActionError(localizeBackendError(requestError, t));
    }
  };

  const rejectPayment = async (reason: string) => {
    if (!order) return;
    setActionError(null);
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/payment-review`, { decision: 'REJECTED', reason });
      await refreshOrder();
      setDialog(null);
    } catch (requestError) {
      setActionError(localizeBackendError(requestError, t));
    }
  };

  const refundWallet = async (reason: string) => {
    if (!order) return;
    setActionError(null);
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/refund`, { reason });
      await refreshOrder();
      setDialog(null);
    } catch (requestError) {
      setActionError(localizeBackendError(requestError, t));
    }
  };

  const approveShortageRefund = async (refundId: number) => {
    setActionError(null);
    try {
      await api.post(`/api/v1/admin/order-refunds/${refundId}/approve`, {});
      await refreshOrder();
    } catch (requestError) {
      setActionError(localizeBackendError(requestError, t));
    }
  };

  const uploadProof = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.currentTarget.value = '';
    if (!order || !file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) {
      setActionError(t('admin.uploadImageConstraint'));
      return;
    }
    const form = new FormData();
    form.append('file', file);
    setProofUploading(true);
    setActionError(null);
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/payment-proof`, form);
      await refreshOrder();
    } catch (requestError) {
      setActionError(localizeBackendError(requestError, t));
    } finally {
      setProofUploading(false);
    }
  };

  const canCancel = (current: OrderDetail) => {
    const walletOnly = current.payment_method === 'WALLET';
    return !['CANCELLED', 'COMPLETED'].includes(current.order_status)
      && (current.payment_status !== 'PAID_CONFIRMED' || (walletOnly && ['NEW', 'ACCEPTED', 'PREPARING'].includes(current.order_status)));
  };
  const navigateSection = (section: ComponentProps<typeof AdminLayout>['activeSection']) => navigate(`/admin/${section}`);

  return <AdminLayout role={role || 'STAFF'} activeSection="orders" onNavigate={navigateSection} onSignOut={() => { logoutAdmin(); navigate('/admin/login'); }} isHq={role === 'MANAGER' && storeId === null}>
    <section className="admin-page admin-order-detail">
      <Link className="admin-breadcrumb" to="/admin/orders">{t('admin.orders')}</Link>
      {loadError ? <p className="admin-inline-error" role="alert">{loadError}</p> : !order ? <p>{t('common.loading')}</p> : (() => {
        const hasOpenShortage = order.open_issue_count > 0 || order.issues.some((issue) => issue.status === 'OPEN');
        const pendingRefundNote = order.refund_pending_minor > 0
          ? <small className="pending-refund-note">{t('admin.pendingShort', { amount: formatMinor(order.refund_pending_minor, order.currency) })}</small>
          : null;
        return <>
          {actionError && <p className="admin-inline-error" role="alert">{actionError}</p>}
          <div className="admin-page-heading"><div><span className="eyebrow">{order.public_code}</span><h2>{t('admin.orderDetail')}</h2></div><span className={`status-pill order-${order.order_status.toLowerCase()}`}>{statusLabel(t, order.order_status)}</span></div>
          <div className="admin-card admin-order-actions">
            <h3>{t('admin.actions')}</h3>
            {hasOpenShortage && <p className="admin-action-blocked" role="status">{t('order.openShortage')}</p>}
            <div className="admin-action-row">
              {order.order_status === 'NEW' && <button type="button" className="admin-button primary" disabled={hasOpenShortage} title={hasOpenShortage ? t('order.openShortage') : undefined} onClick={() => void transition('ACCEPTED')}>{t('order.accept')}</button>}
              {order.payment_status === 'PROOF_SUBMITTED' && order.order_status === 'ACCEPTED' && <><button type="button" className="admin-button primary" disabled={hasOpenShortage} title={hasOpenShortage ? t('order.openShortage') : undefined} onClick={() => void reviewPayment('APPROVED')}>{t('admin.approvePayment')}</button><button type="button" className="admin-button secondary" onClick={() => setDialog({ type: 'rejectPayment' })}>{t('admin.rejectPayment')}</button></>}
              {order.order_status === 'PREPARING' && order.payment_status === 'PAID_CONFIRMED' && <button type="button" className="admin-button primary" disabled={hasOpenShortage} title={hasOpenShortage ? t('order.openShortage') : undefined} onClick={() => void transition('READY')}>{t('order.markReady')}</button>}
              {order.order_status === 'READY' && <button type="button" className="admin-button primary" onClick={() => void transition('DELIVERED')}>{t('admin.markDelivered')}</button>}
              {order.order_status === 'DELIVERED' && <button type="button" className="admin-button primary" onClick={() => void transition('COMPLETED')}>{t('order.complete')}</button>}
              {canCancel(order) && <button type="button" className="admin-button secondary" onClick={() => setDialog({ type: 'cancel' })}>{t('order.cancel')}</button>}
              {role === 'MANAGER' && ['UNPAID', 'REJECTED'].includes(order.payment_status) && <label className="payment-proof-upload"><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void uploadProof(event)} disabled={proofUploading} /><span>{proofUploading ? t('admin.uploadingImage') : t('admin.uploadPaymentProof')}</span></label>}
              {role === 'MANAGER' && order.refunds.filter((refund) => refund.method === 'WALLET' && refund.status === 'PENDING' && refund.requires_approval).map((refund) => <button type="button" className="admin-button secondary" key={`approve-${refund.id}`} onClick={() => void approveShortageRefund(refund.id)}>{t('admin.approveRefund')} · {formatMinor(refund.amount_minor, order.currency)}</button>)}
              {role === 'MANAGER' && ['WALLET', 'MIXED'].includes(order.payment_method || '') && ['PAID_CONFIRMED', 'PARTIALLY_REFUNDED'].includes(order.payment_status) && <button type="button" className="admin-button secondary" onClick={() => setDialog({ type: 'refund' })}>{t('admin.refundOrder')}</button>}
            </div>
          </div>
          <div className="admin-detail-grid">
            <article className="admin-card"><h3>{t('admin.customer')}</h3><p>{order.customer?.display_name || '—'} {order.customer?.username ? `@${order.customer.username.replace(/^@/, '')}` : ''}</p><p>{t('admin.roomNumber')}: {order.room_number}</p>{order.customer?.telegram_chat_url && <a href={order.customer.telegram_chat_url}>{t('admin.openTelegram')}</a>}</article>
            <article className="admin-card"><h3>{t('admin.paymentStatus')}</h3><p className={`status-pill payment-${order.payment_status.toLowerCase()}`}>{paymentLabel(t, order.payment_status)}</p><div className="admin-amount-breakdown"><p>{t('common.subtotal')}: {formatMinor(order.subtotal_minor, order.currency)}</p><p>{t('coupon.couponDiscount')}: {order.coupon_discount_minor ? `-${formatMinor(order.coupon_discount_minor, order.currency)}` : '—'}</p><p>{t('admin.deliveryFee')}: {formatMinor(order.delivery_fee_minor, order.currency)}</p><p>{t('admin.serviceFee')}: {formatMinor(order.service_fee_minor, order.currency)}</p><p>{t('admin.payableAmount')}: <strong>{formatMinor(order.total_minor, order.currency)}</strong></p><p>{t('payment.walletApplied')}: {formatMinor(order.wallet_paid_minor, order.currency)}</p><p>{t('admin.receivedAmount')}: {formatMinor(order.received_minor, order.currency)} {pendingRefundNote}</p><p>{t('admin.refundedAmount')}: {formatMinor(order.refunded_minor, order.currency)}</p><p>{t('admin.pendingRefund')}: {formatMinor(order.refund_pending_minor, order.currency)}</p></div>{order.proof && <><img className="admin-payment-proof" src={order.proof.url} alt={t('admin.paymentImage')} /><p>{t('admin.checkActualPayment')}</p></>}</article>
          </div>
          <div className="admin-card"><h3>{t('admin.orderItems')}</h3><DataTable columns={[{ key: 'name', header: t('admin.product'), render: (item: OrderDetail['items'][number]) => <span>{typeof item.name === 'string' ? item.name : Object.values(item.name)[0]}</span> }, { key: 'quantity', header: t('admin.quantity'), render: (item: OrderDetail['items'][number]) => item.quantity }, { key: 'line_total_minor', header: t('admin.total'), render: (item: OrderDetail['items'][number]) => formatMinor(item.line_total_minor, order.currency) }, { key: 'shortage', header: t('admin.reportShortage'), render: (item: OrderDetail['items'][number]) => { const issue = order.issues.find((candidate) => candidate.order_item_id === item.id); const unpaid = order.order_status === 'NEW' && ['UNPAID', 'REJECTED'].includes(order.payment_status); const paid = ['ACCEPTED', 'PREPARING'].includes(order.order_status) && ['PAID_CONFIRMED', 'PARTIALLY_REFUNDED'].includes(order.payment_status); return issue?.status === 'OPEN' ? <span className="status-pill payment-proof_submitted">{t('admin.openIssues', { count: 1 })}</span> : (unpaid || paid) ? <button type="button" className="admin-button secondary" onClick={(event) => { event.stopPropagation(); setDialog({ type: 'shortage', itemId: item.id }); }}>{t('admin.reportShortage')}</button> : null; } }]} rows={order.items} empty={t('admin.noOrders')} /></div>
          <div className="admin-card"><h3>{t('admin.activity')}</h3>{order.events.length ? <ul className="admin-event-list">{order.events.map((event) => <li key={`${event.event}-${event.created_at}`}><strong>{event.event}</strong><span>{event.created_at}</span></li>)}</ul> : <p>{t('admin.noActivity')}</p>}</div>
          <ConfirmDialog open={dialog !== null} title={dialog?.type === 'cancel' ? t('order.cancel') : dialog?.type === 'rejectPayment' ? t('admin.rejectPayment') : dialog?.type === 'refund' ? t('admin.refundOrder') : t('admin.reportShortage')} description={dialog?.type === 'cancel' ? t('order.cancelConfirm') : undefined} confirmLabel={dialog?.type === 'cancel' ? t('order.cancel') : t('common.confirm')} cancelLabel={t('common.cancel')} reasonLabel={t('common.reason')} requireReason onClose={() => setDialog(null)} onConfirm={(reason) => { if (dialog?.type === 'cancel') void transition('CANCELLED', reason); else if (dialog?.type === 'rejectPayment') void rejectPayment(reason || ''); else if (dialog?.type === 'refund') void refundWallet(reason || ''); else if (dialog?.type === 'shortage') void submitShortage(dialog.itemId, reason || ''); }} />
        </>;
      })()}
    </section>
  </AdminLayout>;
}
