import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { formatAmount, formatDateTime } from '../format';

export type ReportPayload = {
  date: string;
  timezone: string;
  currency: string;
  orders: number;
  orders_total_minor: number;
  order_sales_total_minor: number;
  order_sales_subtotal_minor: number;
  order_sales_discount_minor: number;
  order_sales_delivery_fee_minor: number;
  order_sales_service_fee_minor: number;
  shortage_removed_minor: number;
  refund_wallet_done_minor: number;
  refund_aba_done_minor: number;
  refund_pending_minor: number;
  refund_pending_count: number;
  refund_reconcile_diff_minor: number;
  received_total_minor: number;
  received_manual_minor: number;
  received_wallet_minor: number;
  refunded_minor: number;
  recharges_credited: number;
  recharges_amount_minor: number;
  recharges_bonus_minor: number;
  recharges_pending: number;
  total_customers: number;
  new_customers: number;
  awaiting_payment_review: number;
};

export type ReportResponse = {
  report: ReportPayload;
  text: string;
  delivery: { chat_id: string; message_id: string | null; sent_at: string } | null;
  target_chat_id: string | null;
  scheduled_hour: number;
  scheduled_minute: number;
};

const warningLabel = (label: string, attention: string) => <span className="metric-warning-heading"><span aria-hidden="true">⚠</span>{attention} · {label}</span>;

export function DailyReportMetrics({ report }: { report: ReportPayload }) {
  const { t, i18n } = useTranslation();
  const amount = (minor: number) => formatAmount(minor, report.currency, i18n.language);
  return <div className="daily-report-metrics">
    <div className="metric"><span>{t('admin.reportOrders')}</span><strong>{report.orders}</strong></div>
    <div className="metric"><span>{t('admin.orderSales')}</span><strong>{amount(report.order_sales_total_minor)}</strong></div>
    <div className="metric"><span>{t('admin.reportOrderSubtotal')}</span><strong>{amount(report.order_sales_subtotal_minor)}</strong></div>
    <div className="metric"><span>{t('admin.reportCouponDiscount')}</span><strong>{report.order_sales_discount_minor > 0 ? `-${amount(report.order_sales_discount_minor)}` : '—'}</strong></div>
    <div className="metric"><span>{t('admin.reportDeliveryFee')}</span><strong>{amount(report.order_sales_delivery_fee_minor)}</strong></div>
    <div className="metric"><span>{t('admin.reportServiceFee')}</span><strong>{amount(report.order_sales_service_fee_minor)}</strong></div>
    <div className="metric"><span>{t('admin.orderTotalGross')}</span><strong>{amount(report.orders_total_minor)}</strong></div>
    <div className="metric"><span>{t('admin.confirmedReceipts')}</span><strong>{amount(report.received_total_minor)}</strong></div>
    <div className="metric"><span>{t('admin.shortageRemoved')}</span><strong>{amount(report.shortage_removed_minor)}</strong></div>
    <div className="metric"><span>{t('admin.refundWalletDone')}</span><strong>{amount(report.refund_wallet_done_minor)}</strong></div>
    <div className="metric"><span>{t('admin.refundAbaDone')}</span><strong>{amount(report.refund_aba_done_minor)}</strong></div>
    <div className={`metric${report.refund_pending_count > 0 ? ' warning' : ''}`} role={report.refund_pending_count > 0 ? 'alert' : undefined}><span>{report.refund_pending_count > 0 ? warningLabel(t('admin.refundPending'), t('admin.attention')) : t('admin.refundPending')}</span><strong>{amount(report.refund_pending_minor)}</strong><small>{t('admin.refundPendingDetail', { count: report.refund_pending_count })}</small></div>
    {report.refund_reconcile_diff_minor !== 0 && <div className="metric warning" role="alert"><span>{warningLabel(t('admin.refundReconcileDiff'), t('admin.attention'))}</span><strong>{amount(report.refund_reconcile_diff_minor)}</strong></div>}
    <div className="metric"><span>{t('admin.reportTopUps')} × {report.recharges_credited}</span><strong>{amount(report.recharges_amount_minor)}</strong></div>
    <div className="metric"><span>{t('admin.reportBonus')}</span><strong>{amount(report.recharges_bonus_minor)}</strong></div>
    <div className="metric"><span>{t('admin.reportTotalCustomers')}</span><strong>{report.total_customers}</strong></div>
    <div className="metric"><span>{t('admin.reportNewCustomers')}</span><strong>{report.new_customers}</strong></div>
    <div className={`metric${report.awaiting_payment_review ? ' warning' : ''}`} role={report.awaiting_payment_review ? 'alert' : undefined}><span>{report.awaiting_payment_review ? warningLabel(t('admin.reportAwaitingReview'), t('admin.attention')) : t('admin.reportAwaitingReview')}</span><strong>{report.awaiting_payment_review}</strong></div>
  </div>;
}

/**
 * 管理端「每日报表」卡片。
 *
 * 报表按店铺时区当天 00:00:00 ~ 22:30:00（右边界不计入）统计。
 * 后端每天 22:35（DAILY_REPORT_HOUR/DAILY_REPORT_MINUTE 可调）自动推送到员工群，
 * 这里提供预览和一个手动补发入口——自动发送失败时不用等第二天。
 * 发送是幂等的：当天已经发过就会提示「已发送」，不会重复发。
 */
export default function AdminDailyReport({ role }: { role: string | null }) {
  const { t, i18n } = useTranslation();
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [data, setData] = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await api.get('/api/v1/admin/reports/daily', { params: { date } });
      setData(response.data);
    } catch {
      setError(t('error.network'));
    } finally {
      setLoading(false);
    }
  }, [date, t]);

  useEffect(() => { void load(); }, [load]);

  const send = async (force: boolean) => {
    setBusy(true);
    setNotice('');
    try {
      const response = await api.post('/api/v1/admin/reports/daily/send', { date, force });
      const body = response.data;
      setNotice(body.sent
        ? t('common.save')
        : body.skipped
          ? t('admin.dailyReportSent', { time: formatDateTime(body.delivery?.sent_at, i18n.language) })
          : String(body.error || t('error.generic')));
      await load();
    } catch (requestError: any) {
      setNotice(requestError?.response?.data?.detail || t('error.network'));
    } finally {
      setBusy(false);
    }
  };

  return <section className="panel daily-report-panel">
    <div className="panel-heading">
      <div>
        <span className="eyebrow">{t('report.daily.title')} · {t('admin.timezone')} {data?.report.timezone || '—'}</span>
        <h2>{t('admin.dailyReport')}</h2>
      </div>
      <label>{t('admin.reportDate')}<input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
      <button type="button" onClick={() => void load()} disabled={loading}>{t('admin.dailyReportPreview')}</button>
    </div>
    {loading && <p className="state" role="status">{t('common.loading')}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="status" role="status">{notice}</p>}
    {data && !loading && <>
      <div className="daily-report-status">
        {data.delivery ? <span className="wallet-status wallet-status-credited">{t('admin.dailyReportSent', { time: formatDateTime(data.delivery.sent_at, i18n.language) })}</span> : <span className="wallet-status">{t('admin.dailyReportNotSent')}</span>}
        <small>{t('report.daily.title')} · {data.report.date} · {String(data.scheduled_hour).padStart(2, '0')}:{String(data.scheduled_minute).padStart(2, '0')}</small>
      </div>
      <DailyReportMetrics report={data.report} />
      <pre className="daily-report-text">{data.text}</pre>
      {role === 'MANAGER' && <div className="actions"><button className="primary" type="button" disabled={busy} onClick={() => void send(false)}>{t('admin.dailyReportSend')}</button>{data.delivery && <button type="button" disabled={busy} onClick={() => void send(true)}>{t('admin.dailyReportResend')}</button>}</div>}
      {!data.target_chat_id && <p className="error">{t('admin.groupIdHelp')}</p>}
    </>}
  </section>;
}
