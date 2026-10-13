import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { formatAmount } from '../format';
import { DataTable } from './components/DataTable';
import { useAuthStore } from '../store/authStore';
import { AdminSectionPage } from './layout/AdminSectionPage';
import { hasMissingTranslation, localizedLabel, type Category, type Product } from './catalog/types';

type StatusFilter = 'all' | 'available' | 'soldOut';

export default function AdminProductsPage() {
  const { t, i18n } = useTranslation();
  const { catalogWriteAllowed } = useAuthStore();
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [missingOnly, setMissingOnly] = useState(false);
  const [withOptionsOnly, setWithOptionsOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [productResponse, categoryResponse] = await Promise.all([
        api.get('/api/v1/admin/products'),
        api.get('/api/v1/admin/categories'),
      ]);
      setProducts(productResponse.data);
      setCategories(categoryResponse.data);
    } catch {
      setError(t('error.network'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { void load(); }, [load]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return products.filter((product) => {
      const categoryIds = product.category_ids?.length ? product.category_ids : [product.category_id];
      const searchable = Object.values(product.name || {}).join(' ').toLowerCase();
      if (query && !searchable.includes(query)) return false;
      if (category !== 'all' && !categoryIds.includes(Number(category))) return false;
      if (status === 'available' && !product.available) return false;
      if (status === 'soldOut' && product.available) return false;
      if (missingOnly && !hasMissingTranslation(product.name)) return false;
      if (withOptionsOnly && !(product.option_groups || []).length) return false;
      return true;
    });
  }, [category, missingOnly, products, search, status, withOptionsOnly]);

  const toggleAvailable = async (product: Product) => {
    setBusyId(product.id);
    try {
      await api.patch(`/api/v1/admin/products/${product.id}`, { available: !product.available });
      setProducts((current) => current.map((item) => item.id === product.id ? { ...item, available: !item.available } : item));
    } catch {
      setError(t('error.generic'));
    } finally {
      setBusyId(null);
    }
  };

  const categoryName = (id: number) => localizedLabel(categories.find((item) => item.id === id)?.name, i18n.language) || '—';
  const columns = [
    { key: 'image', header: t('admin.productImage'), render: (product: Product) => product.image_key ? <img className="catalog-thumb" src={product.image_key} alt="" /> : <span className="catalog-thumb-placeholder" aria-hidden="true">{localizedLabel(product.name, i18n.language).slice(0, 1)}</span> },
    { key: 'name', header: t('admin.productName'), render: (product: Product) => <span className="catalog-name-cell"><strong>{localizedLabel(product.name, i18n.language)}</strong>{hasMissingTranslation(product.name) && <small className="translation-missing">{t('admin.localized.missing')}</small>}</span> },
    { key: 'categories', header: t('admin.category'), render: (product: Product) => <span className="catalog-chips">{(product.category_ids?.length ? product.category_ids : [product.category_id]).map((id) => <span className="catalog-chip" key={id}>{categoryName(id)}</span>)}</span> },
    { key: 'price', header: t('admin.price'), render: (product: Product) => formatAmount(product.price_minor, product.currency, i18n.language) },
    { key: 'options', header: t('admin.productOptions'), render: (product: Product) => (product.option_groups || []).length || '—' },
    { key: 'saleWindows', header: t('admin.saleWindows'), render: (product: Product) => product.has_sale_window ? (product.sale_windows || []).map((window) => `${window.start}–${window.end}`).join(', ') : t('admin.allDay') },
    { key: 'sweetness', header: t('admin.sweetness'), render: (product: Product) => product.sweetness_enabled ? t('admin.sweetnessOn') : t('admin.sweetnessOff') },
    { key: 'status', header: t('admin.productStatus'), render: (product: Product) => product.available ? t('common.available') : t('common.soldOut') },
    { key: 'sort', header: t('admin.sortOrder'), render: (product: Product) => product.sort_order },
    { key: 'actions', header: t('admin.actions'), render: (product: Product) => <span className="catalog-actions"><Link className="admin-link-button" to={`/admin/products/${product.id}`}>{t('common.edit')}</Link><button type="button" className="admin-link-button" disabled={!catalogWriteAllowed || busyId === product.id} onClick={() => void toggleAvailable(product)}>{product.available ? t('common.soldOut') : t('common.available')}</button></span> },
  ];

  return <AdminSectionPage activeSection="products"><section className="panel catalog-page">
    <div className="panel-heading"><div><span className="eyebrow">{t('admin.catalog')}</span><h2>{t('nav.products')}</h2></div><Link className="primary admin-button" to="/admin/products/new">{t('admin.createProduct')}</Link></div>
    {!catalogWriteAllowed && <p className="notice" role="status">{t('admin.catalogReadonly')}</p>}
    <div className="catalog-toolbar" aria-label={t('common.search')}>
      <label>{t('common.search')}<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t('admin.searchProducts')} /></label>
      <label>{t('admin.category')}<select value={category} onChange={(event) => setCategory(event.target.value)}><option value="all">{t('admin.allCategories')}</option>{categories.map((item) => <option value={item.id} key={item.id}>{localizedLabel(item.name, i18n.language)}</option>)}</select></label>
      <label>{t('admin.productStatus')}<select value={status} onChange={(event) => setStatus(event.target.value as StatusFilter)}><option value="all">{t('admin.allProducts')}</option><option value="available">{t('common.available')}</option><option value="soldOut">{t('common.soldOut')}</option></select></label>
      <label className="catalog-check"><input type="checkbox" checked={missingOnly} onChange={(event) => setMissingOnly(event.target.checked)} />{t('admin.missingTranslation')}</label>
      <label className="catalog-check"><input type="checkbox" checked={withOptionsOnly} onChange={(event) => setWithOptionsOnly(event.target.checked)} />{t('admin.hasOptions')}</label>
    </div>
    <DataTable columns={columns} rows={filtered} loading={loading} error={error} empty={t('admin.noProducts')} />
  </section></AdminSectionPage>;
}
