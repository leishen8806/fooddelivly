import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import AdminProductRules, { toRulesGroups, toRulesWindows } from '../components/AdminProductRules';
import { LocalizedInput, localizedInputIsValid } from './components/LocalizedInput';
import { MoneyInput } from './components/MoneyInput';
import ImageUpload from './components/ImageUpload';
import { useAuthStore } from '../store/authStore';
import { AdminSectionPage } from './layout/AdminSectionPage';
import { localizedLabel, type Category, type Product } from './catalog/types';

const emptyLocalized = { en: '', 'zh-CN': '', km: '' };

export default function AdminProductEditPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const isNew = id === undefined;
  const { catalogWriteAllowed } = useAuthStore();
  const [product, setProduct] = useState<Product | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [currency, setCurrency] = useState('USD');
  const [name, setName] = useState<Record<string, string>>(emptyLocalized);
  const [description, setDescription] = useState<Record<string, string>>(emptyLocalized);
  const [imageKey, setImageKey] = useState<string | null>(null);
  const [priceMinor, setPriceMinor] = useState<number | null>(null);
  const [categoryIds, setCategoryIds] = useState<number[]>([]);
  const [sortOrder, setSortOrder] = useState('0');
  const [sweetnessEnabled, setSweetnessEnabled] = useState(false);
  const [available, setAvailable] = useState(true);
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const [categoryResponse, settingsResponse, productResponse] = await Promise.all([
          api.get('/api/v1/admin/categories'),
          api.get('/api/v1/admin/settings/'),
          isNew ? Promise.resolve({ data: [] }) : api.get('/api/v1/admin/products'),
        ]);
        if (!active) return;
        setCategories(categoryResponse.data);
        const loadedCurrency = settingsResponse.data.currency || 'USD';
        setCurrency(loadedCurrency);
        if (!isNew) {
          const found = (productResponse.data as Product[]).find((item) => String(item.id) === id);
          if (!found) {
            setError(t('admin.productNotFound'));
          } else {
            setProduct(found);
            setCurrency(found.currency);
            setName({ en: found.name.en || '', 'zh-CN': found.name['zh-CN'] || '', km: found.name.km || '' });
            setDescription({ en: found.description?.en || '', 'zh-CN': found.description?.['zh-CN'] || '', km: found.description?.km || '' });
            setImageKey(found.image_key || null);
            setPriceMinor(found.price_minor);
            setCategoryIds(found.category_ids?.length ? found.category_ids : [found.category_id]);
            setSortOrder(String(found.sort_order || 0));
            setSweetnessEnabled(found.sweetness_enabled);
            setAvailable(found.available);
          }
        }
      } catch {
        if (active) setError(t('error.network'));
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => { active = false; };
  }, [id, isNew, t]);

  const rulesName = useMemo(() => localizedLabel(name, i18n.language), [i18n.language, name]);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setNotice('');
    if (!catalogWriteAllowed) {
      setError(t('admin.catalogReadonly'));
      return;
    }
    if (!localizedInputIsValid(name, true) || !localizedInputIsValid(description, true)) {
      setError(t('admin.localized.englishRequired'));
      return;
    }
    if (!categoryIds.length) {
      setError(t('admin.categoryRequired'));
      return;
    }
    if (priceMinor === null || priceMinor <= 0) {
      setError(t('admin.priceRequired'));
      return;
    }
    setSaving(true);
    try {
      const payload = {
        category_id: categoryIds[0],
        category_ids: categoryIds,
        name,
        description: description.en || description['zh-CN'] || description.km ? description : null,
        price_minor: priceMinor,
        currency,
        image_key: imageKey,
        available,
        sort_order: Number(sortOrder) || 0,
        sweetness_enabled: sweetnessEnabled,
      };
      const response = isNew
        ? await api.post('/api/v1/admin/products', payload)
        : await api.patch(`/api/v1/admin/products/${id}`, payload);
      if (isNew) {
        navigate(`/admin/products/${response.data.id}`);
      } else {
        navigate('/admin/products');
      }
    } catch (requestError: any) {
      setError(requestError?.response?.data?.detail || t('error.generic'));
    } finally {
      setSaving(false);
    }
  };

  return <AdminSectionPage activeSection="products"><section className="panel catalog-edit-page">
    <nav className="admin-breadcrumb"><Link to="/admin/products">{t('nav.products')}</Link><span aria-hidden="true">/</span><strong>{isNew ? t('admin.newProductTitle') : t('admin.editProductTitle')}</strong></nav>
    <div className="panel-heading"><div><span className="eyebrow">{t('admin.catalog')}</span><h2>{isNew ? t('admin.newProductTitle') : t('admin.editProductTitle')}</h2></div></div>
    {!catalogWriteAllowed && <p className="notice" role="status">{t('admin.catalogReadonly')}</p>}
    {loading && <p className="state" role="status">{t('common.loading')}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {!loading && !error && <>
      <form className="catalog-form" onSubmit={(event) => void save(event)}>
        <fieldset disabled={!catalogWriteAllowed || saving}><legend>{t('admin.productBasics')}</legend>
          <LocalizedInput label={t('admin.productName')} value={name} onChange={setName} requireEnglish />
          <LocalizedInput label={t('admin.description')} value={description} onChange={setDescription} multiline requireEnglish />
          <ImageUpload label={t('admin.imageUrl')} value={imageKey} onChange={setImageKey} />
          <MoneyInput label={t('admin.price')} valueMinor={priceMinor} currency={currency} onChange={setPriceMinor} required />
          <label>{t('common.currency')}<input value={currency} readOnly /></label>
          <label>{t('admin.category')}<select multiple size={Math.min(8, Math.max(3, categories.length))} value={categoryIds.map(String)} onChange={(event) => setCategoryIds(Array.from(event.target.selectedOptions, (option) => Number(option.value)))} required>{categories.map((category) => <option key={category.id} value={category.id}>{localizedLabel(category.name, i18n.language)}</option>)}</select><small>{t('admin.categoryMultiHint')}</small></label>
          <label>{t('admin.sortOrder')}<input type="number" step="1" value={sortOrder} onChange={(event) => setSortOrder(event.target.value)} /></label>
          <label className="product-sweetness-toggle"><input type="checkbox" checked={sweetnessEnabled} onChange={(event) => setSweetnessEnabled(event.target.checked)} /><span><strong>{t('admin.enableSweetness')}</strong><small>{t('admin.enableSweetnessHint')}</small></span></label>
          <label className="product-sweetness-toggle"><input type="checkbox" checked={available} onChange={(event) => setAvailable(event.target.checked)} /><span><strong>{t('common.available')}</strong></span></label>
        </fieldset>
        {catalogWriteAllowed && <div className="catalog-form-actions"><Link className="admin-button" to="/admin/products">{t('common.cancel')}</Link><button className="primary admin-button" type="submit" disabled={saving}>{saving ? t('common.loading') : t('common.save')}</button></div>}
      </form>
      {product && <div className="catalog-rule-sections"><h3>{t('admin.productRules')}</h3><AdminProductRules productId={product.id} productName={rulesName} currency={currency} initialWindows={toRulesWindows(product.sale_windows)} initialGroups={toRulesGroups(product.option_groups)} onSaved={async () => { const response = await api.get('/api/v1/admin/products'); const refreshed = (response.data as Product[]).find((item) => item.id === product.id); if (refreshed) setProduct(refreshed); }} /></div>}
      {notice && <p className="status" role="status">{notice}</p>}
    </>}
  </section></AdminSectionPage>;
}
