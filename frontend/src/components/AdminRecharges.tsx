import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { formatAmount, formatDateTime, toMinorUnits } from '../format';
import type { LedgerEntry, RechargeOrder, WalletSummary } from '../types';

type AdminWallet = WalletSummary & { ledger: LedgerEntry[]; display_name?: string | null };

/**
 * 管理端「充值管理」。
 *
 * 表格列：用户ID / 用户名称 / Telegram / 余额 / 充值金额 / 状态 / 提交时间 / 操作。
 *
 * 「余额」是客户**当前**的钱包余额（后端 LEFT JOIN wallets 取，没有钱包按 0），
 * 审核时用来判断这是不是老客户、账户里已经有多少钱。
 *
 * 与订单收款审核的区别：
 *   * 充值到账会**直接产生可消费余额**，所以「不能审核自己的单」由数据库函数兜底；
 *   * 实收金额可以先暂存（POST /received），点「确认到账」时由数据库按
 *     「显式传入 > 暂存值 > 订单金额」取值，并按实收重算赠送；
 *   * 调账只允许 MANAGER（后端与数据库都会再校验一次）。
 */
export default function AdminRecharges({ role }: { role: string | null }) {
  const { t, i18n } = useTranslation();
  const [recharges, setRecharges] = useState<RechargeOrder[]>([]);
  const [pendingOnly, setPendingOnly] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busyId, setBusyId] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [imageErrors, setImageErrors] = useState<Set<number>>(() => new Set());
  const [receivedDraft, setReceivedDraft] = useState<Record<number, string>>({});
  const [rejectDraft, setRejectDraft] = useState<Record<number, string>>({});
  const [walletCustomerId, setWalletCustomerId] = useState<number | null>(null);
  const [wallet, setWallet] = useState<AdminWallet | null>(null);
  const [adjust, setAdjust] = useState({ bucket: 'principal', amount: '', reason: '' });
  const rejectRef = useRef<HTMLInputElement>(null);
  const focusReject = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await api.get('/api/v1/admin/recharges',
        { params: pendingOnly ? { status: 'under_review' } : {} });
      setRecharges(response.data);
    } catch {
      setError(t('error.network'));
    } finally {
      setLoading(false);
    }
  }, [pendingOnly, t]);

  useEffect(() => { void load(); }, [load]);

  // 点「驳回」先展开详情行，展开后把焦点落到原因输入框
  useEffect(() => {
    if (focusReject.current && expandedId !== null) {
      focusReject.current = false;
      rejectRef.current?.focus();
    }
  });

  const openWallet = async (customerId: number) => {
    setWalletCustomerId(customerId);
    setWallet(null);
    try {
      const response = await api.get(`/api/v1/admin/wallets/${customerId}`);
      setWallet(response.data);
    } catch {
      setNotice(t('error.network'));
    }
  };

  const act = async (order: RechargeOrder, path: string, body: Record<string, unknown>, message: string, closeDetail = false) => {
    setBusyId(order.id);
    setNotice('');
    try {
      const response = await api.post(`/api/v1/admin/recharges/${order.id}/${path}`, body);
      if (path === 'approve') {
        // 大额双人复核：只凑到一位时订单不会入账，必须说清楚还差一人，
        // 否则员工以为没生效又点一次（重复点也不会凑数，因为同一个人只记一票）
        const done = response.data?.approval_count ?? 1;
        const required = response.data?.required_approvals ?? 1;
        setNotice(done < required
          ? `${t('admin.awaitingSecond')}（${t('admin.approvalProgress', { done, required })}）`
          : message);
      } else {
        setNotice(message);
      }
      setReceivedDraft((current) => ({ ...current, [order.id]: '' }));
      setRejectDraft((current) => ({ ...current, [order.id]: '' }));
      if (closeDetail) setExpandedId(null);
      await load();
      if (walletCustomerId === order.customer_id) await openWallet(order.customer_id!);
    } catch (requestError: any) {
      setNotice(requestError?.response?.data?.detail || t('error.network'));
    } finally {
      setBusyId(null);
    }
  };

  const submitAdjust = async (event: React.FormEvent) => {
    event.preventDefault();
    if (walletCustomerId === null) return;
    const value = Number(adjust.amount);
    if (!Number.isFinite(value) || value === 0 || !adjust.reason.trim()) {
      setNotice(t('wallet.errorReasonRequired'));
      return;
    }
    try {
      await api.post(`/api/v1/admin/wallets/${walletCustomerId}/adjust`, {
        bucket: adjust.bucket,
        delta_minor: toMinorUnits(value, wallet?.currency || 'USD'),
        reason: adjust.reason.trim(),
      });
      setNotice(t('admin.adjustDone'));
      setAdjust({ bucket: adjust.bucket, amount: '', reason: '' });
      await openWallet(walletCustomerId);
    } catch (requestError: any) {
      setNotice(requestError?.response?.data?.detail || t('error.network'));
    }
  };

  const statusLabel = (status: string) => t(`wallet.status.${status}`, { defaultValue: status });
  const entryLabel = (entry: LedgerEntry) => t(`wallet.entry.${entry.entry_type}`, { defaultValue: entry.entry_type });
  const isOpen = (order: RechargeOrder) => order.status === 'under_review' || order.status === 'awaiting_proof';

  return <section className="panel recharge-panel">
    <div className="panel-heading">
      <div><span className="eyebrow">{t('nav.wallet')}</span><h2>{t('admin.recharges')}</h2></div>
      <label>{t('admin.rechargeStatusFilter')}
        <select value={pendingOnly ? 'PENDING' : 'ALL'} onChange={(event) => setPendingOnly(event.target.value === 'PENDING')}>
          <option value="PENDING">{t('admin.pendingOnly')}</option>
          <option value="ALL">{t('admin.allRecharges')}</option>
        </select>
      </label>
      <button type="button" onClick={() => void load()}>{t('common.retry')}</button>
    </div>

    {loading && <p className="state" role="status">{t('common.loading')}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="status" role="status">{notice}</p>}
    {!loading && recharges.length === 0 && <p className="state">{t('admin.noRecharges')}</p>}

    {recharges.length > 0 && <div className="customer-table-wrap">
      <table className="customer-table recharge-table">
        <thead><tr>
          <th>{t('admin.rechargeCustomerId')}</th>
          <th>{t('admin.rechargeCustomerName')}</th>
          <th>{t('admin.rechargeTelegram')}</th>
          <th>{t('admin.rechargeBalance')}</th>
          <th>{t('admin.rechargeAmount')}</th>
          <th>{t('admin.rechargeStatus')}</th>
          <th>{t('admin.rechargeTime')}</th>
          <th>{t('admin.rechargeActions')}</th>
        </tr></thead>
        <tbody>
          {recharges.map((order) => <Fragment key={order.id}>
            <tr>
              <td>{order.customer_id}</td>
              <td>{order.display_name || order.username || '—'}</td>
              <td>{order.username ? <a href={`https://t.me/${order.username}`} target="_blank" rel="noreferrer">@{order.username}</a> : '—'}<small>{order.telegram_user_id}</small></td>
              <td><strong>{formatAmount(order.balance_minor ?? 0, order.currency, i18n.language)}</strong><small>{t('wallet.bucket.principal')} {formatAmount(order.principal_minor ?? 0, order.currency, i18n.language)} · {t('wallet.bucket.bonus')} {formatAmount(order.bonus_minor ?? 0, order.currency, i18n.language)}</small></td>
              <td><strong>{formatAmount(order.amount_minor, order.currency, i18n.language)}</strong>{order.bonus_amount_minor > 0 && <small>+{formatAmount(order.bonus_amount_minor, order.currency, i18n.language)} {t('wallet.bucket.bonus')}</small>}<small>{order.order_no}</small></td>
              <td>
                <span className={`wallet-status wallet-status-${order.status}`}>{statusLabel(order.status)}</span>
                {order.proof_count > 0 && <small>{t('admin.rechargeProof')} × {order.proof_count}</small>}
                {(order.required_approvals ?? 1) > 1 && order.status === 'under_review' && <small className="wallet-status-awaiting">
                  {t('admin.awaitingSecond')} · {t('admin.approvalProgress', { done: order.approval_count ?? 0, required: order.required_approvals })}
                </small>}
              </td>
              <td>{formatDateTime(order.created_at, i18n.language)}{order.submitted_at && <small>{formatDateTime(order.submitted_at, i18n.language)}</small>}</td>
              <td className="recharge-actions">
                {isOpen(order) && <>
                  <button type="button" disabled={busyId === order.id}
                    onClick={() => void act(order, 'approve', {}, t('admin.confirmRecharge'), true)}>{t('admin.confirmRecharge')}</button>
                  <button type="button" disabled={busyId === order.id}
                    onClick={() => { focusReject.current = true; setExpandedId(order.id); }}>{t('admin.rejectRecharge')}</button>
                </>}
                <button type="button" onClick={() => setExpandedId(expandedId === order.id ? null : order.id)}>{t('admin.rechargeDetail')}</button>
              </td>
            </tr>
            {expandedId === order.id && <tr className="recharge-detail-row"><td colSpan={8}>
              <div className="recharge-detail">
                <div>
                  {order.proof_count === 0
                    ? <p className="state">{t('admin.noProofYet')}</p>
                    : imageErrors.has(order.id)
                      ? <p className="error">{t('admin.paymentImageUnavailable')}
                          <button type="button" onClick={() => setImageErrors((current) => { const next = new Set(current); next.delete(order.id); return next; })}>{t('common.retry')}</button></p>
                      : <img className="payment-proof" src={`/api/v1/admin/recharges/${order.id}/proof`} alt={t('admin.rechargeProof')}
                          onError={() => setImageErrors((current) => new Set(current).add(order.id))} />}
                </div>
                <div className="recharge-detail-form">
                  {order.pending_received_amount_minor
                    ? <p className="wallet-hint">{t('admin.receivedAmount')}: {formatAmount(order.pending_received_amount_minor, order.currency, i18n.language)}</p>
                    : null}
                  {order.reject_reason && <p className="error">{order.reject_reason}</p>}
                  {isOpen(order) && <>
                    <label>{t('admin.receivedAmount')}
                      <input type="number" min="0.01" step="0.01" inputMode="decimal"
                        placeholder={t('admin.receivedPlaceholder')}
                        value={receivedDraft[order.id] ?? ''}
                        onChange={(event) => setReceivedDraft((current) => ({ ...current, [order.id]: event.target.value }))} />
                    </label>
                    <div className="actions">
                      <button type="button" disabled={busyId === order.id}
                        onClick={() => void act(order, 'received', {
                          amount_minor: receivedDraft[order.id] ? toMinorUnits(Number(receivedDraft[order.id]), order.currency) : null,
                        }, t('admin.receivedAmount'))}>{t('common.save')}</button>
                    </div>
                    <label>{t('admin.rejectPrompt')}
                      <input ref={rejectRef} value={rejectDraft[order.id] ?? ''}
                        onChange={(event) => setRejectDraft((current) => ({ ...current, [order.id]: event.target.value }))} />
                    </label>
                    <div className="actions">
                      <button type="button" disabled={busyId === order.id || !(rejectDraft[order.id] || '').trim()}
                        onClick={() => void act(order, 'reject', { reason: rejectDraft[order.id].trim() }, t('admin.rejectRecharge'), true)}>{t('admin.rejectRecharge')}</button>
                    </div>
                  </>}
                  <div className="actions">
                    <button type="button" onClick={() => void openWallet(order.customer_id!)}>{t('admin.walletBalance')}</button>
                  </div>
                </div>
              </div>
            </td></tr>}
          </Fragment>)}
        </tbody>
      </table>
    </div>}

    {walletCustomerId !== null && <article className="wallet-panel">
      <h3>{t('admin.walletBalance')} · #{walletCustomerId}{wallet?.display_name ? ` · ${wallet.display_name}` : ''}</h3>
      {!wallet ? <p className="state">{t('common.loading')}</p> : <>
        <p>{t('wallet.bucket.principal')}: <strong>{formatAmount(wallet.principal_minor, wallet.currency, i18n.language)}</strong>
          {' · '}{t('wallet.bucket.bonus')}: <strong>{formatAmount(wallet.bonus_minor, wallet.currency, i18n.language)}</strong></p>
        {role === 'MANAGER' && <form className="wallet-custom" onSubmit={submitAdjust}>
          <label>{t('admin.adjust')}
            <select value={adjust.bucket} onChange={(event) => setAdjust({ ...adjust, bucket: event.target.value })}>
              <option value="principal">{t('wallet.bucket.principal')}</option>
              <option value="bonus">{t('wallet.bucket.bonus')}</option>
            </select>
          </label>
          <label>{t('wallet.topUpAmount')}
            <input type="number" step="0.01" inputMode="decimal" value={adjust.amount}
              onChange={(event) => setAdjust({ ...adjust, amount: event.target.value })}
              placeholder="+1.00 / -1.00" />
          </label>
          <label>{t('admin.adjustReason')}
            <input value={adjust.reason} onChange={(event) => setAdjust({ ...adjust, reason: event.target.value })} />
          </label>
          <button className="primary" type="submit">{t('admin.adjust')}</button>
        </form>}
        <div className="wallet-list">
          {wallet.ledger.map((entry) => <div className="wallet-row wallet-row-ledger" key={entry.id}>
            <div className="wallet-row-main">
              <strong>{entryLabel(entry)}</strong>
              <span>{t(`wallet.bucket.${entry.bucket}`)}{entry.remark ? ` · ${entry.remark}` : ''}</span>
              <time dateTime={entry.created_at}>{formatDateTime(entry.created_at, i18n.language)}</time>
            </div>
            <b className={entry.direction > 0 ? 'wallet-amount-in' : 'wallet-amount-out'}>
              {entry.direction > 0 ? '+' : '−'}{formatAmount(entry.amount_minor, wallet.currency, i18n.language)}
            </b>
          </div>)}
        </div>
      </>}
    </article>}
  </section>;
}
