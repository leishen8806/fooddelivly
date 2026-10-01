import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { formatAmount, formatDateTime, toMinorUnits } from '../format';
import type { LedgerEntry, RechargeOrder, WalletSummary } from '../types';

/**
 * 钱包区块 —— 挂在「我的」页面里（不再单独占一个底部导航 tab）。
 *
 * 两个金额要分清（对应后端 get_summary 的两个字段）：
 *   * 余额 balance   = 本金 + 赠送 + 冻结  —— 账户总额
 *   * 可用 available = 本金 + 赠送         —— 现在能花的部分
 * 冻结目前恒为 0（预留提现/风控冻结），所以两个数通常相等；一旦有冻结就会分开。
 *
 * 与后端的分工：金额一律用最小货币单位整数（USD cents）传输，这里只做展示
 * 与输入换算；档位、限额、赠送额全部由后端决定，前端不参与任何资金计算。
 * 建单带 `Idempotency-Key`，重复点击 / 网络重试不会建出第二张单。
 */
export default function CustomerWallet({ onBalanceChange }: { onBalanceChange?: () => void }) {
  const { t, i18n } = useTranslation();
  const [summary, setSummary] = useState<WalletSummary | null>(null);
  const [recharges, setRecharges] = useState<RechargeOrder[]>([]);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [custom, setCustom] = useState('');
  const [created, setCreated] = useState<RechargeOrder | null>(null);
  const idempotencyKey = useRef<string | null>(null);

  const currency = summary?.currency || 'USD';

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    const [walletResult, rechargeResult, ledgerResult] = await Promise.allSettled([
      api.get('/api/v1/wallet'),
      api.get('/api/v1/wallet/recharges'),
      api.get('/api/v1/wallet/ledger'),
    ]);
    if (walletResult.status === 'fulfilled') setSummary(walletResult.value.data);
    else setError(t('error.network'));
    if (rechargeResult.status === 'fulfilled') setRecharges(rechargeResult.value.data.recharges);
    if (ledgerResult.status === 'fulfilled') setLedger(ledgerResult.value.data.entries);
    setLoading(false);
  }, [t]);

  useEffect(() => { void load(); }, [load]);

  const createRecharge = async (amountMinor: number) => {
    if (!amountMinor || amountMinor < (summary?.min_recharge_minor ?? 1)) {
      setNotice(t('wallet.errorMinAmount'));
      return;
    }
    if (amountMinor > (summary?.max_recharge_minor ?? Number.MAX_SAFE_INTEGER)) {
      setNotice(t('wallet.errorMaxAmount'));
      return;
    }
    if (!idempotencyKey.current) idempotencyKey.current = crypto.randomUUID();
    setBusy(true);
    setNotice('');
    try {
      const response = await api.post('/api/v1/wallet/recharges',
        { amount_minor: amountMinor },
        { headers: { 'Idempotency-Key': idempotencyKey.current } });
      idempotencyKey.current = null;
      setCustom('');
      setCreated(response.data);
      await load();
      onBalanceChange?.();
    } catch (requestError: any) {
      // 业务错误（限额 / 未完成单过多 / 来源校验）后端已经给了可读文案
      setNotice(requestError?.response?.data?.detail || t('error.network'));
    } finally {
      setBusy(false);
    }
  };

  const cancelRecharge = async (order: RechargeOrder) => {
    if (!window.confirm(t('wallet.cancelConfirm'))) return;
    setBusy(true);
    setNotice('');
    try {
      await api.post(`/api/v1/wallet/recharges/${order.id}/cancel`);
      if (created?.id === order.id) setCreated(null);
      await load();
    } catch (requestError: any) {
      setNotice(requestError?.response?.data?.detail || t('error.network'));
    } finally {
      setBusy(false);
    }
  };

  const submitCustom = (event: React.FormEvent) => {
    event.preventDefault();
    const value = Number(custom);
    if (!Number.isFinite(value) || value <= 0) { setNotice(t('wallet.errorMinAmount')); return; }
    void createRecharge(toMinorUnits(value, currency));
  };

  const statusLabel = (status: string) => t(`wallet.status.${status}`, { defaultValue: status });
  const entryLabel = (entry: LedgerEntry) => t(`wallet.entry.${entry.entry_type}`, { defaultValue: entry.entry_type });

  return <section className="wallet-section">
    {/* 「我的」页里已经有页面标题了，这里只放一个区块标题，避免重复 */}
    <div className="customer-page-heading"><h2>{t('wallet.myWallet')}</h2></div>

    {loading && <p className="state" role="status">{t('common.loading')}</p>}
    {error && <p className="error" role="alert">{error} <button type="button" onClick={() => void load()}>{t('common.retry')}</button></p>}

    {summary && <>
      <article className="wallet-card">
        <div className="wallet-card-line">
          <span>{t('wallet.balance')}</span>
          <strong className="wallet-card-primary">{formatAmount(summary.balance_minor, currency, i18n.language)}</strong>
        </div>
        <div className="wallet-card-line">
          <span>{t('wallet.available')}</span>
          <strong>{formatAmount(summary.available_minor, currency, i18n.language)}</strong>
        </div>
        {summary.frozen_minor > 0 && <div className="wallet-card-line">
          <span>{t('wallet.frozen')}</span>
          <strong>{formatAmount(summary.frozen_minor, currency, i18n.language)}</strong>
        </div>}
        <div className="wallet-card-split">
          <div><span>{t('wallet.bucket.principal')}</span><strong>{formatAmount(summary.principal_minor, currency, i18n.language)}</strong></div>
          <div><span>{t('wallet.bucket.bonus')}</span><strong>{formatAmount(summary.bonus_minor, currency, i18n.language)}</strong></div>
        </div>
        {summary.bonus_minor > 0 && summary.bonus_expire_at && <p className="wallet-hint">{t('wallet.bonusWillExpire', { date: formatDateTime(summary.bonus_expire_at, i18n.language) })}</p>}
        <p className="wallet-hint">{t('wallet.bonusFirst')}</p>
      </article>

      <article className="wallet-panel">
        <h3>{t('wallet.topUpAmount')}</h3>
        <div className="wallet-presets">
          {summary.presets_minor.map((preset) => <button key={preset} type="button" disabled={busy}
            onClick={() => void createRecharge(preset)}>{formatAmount(preset, currency, i18n.language)}</button>)}
        </div>
        <form className="wallet-custom" onSubmit={submitCustom}>
          <label htmlFor="wallet-custom-amount">{t('wallet.customAmount')}</label>
          <input id="wallet-custom-amount" type="number" min="0.01" step="0.01" inputMode="decimal"
            value={custom} onChange={(event) => { idempotencyKey.current = null; setCustom(event.target.value); }}
            placeholder={formatAmount(summary.min_recharge_minor, currency, i18n.language)} />
          <button className="primary" type="submit" disabled={busy}>{busy ? t('common.loading') : t('wallet.submit')}</button>
        </form>
        {notice && <p className="status" role="status">{notice}</p>}
      </article>

      {created && <article className="wallet-panel wallet-created">
        <h3>{t('wallet.created')}</h3>
        <p className="wallet-order-no">{t('payment.order')} <strong>{created.order_no}</strong></p>
        <p>{t('payment.amountDue')}: <strong>{formatAmount(created.amount_minor, created.currency, i18n.language)}</strong></p>
        {created.bonus_amount_minor > 0 && <p>{t('wallet.bonusPreview', { bonus: formatAmount(created.bonus_amount_minor, created.currency, i18n.language) })}</p>}
        <p>{t('wallet.expiresAt', { date: formatDateTime(created.expires_at, i18n.language) })}</p>
        {created.payment_qr_url && <img className="payment-qr" src={created.payment_qr_url} alt={t('payment.aba')} />}
        {created.payment_link && <a className="primary payment-link" href={created.payment_link} target="_blank" rel="noreferrer">{t('payment.openLink')}</a>}
        <p>{t('wallet.remarkHint', { order: created.order_no })}</p>
        {created.bot_deeplink
          ? <a className="primary payment-link" href={created.bot_deeplink}>{t('wallet.uploadProof')}</a>
          : <p className="error">{t('payment.botNotConfigured')}</p>}
        <div className="actions">
          <button type="button" disabled={busy} onClick={() => void cancelRecharge(created)}>{t('wallet.cancelOrder')}</button>
          <button type="button" onClick={() => { setCreated(null); void load(); }}>{t('wallet.refresh')}</button>
        </div>
      </article>}

      <article className="wallet-panel">
        <h3>{t('wallet.myOrders')}</h3>
        {recharges.length === 0 ? <p className="state">{t('wallet.none')}</p> : <div className="wallet-list">
          {recharges.map((order) => <div className="wallet-row" key={order.id}>
            <div className="wallet-row-main">
              <strong>{order.order_no}</strong>
              <span>{formatAmount(order.amount_minor, order.currency, i18n.language)}
                {order.bonus_amount_minor > 0 && <small> +{formatAmount(order.bonus_amount_minor, order.currency, i18n.language)} {t('wallet.bucket.bonus')}</small>}
              </span>
              <span className={`wallet-status wallet-status-${order.status}`}>{statusLabel(order.status)}</span>
              <time dateTime={order.created_at}>{formatDateTime(order.created_at, i18n.language)}</time>
            </div>
            {order.reject_reason && <p className="error">{order.reject_reason}</p>}
            {(order.status === 'awaiting_proof' || order.status === 'under_review') && <div className="actions">
              {order.bot_deeplink && <a className="payment-link" href={order.bot_deeplink}>{t('wallet.uploadProof')}</a>}
              {order.status === 'awaiting_proof' && <button type="button" disabled={busy} onClick={() => void cancelRecharge(order)}>{t('wallet.cancelOrder')}</button>}
            </div>}
          </div>)}
        </div>}
      </article>

      <article className="wallet-panel">
        <h3>{t('wallet.ledger')}</h3>
        {ledger.length === 0 ? <p className="state">{t('wallet.noLedger')}</p> : <div className="wallet-list">
          {ledger.map((entry) => <div className="wallet-row wallet-row-ledger" key={entry.id}>
            <div className="wallet-row-main">
              <strong>{entryLabel(entry)}</strong>
              <span>{t(`wallet.bucket.${entry.bucket}`)}{entry.remark ? ` · ${entry.remark}` : ''}</span>
              <time dateTime={entry.created_at}>{formatDateTime(entry.created_at, i18n.language)}</time>
            </div>
            <b className={entry.direction > 0 ? 'wallet-amount-in' : 'wallet-amount-out'}>
              {entry.direction > 0 ? '+' : '−'}{formatAmount(entry.amount_minor, currency, i18n.language)}
              <small>{formatAmount(entry.balance_after_minor, currency, i18n.language)}</small>
            </b>
          </div>)}
        </div>}
      </article>
    </>}
  </section>;
}
