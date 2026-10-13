import { useEffect, useState, type ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import AdminDailyReport from '../components/AdminDailyReport';
import { useAuthStore } from '../store/authStore';
import AdminLegacyPage from './AdminLegacyPage';
import AdminFinanceStatsPage from './AdminFinanceStatsPage';
import AdminOrdersPage from './AdminOrdersPage';
import { AdminSectionPage } from './layout/AdminSectionPage';
import AdminProductsPage from './AdminProductsPage';
import AdminProductEditPage from './AdminProductEditPage';
import AdminCategoriesPage from './AdminCategoriesPage';
import AdminCategoryEditPage from './AdminCategoryEditPage';
import AdminCouponsPage from './AdminCouponsPage';
import AdminCouponEditPage from './AdminCouponEditPage';

function AdminGuard({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { isAdminAuthenticated, setAdminAuth, logoutAdmin } = useAuthStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    api.get('/api/v1/auth/admin/me').then((response) => {
      if (active) setAdminAuth(String(response.data.staff_id), response.data.role, response.data.store_id ?? null, response.data.catalog_write_allowed ?? false);
    }).catch(() => { if (active) logoutAdmin(); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [logoutAdmin, setAdminAuth]);

  if (loading) return <main className="auth-shell"><p>{t('common.loading')}</p></main>;
  if (!isAdminAuthenticated) return <Navigate to="/admin/login" replace />;
  return children;
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
    <Route path="products" element={<AdminProductsPage />} />
    <Route path="products/new" element={<AdminProductEditPage />} />
    <Route path="products/:id" element={<AdminProductEditPage />} />
    <Route path="categories" element={<AdminCategoriesPage />} />
    <Route path="categories/new" element={<AdminCategoryEditPage />} />
    <Route path="categories/:id" element={<AdminCategoryEditPage />} />
    <Route path="coupons" element={<AdminCouponsPage />} />
    <Route path="coupons/new" element={<AdminCouponEditPage />} />
    <Route path="coupons/:id" element={<AdminCouponEditPage />} />
    <Route path="finance" element={<Navigate to="/admin/finance/daily" replace />} />
    <Route path="finance/daily" element={<AdminFinanceDailyPage />} />
    <Route path="finance/stats" element={<AdminFinanceStatsRoute />} />
    <Route path="audit" element={<AdminLegacyPage initialSection="audit" />} />
    <Route path="staff" element={<AdminLegacyPage initialSection="staff" />} />
    <Route path="settings" element={<AdminLegacyPage initialSection="settings" />} />
    <Route path="*" element={<Navigate to="/admin/overview" replace />} />
  </Routes></AdminGuard>;
}

