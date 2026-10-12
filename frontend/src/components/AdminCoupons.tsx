import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../api';

type Coupon = {
  id: number; name: string; description?: string | null; currency: string;
  coupon_type: 'DISCOUNT' | 'FIXED'; distribution_mode: 'PRIVATE' | 'PUBLIC';
  min_spend_minor: number; discount_percent?: number | null; discount_cap_minor?: number | null;
  fixed_discount_minor?: number | null; starts_at: string; ends_at: string;
  per_user_claim_limit: number; total_claim_limit?: number | null; total_claimed_quantity: number;
  status: string; public_claim_url?: string | null; public_share_url?: string | null;
  private_link_count: number; private_claimed_count: number;
};

const minor = (value: string) => Math.round(Number(value || 0) * 100);
const displayAmount = (value: number, currency: string) => `${(value / 100).toFixed(2)} ${currency}`;
const localDateTime = (date = new Date(Date.now() + 86400000)) => {
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
};

export default function AdminCoupons() {
  const { t } = useTranslation();
  const [coupons, setCoupons] = useState<Coupon[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ name: '', description: '', coupon_type: 'DISCOUNT', distribution_mode: 'PUBLIC', min_spend: '', discount_percent: '10', discount_cap: '', fixed_discount: '', starts_at: localDateTime(new Date()), ends_at: localDateTime(new Date(Date.now() + 86400000)), per_user_claim_limit: '1', total_claim_limit: '' });

  const load = async () => {
    try { setCoupons((await api.get('/api/v1/admin/coupons')).data); } catch { setError(t('error.network')); }
  };
  useEffect(() => { void load(); }, []);

  const setField = (key: string, value: string) => setForm((current) => ({ ...current, [key]: value }));
  const create = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      await api.post('/api/v1/admin/coupons', {
        name: form.name,
        description: form.description || null,
        coupon_type: form.coupon_type,
        distribution_mode: form.distribution_mode,
        min_spend_minor: minor(form.min_spend),
        discount_percent: form.coupon_type === 'DISCOUNT' ? Number(form.discount_percent) : null,
        discount_cap_minor: form.coupon_type === 'DISCOUNT' && form.discount_cap ? minor(form.discount_cap) : null,
        fixed_discount_minor: form.coupon_type === 'FIXED' ? minor(form.fixed_discount) : null,
        starts_at: new Date(form.starts_at).toISOString(),
        ends_at: new Date(form.ends_at).toISOString(),
        per_user_claim_limit: Number(form.per_user_claim_limit || 1),
        total_claim_limit: form.total_claim_limit ? Number(form.total_claim_limit) : null,
      });
      setForm((current) => ({ ...current, name: '', description: '' }));
      await load();
    } catch (requestError: any) { setError(requestError?.response?.data?.detail || t('coupon.saveFailed')); }
    finally { setBusy(false); }
  };
  const publish = async (coupon: Coupon) => { try { await api.post(`/api/v1/admin/coupons/${coupon.id}/publish`); await load(); } catch (e: any) { setError(e?.response?.data?.detail || t('coupon.saveFailed')); } };
  const toggle = async (coupon: Coupon) => { try { await api.post(`/api/v1/admin/coupons/${coupon.id}/status`, { status: coupon.status === 'ACTIVE' ? 'PAUSED' : 'ACTIVE' }); await load(); } catch (e: any) { setError(e?.response?.data?.detail || t('coupon.saveFailed')); } };
  const privateLink = async (coupon: Coupon) => { try { const response = await api.post(`/api/v1/admin/coupons/${coupon.id}/private-link`); await navigator.clipboard?.writeText(response.data.claim_url || ''); if (response.data.share_url) window.open(response.data.share_url, '_blank', 'noopener,noreferrer'); setError(t('coupon.linkCopied')); } catch (e: any) { setError(e?.response?.data?.detail || t('coupon.saveFailed')); } };

  return <section className="panel coupon-admin-panel">
    <div className="panel-heading"><div><span className="eyebrow">{t('nav.operations')}</span><h2>{t('coupon.title')}</h2></div><button type="button" onClick={() => void load()}>{t('common.retry')}</button></div>
    {error && <p className="error" role="alert">{error}</p>}
    <form className="product-form coupon-form" onSubmit={create}>
      <label>{t('coupon.name')}<input value={form.name} onChange={(e) => setField('name', e.target.value)} required /></label>
      <label>{t('coupon.description')}<input value={form.description} onChange={(e) => setField('description', e.target.value)} /></label>
      <label>{t('coupon.type')}<select value={form.coupon_type} onChange={(e) => setField('coupon_type', e.target.value)}><option value="DISCOUNT">{t('coupon.discount')}</option><option value="FIXED">{t('coupon.fixed')}</option></select></label>
      <label>{t('coupon.distribution')}<select value={form.distribution_mode} onChange={(e) => setField('distribution_mode', e.target.value)}><option value="PRIVATE">{t('coupon.private')}</option><option value="PUBLIC">{t('coupon.public')}</option></select></label>
      <label>{t('coupon.minimumSpend')}<input type="number" min="0" step="0.01" value={form.min_spend} onChange={(e) => setField('min_spend', e.target.value)} /></label>
      {form.coupon_type === 'DISCOUNT' ? <><label>{t('coupon.discountPercent')}<input type="number" min="1" max="100" value={form.discount_percent} onChange={(e) => setField('discount_percent', e.target.value)} required /></label><label>{t('coupon.discountCap')}<input type="number" min="0" step="0.01" value={form.discount_cap} onChange={(e) => setField('discount_cap', e.target.value)} /></label></> : <label>{t('coupon.fixedAmount')}<input type="number" min="0.01" step="0.01" value={form.fixed_discount} onChange={(e) => setField('fixed_discount', e.target.value)} required /></label>}
      <label>{t('coupon.startsAt')}<input type="datetime-local" value={form.starts_at} onChange={(e) => setField('starts_at', e.target.value)} required /></label><label>{t('coupon.endsAt')}<input type="datetime-local" value={form.ends_at} onChange={(e) => setField('ends_at', e.target.value)} required /></label>
      <label>{t('coupon.perUserLimit')}<input type="number" min="1" value={form.per_user_claim_limit} onChange={(e) => setField('per_user_claim_limit', e.target.value)} /></label><label>{t('coupon.totalLimit')}<input type="number" min="1" value={form.total_claim_limit} onChange={(e) => setField('total_claim_limit', e.target.value)} /></label>
      <button className="primary" type="submit" disabled={busy}>{busy ? t('common.loading') : t('coupon.create')}</button>
    </form>
    <div className="order-list">{coupons.map((coupon) => <article className="order-row coupon-admin-row" key={coupon.id}><div><strong>{coupon.name}</strong><small>{coupon.coupon_type === 'DISCOUNT' ? `${coupon.discount_percent}% OFF` : displayAmount(coupon.fixed_discount_minor || 0, coupon.currency)} · {coupon.distribution_mode === 'PUBLIC' ? t('coupon.public') : t('coupon.private')}</small><small>{coupon.total_claimed_quantity}{coupon.total_claim_limit ? ` / ${coupon.total_claim_limit}` : ''} · {coupon.per_user_claim_limit} {t('coupon.perUserLimit')}</small></div><span>{t(`coupon.status.${coupon.status.toLowerCase()}`, { defaultValue: coupon.status })}</span>{coupon.status === 'DRAFT' ? <button type="button" onClick={() => void publish(coupon)}>{t('coupon.publish')}</button> : <button type="button" onClick={() => void toggle(coupon)}>{coupon.status === 'ACTIVE' ? t('coupon.offline') : t('coupon.online')}</button>}{coupon.distribution_mode === 'PUBLIC' && coupon.public_share_url && <a href={coupon.public_share_url} target="_blank" rel="noreferrer">{t('coupon.share')}</a>}{coupon.distribution_mode === 'PRIVATE' && <button type="button" onClick={() => void privateLink(coupon)}>{t('coupon.generatePrivateLink')}</button>}</article>)}</div>
  </section>;
}
