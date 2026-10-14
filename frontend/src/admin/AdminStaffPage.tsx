import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { DataTable } from './components/DataTable';
import { AdminSectionPage } from './layout/AdminSectionPage';
import { useAuthStore } from '../store/authStore';

type StaffRow = { id: number; login_name: string; role: string; active: boolean; store_id: number | null; telegram_user_id: string | null; last_login_at?: string | null; must_change_password?: boolean };

export default function AdminStaffPage() {
  const { t, i18n } = useTranslation();
  const { role } = useAuthStore();
  const [rows, setRows] = useState<StaffRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try { const response = await api.get('/api/v1/admin/staff'); setRows(response.data); }
    catch { setError(t('auth.staffOnly')); }
    finally { setLoading(false); }
  }, [t]);
  useEffect(() => { void load(); }, [load]);
  const columns = [
    { key: 'login', header: t('admin.staff.account'), render: (row: StaffRow) => <strong>{row.login_name}</strong> },
    { key: 'role', header: t('admin.staffRole'), render: (row: StaffRow) => row.role === 'MANAGER' ? t('admin.manager') : t('admin.waiter') },
    { key: 'store', header: t('admin.staff.store'), render: (row: StaffRow) => row.store_id === null ? t('admin.staff.hq') : String(row.store_id) },
    { key: 'telegram', header: t('admin.telegramId'), render: (row: StaffRow) => row.telegram_user_id || '—' },
    { key: 'active', header: t('admin.status'), render: (row: StaffRow) => row.active ? t('admin.active') : t('admin.deactivate') },
    { key: 'last', header: t('admin.staff.lastLogin'), render: (row: StaffRow) => row.last_login_at ? new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(row.last_login_at)) : '—' },
    { key: 'actions', header: t('admin.actions'), render: (row: StaffRow) => <Link className="admin-link-button" to={`/admin/staff/${row.id}`}>{t('common.edit')}</Link> },
  ];
  return <AdminSectionPage activeSection="staff"><section className="panel catalog-page">
    <div className="panel-heading"><div><span className="eyebrow">{t('admin.staffAccess')}</span><h2>{t('admin.staffList')}</h2></div>{role !== 'STAFF' && <Link className="primary admin-button" to="/admin/staff/new">{t('admin.staff.new')}</Link>}</div>
    <DataTable columns={columns} rows={rows} loading={loading} error={error} empty={t('admin.noStaff')} />
  </section></AdminSectionPage>;
}
