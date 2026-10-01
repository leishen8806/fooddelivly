import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { formatAmount, formatDateTime, toMinorUnits } from '../format';
import type { LedgerEntry, RechargeOrder, WalletSummary } from '../types';

/**
 * 管理端「充值审核」。
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
  const [imageErrors, setImageErrors] = useState<Set<number>>(() => new Set());
  const [receivedDraft, setReceivedDraft] = useState<Record<number, string>>({});
  const [rejectDraft, setRejectDraft] = useState<Record<number, string>>({});
  const [walletCustomerId, setWalletCustomerId] = useState<number | null>(null);
  const [wallet, setWallet] = useState<(WalletSummary & { ledger: LedgerEntry[]; display_name?: string | null }) | null>(null);
  const [adjust, setAdjust] = useState({ bucket: 'principal', amount: '', reason: '' });

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

  const act = async (order: RechargeOrder, path: string, body: Record<string, unknown>, message: string) => {
    setBusyId(order.id);
    setNotice('');
    try {
      await api.post(`/api/v1/admin/recharges/${order.id}/${path}`, body);
      setNotice(message);
      setReceivedDraft((current) => ({ ...current, [order.id]: '' }));
      setRejectDraft((current) => ({ ...current, [order.id]: '' }));
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

  return <section className="panel">
    <div className="panel-heading">
      <div><span className="eyebrow">{t('admin.recharges')}</span><h2>{t('nav.wallet')}</h2></div>
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

    <div className="order-list">
      {recharges.map((order) => <article className="order-row wallet-admin-row" key={order.id}>
        <strong>{order.order_no}</strong>
        <span>{order.display_name || order.telegram_user_id || `#${order.customer_id}`}</span>
        <span>{formatAmount(order.amount_minor, order.currency, i18n.language)}
          {order.bonus_amount_minor > 0 && <small> +{formatAmount(order.bonus_amount_minor, order.currency, i18n.language)}</small>}
        </span>
        <span className={`wallet-status wallet-status-${order.status}`}>{statusLabel(order.status)}</span>
        <time dateTime={order.created_at}>{formatDateTime(order.created_at, i18n.language)}</time>
        <b>{formatAmount(order.received_amount_minor ?? order.amount_minor, order.currency, i18n.language)}</b>

        <details>
          <summary>{t('admin.openRechargeProof')}</summary>
          {order.proof_count === 0
            ? <p className="state">{t('admin.noProofYet')}</p>
            : imageErrors.has(order.id)
              ? <p className="error">{t('admin.paymentImageUnavailable')}
                  <button type="button" onClick={() => setImageErrors((current) => { const next = new Set(current); next.delete(order.id); return next; })}>{t('common.retry')}</button></p>
              : <img className="payment-proof" src={`/api/v1/admin/recharges/${order.id}/proof`} alt={t('admin.rechargeProof')}
                  onError={() => setImageErrors((current) => new Set(current).add(order.id))} />}

          {order.pending_received_amount_minor
            ? <p className="wallet-hint">{t('admin.receivedAmount')}: {formatAmount(order.pending_received_amount_minor, order.currency, i18n.language)}</p>
            : null}

          {(order.status === 'under_review' || order.status === 'awaiting_proof') && <>
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
                }, t('admin.receivedAmount'))}>{t('admin.receivedAmount')}</button>
              <button type="button" disabled={busyId === order.id}
                onClick={() => void act(order, 'approve', {}, t('admin.confirmRecharge'))}>{t('admin.confirmRecharge')}</button>
            </div>
            <label>{t('admin.rejectPrompt')}
              <input value={rejectDraft[order.id] ?? ''}
                onChange={(event) => setRejectDraft((current) => ({ ...current, [order.id]: event.target.value }))} />
            </label>
            <div className="actions">
              <button type="button" disabled={busyId === order.id || !(rejectDraft[order.id] || '').trim()}
                onClick={() => void act(order, 'reject', { reason: rejectDraft[order.id].trim() }, t('admin.rejectRecharge'))}>{t('admin.rejectRecharge')}</button>
            </div>
          </>}

          <div className="actions">
            <button type="button" onClick={() => void openWallet(order.customer_id!)}>{t('admin.walletBalance')}</button>
          </div>
        </details>
      </article>)}
    </div>

    {walletCustomerId !== null && <article className="wallet-panel">
      <h3>{t('admin.walletBalance')} · #{walletCustomerId}</h3>
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
