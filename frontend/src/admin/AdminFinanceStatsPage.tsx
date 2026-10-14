import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { formatAmount } from '../format';

export type FinanceCurrencyRow = {
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

export type FinanceStats = { order_volume?: number; cancelled_count?: number; timezone?: string; by_currency?: FinanceCurrencyRow[] };

const warningLabel = (label: string, attention: string) => <span className="metric-warning-heading"><span aria-hidden="true">⚠</span>{attention} · {label}</span>;

export function FinanceStatsCards({ stats }: { stats: FinanceStats }) {
  const { t, i18n } = useTranslation();
  const amount = (minor: number, currency: string) => formatAmount(minor, currency, i18n.language);
  return <>
    <p>{t('admin.timezone')}: {stats.timezone || '—'}</p>
    <p>{t('admin.manualReviewBasis')}</p>
    <p className="finance-note">{t('admin.financeSeparationHint')}</p>
    <div className="metric-row"><div className="metric"><span>{t('admin.orderVolume')}</span><strong>{stats.order_volume ?? '—'}</strong></div><div className="metric"><span>{t('admin.cancelledCount')}</span><strong>{stats.cancelled_count ?? '—'}</strong></div></div>
    {(stats.by_currency || []).map((row) => {
      const discount = row.order_sales_discount_minor ?? 0;
      const pending = row.refund_pending_count ?? 0;
      const reconcileDiff = row.refund_reconcile_diff_minor ?? 0;
      const inReview = row.in_review_minor ?? 0;
      return <div className="metric-row" key={row.currency}>
        <div className="metric"><span>{t('admin.orderSales')}</span><strong>{amount(row.order_sales_minor ?? 0, row.currency)}</strong><small>{t('admin.reportOrderSubtotal')}: {amount(row.order_sales_subtotal_minor ?? 0, row.currency)} · {t('admin.reportCouponDiscount')}: {discount > 0 ? `-${amount(discount, row.currency)}` : '—'} · {t('admin.orderTotalGross')}: {amount(row.order_total_minor ?? 0, row.currency)}</small></div>
        <div className="metric"><span>{t('admin.externalOrderReceipts')}</span><strong>{amount(row.external_order_receipts_minor ?? 0, row.currency)}</strong></div>
        <div className="metric"><span>{t('admin.walletOrderPayments')}</span><strong>{amount(row.wallet_paid_minor ?? 0, row.currency)}</strong></div>
        <div className="metric"><span>{t('admin.rechargeCredited')}</span><strong>{amount(row.recharge_credited_minor ?? 0, row.currency)}</strong></div>
        <div className="metric accent" title={t('admin.netCashInflowHint')}><span>{t('admin.netCashInflow')}</span><strong>{amount(row.net_cash_inflow_minor ?? row.cash_inflow_minor ?? 0, row.currency)}</strong><small>{t('admin.cashInflow')}: {amount(row.cash_inflow_minor ?? 0, row.currency)} · {t('admin.refundAbaDone')}: -{amount(row.refund_aba_done_minor ?? 0, row.currency)}</small></div>
        <div className="metric"><span>{t('admin.refundWalletDone')}</span><strong>{amount(row.refund_wallet_done_minor ?? 0, row.currency)}</strong></div>
        <div className={`metric${pending > 0 ? ' warning' : ''}`} role={pending > 0 ? 'alert' : undefined}><span>{pending > 0 ? warningLabel(t('admin.refundPending'), t('admin.attention')) : t('admin.refundPending')}</span><strong>{amount(row.refund_pending_minor ?? 0, row.currency)}</strong><small>{t('admin.refundPendingDetail', { count: pending })}</small></div>
        <div className="metric"><span>{t('admin.shortageRemoved')}</span><strong>{amount(row.shortage_removed_minor ?? 0, row.currency)}</strong></div>
        {reconcileDiff !== 0 && <div className="metric warning" role="alert"><span>{warningLabel(t('admin.refundReconcileDiff'), t('admin.attention'))}</span><strong>{amount(reconcileDiff, row.currency)}</strong></div>}
        <div className={`metric${inReview > 0 ? ' warning' : ''}`} role={inReview > 0 ? 'alert' : undefined}><span>{inReview > 0 ? warningLabel(t('admin.inReview'), t('admin.attention')) : t('admin.inReview')}</span><strong>{amount(inReview, row.currency)}</strong></div>
        <div className="metric"><span>{t('admin.cancelledAmount')}</span><strong>{amount(row.cancelled_total_minor ?? 0, row.currency)}</strong></div>
      </div>;
    })}
  </>;
}

export default function AdminFinanceStatsPage() {
  const { t } = useTranslation();
  const [from, setFrom] = useState(() => new Date(Date.now() - 6 * 86400000).toISOString().slice(0, 10));
  const [to, setTo] = useState(() => new Date().toISOString().slice(0, 10));
  const [stats, setStats] = useState<FinanceStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await api.get('/api/v1/admin/analytics/', { params: { from, to } });
      setStats(response.data);
    } catch {
      setError(t('error.network'));
    } finally {
      setLoading(false);
    }
  }, [from, to, t]);
  useEffect(() => { void load(); }, [load]);

  return <section className="panel finance-stats-panel">
    <div className="panel-heading"><div><span className="eyebrow">{t('admin.finance')}</span><h2>{t('nav.finance')}</h2></div><div className="finance-date-range"><label>{t('admin.from')} <input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label><label>{t('admin.to')} <input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label><button type="button" onClick={() => void load()} disabled={loading}>{t('common.retry')}</button></div></div>
    {loading && <p className="state" role="status">{t('common.loading')}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {stats && !loading && <FinanceStatsCards stats={stats} />}
  </section>;
}
