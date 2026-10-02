import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { formatAmount, formatDateTime } from '../format';

type ReportPayload = {
  date: string;
  timezone: string;
  currency: string;
  orders: number;
  orders_total_minor: number;
  received_total_minor: number;
  received_manual_minor: number;
  received_wallet_minor: number;
  refunded_minor: number;
  recharges_credited: number;
  recharges_amount_minor: number;
  recharges_bonus_minor: number;
  recharges_pending: number;
  new_customers: number;
  awaiting_payment_review: number;
};

type ReportResponse = {
  report: ReportPayload;
  text: string;
  delivery: { chat_id: string; message_id: string | null; sent_at: string } | null;
  target_chat_id: string | null;
  scheduled_hour: number;
};

/**
 * 管理端「每日报表」卡片。
 *
 * 报表按**店铺时区的自然日**统计（前一天 00:00:00 ~ 23:59:59.999）。
 * 后端每天早上 8:00（DAILY_REPORT_HOUR 可调）自动推送到员工群，
 * 这里提供预览和一个手动补发入口——自动发送失败时不用等第二天。
 * 发送是幂等的：当天已经发过就会提示「已发送」，不会重复发。
 */
export default function AdminDailyReport({ role }: { role: string | null }) {
  const { t, i18n } = useTranslation();
  const [date, setDate] = useState(() => {
    const yesterday = new Date(Date.now() - 86400000);
    return yesterday.toISOString().slice(0, 10);
  });
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
      <label>{t('admin.reportDate')}
        <input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
      </label>
      <button type="button" onClick={() => void load()} disabled={loading}>{t('admin.dailyReportPreview')}</button>
    </div>

    {loading && <p className="state" role="status">{t('common.loading')}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="status" role="status">{notice}</p>}

    {data && !loading && <>
      <div className="daily-report-status">
        {data.delivery
          ? <span className="wallet-status wallet-status-credited">
              {t('admin.dailyReportSent', { time: formatDateTime(data.delivery.sent_at, i18n.language) })}
            </span>
          : <span className="wallet-status">{t('admin.dailyReportNotSent')}</span>}
        <small>{t('report.daily.title')} · {data.report.date} · {data.scheduled_hour}:00</small>
      </div>

      <div className="daily-report-metrics">
        <div className="metric"><span>{t('admin.reportOrders')}</span><strong>{data.report.orders}</strong></div>
        <div className="metric"><span>{t('admin.orderTotal')}</span><strong>{formatAmount(data.report.orders_total_minor, data.report.currency, i18n.language)}</strong></div>
        <div className="metric"><span>{t('admin.confirmedReceipts')}</span><strong>{formatAmount(data.report.received_total_minor, data.report.currency, i18n.language)}</strong></div>
        <div className="metric"><span>{t('admin.reportTopUps')} × {data.report.recharges_credited}</span><strong>{formatAmount(data.report.recharges_amount_minor, data.report.currency, i18n.language)}</strong></div>
        <div className="metric"><span>{t('admin.reportBonus')}</span><strong>{formatAmount(data.report.recharges_bonus_minor, data.report.currency, i18n.language)}</strong></div>
        <div className={`metric${data.report.awaiting_payment_review ? ' warning' : ''}`}><span>{t('admin.reportAwaitingReview')}</span><strong>{data.report.awaiting_payment_review}</strong></div>
      </div>

      <pre className="daily-report-text">{data.text}</pre>

      {role === 'MANAGER' && <div className="actions">
        <button className="primary" type="button" disabled={busy} onClick={() => void send(false)}>
          {t('admin.dailyReportSend')}
        </button>
        {data.delivery && <button type="button" disabled={busy} onClick={() => void send(true)}>
          {t('admin.dailyReportResend')}
        </button>}
      </div>}
      {!data.target_chat_id && <p className="error">{t('admin.groupIdHelp')}</p>}
    </>}
  </section>;
}
