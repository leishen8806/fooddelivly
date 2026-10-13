import { useEffect, useState, type ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { useAuthStore } from '../store/authStore';
import AdminLegacyPage from './AdminLegacyPage';
import AdminOrdersPage from './AdminOrdersPage';

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

export default function AdminApp() {
  return <AdminGuard><Routes>
    <Route index element={<Navigate to="overview" replace />} />
    <Route path="overview" element={<AdminLegacyPage initialSection="overview" />} />
    <Route path="orders" element={<AdminOrdersPage />} />
    <Route path="orders/:code" element={<AdminOrdersPage />} />
    <Route path="recharges" element={<AdminLegacyPage initialSection="recharges" />} />
    <Route path="customers" element={<AdminLegacyPage initialSection="customers" />} />
    <Route path="products" element={<AdminLegacyPage initialSection="products" />} />
    <Route path="coupons" element={<AdminLegacyPage initialSection="coupons" />} />
    <Route path="finance" element={<AdminLegacyPage initialSection="finance" />} />
    <Route path="audit" element={<AdminLegacyPage initialSection="audit" />} />
    <Route path="staff" element={<AdminLegacyPage initialSection="staff" />} />
    <Route path="settings" element={<AdminLegacyPage initialSection="settings" />} />
    <Route path="*" element={<Navigate to="overview" replace />} />
  </Routes></AdminGuard>;
}

