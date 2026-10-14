import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { formatDateTick, getTickIndexes, HourlyChart, localizedProductName, normalizeChartWidth, OrderStatusChart, OrderTrendChart, PaymentMethodChart, TopProductsChart } from './AdminOverviewPage';

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

  it('provides a table alternative for hourly distribution', () => {
    const html = render(<HourlyChart values={[3, 1]} label="Orders by hour" hourLabel="Hour" valueLabel="Value" />);
    expect((html.match(/<rect /g) || []).length).toBe(2);
    expect(html).toContain('00:00');
    expect(html).toContain('01:00');
    expect(html).toContain('<th>Value</th>');
  });

  it('calculates responsive date ticks and chart width', () => {
    expect(getTickIndexes(7, 7)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(getTickIndexes(30, 30)).toEqual([0, 5, 10, 15, 20, 25, 29]);
    expect(formatDateTick('2026-02-03', 'en')).toBe('02-03');
    expect(normalizeChartWidth(0)).toBe(640);
    expect(normalizeChartWidth(375)).toBe(375);
  });

  it('formats localized product names with language fallback', () => {
    const names = { 'zh-CN': '柠檬茶', en: 'Lemon tea', km: 'តែ​ក្រូចឆ្មា' };
    expect(localizedProductName(names, 'zh-CN')).toBe('柠檬茶');
    expect(localizedProductName(names, 'km')).toBe('តែ​ក្រូចឆ្មា');
    expect(localizedProductName(names, 'fr')).toBe('Lemon tea');
  });

  it('renders a compact single payment state and an empty trend state', () => {
    const payment = render(<PaymentMethodChart values={{ Wallet: 4 }} label="Payment methods" valueLabel="Value" percentLabel="Percent" emptyLabel="No data" />);
    expect(payment).toContain('Wallet 4 (100.0%)');
    expect(payment).not.toContain('<svg');
    const empty = render(<OrderTrendChart values={[{ date: '2026-01-01', orders: 0, sales_minor: 0, new_users: 0, daily_active: 0 }]} currency="USD" dateLabel="Date" ordersLabel="Orders" salesLabel="Sales" emptyLabel="No data" />);
    expect(empty).toContain('No data');
  });
});
