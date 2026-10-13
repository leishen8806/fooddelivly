import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { AdminSectionPage } from './layout/AdminSectionPage';
import { useAuthStore } from '../store/authStore';

type StaffRow = { id: number; login_name: string; role: string; active: boolean; store_id: number | null; telegram_user_id: string | null };
export default function AdminStaffEditPage() {
  const { t } = useTranslation(); const navigate = useNavigate(); const { id } = useParams<{ id: string }>();
  const { role, staffId } = useAuthStore(); const isNew = !id;
  const [row, setRow] = useState<StaffRow | null>(null); const [login, setLogin] = useState(''); const [password, setPassword] = useState(''); const [roleValue, setRoleValue] = useState('STAFF'); const [telegramId, setTelegramId] = useState(''); const [active, setActive] = useState(true); const [storeId, setStoreId] = useState('');
  const [error, setError] = useState(''); const [saving, setSaving] = useState(false); const [temporaryPassword, setTemporaryPassword] = useState('');
  useEffect(() => { if (isNew) return; let live = true; api.get('/api/v1/admin/staff').then((response) => { const found = (response.data as StaffRow[]).find((item) => String(item.id) === id); if (live && found) { setRow(found); setLogin(found.login_name); setRoleValue(found.role); setTelegramId(found.telegram_user_id || ''); setActive(found.active); setStoreId(found.store_id === null ? '' : String(found.store_id)); } }).catch(() => live && setError(t('auth.staffOnly'))); return () => { live = false; }; }, [id, isNew, t]);
  const save = async (event: FormEvent) => { event.preventDefault(); setSaving(true); setError(''); try { if (isNew) { await api.post('/api/v1/admin/staff', { login_name: login, password, role: roleValue, telegram_user_id: telegramId || null, store_id: storeId ? Number(storeId) : null }); } else { await api.patch(`/api/v1/admin/staff/${id}`, { role: roleValue, telegram_user_id: telegramId || null, active }); } navigate('/admin/staff'); } catch (cause) { setError((cause as { response?: { data?: { detail?: string } } }).response?.data?.detail || t('error.generic')); } finally { setSaving(false); } };
  const reset = async () => { if (!id) return; setError(''); try { const response = await api.post(`/api/v1/admin/staff/${id}/reset-password`); setTemporaryPassword(response.data.temporary_password); } catch (cause) { setError((cause as { response?: { data?: { detail?: string } } }).response?.data?.detail || t('error.generic')); } };
  const own = String(row?.id || '') === staffId;
  return <AdminSectionPage activeSection="staff"><section className="panel catalog-edit-page"><p className="breadcrumb"><Link to="/admin/staff">{t('admin.staffList')}</Link> / {isNew ? t('admin.staff.new') : t('admin.staff.edit')}</p><h2>{isNew ? t('admin.staff.new') : t('admin.staff.edit')}</h2>{error && <p className="error" role="alert">{error}</p>}
    <form className="product-form" onSubmit={(event) => void save(event)}>
      {isNew && <label>{t('admin.loginName')}<input value={login} onChange={(event) => setLogin(event.target.value)} minLength={3} required /></label>}
      {isNew && <label>{t('admin.password')}<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} required /><small>{t('admin.staff.passwordHint')}</small></label>}
      <label>{t('admin.staffRole')}<select value={roleValue} onChange={(event) => setRoleValue(event.target.value)} disabled={own}><option value="STAFF">{t('admin.waiter')}</option><option value="MANAGER">{t('admin.manager')}</option></select></label>
      <label>{t('admin.telegramId')}<input inputMode="numeric" value={telegramId} onChange={(event) => setTelegramId(event.target.value)} /></label>
      {isNew && <label>{t('admin.staff.store')}<input inputMode="numeric" value={storeId} onChange={(event) => setStoreId(event.target.value)} placeholder={t('admin.staff.hq')} /></label>}
      {!isNew && <label className="product-sweetness-toggle"><input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} disabled={own} /><span>{t('admin.active')}</span></label>}
      <div className="actions"><Link className="admin-button" to="/admin/staff">{t('common.cancel')}</Link><button className="primary" disabled={saving || role !== 'MANAGER'}>{saving ? t('common.loading') : t('common.save')}</button>{!isNew && <button type="button" onClick={() => void reset()} disabled={own}>{t('admin.staff.resetPassword')}</button>}</div>
    </form>{temporaryPassword && <aside className="notice" role="status"><strong>{t('admin.staff.temporaryPassword')}</strong><code>{temporaryPassword}</code><p>{t('admin.staff.forceChange')}</p></aside>}
  </section></AdminSectionPage>;
}
