import { useNavigate } from 'react-router-dom';
import type { ReactNode } from 'react';
import api from '../../api';
import { useAuthStore } from '../../store/authStore';
import { AdminLayout } from './AdminLayout';
import { adminPathForSection, type AdminSection } from './nav';

export function AdminSectionPage({ activeSection, children }: { activeSection: AdminSection; children: ReactNode }) {
  const navigate = useNavigate();
  const { role, storeId, logoutAdmin } = useAuthStore();
  const signOut = async () => { try { await api.post('/api/v1/auth/admin/logout'); } finally { logoutAdmin(); navigate('/admin/login'); } };
  return <AdminLayout role={role || 'STAFF'} activeSection={activeSection} onNavigate={(section) => navigate(adminPathForSection(section))} onSignOut={signOut} isHq={role === 'MANAGER' && storeId === null}>{children}</AdminLayout>;
}
