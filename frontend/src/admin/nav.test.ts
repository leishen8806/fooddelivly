import { describe, expect, it } from 'vitest';
import { ADMIN_NAV_GROUPS, isNavItemVisible } from './layout/nav';

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
});

