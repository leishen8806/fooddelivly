import { useCallback, useEffect, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import AdminRecharges from '../components/AdminRecharges';
import AdminCoupons from '../components/AdminCoupons';
import ImageUpload from './components/ImageUpload';
import { useAuthStore } from '../store/authStore';
import { AdminLayout } from './layout/AdminLayout';
import { adminPathForSection, type AdminSection } from './layout/nav';

type OrderItemOptions = { sweetness?: number; price_delta_minor?: number; selections?: Array<{ group_id: number; group_name?: Record<string, string>; option_id: number; option_name?: Record<string, string>; price_delta_minor: number }> };
type OrderRefund = { id: number; amount_minor: number; method: string; status: string; requires_approval?: boolean };
type Order = { id: number; public_code: string; room_number: string; order_status: string; payment_status: string; payment_method?: string; currency: string; total_minor: number; subtotal_minor?: number; coupon_name_snapshot?: string | null; coupon_discount_minor?: number; delivery_fee_minor?: number; service_fee_minor?: number; wallet_paid_minor?: number; external_due_minor?: number; refunds?: OrderRefund[]; items?: Array<{ id?: number; name: Record<string, string> | string; quantity: number; line_total_minor: number; options?: OrderItemOptions }> };
type StoreSettings = { currency: string; timezone: string; aba_qr_asset_key: string | null; payment_link: string | null; telegram_staff_group_id: string | null; staff_group_language: string; open_hours: string | null; is_accepting_orders: boolean; business_hours: Array<{ start: string; end: string }>; min_order_minor: number; delivery_fee_minor: number; service_fee_minor: number; unpaid_order_timeout_minutes: number; unpaid_order_reminder_minutes: number; shortage_customer_response_timeout_minutes: number; shortage_refund_review_minor: number };
type Staff = { id: number; login_name: string; role: string; active: boolean; telegram_user_id: string | null };
type Customer = { id: number; telegram_user_id: string; display_name: string | null; username: string | null; telegram_chat_url: string; order_count: number; completed_order_count: number; created_at: string };
type AuditEntry = { id: number; entity_type: string; entity_id: string; action: string; operator_name: string; operator_telegram_id: string | null; source: string; details: Record<string, string | null | undefined>; created_at: string };
type FinanceCurrencyRow = {
  currency: string;
  order_sales_minor?: number;
  order_sales_subtotal_minor?: number;
  order_sales_discount_minor?: number;
  order_total_minor?: number;
  external_order_receipts_minor?: number;
  wallet_paid_minor?: number;
  recharge_credited_minor?: number;
  net_cash_inflow_minor?: number;
  cash_inflow_minor?: number;
  refund_aba_done_minor?: number;
  refund_wallet_done_minor?: number;
  refund_pending_minor?: number;
  refund_pending_count?: number;
  shortage_removed_minor?: number;
  refund_reconcile_diff_minor?: number;
  in_review_minor?: number;
  cancelled_total_minor?: number;
};
type AdminStats = { order_volume?: number; cancelled_count?: number; timezone?: string; by_currency?: FinanceCurrencyRow[] };

const label = (value: Record<string, string> | undefined, language: string) => value?.[language] || value?.en || '';
const amount = (minor: number, currency: string, locale = 'en') => {
  try {
    const digits = new Intl.NumberFormat(locale, { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2;
    return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(minor / (10 ** digits));
  } catch {
    return `${minor} ${currency}`;
  }
};
const fromMinor = (minor: number, currency: string) => {
  try { return minor / (10 ** (new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2)); }
  catch { return minor / 100; }
};
const toMinor = (value: number, currency: string) => {
  try { return Math.round(value * (10 ** (new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2))); }
  catch { return Math.round(value * 100); }
};
const apiDetail = (error: unknown) => {
  if (typeof error !== 'object' || error === null || !('response' in error)) return undefined;
  const response = (error as { response?: unknown }).response;
  if (typeof response !== 'object' || response === null || !('data' in response)) return undefined;
  const data = (response as { data?: unknown }).data;
  if (typeof data !== 'object' || data === null || !('detail' in data)) return undefined;
  const detail = (data as { detail?: unknown }).detail;
  return typeof detail === 'string' ? detail : undefined;
};

export default function AdminLegacyPage({ initialSection = 'overview' }: { initialSection?: AdminSection }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { isAdminAuthenticated, logoutAdmin, role, storeId, adminStoreResolved } = useAuthStore();
  const [orders, setOrders] = useState<Order[]>([]);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [section, setSection] = useState<AdminSection>(initialSection);
  const selectSection = (next: AdminSection) => { setSection(next); navigate(adminPathForSection(next)); };
  const [orderFilter, setOrderFilter] = useState('ALL');
  const [error, setError] = useState('');
  const [from] = useState(() => new Date(Date.now() - 6 * 86400000).toISOString().slice(0, 10));
  const [to] = useState(() => new Date().toISOString().slice(0, 10));
  const [busyId, setBusyId] = useState<number | null>(null);
  const [feeInputs, setFeeInputs] = useState({ minOrder: '', delivery: '', service: '' });
  const [storeSettings, setStoreSettings] = useState<StoreSettings>({ currency: 'USD', timezone: 'Asia/Phnom_Penh', aba_qr_asset_key: null, payment_link: null, telegram_staff_group_id: null, staff_group_language: 'en', open_hours: null, is_accepting_orders: true, business_hours: [], min_order_minor: 0, delivery_fee_minor: 0, service_fee_minor: 0, unpaid_order_timeout_minutes: 0, unpaid_order_reminder_minutes: 0, shortage_customer_response_timeout_minutes: 0, shortage_refund_review_minor: 0 });
  const [staff, setStaff] = useState<Staff[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [customerSearch, setCustomerSearch] = useState('');
  const [imageErrors, setImageErrors] = useState<Set<number>>(() => new Set());
  const [proofUploadingId, setProofUploadingId] = useState<number | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditEntry[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditOffset, setAuditOffset] = useState(0);
  const [auditLoading, setAuditLoading] = useState(false);
  const [telegramIdEdits, setTelegramIdEdits] = useState<Record<number, string>>({});
  const [busyStaffId, setBusyStaffId] = useState<number | null>(null);
  const [newStaff, setNewStaff] = useState({ login: '', password: '', telegramId: '', role: 'STAFF' });

  const fetchData = useCallback(async () => {
    setError('');
    const [orderResult, statsResult, settingsResult, staffResult, customerResult] = await Promise.allSettled([
      api.get('/api/v1/admin/orders'), api.get('/api/v1/admin/analytics/', { params: { from, to } }),
      api.get('/api/v1/admin/settings/'), api.get('/api/v1/admin/staff'),
      role === 'MANAGER' ? api.get('/api/v1/admin/customers') : Promise.resolve({ data: [] }),
    ]);
    if (orderResult.status === 'fulfilled') setOrders(orderResult.value.data); else setError(t('error.network'));
    if (statsResult.status === 'fulfilled') setStats(statsResult.value.data);
    if (settingsResult.status === 'fulfilled') {
      const loaded = settingsResult.value.data as StoreSettings;
      setStoreSettings(loaded);
      setFeeInputs({
        minOrder: String(fromMinor(loaded.min_order_minor ?? 0, loaded.currency)),
        delivery: String(fromMinor(loaded.delivery_fee_minor ?? 0, loaded.currency)),
        service: String(fromMinor(loaded.service_fee_minor ?? 0, loaded.currency)),
      });
    }
    if (staffResult.status === 'fulfilled') setStaff(staffResult.value.data);
    if (customerResult.status === 'fulfilled') setCustomers(customerResult.value.data);
  }, [from, to, role, t]);

  useEffect(() => { if (isAdminAuthenticated) void fetchData(); }, [isAdminAuthenticated, fetchData]);
  const fetchAuditLogs = useCallback(async () => {
    if (role !== 'MANAGER') return;
    setAuditLoading(true);
    try {
      const response = await api.get('/api/v1/admin/audit-logs', { params: { limit: 25, offset: auditOffset } });
      setAuditLogs(response.data.items);
      setAuditTotal(response.data.total);
    } catch { setError(t('error.network')); }
    finally { setAuditLoading(false); }
  }, [auditOffset, role, t]);
  useEffect(() => { if (isAdminAuthenticated && section === 'audit') void fetchAuditLogs(); }, [isAdminAuthenticated, section, fetchAuditLogs]);
  useEffect(() => { if (!isAdminAuthenticated) navigate('/admin/login', { replace: true }); }, [isAdminAuthenticated, navigate]);

  const signOut = async () => { try { await api.post('/api/v1/auth/admin/logout'); } finally { logoutAdmin(); navigate('/admin/login'); } };
  const reviewPayment = async (order: Order, decision: 'APPROVED' | 'REJECTED') => {
    const reason = decision === 'REJECTED' ? window.prompt(t('payment.rejectReasonInput'))?.trim() : undefined;
    if (decision === 'REJECTED' && !reason) return;
    setBusyId(order.id);
    try { await api.post(`/api/v1/admin/orders/${order.id}/payment-review`, { decision, reason }); await fetchData(); }
    catch { setError(t('error.generic')); } finally { setBusyId(null); }
  };
  const uploadPaymentProof = async (order: Order, event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      setError(t('admin.uploadTooLarge'));
      return;
    }
    const form = new FormData();
    form.append('file', file);
    setProofUploadingId(order.id);
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/payment-proof`, form);
      await fetchData();
    } catch {
      setError(t('admin.uploadFailed'));
    } finally {
      setProofUploadingId(null);
    }
  };
  const changeStatus = async (order: Order, status: string) => {
    const reason = status === 'CANCELLED' ? window.prompt(t('order.cancelReason'))?.trim() : undefined;
    if (status === 'CANCELLED' && !reason) return;
    setBusyId(order.id);
    try { await api.post(`/api/v1/admin/orders/${order.id}/status`, { status, reason }); await fetchData(); }
    catch { setError(t('error.generic')); } finally { setBusyId(null); }
  };
  const reportShortage = async (order: Order, item: { id?: number; name: Record<string, string> | string }) => {
    if (!item.id) return;
    const note = window.prompt(t('admin.shortageNotePrompt'), label(typeof item.name === 'string' ? undefined : item.name, i18n.language) || (typeof item.name === 'string' ? item.name : ''));
    if (note === null) return;
    setBusyId(order.id);
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/shortage`, { order_item_id: item.id, note });
      await fetchData();
    } catch (requestError: unknown) {
      setError(apiDetail(requestError) || t('error.generic'));
    } finally { setBusyId(null); }
  };
  const saveSettings = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await api.patch('/api/v1/admin/settings/', {
        ...storeSettings,
        // 金额输入是主单位，提交前换成最小单位
        min_order_minor: toMinor(Number(feeInputs.minOrder || 0), storeSettings.currency),
        delivery_fee_minor: toMinor(Number(feeInputs.delivery || 0), storeSettings.currency),
        service_fee_minor: toMinor(Number(feeInputs.service || 0), storeSettings.currency),
        unpaid_order_timeout_minutes: storeSettings.unpaid_order_timeout_minutes,
        unpaid_order_reminder_minutes: storeSettings.unpaid_order_reminder_minutes,
        shortage_customer_response_timeout_minutes: storeSettings.shortage_customer_response_timeout_minutes,
        shortage_refund_review_minor: storeSettings.shortage_refund_review_minor,
      });
      await fetchData();
      setError('');
    } catch (requestError: unknown) {
      setError(apiDetail(requestError) || t('error.generic'));
    }
  };
  const refundOrder = async (order: Order) => {
    // 出餐后的钱包订单不能用「取消」退款，必须走这条仅 MANAGER 的退款路径
    const reason = window.prompt(t('admin.refundReasonPrompt'));
    if (!reason || !reason.trim()) return;
    try {
      await api.post(`/api/v1/admin/orders/${order.id}/refund`, { reason: reason.trim() });
      await fetchData();
    } catch (requestError: unknown) {
      setError(apiDetail(requestError) || t('error.generic'));
    }
  };
  const approveShortageRefund = async (refund: OrderRefund) => {
    setError('');
    try {
      await api.post(`/api/v1/admin/order-refunds/${refund.id}/approve`, {});
      await fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common.error'));
    }
  };

  const addStaff = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await api.post('/api/v1/admin/staff', { login_name: newStaff.login, password: newStaff.password, role: newStaff.role, telegram_user_id: newStaff.telegramId || null });
      setNewStaff({ login: '', password: '', telegramId: '', role: 'STAFF' }); await fetchData();
    } catch { setError(t('error.generic')); }
  };
  const toggleStaff = async (member: Staff) => {
    try { await api.patch(`/api/v1/admin/staff/${member.id}`, { active: !member.active }); await fetchData(); }
    catch { setError(t('error.generic')); }
  };
  const saveStaffTelegramId = async (member: Staff) => {
    setBusyStaffId(member.id);
    try {
      const telegram_user_id = telegramIdEdits[member.id]?.trim() || null;
      await api.patch(`/api/v1/admin/staff/${member.id}`, { telegram_user_id });
      setTelegramIdEdits((current) => { const next = { ...current }; delete next[member.id]; return next; });
      await fetchData();
    } catch { setError(t('error.generic')); }
    finally { setBusyStaffId(null); }
  };
  const changeStaffRole = async (member: Staff, roleValue: string) => {
    try { await api.patch(`/api/v1/admin/staff/${member.id}`, { role: roleValue }); await fetchData(); }
    catch { setError(t('error.generic')); }
  };

  const isHq = role === 'MANAGER' && adminStoreResolved && storeId === null;
  return <AdminLayout role={role || 'STAFF'} activeSection={section} onNavigate={selectSection} onSignOut={signOut} isHq={isHq}>
    <div className="admin-content">{error && <p className="error" role="alert">{error}</p>}
    {section === 'overview' && <section className="overview-grid"><div className="metric"><span>{t('admin.orderVolume')}</span><strong>{stats?.order_volume ?? '—'}</strong></div><button className="metric metric-action warning" onClick={() => { setOrderFilter('ALL'); selectSection('orders'); }}><span>{t('admin.needsReview')}</span><strong>{orders.filter((order) => order.payment_status === 'PROOF_SUBMITTED').length}</strong></button><div className="metric"><span>{t('admin.confirmedPaid')}</span><strong>{orders.filter((order) => order.payment_status === 'PAID_CONFIRMED').length}</strong></div><div className="metric"><span>{t('admin.cancelledCount')}</span><strong>{stats?.cancelled_count ?? '—'}</strong></div><section className="panel overview-recent"><div className="panel-heading"><div><span className="eyebrow">{t('admin.orders')}</span><h2>{t('admin.recentOrders')}</h2></div><button onClick={() => selectSection('orders')}>{t('admin.viewOrders')}</button></div>{orders.slice(0, 6).map((order) => <div className="overview-order" key={order.id}><strong>{order.public_code}</strong><span>{t('checkout.roomNumber')} {order.room_number}</span><span className={`admin-order-status status-${order.order_status.toLowerCase()}`}>{t(`order.status.${order.order_status.toLowerCase()}`)}</span><b>{amount(order.total_minor, order.currency, i18n.language)}</b></div>)}</section></section>}
    {section === 'orders' && <>
      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">{t('admin.orders')}</span><h2>{t('nav.orders')}</h2></div><label>{t('admin.allStatuses')}<select value={orderFilter} onChange={(event) => setOrderFilter(event.target.value)}><option value="ALL">{t('admin.allStatuses')}</option>{['NEW', 'ACCEPTED', 'PREPARING', 'READY', 'DELIVERED', 'COMPLETED', 'CANCELLED'].map((value) => <option key={value} value={value}>{t(`order.status.${value.toLowerCase()}`)}</option>)}</select></label><button onClick={() => void fetchData()}>{t('common.retry')}</button></div>
        {orders.length === 0 ? <p className="state">{t('admin.noOrders')}</p> : <div className="order-list">{orders.filter((order) => orderFilter === 'ALL' || order.order_status === orderFilter).map((order) => <article className="order-row" key={order.id}>
          <strong>{order.public_code}</strong><span>{t('checkout.roomNumber')} {order.room_number}</span><span className={`admin-order-status status-${order.order_status.toLowerCase()}`}>{t(`order.status.${order.order_status.toLowerCase()}`)}</span><span className={`admin-payment-status payment-status-${order.payment_status.toLowerCase()}`}>{order.payment_status === 'PAID_CONFIRMED' ? t('payment.confirmed') : order.payment_status === 'REFUNDED' ? t('order.status.refunded') : order.payment_status === 'PARTIALLY_REFUNDED' ? t('payment.partiallyRefunded') : order.payment_status === 'PROOF_SUBMITTED' ? t('payment.pending') : order.payment_status === 'REJECTED' ? t('payment.rejected') : t('order.status.unpaid')}</span><b>{t('common.total')}: {amount(order.total_minor, order.currency, i18n.language)}</b>
          {((order.subtotal_minor ?? 0) > 0 || order.coupon_name_snapshot || (order.coupon_discount_minor ?? 0) > 0) && <small className="order-amount-breakdown">{order.subtotal_minor !== undefined && <>{t('common.subtotal')}: {amount(order.subtotal_minor, order.currency, i18n.language)}</>}{order.coupon_name_snapshot && <> · {t('bot.coupon')}: {order.coupon_name_snapshot}</>}{(order.coupon_discount_minor ?? 0) > 0 && <> · {t('coupon.couponDiscount')}: -{amount(order.coupon_discount_minor ?? 0, order.currency, i18n.language)}</>}</small>}
          {(order.wallet_paid_minor ?? 0) > 0 && <small className="order-payment-breakdown">{t('payment.walletApplied')}: {amount(order.wallet_paid_minor ?? 0, order.currency, i18n.language)}{(order.external_due_minor ?? 0) > 0 && <> · {t('payment.abaDue')}: {amount(order.external_due_minor ?? 0, order.currency, i18n.language)}</>}</small>}
          {order.items?.length ? <div className="order-items">{order.items.map((item, index) => <span key={`${order.id}-${index}`}><span>{typeof item.name === 'string' ? item.name : label(item.name, i18n.language)} × {item.quantity}{item.options?.sweetness !== undefined ? ` · ${t('order.sweetnessValue', { value: item.options.sweetness })}` : ''} · {amount(item.line_total_minor, order.currency, i18n.language)}</span>{item.id && order.order_status === 'NEW' && ['UNPAID', 'REJECTED'].includes(order.payment_status) && <button type="button" onClick={() => void reportShortage(order, item)}>{t('admin.reportShortage')}</button>}</span>)}</div> : null}
          {order.payment_status === 'PROOF_SUBMITTED' && <details><summary>{t('admin.openPaymentProof')}</summary>{imageErrors.has(order.id) ? <p className="error">{t('admin.paymentImageUnavailable')} <button type="button" onClick={() => setImageErrors((current) => { const next = new Set(current); next.delete(order.id); return next; })}>{t('common.retry')}</button></p> : <img className="payment-proof" src={`/api/v1/admin/orders/${order.id}/payment-proof`} alt={t('admin.paymentImage')} onError={() => setImageErrors((current) => new Set(current).add(order.id))} />}<p>{t('admin.checkActualPayment')}</p>{order.order_status === 'ACCEPTED' && <div className="actions"><button disabled={busyId === order.id} onClick={() => void reviewPayment(order, 'APPROVED')}>{t('order.confirmPayment')}</button><button disabled={busyId === order.id} onClick={() => void reviewPayment(order, 'REJECTED')}>{t('order.reject')}</button></div>}</details>}
          <div className="actions">{role === 'MANAGER' && order.refunds?.filter((refund) => refund.method === 'WALLET' && refund.status === 'PENDING' && refund.requires_approval).map((refund) => <button key={`approve-refund-${refund.id}`} onClick={() => void approveShortageRefund(refund)}>{t('admin.approveRefund')} · {amount(refund.amount_minor, order.currency, i18n.language)}</button>)}{role === 'MANAGER' && ['UNPAID', 'REJECTED'].includes(order.payment_status) && <label className="payment-proof-upload"><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void uploadPaymentProof(order, event)} /><span>{proofUploadingId === order.id ? t('admin.uploadingImage') : t('admin.uploadPaymentProof')}</span></label>}{order.order_status === 'NEW' && <button onClick={() => void changeStatus(order, 'ACCEPTED')}>{t('order.accept')}</button>}{order.order_status === 'PREPARING' && order.payment_status === 'PAID_CONFIRMED' && <button onClick={() => void changeStatus(order, 'READY')}>{t('order.markReady')}</button>}{order.order_status === 'READY' && <button onClick={() => void changeStatus(order, 'DELIVERED')}>{t('order.markDelivered')}</button>}{order.order_status === 'DELIVERED' && <button onClick={() => void changeStatus(order, 'COMPLETED')}>{t('order.complete')}</button>}{!['CANCELLED', 'COMPLETED'].includes(order.order_status) && order.payment_status !== 'PAID_CONFIRMED' && <button onClick={() => void changeStatus(order, 'CANCELLED')}>{t('order.cancel')}</button>}{['WALLET', 'MIXED'].includes(order.payment_method || '') && ['PAID_CONFIRMED', 'PARTIALLY_REFUNDED'].includes(order.payment_status) && role === 'MANAGER' && <button onClick={() => void refundOrder(order)}>{t('admin.refundOrder')}</button>}</div>
        </article>)}</div>}
      </section>
    </>}
    {section === 'recharges' && <AdminRecharges role={role} />}

    {section === 'customers' && role === 'MANAGER' && <section className="panel"><div className="panel-heading"><div><span className="eyebrow">{t('admin.customerList')}</span><h2>{t('nav.customers')}</h2></div><label>{t('common.search')}<input type="search" value={customerSearch} onChange={(event) => setCustomerSearch(event.target.value)} placeholder={t('admin.searchCustomers')} /></label></div>
      {customers.length === 0 ? <p className="state">{t('admin.noCustomers')}</p> : <div className="customer-table-wrap"><table className="customer-table"><thead><tr><th>{t('admin.telegramName')}</th><th>{t('admin.telegramId')}</th><th>{t('admin.telegramChat')}</th><th>{t('admin.orderCount')}</th><th>{t('admin.completedOrderCount')}</th></tr></thead><tbody>{customers.filter((customer) => `${customer.display_name || ''} ${customer.username || ''} ${customer.telegram_user_id}`.toLowerCase().includes(customerSearch.trim().toLowerCase())).map((customer) => <tr key={customer.id}><td>{customer.display_name || customer.username || '—'}{customer.username && <small>@{customer.username}</small>}</td><td>{customer.telegram_user_id}</td><td><a href={customer.telegram_chat_url}>{t('admin.telegramChat')}</a></td><td>{customer.order_count}</td><td>{customer.completed_order_count}</td></tr>)}</tbody></table></div>}
    </section>}
    {section === 'audit' && role === 'MANAGER' && <section className="panel audit-panel" aria-busy={auditLoading}><div className="panel-heading"><div><span className="eyebrow">{t('admin.auditDescription')}</span><h2>{t('nav.auditLogs')}</h2></div><button onClick={() => void fetchAuditLogs()} disabled={auditLoading}>{auditLoading ? t('common.loading') : t('common.retry')}</button></div>
      {auditLogs.length === 0 && !auditLoading ? <p className="state">{t('admin.noAuditLogs')}</p> : <div className="customer-table-wrap"><table className="customer-table audit-table"><thead><tr><th>{t('admin.logTime')}</th><th>{t('admin.logAction')}</th><th>{t('admin.logOrder')}</th><th>{t('admin.logOperator')}</th><th>{t('admin.logChannel')}</th><th>{t('admin.logState')}</th></tr></thead><tbody>{auditLogs.map((entry) => {
        const groupAction = entry.source === 'telegram_group' && entry.details.action ? `audit.groupAction.${entry.details.action}` : null;
        const actionKey = groupAction || `audit.action.${entry.action}`;
        const actionLabel = t(actionKey, { defaultValue: entry.action });
        const orderLabel = entry.details.public_code || `${t(`audit.entity.${entry.entity_type}`, { defaultValue: entry.entity_type })} #${entry.entity_id}`;
        let timeLabel = entry.created_at;
        try { timeLabel = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short', timeZone: storeSettings.timezone }).format(new Date(entry.created_at)); } catch { /* retain server timestamp */ }
        return <tr key={entry.id}><td>{timeLabel}</td><td>{actionLabel}{entry.details.reason && <small>{entry.details.reason}</small>}</td><td>{orderLabel}</td><td>{entry.operator_name}<small>{entry.operator_telegram_id ? `${t('admin.telegramId')}: ${entry.operator_telegram_id}` : ''}</small></td><td>{t(`audit.source.${entry.source}`, { defaultValue: entry.source })}{entry.source === 'telegram_group' && entry.details.group_id && <small>{entry.details.group_id}</small>}</td><td><span>{entry.details.from_state || '—'}</span>{entry.details.to_state && <small>→ {entry.details.to_state}</small>}</td></tr>;
      })}</tbody></table></div>}
      {auditTotal > 0 && <div className="audit-pagination"><span>{t('admin.logRange', { from: auditOffset + 1, to: Math.min(auditOffset + auditLogs.length, auditTotal), total: auditTotal })}</span><div><button disabled={auditOffset === 0 || auditLoading} onClick={() => setAuditOffset((offset) => Math.max(0, offset - 25))}>{t('common.back')}</button><button disabled={auditOffset + 25 >= auditTotal || auditLoading} onClick={() => setAuditOffset((offset) => offset + 25)}>{t('admin.nextPage')}</button></div></div>}
    </section>}
    {section === 'coupons' && role === 'MANAGER' && <AdminCoupons />}

    {section === 'settings' && role === 'MANAGER' && <section className="panel"><div className="panel-heading"><h2>{t('nav.settings')}</h2></div><form className="product-form" onSubmit={saveSettings}>
      <label>{t('common.currency')}<input maxLength={3} value={storeSettings.currency} onChange={(event) => setStoreSettings({ ...storeSettings, currency: event.target.value.toUpperCase() })} required /></label>
      <label>{t('admin.timezone')}<input value={storeSettings.timezone} onChange={(event) => setStoreSettings({ ...storeSettings, timezone: event.target.value })} required /></label>
      <label>{t('admin.paymentLink')}<input type="url" value={storeSettings.payment_link || ''} onChange={(event) => setStoreSettings({ ...storeSettings, payment_link: event.target.value || null })} /></label>
      <ImageUpload label={t('admin.qrImageUrl')} value={storeSettings.aba_qr_asset_key} onChange={(aba_qr_asset_key) => setStoreSettings((current) => ({ ...current, aba_qr_asset_key }))} />
      <label>{t('admin.groupId')}<input inputMode="numeric" value={storeSettings.telegram_staff_group_id || ''} onChange={(event) => setStoreSettings({ ...storeSettings, telegram_staff_group_id: event.target.value || null })} /><small>{t('admin.groupIdHelp')}</small></label>
      <label>{t('admin.groupLanguage')}<select value={storeSettings.staff_group_language} onChange={(event) => setStoreSettings({ ...storeSettings, staff_group_language: event.target.value })}><option value="en">English</option><option value="zh-CN">中文</option><option value="km">ខ្មែរ</option></select></label>
      <label className="product-sweetness-toggle"><input type="checkbox" checked={storeSettings.is_accepting_orders} onChange={(event) => setStoreSettings({ ...storeSettings, is_accepting_orders: event.target.checked })} /><span><strong>{t('admin.acceptingOrders')}</strong><small>{t('admin.acceptingOrdersHint')}</small></span></label>

      <div className="settings-block">
        <strong>{t('admin.businessHours')}</strong>
        <small>{t('admin.businessHoursHint')}</small>
        <div className="rules-rows">
          {(storeSettings.business_hours ?? []).map((window, index) => <div className="rules-row" key={index}>
            <input type="time" value={window.start} onChange={(event) => setStoreSettings({ ...storeSettings, business_hours: (storeSettings.business_hours ?? []).map((w, i) => i === index ? { ...w, start: event.target.value } : w) })} />
            <span>–</span>
            <input type="time" value={window.end} onChange={(event) => setStoreSettings({ ...storeSettings, business_hours: (storeSettings.business_hours ?? []).map((w, i) => i === index ? { ...w, end: event.target.value } : w) })} />
            <button type="button" onClick={() => setStoreSettings({ ...storeSettings, business_hours: (storeSettings.business_hours ?? []).filter((_, i) => i !== index) })}>{t('common.delete')}</button>
          </div>)}
        </div>
        <div className="actions"><button type="button" onClick={() => setStoreSettings({ ...storeSettings, business_hours: [...(storeSettings.business_hours ?? []), { start: '08:00', end: '22:00' }] })}>{t('admin.addWindow')}</button></div>
      </div>

      <label>{t('admin.minOrder')}<input type="number" min="0" step="0.01" value={feeInputs.minOrder} onChange={(event) => setFeeInputs({ ...feeInputs, minOrder: event.target.value })} /><small>{t('admin.minOrderHint')}</small></label>
      <label>{t('admin.deliveryFee')}<input type="number" min="0" step="0.01" value={feeInputs.delivery} onChange={(event) => setFeeInputs({ ...feeInputs, delivery: event.target.value })} /></label>
      <label>{t('admin.serviceFee')}<input type="number" min="0" step="0.01" value={feeInputs.service} onChange={(event) => setFeeInputs({ ...feeInputs, service: event.target.value })} /></label>
      <label>{t('admin.unpaidOrderTimeout')}<input type="number" min="0" max="1440" step="1" value={storeSettings.unpaid_order_timeout_minutes} onChange={(event) => setStoreSettings({ ...storeSettings, unpaid_order_timeout_minutes: Number(event.target.value) || 0 })} /><small>{t('admin.unpaidOrderTimeoutHint')}</small></label>
      <label>{t('admin.unpaidOrderReminder')}<input type="number" min="0" max="1440" step="1" value={storeSettings.unpaid_order_reminder_minutes} onChange={(event) => setStoreSettings({ ...storeSettings, unpaid_order_reminder_minutes: Number(event.target.value) || 0 })} /><small>{t('admin.unpaidOrderReminderHint')}</small></label>
      <label>{t('admin.shortageResponseTimeout')}<input type="number" min="0" max="10080" step="1" value={storeSettings.shortage_customer_response_timeout_minutes} onChange={(event) => setStoreSettings({ ...storeSettings, shortage_customer_response_timeout_minutes: Number(event.target.value) || 0 })} /><small>{t('admin.shortageResponseTimeoutHint')}</small></label>
      <label>{t('admin.shortageRefundReview')}<input type="number" min="0" step="0.01" value={(storeSettings.shortage_refund_review_minor / 100).toFixed(2)} onChange={(event) => setStoreSettings({ ...storeSettings, shortage_refund_review_minor: Math.round((Number(event.target.value) || 0) * 100) })} /><small>{t('admin.shortageRefundReviewHint')}</small></label>
      <button className="primary" type="submit">{t('admin.updateSettings')}</button>
    </form></section>}
    {section === 'staff' && role === 'MANAGER' && <section className="panel"><div className="panel-heading"><h2>{t('admin.staffList')}</h2></div><form className="product-form" onSubmit={addStaff}>
      <label>{t('admin.loginName')}<input value={newStaff.login} onChange={(event) => setNewStaff({ ...newStaff, login: event.target.value })} required minLength={3} /></label>
      <label>{t('admin.password')}<input type="password" value={newStaff.password} onChange={(event) => setNewStaff({ ...newStaff, password: event.target.value })} required minLength={12} /></label>
      <label>{t('admin.telegramUserId')}<input inputMode="numeric" value={newStaff.telegramId} onChange={(event) => setNewStaff({ ...newStaff, telegramId: event.target.value })} /></label>
      <label>{t('admin.staffRole')}<select value={newStaff.role} onChange={(event) => setNewStaff({ ...newStaff, role: event.target.value })}><option value="STAFF">{t('admin.waiter')}</option><option value="MANAGER">{t('admin.manager')}</option></select></label>
      <button className="primary" type="submit">{t('admin.addStaff')}</button>
    </form><div className="order-list">{staff.map((member) => <article className="order-row" key={member.id}><strong>{member.login_name}</strong><input inputMode="numeric" aria-label={t('admin.telegramUserId')} placeholder={t('admin.telegramUserId')} value={telegramIdEdits[member.id] ?? member.telegram_user_id ?? ''} onChange={(event) => setTelegramIdEdits((current) => ({ ...current, [member.id]: event.target.value }))} /><button type="button" disabled={busyStaffId === member.id} onClick={() => void saveStaffTelegramId(member)}>{t('admin.linkTelegramId')}</button><select value={member.role} onChange={(event) => void changeStaffRole(member, event.target.value)}><option value="STAFF">{t('admin.waiter')}</option><option value="MANAGER">{t('admin.manager')}</option></select><span>{member.active ? t('admin.active') : t('admin.deactivate')}</span><button onClick={() => void toggleStaff(member)}>{member.active ? t('admin.deactivate') : t('admin.activate')}</button></article>)}</div></section>}
    </div>
  </AdminLayout>;
}
