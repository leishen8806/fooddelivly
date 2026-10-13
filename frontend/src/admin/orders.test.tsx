import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { I18nextProvider } from 'react-i18next';
import { DataTable } from './components/DataTable';
import AdminOrdersPage, { formatMinor, parseDateRange } from './AdminOrdersPage';

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
  });
});
