import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { I18nextProvider } from 'react-i18next';
import { DataTable } from './components/DataTable';
import AdminOrdersPage, { AdminOrderDetail, formatMinor, parseDateRange } from './AdminOrdersPage';

vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => undefined });
vi.stubGlobal('document', { documentElement: { lang: '' } });
const { default: i18n } = await import('../i18n');

describe('B1 order workbench helpers', () => {
  it('validates date ranges and formats integer minor units by currency', () => {
    expect(parseDateRange('2026-01-02', '2026-01-01')).toBe('reversed');
    expect(parseDateRange('bad', '')).toBe('invalid');
    expect(parseDateRange('2026-01-01', '2026-01-02')).toBeNull();
    expect(formatMinor(105, 'USD')).toBe('USD 1.05');
    expect(formatMinor(105, 'KHR')).toBe('KHR 105');
  });

  it('exposes sortable headers and clickable rows', () => {
    const html = renderToStaticMarkup(<DataTable columns={[{ key: 'total_minor', header: 'Total', render: (row: { id: number }) => row.id }]} rows={[{ id: 1 }]} empty="Empty" onRowClick={() => undefined} onSort={() => undefined} sortKey="total_minor" sortDirection="desc" />);
    expect(html).toContain('admin-table-sort');
    expect(html).toContain('tabindex="0"');
    expect(html).toContain('↓');
  });

  it('keeps the list page free of create/edit forms', () => {
    const html = renderToStaticMarkup(<MemoryRouter initialEntries={['/admin/orders']}><I18nextProvider i18n={i18n}><AdminOrdersPage /></I18nextProvider></MemoryRouter>);
    expect(html).not.toContain('<form');
    expect(html).toContain('type="date"');
    expect(html).toContain('admin-status-tabs');
    for (const label of ['Created', 'Order user', 'Open chat', 'Room', 'Subtotal', 'Coupon discount', 'Amount due', 'Received', 'Payment', 'Order status', 'Actions']) expect(html).toContain(label);
  });

  it('keeps the detail cancellation and manager upload entry points', () => {
    const html = renderToStaticMarkup(<MemoryRouter initialEntries={['/admin/orders/ORD-1']}><I18nextProvider i18n={i18n}><AdminOrderDetail code="ORD-1" roleOverride="MANAGER" initialOrder={{ id: 1, public_code: 'ORD-1', room_number: '101', order_status: 'NEW', payment_status: 'UNPAID', payment_method: 'ABA', currency: 'USD', total_minor: 200, subtotal_minor: 200, delivery_fee_minor: 0, service_fee_minor: 0, wallet_paid_minor: 0, external_due_minor: 200, paid_confirmed_minor: 0, removed_minor: 0, refunded_minor: 0, received_minor: 0, refund_pending_minor: 0, coupon_discount_minor: 0, items_preview: ['Tea'], item_count: 1, open_issue_count: 0, created_at: '2026-01-01T00:00:00Z', items: [], events: [], issues: [], refunds: [] }} /></I18nextProvider></MemoryRouter>);
    expect(html).toContain('Cancel order');
    expect(html).toContain('Upload payment proof');
    expect(html).toContain('accept="image/png,image/jpeg,image/webp"');
    expect(html).toContain('Subtotal');
    expect(html).toContain('Refunded');
  });

  it('disables detail actions while a shortage is unresolved', () => {
    const html = renderToStaticMarkup(<MemoryRouter initialEntries={['/admin/orders/ORD-2']}><I18nextProvider i18n={i18n}><AdminOrderDetail code="ORD-2" roleOverride="MANAGER" initialOrder={{ id: 2, public_code: 'ORD-2', room_number: '101', order_status: 'NEW', payment_status: 'UNPAID', payment_method: 'ABA', currency: 'USD', total_minor: 200, subtotal_minor: 200, delivery_fee_minor: 0, service_fee_minor: 0, wallet_paid_minor: 0, external_due_minor: 200, paid_confirmed_minor: 0, removed_minor: 0, refunded_minor: 0, received_minor: 0, refund_pending_minor: 0, coupon_discount_minor: 0, items_preview: ['Tea'], item_count: 1, open_issue_count: 1, created_at: '2026-01-01T00:00:00Z', items: [], events: [], issues: [{ id: 1, order_item_id: 1, issue_type: 'OUT_OF_STOCK', status: 'OPEN' }], refunds: [] }} /></I18nextProvider></MemoryRouter>);
    expect(html).toContain('disabled=""');
    expect(html).toContain('unresolved shortage');
  });
});
