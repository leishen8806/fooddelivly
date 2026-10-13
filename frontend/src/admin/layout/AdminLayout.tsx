import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ADMIN_NAV_GROUPS, isNavItemVisible, type AdminRole, type AdminSection } from './nav';

export function AdminLayout({ role, activeSection, onNavigate, onSignOut, children, isHq = false }: {
  role: AdminRole;
  activeSection: AdminSection;
  onNavigate: (section: AdminSection) => void;
  onSignOut: () => void;
  children: ReactNode;
  isHq?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const activeItem = ADMIN_NAV_GROUPS.flatMap((group) => group.items).find((item) => item.key === activeSection);
  return <main className="admin-shell"><div className="admin-layout">
    <aside className="admin-sidebar"><div className="admin-sidebar-brand"><img className="brand-logo" src="/tea-cafe-logo.png" alt="Tea Cafe" /><div><strong>Tea Cafe</strong><small>STORE CONSOLE</small></div></div>
      <nav className="admin-nav" aria-label={t('admin.navigation')}>
        {ADMIN_NAV_GROUPS.map((group) => {
          const items = group.items.filter((item) => isNavItemVisible(item, role, isHq));
          if (!items.length) return null;
          return <div className="admin-nav-group" key={group.key}><span className="admin-nav-group-label">{t(group.labelKey)}</span>{items.map((item) => <button className={activeSection === item.key ? 'active' : ''} key={item.key} type="button" onClick={() => onNavigate(item.key)}><span aria-hidden="true">{item.key === 'overview' ? '▦' : item.key === 'orders' ? '▤' : item.key === 'recharges' ? '💰' : item.key === 'products' ? '▧' : item.key === 'categories' ? '☷' : item.key === 'coupons' ? '🎟' : item.key === 'financeDaily' || item.key === 'financeStats' || item.key === 'finance' ? '▥' : item.key === 'audit' ? '◷' : item.key === 'staff' ? '⚙' : item.key === 'settings' ? '⌘' : '♧'}</span>{t(item.labelKey)}</button>)}</div>;
        })}
      </nav><div className="sidebar-footer"><span>{role === 'MANAGER' ? t('admin.manager') : t('admin.waiter')}</span><span>TEA CAFE</span></div>
    </aside>
    <div className="admin-main"><header className="admin-header"><div><span className="eyebrow">FOOD.WORKLINE.INK/ADMIN</span><h1>{activeItem ? t(activeItem.labelKey) : t('nav.overview')}</h1></div><div className="header-actions"><label className="admin-language-switch"><span className="sr-only">{t('common.language')}</span><select aria-label={t('common.language')} value={i18n.language} onChange={(event) => { localStorage.setItem('teacafe.language', event.target.value); void i18n.changeLanguage(event.target.value); }}><option value="en">English</option><option value="zh-CN">中文</option><option value="km">ខ្មែរ</option></select></label>{isHq && <label className="admin-store-switcher"><span className="sr-only">{t('admin.storeSwitcher')}</span><select aria-label={t('admin.storeSwitcher')} defaultValue="all"><option value="all">{t('admin.allStores')}</option></select></label>}<button type="button" onClick={onSignOut}>{t('auth.logout')}</button></div></header>{children}</div>
  </div></main>;
}

