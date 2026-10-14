import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { MemoryRouter } from 'react-router-dom';
import AdminCouponsPage from './AdminCouponsPage';
import AdminCouponEditPage from './AdminCouponEditPage';
import { useAuthStore } from '../store/authStore';

vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => undefined });
vi.stubGlobal('document', { documentElement: { lang: '' } });
const { default: i18n } = await import('../i18n');

const render = (node: ReactNode, path: string) => renderToStaticMarkup(
  <MemoryRouter initialEntries={[path]}><I18nextProvider i18n={i18n}>{node}</I18nextProvider></MemoryRouter>,
);

describe('C2 coupon pages', () => {
  it('keeps the coupon list free of forms and points to the absolute create route', () => {
    const html = render(<AdminCouponsPage />, '/admin/coupons');
    expect(html).not.toContain('<form');
    expect(html).toContain('href="/admin/coupons/new"');
    for (const label of ['Coupon name', 'Rule', 'Distribution', 'Validity', 'Claims', 'Actions']) expect(html).toContain(label);
  });

  it('puts the coupon form on its own route with absolute cancel navigation', () => {
    useAuthStore.setState({ role: 'MANAGER', catalogWriteAllowed: true, isAdminAuthenticated: true, adminStoreResolved: true });
    const html = render(<AdminCouponEditPage />, '/admin/coupons/new');
    expect(html).toContain('<form');
    expect(html).toContain('href="/admin/coupons"');
    expect(html).toContain('Coupon rules');
    expect(html).toContain('Save');
  });
});
