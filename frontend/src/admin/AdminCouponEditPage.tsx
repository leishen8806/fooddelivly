import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { MoneyInput } from './components/MoneyInput';
import { AdminSectionPage } from './layout/AdminSectionPage';
import { useAuthStore } from '../store/authStore';
import type { AdminCoupon } from './AdminCouponsPage';

const localDateTime = (date: Date) => {
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
};

const asLocalDateTime = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? localDateTime(new Date()) : localDateTime(date);
};

const detailOf = (error: unknown, fallback: string) => {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    if (typeof response?.data?.detail === 'string') return response.data.detail;
  }
  return fallback;
};

export default function AdminCouponEditPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const isNew = id === undefined;
  const { role, catalogWriteAllowed } = useAuthStore();
  const [currency, setCurrency] = useState('USD');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [couponType, setCouponType] = useState<'DISCOUNT' | 'FIXED'>('DISCOUNT');
  const [distributionMode, setDistributionMode] = useState<'PRIVATE' | 'PUBLIC'>('PUBLIC');
  const [minSpendMinor, setMinSpendMinor] = useState<number | null>(null);
  const [discountPercent, setDiscountPercent] = useState('10');
  const [discountCapMinor, setDiscountCapMinor] = useState<number | null>(null);
  const [fixedDiscountMinor, setFixedDiscountMinor] = useState<number | null>(null);
  const [startsAt, setStartsAt] = useState(() => localDateTime(new Date()));
  const [endsAt, setEndsAt] = useState(() => localDateTime(new Date(Date.now() + 86400000)));
  const [perUserClaimLimit, setPerUserClaimLimit] = useState('1');
  const [totalClaimLimit, setTotalClaimLimit] = useState('');
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    const load = async () => {
      if (isNew) {
        try {
          const response = await api.get('/api/v1/admin/settings/');
          if (active) setCurrency(response.data.currency || 'USD');
        } catch {
          if (active) setError(t('error.network'));
        } finally {
          if (active) setLoading(false);
        }
        return;
      }
      setLoading(true);
      try {
        const response = await api.get<AdminCoupon[]>('/api/v1/admin/coupons');
        const coupon = response.data.find((item) => String(item.id) === id);
        if (!coupon) {
          if (active) setError(t('coupon.notFound'));
          return;
        }
        if (!active) return;
        setCurrency(coupon.currency);
        setName(coupon.name);
        setDescription(coupon.description || '');
        setCouponType(coupon.coupon_type);
        setDistributionMode(coupon.distribution_mode);
        setMinSpendMinor(coupon.min_spend_minor);
        setDiscountPercent(String(coupon.discount_percent ?? 10));
        setDiscountCapMinor(coupon.discount_cap_minor ?? null);
        setFixedDiscountMinor(coupon.fixed_discount_minor ?? null);
        setStartsAt(asLocalDateTime(coupon.starts_at));
        setEndsAt(asLocalDateTime(coupon.ends_at));
        setPerUserClaimLimit(String(coupon.per_user_claim_limit));
        setTotalClaimLimit(coupon.total_claim_limit == null ? '' : String(coupon.total_claim_limit));
      } catch (requestError) {
        if (active) setError(detailOf(requestError, t('error.network')));
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => { active = false; };
  }, [id, isNew, t]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    if (!catalogWriteAllowed) {
      setError(t('admin.catalogReadonly'));
      return;
    }
    if (!name.trim() || !startsAt || !endsAt) {
      setError(t('coupon.requiredFields'));
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        description: description.trim() || null,
        coupon_type: couponType,
        distribution_mode: distributionMode,
        currency,
        min_spend_minor: minSpendMinor ?? 0,
        discount_percent: couponType === 'DISCOUNT' ? Number(discountPercent || 0) : null,
        discount_cap_minor: couponType === 'DISCOUNT' ? discountCapMinor : null,
        fixed_discount_minor: couponType === 'FIXED' ? fixedDiscountMinor : null,
        starts_at: new Date(startsAt).toISOString(),
        ends_at: new Date(endsAt).toISOString(),
        per_user_claim_limit: Number(perUserClaimLimit || 1),
        total_claim_limit: totalClaimLimit ? Number(totalClaimLimit) : null,
      };
      if (isNew) await api.post('/api/v1/admin/coupons', payload);
      else await api.patch(`/api/v1/admin/coupons/${id}`, payload);
      navigate('/admin/coupons');
    } catch (requestError) {
      setError(detailOf(requestError, t('coupon.saveFailed')));
    } finally {
      setSaving(false);
    }
  };

  if (role === 'STAFF') return <AdminSectionPage activeSection="coupons"><p className="error" role="alert">{t('auth.staffOnly')}</p></AdminSectionPage>;
  return <AdminSectionPage activeSection="coupons"><section className="panel catalog-edit-page">
    <nav className="admin-breadcrumb"><Link to="/admin/coupons">{t('coupon.title')}</Link><span aria-hidden="true">/</span><strong>{isNew ? t('coupon.new') : t('coupon.edit')}</strong></nav>
    <div className="panel-heading"><div><span className="eyebrow">{t('admin.nav.catalog')}</span><h2>{isNew ? t('coupon.new') : t('coupon.edit')}</h2></div></div>
    {!catalogWriteAllowed && <p className="notice" role="status">{t('admin.catalogReadonly')}</p>}
    {loading && <p className="state" role="status">{t('common.loading')}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {!loading && !error && <form className="catalog-form" onSubmit={(event) => void save(event)}>
      <fieldset disabled={!catalogWriteAllowed || saving}><legend>{t('coupon.basics')}</legend>
        <label>{t('coupon.name')}<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
        <label>{t('coupon.description')}<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></label>
        <label>{t('coupon.type')}<select value={couponType} onChange={(event) => setCouponType(event.target.value as 'DISCOUNT' | 'FIXED')}><option value="DISCOUNT">{t('coupon.discount')}</option><option value="FIXED">{t('coupon.fixed')}</option></select></label>
        <label>{t('coupon.distribution')}<select value={distributionMode} onChange={(event) => setDistributionMode(event.target.value as 'PRIVATE' | 'PUBLIC')}><option value="PRIVATE">{t('coupon.private')}</option><option value="PUBLIC">{t('coupon.public')}</option></select></label>
        <MoneyInput label={t('coupon.minimumSpend')} valueMinor={minSpendMinor} currency={currency} onChange={setMinSpendMinor} />
        {couponType === 'DISCOUNT' ? <><label>{t('coupon.discountPercent')}<input type="number" min="1" max="100" value={discountPercent} onChange={(event) => setDiscountPercent(event.target.value)} required /></label><MoneyInput label={t('coupon.discountCap')} valueMinor={discountCapMinor} currency={currency} onChange={setDiscountCapMinor} /></> : <MoneyInput label={t('coupon.fixedAmount')} valueMinor={fixedDiscountMinor} currency={currency} onChange={setFixedDiscountMinor} required />}
        <label>{t('coupon.startsAt')}<input type="datetime-local" value={startsAt} onChange={(event) => setStartsAt(event.target.value)} required /></label>
        <label>{t('coupon.endsAt')}<input type="datetime-local" value={endsAt} onChange={(event) => setEndsAt(event.target.value)} required /></label>
        <label>{t('coupon.perUserLimit')}<input type="number" min="1" value={perUserClaimLimit} onChange={(event) => setPerUserClaimLimit(event.target.value)} /></label>
        <label>{t('coupon.totalLimit')}<input type="number" min="1" value={totalClaimLimit} onChange={(event) => setTotalClaimLimit(event.target.value)} /></label>
      </fieldset>
      <div className="catalog-form-actions"><Link className="admin-button" to="/admin/coupons">{t('common.cancel')}</Link><button className="primary admin-button" type="submit" disabled={!catalogWriteAllowed || saving}>{saving ? t('common.loading') : t('common.save')}</button></div>
    </form>}
  </section></AdminSectionPage>;
}
