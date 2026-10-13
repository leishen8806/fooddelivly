import { describe, expect, it } from 'vitest';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import AdminStaffPage from './AdminStaffPage';
import AdminSettingsPage from './AdminSettingsPage';
import AdminStaffEditPage from './AdminStaffEditPage';
import { useAuthStore } from '../store/authStore';
vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => undefined });
vi.stubGlobal('document', { documentElement: { lang: '' } });
const { default: i18n } = await import('../i18n');
const render = (node: ReactNode, path: string) => renderToStaticMarkup(<MemoryRouter initialEntries={[path]}><I18nextProvider i18n={i18n}>{node}</I18nextProvider></MemoryRouter>);

describe('staff and settings page layout', () => {
  it('keeps staff list free of inline forms and exposes absolute create link', () => {
    useAuthStore.getState().setAdminAuth('1', 'MANAGER');
    const html = render(<AdminStaffPage />, '/admin/staff');
    expect(html).not.toContain('<form');
    expect(html).toContain('/admin/staff/new');
  });
  it('keeps settings overview read-only with secondary edit links', () => {
    const html = render(<AdminSettingsPage />, '/admin/settings');
    expect(html).not.toContain('<form');
    expect(html).toContain('/admin/settings/business');
  });
  it('renders staff edit route with save and cancel controls', () => {
    const html = render(<AdminStaffEditPage />, '/admin/staff/new');
    expect(html).toContain('Cancel');
    expect(html).toContain('Save');
  });
});
