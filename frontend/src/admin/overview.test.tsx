import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { OrderStatusChart, OrderTrendChart, PaymentMethodChart, TopProductsChart } from './AdminOverviewPage';

vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => undefined });
vi.stubGlobal('document', { documentElement: { lang: '' } });
const { default: i18n } = await import('../i18n');
const render = (node: ReactNode) => renderToStaticMarkup(<I18nextProvider i18n={i18n}>{node}</I18nextProvider>);

describe('dashboard charts', () => {
  it('renders labelled status bars and values', () => {
    const html = render(<OrderStatusChart values={{ New: 4, Completed: 2 }} label="Order status" valueLabel="Value" emptyLabel="No data" />);
    expect((html.match(/<rect /g) || []).length).toBe(2);
    expect(html).toContain('New');
    expect(html).toContain('Completed');
    expect(html).toContain('>4<');
    expect(html).toContain('>2<');
  });

  it('renders payment proportions with legend and accessible table', () => {
    const html = render(<PaymentMethodChart values={{ Wallet: 3, ABA: 1 }} label="Payment methods" valueLabel="Value" percentLabel="Percent" emptyLabel="No data" />);
    expect((html.match(/<rect /g) || []).length).toBe(2);
    expect(html).toContain('75.0%');
    expect(html).toContain('25.0%');
    expect(html).toContain('Payment methods');
  });

  it('renders top product bars, quantities, and full names in titles', () => {
    const longName = 'A very long product name that must be truncated in the chart';
    const html = render(<TopProductsChart values={[{ name: longName, quantity: 12 }, { name: 'Tea', quantity: 4 }]} label="Top products" quantityLabel="Quantity" emptyLabel="No data" />);
    expect((html.match(/<rect /g) || []).length).toBe(2);
    expect(html).toContain(`<title>${longName}</title>`);
    expect(html).toContain('>12<');
    expect(html).toContain('>4<');
  });

  it('renders separate order and sales trend curves and empty state', () => {
    const html = render(<OrderTrendChart values={[{ date: '2026-01-01', orders: 2, sales_minor: 105, new_users: 0, daily_active: 0 }]} currency="USD" dateLabel="Date" ordersLabel="Orders" salesLabel="Sales" emptyLabel="No data" />);
    expect((html.match(/<polyline /g) || []).length).toBe(2);
    expect(html).toContain('Orders');
    expect(html).toContain('Sales');
    expect(html).toContain('1.05 USD');
    expect(render(<TopProductsChart values={[]} label="Top products" quantityLabel="Quantity" emptyLabel="No data" />)).toContain('No data');
  });
});
