import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { DailyReportMetrics, type ReportPayload } from '../components/AdminDailyReport';
import { FinanceStatsCards, type FinanceStats } from './AdminFinanceStatsPage';

vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => undefined });
vi.stubGlobal('document', { documentElement: { lang: '' } });
const { default: i18n } = await import('../i18n');

const report: ReportPayload = {
  date: '2026-10-13', timezone: 'Asia/Phnom_Penh', currency: 'USD', orders: 12, orders_total_minor: 12500,
  order_sales_total_minor: 10000, order_sales_subtotal_minor: 9000, order_sales_discount_minor: 0,
  order_sales_delivery_fee_minor: 500, order_sales_service_fee_minor: 500, shortage_removed_minor: 100,
  refund_wallet_done_minor: 200, refund_aba_done_minor: 300, refund_pending_minor: 400,
  refund_pending_count: 1, refund_reconcile_diff_minor: 50, received_total_minor: 9500,
  received_manual_minor: 8000, received_wallet_minor: 1500, refunded_minor: 500, recharges_credited: 2,
  recharges_amount_minor: 3000, recharges_bonus_minor: 200, recharges_pending: 0, total_customers: 100,
  new_customers: 8, awaiting_payment_review: 2,
};

const stats: FinanceStats = {
  order_volume: 20, cancelled_count: 2, timezone: 'Asia/Phnom_Penh', by_currency: [{
    currency: 'USD', order_sales_minor: 10000, order_sales_subtotal_minor: 9000, order_sales_discount_minor: 0,
    order_total_minor: 12000, external_order_receipts_minor: 7000, wallet_paid_minor: 2000,
    recharge_credited_minor: 3000, net_cash_inflow_minor: 9500, cash_inflow_minor: 10000,
    refund_aba_done_minor: 500, refund_wallet_done_minor: 200, refund_pending_minor: 400,
    refund_pending_count: 1, shortage_removed_minor: 100, refund_reconcile_diff_minor: 50,
    in_review_minor: 600, cancelled_total_minor: 1000,
  }],
};

describe('B2 finance split', () => {
  it('keeps every daily report card and avoids a zero discount amount', () => {
    const html = renderToStaticMarkup(<I18nextProvider i18n={i18n}><DailyReportMetrics report={report} /></I18nextProvider>);
    for (const key of ['admin.reportOrders', 'admin.orderSales', 'admin.reportOrderSubtotal', 'admin.reportCouponDiscount', 'admin.reportDeliveryFee', 'admin.reportServiceFee', 'admin.orderTotalGross', 'admin.confirmedReceipts', 'admin.shortageRemoved', 'admin.refundWalletDone', 'admin.refundAbaDone', 'admin.refundPending', 'admin.refundReconcileDiff', 'admin.reportTopUps', 'admin.reportBonus', 'admin.reportTotalCustomers', 'admin.reportNewCustomers', 'admin.reportAwaitingReview']) expect(html).toContain(i18n.t(key).replaceAll("'", '&#x27;'));
    expect(html).not.toContain('-$0.00');
    expect(html).toContain('⚠');
    expect(html).toContain(i18n.t('admin.attention'));
  });

  it('keeps every interval finance card and marks warning metrics with text and icon', () => {
    const html = renderToStaticMarkup(<I18nextProvider i18n={i18n}><FinanceStatsCards stats={stats} /></I18nextProvider>);
    for (const key of ['admin.timezone', 'admin.manualReviewBasis', 'admin.financeSeparationHint', 'admin.orderVolume', 'admin.cancelledCount', 'admin.orderSales', 'admin.reportOrderSubtotal', 'admin.reportCouponDiscount', 'admin.orderTotalGross', 'admin.externalOrderReceipts', 'admin.walletOrderPayments', 'admin.rechargeCredited', 'admin.netCashInflow', 'admin.cashInflow', 'admin.refundAbaDone', 'admin.refundWalletDone', 'admin.refundPending', 'admin.shortageRemoved', 'admin.refundReconcileDiff', 'admin.inReview', 'admin.cancelledAmount']) expect(html).toContain(i18n.t(key));
    expect(html).not.toContain('-$0.00');
    expect(html).toContain('role="alert"');
    expect(html).toContain('⚠');
  });
});
