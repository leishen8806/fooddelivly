import { useEffect, useState, type ReactNode } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import AdminDailyReport from '../components/AdminDailyReport';
import { useAuthStore } from '../store/authStore';
import AdminLegacyPage from './AdminLegacyPage';
import AdminFinanceStatsPage from './AdminFinanceStatsPage';
import AdminOrdersPage from './AdminOrdersPage';
import { AdminLayout } from './layout/AdminLayout';
import { adminPathForSection, type AdminSection } from './layout/nav';

function AdminGuard({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { isAdminAuthenticated, setAdminAuth, logoutAdmin } = useAuthStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    api.get('/api/v1/auth/admin/me').then((response) => {
      if (active) setAdminAuth(String(response.data.staff_id), response.data.role, response.data.store_id ?? null);
    }).catch(() => { if (active) logoutAdmin(); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [logoutAdmin, setAdminAuth]);

  if (loading) return <main className="auth-shell"><p>{t('common.loading')}</p></main>;
  if (!isAdminAuthenticated) return <Navigate to="/admin/login" replace />;
  return children;
}

function AdminSectionPage({ activeSection, children }: { activeSection: AdminSection; children: ReactNode }) {
  const navigate = useNavigate();
  const { role, storeId, logoutAdmin } = useAuthStore();
  const signOut = async () => { try { await api.post('/api/v1/auth/admin/logout'); } finally { logoutAdmin(); navigate('/admin/login'); } };
  return <AdminLayout role={role || 'STAFF'} activeSection={activeSection} onNavigate={(section) => navigate(adminPathForSection(section))} onSignOut={signOut} isHq={role === 'MANAGER' && storeId === null}>{children}</AdminLayout>;
}

function AdminFinanceDailyPage() {
  const { role } = useAuthStore();
  return <AdminSectionPage activeSection="financeDaily"><AdminDailyReport role={role} /></AdminSectionPage>;
}

function AdminFinanceStatsRoute() {
  return <AdminSectionPage activeSection="financeStats"><AdminFinanceStatsPage /></AdminSectionPage>;
}

export default function AdminApp() {
  return <AdminGuard><Routes>
    <Route index element={<Navigate to="/admin/overview" replace />} />
    <Route path="overview" element={<AdminLegacyPage initialSection="overview" />} />
    <Route path="orders" element={<AdminOrdersPage />} />
    <Route path="orders/:code" element={<AdminOrdersPage />} />
    <Route path="recharges" element={<AdminLegacyPage initialSection="recharges" />} />
    <Route path="customers" element={<AdminLegacyPage initialSection="customers" />} />
    <Route path="products" element={<AdminLegacyPage initialSection="products" />} />
    <Route path="coupons" element={<AdminLegacyPage initialSection="coupons" />} />
    <Route path="finance" element={<Navigate to="/admin/finance/daily" replace />} />
    <Route path="finance/daily" element={<AdminFinanceDailyPage />} />
    <Route path="finance/stats" element={<AdminFinanceStatsRoute />} />
    <Route path="audit" element={<AdminLegacyPage initialSection="audit" />} />
    <Route path="staff" element={<AdminLegacyPage initialSection="staff" />} />
    <Route path="settings" element={<AdminLegacyPage initialSection="settings" />} />
    <Route path="*" element={<Navigate to="/admin/overview" replace />} />
  </Routes></AdminGuard>;
}

