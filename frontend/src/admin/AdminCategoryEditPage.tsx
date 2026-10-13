import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { LocalizedInput, localizedInputIsValid } from './components/LocalizedInput';
import { useAuthStore } from '../store/authStore';
import { AdminSectionPage } from './layout/AdminSectionPage';
import { type Category } from './catalog/types';

const emptyName = { en: '', 'zh-CN': '', km: '' };

export default function AdminCategoryEditPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const isNew = id === undefined;
  const { catalogWriteAllowed } = useAuthStore();
  const [name, setName] = useState<Record<string, string>>(emptyName);
  const [sortOrder, setSortOrder] = useState('0');
  const [active, setActive] = useState(true);
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isNew) return;
    let mounted = true;
    api.get('/api/v1/admin/categories').then((response) => {
      if (!mounted) return;
      const category = (response.data as Category[]).find((item) => String(item.id) === id);
      if (!category) setError(t('admin.categoryNotFound'));
      else {
        setName({ en: category.name.en || '', 'zh-CN': category.name['zh-CN'] || '', km: category.name.km || '' });
        setSortOrder(String(category.sort_order || 0));
        setActive(category.active);
      }
    }).catch(() => { if (mounted) setError(t('error.network')); }).finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [id, isNew, t]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    if (!catalogWriteAllowed) { setError(t('admin.catalogReadonly')); return; }
    if (!localizedInputIsValid(name, true)) { setError(t('admin.localized.englishRequired')); return; }
    setSaving(true);
    try {
      const payload = { name, sort_order: Number(sortOrder) || 0, active };
      if (isNew) await api.post('/api/v1/admin/categories', payload);
      else await api.patch(`/api/v1/admin/categories/${id}`, payload);
      navigate('/admin/categories');
    } catch (requestError: any) {
      setError(requestError?.response?.data?.detail || t('error.generic'));
    } finally {
      setSaving(false);
    }
  };

  return <AdminSectionPage activeSection="categories"><section className="panel catalog-edit-page">
    <nav className="admin-breadcrumb"><Link to="/admin/categories">{t('admin.categories')}</Link><span aria-hidden="true">/</span><strong>{isNew ? t('admin.newCategoryTitle') : t('admin.editCategoryTitle')}</strong></nav>
    <div className="panel-heading"><div><span className="eyebrow">{t('admin.catalog')}</span><h2>{isNew ? t('admin.newCategoryTitle') : t('admin.editCategoryTitle')}</h2></div></div>
    {!catalogWriteAllowed && <p className="notice" role="status">{t('admin.catalogReadonly')}</p>}
    {loading && <p className="state" role="status">{t('common.loading')}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {!loading && !error && <form className="catalog-form" onSubmit={(event) => void save(event)}>
      <fieldset disabled={!catalogWriteAllowed || saving}><legend>{t('admin.categoryBasics')}</legend><LocalizedInput label={t('admin.categoryName')} value={name} onChange={setName} requireEnglish /><label>{t('admin.sortOrder')}<input type="number" step="1" value={sortOrder} onChange={(event) => setSortOrder(event.target.value)} /></label><label className="product-sweetness-toggle"><input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} /><span><strong>{t('common.available')}</strong></span></label></fieldset>
      {catalogWriteAllowed && <div className="catalog-form-actions"><Link className="admin-button" to="/admin/categories">{t('common.cancel')}</Link><button className="primary admin-button" type="submit" disabled={saving}>{saving ? t('common.loading') : t('common.save')}</button></div>}
    </form>}
  </section></AdminSectionPage>;
}
