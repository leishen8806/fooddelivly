import { describe, expect, it } from 'vitest';
import { ADMIN_NAV_GROUPS, adminNavItemForPath, isAdminPathVisible, isNavItemVisible } from './layout/nav';

describe('admin navigation', () => {
  it('keeps manager-only sections hidden from staff', () => {
    const managerOnly = ADMIN_NAV_GROUPS.flatMap((group) => group.items).filter((item) => item.roles?.includes('MANAGER'));
    expect(managerOnly.length).toBeGreaterThan(0);
    expect(managerOnly.every((item) => !isNavItemVisible(item, 'STAFF'))).toBe(true);
    expect(managerOnly.every((item) => isNavItemVisible(item, 'MANAGER'))).toBe(true);
  });

  it('does not expose headquarters-only sections to store users', () => {
    const item = { key: 'overview' as const, path: '/admin/overview', labelKey: 'nav.overview', hqOnly: true };
    expect(isNavItemVisible(item, 'MANAGER', false)).toBe(false);
    expect(isNavItemVisible(item, 'MANAGER', true)).toBe(true);
  });

  it('keeps daily reports and interval finance stats as separate entries', () => {
    const finance = ADMIN_NAV_GROUPS.find((group) => group.key === 'finance');
    expect(finance?.items.map((item) => item.path)).toEqual(['/admin/finance/daily', '/admin/finance/stats', '/admin/refunds']);
  });

  it('reuses navigation role rules for nested route guards', () => {
    expect(adminNavItemForPath('/admin/products/new')?.key).toBe('products');
    expect(adminNavItemForPath('/admin/orders/TC-123')?.key).toBe('orders');
    expect(isAdminPathVisible('/admin/products/new', 'STAFF')).toBe(false);
    expect(isAdminPathVisible('/admin/orders/TC-123', 'STAFF')).toBe(true);
    expect(isAdminPathVisible('/admin/settings/payment', 'MANAGER', false)).toBe(true);
  });

  it('blocks a store manager from a headquarters-only route', () => {
    const item = { key: 'staff' as const, path: '/admin/staff', labelKey: 'admin.staffAccess', hqOnly: true };
    expect(isNavItemVisible(item, 'MANAGER', false)).toBe(false);
    expect(isNavItemVisible(item, 'MANAGER', true)).toBe(true);
  });
});

