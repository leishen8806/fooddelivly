import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { formatAmount } from '../format';
import { DataTable } from './components/DataTable';
import { AdminSectionPage } from './layout/AdminSectionPage';
import { useAuthStore } from '../store/authStore';

export type AdminCoupon = {
  id: number;
  name: string;
  description?: string | null;
  currency: string;
  coupon_type: 'DISCOUNT' | 'FIXED';
  distribution_mode: 'PRIVATE' | 'PUBLIC';
  min_spend_minor: number;
  discount_percent?: number | null;
  discount_cap_minor?: number | null;
  fixed_discount_minor?: number | null;
  starts_at: string;
  ends_at: string;
  per_user_claim_limit: number;
  total_claim_limit?: number | null;
  total_claimed_quantity: number;
  status: string;
  public_claim_url?: string | null;
  public_share_url?: string | null;
  public_share_urls?: Record<string, string>;
  private_link_count: number;
  private_claimed_count: number;
};

const detailOf = (error: unknown, fallback: string) => {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    if (typeof response?.data?.detail === 'string') return response.data.detail;
  }
  return fallback;
};

export default function AdminCouponsPage() {
  const { t, i18n } = useTranslation();
  const { role, catalogWriteAllowed } = useAuthStore();
  const [coupons, setCoupons] = useState<AdminCoupon[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setCoupons((await api.get<AdminCoupon[]>('/api/v1/admin/coupons')).data);
    } catch (requestError) {
      setError(detailOf(requestError, t('error.network')));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { void load(); }, [load]);

  const run = async (coupon: AdminCoupon, action: () => Promise<void>) => {
    setBusyId(coupon.id);
    setError('');
    setNotice('');
    try {
      await action();
      await load();
    } catch (requestError) {
      setError(detailOf(requestError, t('coupon.saveFailed')));
    } finally {
      setBusyId(null);
    }
  };

  const publish = (coupon: AdminCoupon) => run(coupon, () => api.post(`/api/v1/admin/coupons/${coupon.id}/publish`).then(() => undefined));
  const toggle = (coupon: AdminCoupon) => run(coupon, () => api.post(`/api/v1/admin/coupons/${coupon.id}/status`, { status: coupon.status === 'ACTIVE' ? 'PAUSED' : 'ACTIVE' }).then(() => undefined));
  const sharePublic = async (coupon: AdminCoupon) => {
    const url = coupon.public_share_urls?.[i18n.language] || coupon.public_share_url;
    if (!url) return;
    window.open(url, '_blank', 'noopener,noreferrer');
    setNotice(t('coupon.shareOpened'));
  };
  const createPrivateLink = (coupon: AdminCoupon) => run(coupon, async () => {
    const response = await api.post(`/api/v1/admin/coupons/${coupon.id}/private-link`);
    const claimUrl = response.data.claim_url as string | null;
    const shareUrl = (response.data.share_urls?.[i18n.language] || response.data.share_url) as string | null;
    if (claimUrl) await navigator.clipboard?.writeText(claimUrl);
    if (shareUrl) window.open(shareUrl, '_blank', 'noopener,noreferrer');
    setNotice(t('coupon.linkCopied'));
  });

  const columns = [
    { key: 'name', header: t('coupon.name'), render: (coupon: AdminCoupon) => <span className="catalog-name-cell"><strong>{coupon.name}</strong>{coupon.description && <small>{coupon.description}</small>}</span> },
    { key: 'rule', header: t('coupon.rule'), render: (coupon: AdminCoupon) => <span>{coupon.coupon_type === 'DISCOUNT' ? `${coupon.discount_percent ?? 0}%` : formatAmount(coupon.fixed_discount_minor ?? 0, coupon.currency, i18n.language)}<small>{t('coupon.minimumSpend')}: {formatAmount(coupon.min_spend_minor, coupon.currency, i18n.language)}{coupon.discount_cap_minor !== null && coupon.discount_cap_minor !== undefined ? ` · ${t('coupon.discountCap')}: ${formatAmount(coupon.discount_cap_minor, coupon.currency, i18n.language)}` : ''}</small></span> },
    { key: 'distribution', header: t('coupon.distribution'), render: (coupon: AdminCoupon) => coupon.distribution_mode === 'PUBLIC' ? t('coupon.public') : t('coupon.private') },
    { key: 'period', header: t('coupon.validity'), render: (coupon: AdminCoupon) => <span className="catalog-name-cell"><span>{new Date(coupon.starts_at).toLocaleString(i18n.language)}</span><small>{new Date(coupon.ends_at).toLocaleString(i18n.language)}</small></span> },
    { key: 'claims', header: t('coupon.claims'), render: (coupon: AdminCoupon) => `${coupon.total_claimed_quantity}${coupon.total_claim_limit ? ` / ${coupon.total_claim_limit}` : ` / ${t('coupon.unlimited')}`}` },
    { key: 'status', header: t('admin.productStatus'), render: (coupon: AdminCoupon) => t(`coupon.status.${coupon.status.toLowerCase()}`, { defaultValue: coupon.status }) },
    { key: 'actions', header: t('admin.actions'), render: (coupon: AdminCoupon) => <span className="catalog-actions">{coupon.status === 'DRAFT' && <Link className="admin-link-button" to={`/admin/coupons/${coupon.id}`}>{t('common.edit')}</Link>}{coupon.status === 'DRAFT' ? <button type="button" className="admin-link-button" disabled={!catalogWriteAllowed || busyId === coupon.id} onClick={() => void publish(coupon)}>{t('coupon.publish')}</button> : <button type="button" className="admin-link-button" disabled={!catalogWriteAllowed || busyId === coupon.id} onClick={() => void toggle(coupon)}>{coupon.status === 'ACTIVE' ? t('coupon.offline') : t('coupon.online')}</button>}{coupon.distribution_mode === 'PUBLIC' && coupon.public_share_url && <button type="button" className="admin-link-button" onClick={() => void sharePublic(coupon)}>{t('coupon.share')}</button>}{coupon.distribution_mode === 'PRIVATE' && coupon.status !== 'DRAFT' && <button type="button" className="admin-link-button" disabled={!catalogWriteAllowed || busyId === coupon.id} onClick={() => void createPrivateLink(coupon)}>{t('coupon.generatePrivateLink')}</button>}</span> },
  ];

  if (role === 'STAFF') return <AdminSectionPage activeSection="coupons"><p className="error" role="alert">{t('auth.staffOnly')}</p></AdminSectionPage>;
  return <AdminSectionPage activeSection="coupons"><section className="panel catalog-page">
    <div className="panel-heading"><div><span className="eyebrow">{t('admin.nav.catalog')}</span><h2>{t('coupon.title')}</h2></div><Link className="primary admin-button" to="/admin/coupons/new">{t('coupon.new')}</Link></div>
    {!catalogWriteAllowed && <p className="notice" role="status">{t('admin.catalogReadonly')}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="status" role="status">{notice}</p>}
    <DataTable columns={columns} rows={coupons} loading={loading} error={error} empty={t('coupon.empty')} />
  </section></AdminSectionPage>;
}
