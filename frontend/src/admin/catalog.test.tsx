import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { MemoryRouter } from 'react-router-dom';
import AdminProductsPage from './AdminProductsPage';
import AdminCategoriesPage from './AdminCategoriesPage';

vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => undefined });
vi.stubGlobal('document', { documentElement: { lang: '' } });
const { default: i18n } = await import('../i18n');

const render = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter initialEntries={['/admin/products']}><I18nextProvider i18n={i18n}>{node}</I18nextProvider></MemoryRouter>);

describe('C1 catalog list pages', () => {
  it('keeps the product list free of forms and exposes the absolute create route', () => {
    const html = render(<AdminProductsPage />);
    expect(html).not.toContain('<form');
    expect(html).toContain('href="/admin/products/new"');
    for (const key of ['admin.productImage', 'admin.productName', 'admin.category', 'admin.price', 'admin.productOptions', 'admin.saleWindows', 'admin.sweetness', 'admin.productStatus', 'admin.sortOrder', 'admin.actions']) expect(html).toContain(i18n.t(key));
  });

  it('keeps the category list free of forms and exposes the absolute create route', () => {
    const html = render(<AdminCategoriesPage />);
    expect(html).not.toContain('<form');
    expect(html).toContain('href="/admin/categories/new"');
    for (const key of ['admin.categoryName', 'admin.productCount', 'admin.sortOrder', 'admin.productStatus', 'admin.actions']) expect(html).toContain(i18n.t(key));
  });
});
