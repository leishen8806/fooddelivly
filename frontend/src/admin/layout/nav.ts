export type AdminRole = 'STAFF' | 'MANAGER' | string;

export type AdminSection =
  | 'overview'
  | 'orders'
  | 'recharges'
  | 'customers'
  | 'products'
  | 'coupons'
  | 'financeDaily'
  | 'financeStats'
  | 'finance'
  | 'audit'
  | 'staff'
  | 'settings';

export type AdminNavItem = {
  key: AdminSection;
  path: string;
  labelKey: string;
  roles?: AdminRole[];
  hqOnly?: boolean;
};

export type AdminNavGroup = {
  key: string;
  labelKey: string;
  items: AdminNavItem[];
};

export const ADMIN_NAV_GROUPS: AdminNavGroup[] = [
  {
    key: 'operations',
    labelKey: 'admin.nav.operations',
    items: [
      { key: 'overview', path: '/admin/overview', labelKey: 'nav.overview' },
      { key: 'orders', path: '/admin/orders', labelKey: 'nav.orders' },
      { key: 'recharges', path: '/admin/recharges', labelKey: 'admin.recharges' },
    ],
  },
  {
    key: 'catalog',
    labelKey: 'admin.nav.catalog',
    items: [
      { key: 'products', path: '/admin/products', labelKey: 'nav.products', roles: ['MANAGER'] },
      { key: 'coupons', path: '/admin/coupons', labelKey: 'nav.coupons', roles: ['MANAGER'] },
    ],
  },
  {
    key: 'finance',
    labelKey: 'admin.nav.finance',
    items: [
      { key: 'financeDaily', path: '/admin/finance/daily', labelKey: 'admin.dailyReport' },
      { key: 'financeStats', path: '/admin/finance/stats', labelKey: 'nav.finance' },
    ],
  },
  {
    key: 'system',
    labelKey: 'admin.nav.system',
    items: [
      { key: 'customers', path: '/admin/customers', labelKey: 'nav.customers', roles: ['MANAGER'] },
      { key: 'audit', path: '/admin/audit', labelKey: 'nav.auditLogs', roles: ['MANAGER'] },
      { key: 'staff', path: '/admin/staff', labelKey: 'admin.staffAccess', roles: ['MANAGER'] },
      { key: 'settings', path: '/admin/settings', labelKey: 'nav.settings', roles: ['MANAGER'] },
    ],
  },
];

export const adminPathForSection = (section: AdminSection) =>
  ADMIN_NAV_GROUPS.flatMap((group) => group.items).find((item) => item.key === section)?.path || `/admin/${section}`;

export const isNavItemVisible = (item: AdminNavItem, role: AdminRole, isHq = false) => {
  if (item.roles && !item.roles.includes(role)) return false;
  if (item.hqOnly && !isHq) return false;
  return true;
};

