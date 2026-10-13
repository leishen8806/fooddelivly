import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { DataTable } from './components/DataTable';
import { useAuthStore } from '../store/authStore';
import { AdminSectionPage } from './layout/AdminSectionPage';
import { hasMissingTranslation, localizedLabel, type Category } from './catalog/types';

export default function AdminCategoriesPage() {
  const { t, i18n } = useTranslation();
  const { catalogWriteAllowed } = useAuthStore();
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await api.get('/api/v1/admin/categories');
      setCategories(response.data);
    } catch {
      setError(t('error.network'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { void load(); }, [load]);

  const toggleActive = async (category: Category) => {
    setBusyId(category.id);
    try {
      await api.patch(`/api/v1/admin/categories/${category.id}`, { active: !category.active });
      setCategories((current) => current.map((item) => item.id === category.id ? { ...item, active: !item.active } : item));
    } catch {
      setError(t('error.generic'));
    } finally {
      setBusyId(null);
    }
  };

  const columns = [
    { key: 'name', header: t('admin.categoryName'), render: (category: Category) => <span className="catalog-name-cell"><strong>{localizedLabel(category.name, i18n.language)}</strong>{hasMissingTranslation(category.name) && <small className="translation-missing">{t('admin.localized.missing')}</small>}<small>{category.name.en || '—'} · {category.name['zh-CN'] || '—'} · {category.name.km || '—'}</small></span> },
    { key: 'count', header: t('admin.productCount'), render: (category: Category) => category.product_count ?? 0 },
    { key: 'sort', header: t('admin.sortOrder'), render: (category: Category) => category.sort_order },
    { key: 'status', header: t('admin.productStatus'), render: (category: Category) => category.active ? t('common.available') : t('common.soldOut') },
    { key: 'actions', header: t('admin.actions'), render: (category: Category) => <span className="catalog-actions"><Link className="admin-link-button" to={`/admin/categories/${category.id}`}>{t('common.edit')}</Link><button type="button" className="admin-link-button" disabled={!catalogWriteAllowed || busyId === category.id} onClick={() => void toggleActive(category)}>{category.active ? t('admin.deactivate') : t('admin.activate')}</button></span> },
  ];

  return <AdminSectionPage activeSection="categories"><section className="panel catalog-page">
    <div className="panel-heading"><div><span className="eyebrow">{t('admin.catalog')}</span><h2>{t('admin.categories')}</h2></div><Link className="primary admin-button" to="/admin/categories/new">{t('admin.createCategory')}</Link></div>
    {!catalogWriteAllowed && <p className="notice" role="status">{t('admin.catalogReadonly')}</p>}
    <DataTable columns={columns} rows={categories} loading={loading} error={error} empty={t('admin.noCategories')} />
  </section></AdminSectionPage>;
}
