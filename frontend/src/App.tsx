import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Routes, Route, useNavigate } from 'react-router-dom';
import { ClipboardList, Share2, ShoppingCart, Store, UserRound } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import api from './api';
import { TelegramProvider } from './components/TelegramProvider';
import CustomerWallet from './components/CustomerWallet';
import { type OptionGroup } from './components/ProductOptionsPicker';
import ProductOptionsModal from './components/ProductOptionsModal';
import { useAuthStore } from './store/authStore';
import AdminApp from './admin/AdminApp';
import { openTelegramLink, waitForTelegramStartParam } from './telegramLinks';
import './index.css';

type SaleWindow = { start: string; end: string; overnight: boolean };
type Product = { id: number; category_id: number; category_ids?: number[]; name: Record<string, string>; description?: Record<string, string>; price_minor: number; currency: string; image_key?: string; available: boolean; sweetness_enabled: boolean;
  // 售卖时间：has_sale_window 为 true 时只有 sale_windows 内可下单；
  // orderable = available && 当前在售卖时间内（服务端算好的）
  sale_windows?: SaleWindow[]; has_sale_window?: boolean; on_sale_now?: boolean; orderable?: boolean; next_sale_start?: string | null;
  option_groups?: OptionGroup[] };
type Picked = Record<number, number[]>;
type CartSelection = { group_id: number; option_id: number };
type CartLine = { product: Product; quantity: number; sweetness: number | null; selections: CartSelection[]; unitPrice: number };
type Category = { id: number; name: Record<string, string>; products?: Product[]; sort_order?: number; active?: boolean };
export type CustomerOrder = { public_code: string; room_number: string; order_status: string; payment_status: string; currency: string; total_minor: number; wallet_paid_minor?: number; external_due_minor?: number; open_issue_count?: number; created_at: string };
type OrderIssue = { id: number; issue_type: string; status: string; note?: string | null; replacement_name?: string | null; replacement_unit_price_minor?: number | null; order_item_id?: number | null };
export type CustomerOrderDetails = CustomerOrder & { payment_link?: string | null; payment_qr_url?: string | null; bot_deeplink?: string | null; subtotal_minor?: number; coupon_name?: string | null; coupon_discount_minor?: number; delivery_fee_minor?: number; service_fee_minor?: number; paid_confirmed_minor?: number; refunded_done_minor?: number; refund_pending_minor?: number; items: Array<{ name: Record<string, string> | string; quantity: number; unit_price_minor: number; line_total_minor: number; options?: { sweetness?: number } }>; proof_status: string | null; issues?: OrderIssue[] };
type CustomerCoupon = { id: number; coupon_id: number; name: string; description?: string | null; currency: string; coupon_type: 'DISCOUNT' | 'FIXED'; min_spend_minor: number; discount_percent?: number | null; discount_cap_minor?: number | null; fixed_discount_minor?: number | null; starts_at: string; ends_at: string; status: 'AVAILABLE' | 'USED' | 'EXPIRED' | 'REVOKED' };
type LanguageCode = 'zh-CN' | 'en' | 'km';
type CustomerProfile = { customer_id: number; display_name: string | null; username: string | null; language: LanguageCode; preferred_language: LanguageCode | null };
const label = (value: Record<string, string> | undefined, language: string) => value?.[language] || value?.en || '';
const amount = (minor: number, currency: string, locale = 'en') => {
  try {
    const digits = new Intl.NumberFormat(locale, { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2;
    return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(minor / (10 ** digits));
  } catch {
    return `${minor} ${currency}`;
  }
};

const CONFIRMED_PAYMENT_STATUSES = new Set(['PAID_CONFIRMED', 'PARTIALLY_REFUNDED', 'REFUNDED']);

export const isConfirmedCustomerPayment = (paymentStatus: string) => CONFIRMED_PAYMENT_STATUSES.has(paymentStatus);

export function CustomerOrderPaymentSummary({ order }: { order: CustomerOrderDetails }) {
  const { t, i18n } = useTranslation();
  const confirmed = isConfirmedCustomerPayment(order.payment_status);
  const paid = Math.max(order.paid_confirmed_minor ?? 0, 0);
  const refunded = Math.max(order.refunded_done_minor ?? 0, 0);
  const pending = Math.max(order.refund_pending_minor ?? 0, 0);
  return <>
    <p className="order-modal-total">{t('payment.orderTotal')}: <strong>{amount(Math.max(order.total_minor, 0), order.currency, i18n.language)}</strong></p>
    {confirmed ? <>
      <p className="payment-breakdown-line">{t('payment.paid')}: <strong>{amount(paid, order.currency, i18n.language)}</strong></p>
      <p className="payment-breakdown-line">{t('payment.refunded')}: <strong>{amount(refunded, order.currency, i18n.language)}</strong></p>
      <p className="payment-breakdown-line refund-pending-line">{t('payment.refundPending')}: <strong>{amount(pending, order.currency, i18n.language)}</strong>{pending > 0 && <small>{t('payment.refundPendingNote')}</small>}</p>
    </> : <>
      {(order.wallet_paid_minor ?? 0) > 0 && <p className="payment-breakdown-line">{t('payment.walletApplied')}: <strong>{amount(order.wallet_paid_minor ?? 0, order.currency, i18n.language)}</strong></p>}
      {(order.coupon_discount_minor ?? 0) > 0 && <p className="payment-breakdown-line coupon-discount">{t('coupon.couponDiscount')}: <strong>-{amount(order.coupon_discount_minor ?? 0, order.currency, i18n.language)}</strong></p>}
      {(order.external_due_minor ?? order.total_minor) > 0 && <p className="payment-breakdown-line">{t('payment.abaDue')}: <strong>{amount(order.external_due_minor ?? order.total_minor, order.currency, i18n.language)}</strong></p>}
    </>}
  </>;
}

export function CustomerOrderShortageBlock({ order, onResolve }: { order: CustomerOrderDetails; onResolve: (issue: OrderIssue, resolution: 'REMOVE_ITEM' | 'REPLACE_ITEM') => void }) {
  const { t } = useTranslation();
  const issues = (order.issues ?? []).filter((issue) => issue.status === 'OPEN');
  if (!issues.length) return null;
  const paid = isConfirmedCustomerPayment(order.payment_status);
  return <section className="order-issues"><strong>{t('order.shortageChoose')}</strong><p>{t('order.shortageCancelHint')}</p>{issues.map((issue) => <div className="order-issue" key={issue.id}><p>{issue.note || t('order.shortagePending')}</p><div className="actions"><button type="button" onClick={() => onResolve(issue, 'REMOVE_ITEM')}>{t('order.shortageRemove')}</button>{!paid && issue.replacement_name && <button type="button" onClick={() => onResolve(issue, 'REPLACE_ITEM')}>{t('order.shortageReplace')}: {issue.replacement_name}</button>}</div></div>)}</section>;
}

export function CustomerOrderPaymentProofAction({ order, onOpen }: { order: CustomerOrderDetails; onOpen: (url: string) => void }) {
  const { t } = useTranslation();
  const blocked = (order.issues ?? []).some((issue) => issue.status === 'OPEN');
  if (!order.bot_deeplink) return <p>{t('payment.botNotConfigured')}</p>;
  return <><button type="button" className="primary payment-link" disabled={blocked} onClick={() => onOpen(order.bot_deeplink!)}>{t('payment.sendProof')}</button>{blocked && <p className="notice" role="status">{t('order.shortageProofBlocked')}</p>}</>;
}

export function CustomerOrderIssueBadge({ count }: { count: number }) {
  const { t } = useTranslation();
  return count > 0 ? <span className="customer-order-issue-badge" aria-label={t('order.pendingShortageBadge')}>⚠ {t('order.pendingShortageBadge')}</span> : null;
}
function LanguageSwitch() {
  const { i18n, t } = useTranslation();
  return <select aria-label={t('common.language')} value={i18n.language} onChange={(event) => {
    const language = event.target.value;
    localStorage.setItem('teacafe.language', language);
    void i18n.changeLanguage(language);
  }}><option value="en">English</option><option value="zh-CN">中文</option><option value="km">ខ្មែរ</option></select>;
}

function CustomerPage() {
  const { t, i18n } = useTranslation();
  const [activeTab, setActiveTab] = useState<'menu' | 'cart' | 'orders' | 'me'>('menu');
  const [categories, setCategories] = useState<Category[]>([]);
  const [selectedCategoryId, setSelectedCategoryId] = useState<number | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [optionsProduct, setOptionsProduct] = useState<Product | null>(null);   // 规格弹窗当前商品
  const [room, setRoom] = useState('');
  const [orderDetails, setOrderDetails] = useState<CustomerOrderDetails | null>(null);
  const [orderLoading, setOrderLoading] = useState(false);
  const [languageDraft, setLanguageDraft] = useState<LanguageCode>('en');
  const [languageSaving, setLanguageSaving] = useState(false);
  const [languageError, setLanguageError] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [customerOrders, setCustomerOrders] = useState<CustomerOrder[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [ordersError, setOrdersError] = useState('');
  const [profile, setProfile] = useState<CustomerProfile | null>(null);
  // 购物车按 Telegram 用户隔离保存，关闭 Mini App 后再次进入仍可恢复。
  const [cartOwnerId, setCartOwnerId] = useState<number | null>(null);
  // 钱包余额（最小单位）。结算时自动优先抵扣，剩余金额走 ABA。
  const [walletTotal, setWalletTotal] = useState(0);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [profileError, setProfileError] = useState('');
  const [miniAppShareUrl, setMiniAppShareUrl] = useState<string | null>(null);
  const [customerCoupons, setCustomerCoupons] = useState<CustomerCoupon[]>([]);
  const [selectedCouponId, setSelectedCouponId] = useState<number | null>(null);
  const [couponClaimNotice, setCouponClaimNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const idempotencyKey = useRef<string | null>(null);
  const claimedStartParam = useRef<string | null>(null);
  const roomLoadedFor = useRef<number | null>(null);
  const orderCloseRef = useRef<HTMLButtonElement>(null);
  const languageSelectRef = useRef<HTMLSelectElement>(null);
  const cartSubtotal = useMemo(() => cart.reduce((sum, line) => sum + line.unitPrice * line.quantity, 0), [cart]);
  const selectedCoupon = customerCoupons.find((coupon) => coupon.id === selectedCouponId && coupon.status === 'AVAILABLE');
  const couponDiscountInfo = useMemo(() => {
    if (!selectedCoupon || !cart.length || selectedCoupon.currency !== cart[0].product.currency || cartSubtotal < selectedCoupon.min_spend_minor) {
      return { amount: 0, capped: false, cap: null as number | null };
    }
    const raw = selectedCoupon.coupon_type === 'DISCOUNT'
      ? Math.round(cartSubtotal * (selectedCoupon.discount_percent || 0) / 100)
      : (selectedCoupon.fixed_discount_minor || 0);
    const cap = selectedCoupon.discount_cap_minor ?? null;
    return {
      amount: Math.min(Math.max(raw, 0), cartSubtotal, cap ?? Number.MAX_SAFE_INTEGER),
      capped: cap !== null && raw > cap,
      cap,
    };
  }, [cartSubtotal, cart, selectedCoupon]);
  const couponDiscount = couponDiscountInfo.amount;
  const total = Math.max(cartSubtotal - couponDiscount, 0);
  const walletApplied = Math.min(walletTotal, total);
  const abaDue = Math.max(total - walletApplied, 0);
  const itemCount = useMemo(() => cart.reduce((count, line) => count + line.quantity, 0), [cart]);
  const miniAppShareActionUrl = useMemo(() => miniAppShareUrl
    ? `https://t.me/share/url?url=${encodeURIComponent(miniAppShareUrl)}&text=${encodeURIComponent(t('menu.shareText'))}`
    : null, [miniAppShareUrl, t]);
  const selectedCategory = categories.find((category) => category.id === selectedCategoryId) ?? categories[0];
  const orderDialogOpen = orderDetails !== null;
  const languageDialogOpen = profileLoaded && Boolean(profile) && !profile?.preferred_language;

  const loadMenu = useCallback(() => api.get('/api/v1/menu').then((response) => setCategories(response.data)).catch(() => setError(t('error.network'))).finally(() => setLoading(false)), [t]);
  const loadWalletTotal = useCallback(async () => {
    try {
      const response = await api.get('/api/v1/wallet');
      setWalletTotal(response.data.total_minor);
    } catch {
      // 钱包接口失败不影响点单主流程，降级为全额 ABA。
      setWalletTotal(0);
    }
  }, []);

  const loadCustomerCoupons = useCallback(async () => {
    try {
      const response = await api.get('/api/v1/coupons/mine');
      setCustomerCoupons(response.data);
    } catch {
      // Coupon loading is optional and must not block menu browsing.
    }
  }, []);

  useEffect(() => { void loadMenu(); }, [loadMenu]);
  useEffect(() => { void loadWalletTotal(); }, [loadWalletTotal]);
  useEffect(() => {
    void api.get('/api/v1/public/mini-app-share')
      .then((response) => setMiniAppShareUrl(response.data.url || null))
      .catch(() => setMiniAppShareUrl(null));
  }, []);

  const loadCustomerOrders = useCallback(async () => {
    setOrdersLoading(true);
    setOrdersError('');
    try {
      const response = await api.get('/api/v1/orders');
      setCustomerOrders(response.data);
    } catch {
      setOrdersError(t('error.network'));
    } finally {
      setOrdersLoading(false);
    }
  }, [t]);

  const loadProfile = useCallback(async () => {
    try {
      const response = await api.get('/api/v1/auth/me');
      const customerProfile = response.data as CustomerProfile;
      setProfile(customerProfile);
      if (roomLoadedFor.current !== customerProfile.customer_id) {
        setRoom(localStorage.getItem(`teacafe.roomNumber.${customerProfile.customer_id}`) || '');
        roomLoadedFor.current = customerProfile.customer_id;
      }
      if (!customerProfile.preferred_language) setLanguageDraft(customerProfile.language);
    } catch {
      setProfileError(t('error.network'));
    } finally {
      setProfileLoading(false);
      setProfileLoaded(true);
    }
  }, [t]);
  useEffect(() => { void loadProfile(); }, [loadProfile]);
  useEffect(() => {
    const customerId = profile?.customer_id;
    if (!customerId) return;
    setCartOwnerId(null);
    try {
      const stored = localStorage.getItem(`teacafe.cart.${customerId}`);
      const parsed = stored ? JSON.parse(stored) : [];
      const restored = Array.isArray(parsed) ? parsed.filter((line) => (
        line && line.product && Number.isFinite(line.product.id)
        && Number.isInteger(line.quantity) && line.quantity > 0
        && Number.isFinite(line.unitPrice)
        && (line.sweetness === null || Number.isFinite(line.sweetness))
        && Array.isArray(line.selections)
      )).map((line) => ({
        ...line,
        quantity: Math.min(99, line.quantity),
        selections: line.selections.filter((selection: CartSelection) => (
          Number.isFinite(selection?.group_id) && Number.isFinite(selection?.option_id)
        )),
      })) : [];
      setCart(restored);
    } catch {
      // 损坏或不可用的本地缓存不应阻止用户继续点餐。
      setCart([]);
    }
    setCartOwnerId(customerId);
  }, [profile?.customer_id]);
  useEffect(() => {
    const customerId = profile?.customer_id;
    if (!customerId || cartOwnerId !== customerId) return;
    try {
      localStorage.setItem(`teacafe.cart.${customerId}`, JSON.stringify(cart));
    } catch {
      // 浏览器存储空间不足时仍保留当前会话内的购物车。
    }
  }, [cart, cartOwnerId, profile?.customer_id]);
  useEffect(() => { void loadCustomerCoupons(); }, [loadCustomerCoupons]);
  useEffect(() => {
    let active = true;
    void waitForTelegramStartParam().then((startParam) => {
      if (!active || !startParam || claimedStartParam.current === startParam) return;
      claimedStartParam.current = startParam;
      const requestKey = crypto.randomUUID();
      void api.post('/api/v1/coupons/claim', { code: startParam }, { headers: { 'Idempotency-Key': requestKey } })
        .then((response) => {
          if (!active) return;
          const message = response.data?.coupon?.already_claimed ? t('coupon.alreadyClaimed') : t('coupon.claimSuccess');
          setCouponClaimNotice({ kind: 'success', text: `${message}: ${response.data?.coupon?.name || ''}` });
          void loadCustomerCoupons();
        })
        .catch((requestError: any) => {
          const detail = requestError?.response?.data?.detail;
          const message = detail === 'Coupon has been fully claimed' ? t('coupon.fullyClaimed') : t('coupon.claimFailed');
          if (active) setCouponClaimNotice({ kind: 'error', text: message });
        });
    });
    return () => { active = false; };
  }, [loadCustomerCoupons, t]);

  useEffect(() => {
    if (!orderDialogOpen) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    orderCloseRef.current?.focus();
    const dialog = orderCloseRef.current?.closest('.order-modal');
    const focusable = dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]');
    const first = focusable?.[0];
    const last = focusable?.[focusable.length - 1];
    const handleDialogKeys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOrderDetails(null);
      if (event.key === 'Tab' && event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (event.key === 'Tab' && !event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener('keydown', handleDialogKeys);
    return () => {
      document.removeEventListener('keydown', handleDialogKeys);
      previousFocus?.focus();
    };
  }, [orderDialogOpen]);

  useEffect(() => {
    if (!languageDialogOpen) return;
    const dialog = languageSelectRef.current?.closest('.language-modal');
    const focusable = dialog?.querySelectorAll<HTMLElement>('select, button:not(:disabled)');
    const first = focusable?.[0];
    const last = focusable?.[focusable.length - 1];
    languageSelectRef.current?.focus();
    const keepDialogModal = (event: KeyboardEvent) => {
      if (event.key === 'Escape') event.preventDefault();
      if (event.key === 'Tab' && event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (event.key === 'Tab' && !event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener('keydown', keepDialogModal);
    return () => document.removeEventListener('keydown', keepDialogModal);
  }, [languageDialogOpen]);

  const selectTab = (tab: 'menu' | 'cart' | 'orders' | 'me') => {
    setActiveTab(tab);
    if (tab === 'orders' || tab === 'me') void loadCustomerOrders();
    if (tab === 'me') {
      setProfileLoading(true);
      setProfileError('');
      void loadProfile();
    }
    if (tab === 'cart' || tab === 'me') void loadCustomerCoupons();
  };

  const openCustomerOrder = async (publicCode: string) => {
    setOrdersError('');
    setOrderLoading(true);
    try {
      const response = await api.get(`/api/v1/orders/${publicCode}`);
      setOrderDetails(response.data as CustomerOrderDetails);
    } catch {
      setOrdersError(t('error.network'));
    } finally {
      setOrderLoading(false);
    }
  };

  // 有规格/附加、或者需要选甜度的商品，点加购时先弹窗配置
  const needsConfig = (product: Product) =>
    (product.option_groups ?? []).length > 0 || product.sweetness_enabled;

  const toSelections = (picked: Picked): CartSelection[] =>
    Object.entries(picked).flatMap(([groupId, optionIds]) =>
      (optionIds ?? []).map((optionId) => ({ group_id: Number(groupId), option_id: optionId })));

  // 购物车行按「菜品 + 甜度 + 规格组合」区分：同菜不同规格必须是两行
  const cartLineKey = (line: CartLine) => `${line.product.id}:${line.sweetness ?? 'd'}:${line.selections.map((x) => x.option_id).sort((a, b) => a - b).join(',')}`;

  // 购物车里把已选规格显示出来（按界面语言取三语名称），否则顾客没法确认自己选了什么
  const localizeName = (name?: Record<string, string>, fallback?: string) => {
    const lang = i18n.language || 'en';
    return name?.[lang] || name?.[lang.split('-')[0]] || name?.['zh-CN'] || name?.en || fallback || '';
  };
  const selectedNames = (line: CartLine) => line.selections.map((selection) => {
    const group = (line.product.option_groups ?? []).find((item) => item.id === selection.group_id);
    const option = group?.options.find((item) => item.id === selection.option_id);
    return option ? localizeName(option.name, option.name_text) : '';
  }).filter(Boolean);

  // 老接口缓存里没有 orderable 字段，缺省按可售处理，避免把菜单全灰掉
  const orderable = (product: Product) => product.orderable !== false && product.available !== false;

  const saleWindowNote = (product: Product) => {
    const windows = product.sale_windows ?? [];
    if (!windows.length) return t('common.soldOut');
    const hours = windows.map((w) => `${w.start}-${w.end}`).join(' / ');
    if (product.next_sale_start) {
      const next = new Date(product.next_sale_start);
      const hhmm = `${String(next.getHours()).padStart(2, '0')}:${String(next.getMinutes()).padStart(2, '0')}`;
      return t('menu.saleWindowNext', { hours, time: hhmm });
    }
    return t('menu.saleWindow', { hours });
  };

  // 把一行配置好的商品放进购物车（同菜不同规格分行）
  const pushLine = (product: Product, selections: CartSelection[], sweetness: number | null, unitPrice: number, quantity: number) => {
    setStatus('');
    idempotencyKey.current = null;
    setCart((current) => {
      const line: CartLine = { product, quantity, sweetness, selections, unitPrice };
      const key = cartLineKey(line);
      const found = current.find((item) => cartLineKey(item) === key);
      return found
        ? current.map((item) => cartLineKey(item) === key ? { ...item, quantity: Math.min(99, item.quantity + quantity) } : item)
        : [...current, line];
    });
  };

  const guardCurrency = (product: Product) => {
    if (cart.length && cart[0].product.currency !== product.currency) {
      setStatus(t('cart.mixedCurrency'));
      return false;
    }
    return true;
  };

  // 无规格、不需要选甜度的商品：一键加购
  const add = (product: Product) => {
    if (!guardCurrency(product)) return;
    pushLine(product, [], null, product.price_minor, 1);
  };

  // 弹窗确认：规格/附加/甜度都在这里定下来
  const confirmOptions = (product: Product, picked: Picked, sweetness: number | null, quantity: number) => {
    if (!guardCurrency(product)) { setOptionsProduct(null); return; }
    const delta = (product.option_groups ?? []).reduce((sum, group) => sum
      + group.options.filter((option) => (picked[group.id] ?? []).includes(option.id))
          .reduce((inner, option) => inner + option.price_delta_minor, 0), 0);
    pushLine(product, toSelections(picked), sweetness, product.price_minor + delta, quantity);
    setOptionsProduct(null);
  };

  const setQuantity = (key: string, quantity: number) => {
    idempotencyKey.current = null;
    setCart((current) => quantity < 1 ? current.filter((line) => cartLineKey(line) !== key) : current.map((line) => cartLineKey(line) === key ? { ...line, quantity: Math.min(99, quantity) } : line));
  };

  // 普通商品在菜单卡片上直接显示当前数量；有规格的商品仍通过规格弹窗区分不同组合。
  const simpleCartLine = (product: Product) => cart.find((line) =>
    line.product.id === product.id && line.sweetness === null && line.selections.length === 0);
  const simpleQuantity = (product: Product) => simpleCartLine(product)?.quantity ?? 0;
  const adjustSimpleQuantity = (product: Product, delta: number) => {
    const line = simpleCartLine(product);
    if (delta > 0) {
      add(product);
    } else if (line) {
      setQuantity(cartLineKey(line), line.quantity - 1);
    }
  };

  // 多规格商品可能在购物车中有多个配置组合，菜单卡片显示它们的合计数量。
  // 加号再次打开规格弹窗，减号减少最近加入的配置组合。
  const configuredCartLines = (product: Product) => cart.filter((line) =>
    line.product.id === product.id && (line.sweetness !== null || line.selections.length > 0));
  const configuredQuantity = (product: Product) => configuredCartLines(product)
    .reduce((sum, line) => sum + line.quantity, 0);
  const adjustConfiguredQuantity = (product: Product, delta: number) => {
    const lines = configuredCartLines(product);
    if (delta > 0) {
      setOptionsProduct(product);
      return;
    }
    const lastLine = lines[lines.length - 1];
    if (lastLine) setQuantity(cartLineKey(lastLine), lastLine.quantity - 1);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!room.trim()) { setStatus(t('checkout.roomRequired')); return; }
    if (!idempotencyKey.current) idempotencyKey.current = crypto.randomUUID();
    try {
      setStatus(t('checkout.submitting'));
      const response = await api.post('/api/v1/orders', {
        room_number: room.trim(),
        items: cart.map((line) => ({
          product_id: line.product.id,
          quantity: line.quantity,
          // 规格只传 id：价格由服务端回库算，前端算的金额仅用于展示
          options: { ...(line.sweetness === null ? {} : { sweetness: line.sweetness }), selections: line.selections },
        })),
        // 钱包优先：后端在同一事务内扣除可用余额，余额不足时只把差额交给 ABA。
        pay_with_wallet: true,
        customer_coupon_id: selectedCouponId,
      }, { headers: { 'Idempotency-Key': idempotencyKey.current } });
      setCart([]);
      setSelectedCouponId(null);
      idempotencyKey.current = null;
      setStatus('');
      setActiveTab('orders');
      await loadCustomerOrders();
      await openCustomerOrder(response.data.public_code);
    } catch (requestError: any) {
      const status = requestError?.response?.status;
      const detail: string = requestError?.response?.data?.detail || '';
      if (status === 409) idempotencyKey.current = null;
      if (detail.includes('余额')) {
        setStatus(t('wallet.balanceInsufficient'));
      } else if (selectedCouponId && detail === 'Coupon has been fully claimed') {
        setStatus(t('coupon.fullyClaimed'));
      } else if (selectedCouponId && (detail.includes('expired') || detail.includes('not available') || detail.includes('not started'))) {
        setStatus(t('coupon.unavailable'));
      } else if (selectedCouponId && detail.includes('minimum spend')) {
        setStatus(t('coupon.notApplicable'));
      } else {
        setStatus(status === 409 ? t('cart.priceChanged') : t('checkout.failed'));
      }
    } finally {
      void loadWalletTotal();
    }
  };

  const refreshStatus = async () => {
    if (!orderDetails) return;
    await openCustomerOrder(orderDetails.public_code);
    void loadCustomerOrders();
  };

  const cancelCustomerOrder = async () => {
    if (!orderDetails || !window.confirm(t('order.cancelConfirm'))) return;
    const openIssues = (orderDetails.issues ?? []).filter((issue) => issue.status === 'OPEN');
    try {
      if (openIssues.length > 0) {
        // Paid shortage cancellations use the existing issue-resolution rule;
        // unpaid new orders use the regular customer cancellation endpoint.
        await api.post(`/api/v1/orders/${orderDetails.public_code}/issues/${openIssues[0].id}/resolve`, { resolution: 'CANCEL_ORDER' });
      } else {
        await api.post(`/api/v1/orders/${orderDetails.public_code}/cancel`);
      }
      await openCustomerOrder(orderDetails.public_code);
      void loadCustomerOrders();
    } catch (requestError: any) {
      setOrdersError(requestError?.response?.data?.detail || t('error.generic'));
    }
  };

  const resolveCustomerIssue = async (issue: OrderIssue, resolution: 'REMOVE_ITEM' | 'REPLACE_ITEM' | 'CANCEL_ORDER') => {
    if (!orderDetails) return;
    try {
      await api.post(`/api/v1/orders/${orderDetails.public_code}/issues/${issue.id}/resolve`, { resolution });
      await openCustomerOrder(orderDetails.public_code);
      void loadCustomerOrders();
    } catch (requestError: any) {
      setOrdersError(requestError?.response?.data?.detail || t('error.generic'));
    }
  };

  const saveLanguage = async (language = languageDraft) => {
    setLanguageSaving(true);
    setLanguageError('');
    try {
      const response = await api.patch('/api/v1/auth/me/language', { language });
      localStorage.setItem('teacafe.language', response.data.language);
      await i18n.changeLanguage(response.data.language);
      setProfile((current) => current ? { ...current, language: response.data.language, preferred_language: response.data.preferred_language } : current);
    } catch {
      setLanguageError(t('language.saveFailed'));
    } finally {
      setLanguageSaving(false);
    }
  };

  const paymentLabel = (paymentStatus: string) => paymentStatus === 'PAID_CONFIRMED' ? t('payment.confirmed') : paymentStatus === 'REFUNDED' ? t('order.status.refunded') : paymentStatus === 'PARTIALLY_REFUNDED' ? t('payment.partiallyRefunded') : paymentStatus === 'PROOF_SUBMITTED' ? t('payment.pending') : paymentStatus === 'REJECTED' ? t('payment.rejected') : t('order.status.unpaid');
  const orderStatusClass = (orderStatus: string) => `customer-order-status status-${orderStatus.toLowerCase()}`;

  return <main className="customer-shell">
    <header className="topbar"><div className="brand-title"><img className="brand-logo" src="/tea-cafe-logo.png" alt="Tea Cafe" /><div><span className="eyebrow">TEA CAFE</span><h1>{t(activeTab === 'menu' ? 'menu.title' : activeTab === 'cart' ? 'cart.title' : activeTab === 'orders' ? 'order.history' : 'profile.title')}</h1></div></div><div className="customer-header-actions">{activeTab === 'menu' && miniAppShareActionUrl && <a className="share-shortcut" href={miniAppShareActionUrl} aria-label={t('menu.share')} title={t('menu.share')}><Share2 size={17} aria-hidden="true" /><span>{t('menu.share')}</span></a>}{activeTab === 'cart' && <strong className="cart-count">{itemCount}</strong>}</div></header>
    {couponClaimNotice && <div className={`coupon-claim-notice ${couponClaimNotice.kind}`} role="status"><span>{couponClaimNotice.text}</span><button type="button" aria-label={t('common.close')} onClick={() => setCouponClaimNotice(null)}>×</button></div>}

    {activeTab === 'menu' && <>
      <section className="hero"><p className="eyebrow">{t('menu.popular')}</p><h2>{t('brand')}</h2><p>{t('checkout.roomOnly')}</p></section>
      {loading && <p className="state">{t('common.loading')}</p>}{error && <p className="error">{error}</p>}
      {!loading && !error && categories.length === 0 && <section className="state empty-menu" role="status"><h2>{t('menu.emptyTitle')}</h2><p>{t('menu.emptyDescription')}</p></section>}
      <section className="menu-browser" aria-label={t('menu.title')}>
        <nav className="category-rail" aria-label={t('menu.category')}>
          {categories.map((category) => <button key={category.id} type="button" className={`category-rail-item${selectedCategory?.id === category.id ? ' active' : ''}`} aria-current={selectedCategory?.id === category.id ? 'true' : undefined} onClick={() => setSelectedCategoryId(category.id)}>
            <span>{label(category.name, i18n.language)}</span>
          </button>)}
        </nav>
        {selectedCategory && <section className="category-products" aria-labelledby={`category-${selectedCategory.id}`}>
          <h2 id={`category-${selectedCategory.id}`} className="category-products-title">{label(selectedCategory.name, i18n.language)}</h2>
          {(selectedCategory.products || []).length === 0 ? <p className="category-products-empty">{t('menu.emptyDescription')}</p> : <div className="menu-product-list">{(selectedCategory.products || []).map((product) => <article className="product-card menu-product-row" key={product.id}>
            {product.image_key ? <img className="product-image" src={product.image_key} alt={label(product.name, i18n.language)} /> : <div className="product-art">{label(product.name, i18n.language).slice(0, 1)}</div>}
            <div className="product-copy"><h3>{label(product.name, i18n.language)}</h3><p>{label(product.description, i18n.language)}</p></div>
            {orderable(product) === false && <p className="product-window-note">{saleWindowNote(product)}</p>}
            <div className="product-foot">
              <strong>{amount(product.price_minor, product.currency, i18n.language)}</strong>
              {orderable(product)
                ? needsConfig(product)
                  ? configuredQuantity(product) > 0
                    ? <div className="menu-quantity-control" role="group" aria-label={t('common.quantity')}>
                        <button type="button" aria-label={t('menu.decreaseQuantity')} title={t('menu.decreaseQuantity')} onClick={() => adjustConfiguredQuantity(product, -1)}>−</button>
                        <strong aria-live="polite">{configuredQuantity(product)}</strong>
                        <button type="button" aria-label={t('menu.increaseQuantity')} title={t('menu.increaseQuantity')} onClick={() => adjustConfiguredQuantity(product, 1)}>+</button>
                      </div>
                    : <button className="multi-option-button" type="button" aria-label={t('menu.multiOptions')} title={t('menu.multiOptions')} onClick={() => setOptionsProduct(product)}>{t('menu.multiOptions')}</button>
                  : simpleQuantity(product) > 0
                    ? <div className="menu-quantity-control" role="group" aria-label={t('common.quantity')}>
                        <button type="button" aria-label={t('menu.decreaseQuantity')} title={t('menu.decreaseQuantity')} onClick={() => adjustSimpleQuantity(product, -1)}>−</button>
                        <strong aria-live="polite">{simpleQuantity(product)}</strong>
                        <button type="button" aria-label={t('menu.increaseQuantity')} title={t('menu.increaseQuantity')} onClick={() => adjustSimpleQuantity(product, 1)}>+</button>
                      </div>
                    : <button className="product-add-button" type="button" aria-label={t('menu.addToCart')} title={t('menu.addToCart')} onClick={() => adjustSimpleQuantity(product, 1)}>+</button>
                : <span className="product-closed" aria-label={t('menu.notOrderable')}>{t('menu.notOrderableShort')}</span>}
            </div>
          </article>)}</div>}
        </section>}
      </section>
      {status && <p className="status" role="status">{status}</p>}
    </>}

    {activeTab === 'cart' && <section className="customer-page-section cart-page">
      <div className="customer-page-heading"><span className="eyebrow">{t('cart.title')}</span><h2>{itemCount ? `${itemCount} ${t('common.quantity')}` : t('cart.empty')}</h2></div>
      {!cart.length ? <div className="cart-empty"><p>{t('cart.empty')}</p><button className="primary" type="button" onClick={() => selectTab('menu')}>{t('cart.startShopping')}</button></div> : <form className="checkout-card cart-checkout" onSubmit={submit}>
        <div className="cart-lines">{cart.map((line) => <div className="cart-line" key={cartLineKey(line)}><span>{label(line.product.name, i18n.language)}{line.sweetness !== null && <small>{t('menu.sweetness')}: {line.sweetness}%</small>}{selectedNames(line).length > 0 && <small>{selectedNames(line).join(' + ')}</small>}{line.quantity > 1 && <small>{amount(line.unitPrice, line.product.currency, i18n.language)} × {line.quantity}</small>}<small>{amount(line.unitPrice * line.quantity, line.product.currency, i18n.language)}</small></span><div className="quantity-control"><button type="button" aria-label={t('common.delete')} onClick={() => setQuantity(cartLineKey(line), line.quantity - 1)}>−</button><strong>{line.quantity}</strong><button type="button" aria-label={t('common.add')} onClick={() => setQuantity(cartLineKey(line), line.quantity + 1)}>+</button></div></div>)}</div>
        <button className="continue-shopping" type="button" onClick={() => selectTab('menu')}>{t('menu.addMore')}</button>
        <label htmlFor="room">{t('checkout.roomNumber')}</label><input id="room" value={room} onChange={(event) => { idempotencyKey.current = null; setRoom(event.target.value); if (profile) localStorage.setItem(`teacafe.roomNumber.${profile.customer_id}`, event.target.value); }} autoComplete="off" maxLength={32} required />
        <label htmlFor="coupon-select">{t('coupon.selectCoupon')}<select id="coupon-select" value={selectedCouponId ?? ''} onChange={(event) => setSelectedCouponId(event.target.value ? Number(event.target.value) : null)}><option value="">{t('coupon.noCoupon')}</option>{customerCoupons.filter((coupon) => coupon.status === 'AVAILABLE' && coupon.currency === cart[0].product.currency).map((coupon) => <option key={coupon.id} value={coupon.id}>{coupon.name} · {coupon.coupon_type === 'DISCOUNT' ? `${coupon.discount_percent}%` : amount(coupon.fixed_discount_minor || 0, coupon.currency, i18n.language)}{coupon.min_spend_minor ? ` · ${t('coupon.minimumSpend')} ${amount(coupon.min_spend_minor, coupon.currency, i18n.language)}` : ''}</option>)}</select></label>
        {selectedCoupon && couponDiscount === 0 && <small className="error">{t('coupon.notApplicable')}</small>}
        <fieldset className="payment-method-picker">
          <legend>{t('payment.chooseMethod')}</legend>
          <div className="payment-method-breakdown">
            <div><span>{t('payment.method.wallet')}</span><strong>{amount(walletApplied, cart[0].product.currency, i18n.language)}</strong></div>
            <div><span>{t('payment.method.aba')}</span><strong>{amount(abaDue, cart[0].product.currency, i18n.language)}</strong></div>
          </div>
          <small className="payment-method-hint">{t('payment.walletFirstHint')}</small>
        </fieldset>
        {couponDiscount > 0 && <div className="payment-breakdown-line coupon-discount"><span>{t('coupon.couponDiscount')}{couponDiscountInfo.capped && couponDiscountInfo.cap !== null && <small className="coupon-cap-hint">{t('coupon.discountCapHint', { amount: amount(couponDiscountInfo.cap, cart[0].product.currency, i18n.language) })}</small>}</span><strong>-{amount(couponDiscount, cart[0].product.currency, i18n.language)}</strong></div>}
        <div className="total-row"><span>{t('common.total')}</span><strong>{amount(total, cart[0].product.currency, i18n.language)}</strong></div><button className="primary" type="submit">{t('checkout.placeOrder')}</button>
        {status && <p className="status" role="status">{status}</p>}
      </form>}
    </section>}

    {activeTab === 'orders' && <section className="customer-page-section">
      <div className="customer-page-heading"><span className="eyebrow">{t('order.history')}</span><h2>{t('nav.orders')}</h2></div>
      {ordersLoading && <p className="state" role="status">{t('common.loading')}</p>}
      {ordersError && <p className="error" role="alert">{ordersError} <button type="button" onClick={() => void loadCustomerOrders()}>{t('common.retry')}</button></p>}
      {!ordersLoading && !ordersError && customerOrders.length === 0 && <p className="state empty-menu">{t('order.empty')}</p>}
      <div className="customer-orders">{customerOrders.map((customerOrder) => <button type="button" className="customer-order-card" key={customerOrder.public_code} onClick={() => void openCustomerOrder(customerOrder.public_code)}>
        <div className="customer-order-heading"><strong>{t('payment.order')} {customerOrder.public_code}</strong><span className={orderStatusClass(customerOrder.order_status)}>{t(`order.status.${customerOrder.order_status.toLowerCase()}`)}</span></div>
        <CustomerOrderIssueBadge count={customerOrder.open_issue_count ?? 0} />
        <div className="customer-order-meta"><span>{t('order.room')}: {customerOrder.room_number}</span><time dateTime={customerOrder.created_at}>{new Date(customerOrder.created_at).toLocaleString(i18n.language, { dateStyle: 'medium', timeStyle: 'short' })}</time></div>
        <div className="customer-order-total"><span>{paymentLabel(customerOrder.payment_status)}</span><strong>{amount(customerOrder.total_minor, customerOrder.currency, i18n.language)}</strong></div>
      </button>)}</div>
    </section>}

    {optionsProduct && <ProductOptionsModal
      product={optionsProduct}
      currency={optionsProduct.currency}
      language={i18n.language}
      orderable={orderable(optionsProduct)}
      note={saleWindowNote(optionsProduct)}
      onClose={() => setOptionsProduct(null)}
      onConfirm={(picked, sweetness, quantity) => confirmOptions(optionsProduct, picked, sweetness, quantity)}
    />}
    {activeTab === 'me' && <section className="customer-page-section customer-profile">
      <div className="customer-page-heading"><span className="eyebrow">{t('profile.telegramConnected')}</span></div>
      {profileLoading && <p className="state" role="status">{t('common.loading')}</p>}
      {profileError && <p className="error" role="alert">{profileError} <button type="button" onClick={() => { setProfileLoading(true); setProfileError(''); void loadProfile(); }}>{t('common.retry')}</button></p>}
      {ordersLoading && <p className="state" role="status">{t('common.loading')}</p>}
      {ordersError && <p className="error" role="alert">{ordersError} <button type="button" onClick={() => void loadCustomerOrders()}>{t('common.retry')}</button></p>}
      {profile && <>
        <section className="profile-identity"><UserRound size={28} aria-hidden="true" /><div><strong>{profile.display_name || profile.username || 'Tea Cafe'}</strong>{profile.username && <span>@{profile.username}</span>}</div></section>
        {!ordersLoading && !ordersError && <div className="profile-stats"><article><span>{t('profile.orderCount')}</span><strong>{customerOrders.length}</strong></article><article><span>{t('profile.completedOrders')}</span><strong>{customerOrders.filter((customerOrder) => customerOrder.order_status === 'COMPLETED').length}</strong></article></div>}
        <CustomerWallet onBalanceChange={() => void loadWalletTotal()} />
        <section className="coupon-list"><div className="customer-page-heading"><span className="eyebrow">{t('coupon.myCoupons')}</span><h2>{t('coupon.title')}</h2></div>{customerCoupons.length === 0 ? <p className="state">{t('coupon.noCoupon')}</p> : customerCoupons.map((coupon) => <article className={`coupon-card coupon-${coupon.status.toLowerCase()}`} key={coupon.id}><div><strong>{coupon.name}</strong><small>{coupon.description || (coupon.coupon_type === 'DISCOUNT' ? `${coupon.discount_percent}% OFF` : `${t('coupon.fixed')}: ${amount(coupon.fixed_discount_minor || 0, coupon.currency, i18n.language)}`)}</small></div><span>{t(`coupon.status.${coupon.status.toLowerCase()}`, { defaultValue: coupon.status })}</span>{coupon.status === 'AVAILABLE' && <button type="button" onClick={() => { setSelectedCouponId(coupon.id); selectTab('cart'); }}>{t('coupon.goUse')}</button>}</article>)}</section>
        <label className="profile-language">{t('common.language')}<select aria-label={t('common.language')} value={profile.preferred_language || languageDraft} disabled={languageSaving} onChange={(event) => { const language = event.target.value as LanguageCode; setLanguageDraft(language); void saveLanguage(language); }}><option value="en">English</option><option value="zh-CN">中文</option><option value="km">ខ្មែរ</option></select></label>
        {languageError && <p className="error" role="alert">{languageError}</p>}
      </>}
    </section>}

    <nav className="customer-bottom-nav" aria-label={t('common.navigation')}>
      <button type="button" className={activeTab === 'menu' ? 'active' : ''} aria-current={activeTab === 'menu' ? 'page' : undefined} onClick={() => selectTab('menu')}><Store size={20} aria-hidden="true" /><span>{t('nav.menu')}</span></button>
      <button type="button" className={activeTab === 'cart' ? 'active' : ''} aria-current={activeTab === 'cart' ? 'page' : undefined} onClick={() => selectTab('cart')}><span className="nav-icon-wrap"><ShoppingCart size={20} aria-hidden="true" />{itemCount > 0 && <span className="cart-badge">{itemCount > 99 ? '99+' : itemCount}</span>}</span><span>{t('nav.cart')}</span></button>
      <button type="button" className={activeTab === 'orders' ? 'active' : ''} aria-current={activeTab === 'orders' ? 'page' : undefined} onClick={() => selectTab('orders')}><ClipboardList size={20} aria-hidden="true" /><span>{t('nav.orders')}</span></button>
      <button type="button" className={activeTab === 'me' ? 'active' : ''} aria-current={activeTab === 'me' ? 'page' : undefined} onClick={() => selectTab('me')}><UserRound size={20} aria-hidden="true" /><span>{t('nav.me')}</span></button>
    </nav>

    {orderLoading && <div className="order-modal-backdrop" role="presentation"><section className="order-modal" role="status">{t('common.loading')}</section></div>}
    {orderDetails && <div className="order-modal-backdrop" role="presentation" onClick={() => setOrderDetails(null)}><section className="order-modal" role="dialog" aria-modal="true" aria-labelledby="order-modal-title" onClick={(event) => event.stopPropagation()}>
      <div className="order-modal-header"><div><span className="eyebrow">{t('payment.title')}</span><h2 id="order-modal-title">{t('payment.order')} {orderDetails.public_code}</h2></div><button ref={orderCloseRef} type="button" aria-label={t('common.close')} onClick={() => setOrderDetails(null)}>×</button></div>
      <div className="customer-order-heading"><strong className={orderStatusClass(orderDetails.order_status)}>{t(`order.status.${orderDetails.order_status.toLowerCase()}`)}</strong><span className={`customer-payment-status payment-status-${orderDetails.payment_status.toLowerCase()}`}>{paymentLabel(orderDetails.payment_status)}</span></div>
      <p>{t('order.room')}: <strong>{orderDetails.room_number}</strong></p>
      <div className="customer-order-items">{orderDetails.items.map((item, index) => <div key={`${orderDetails.public_code}:${index}`}><span>{typeof item.name === 'string' ? item.name : label(item.name, i18n.language)} × {item.quantity}{item.options?.sweetness !== undefined && <small>{t('menu.sweetness')}: {item.options.sweetness}%</small>}</span><strong>{amount(item.line_total_minor, orderDetails.currency, i18n.language)}</strong></div>)}</div>
      <CustomerOrderPaymentSummary order={orderDetails} />
      <CustomerOrderShortageBlock order={orderDetails} onResolve={(issue, resolution) => void resolveCustomerIssue(issue, resolution)} />
      {!['PAID_CONFIRMED', 'REFUNDED', 'PARTIALLY_REFUNDED'].includes(orderDetails.payment_status) && <>
        {orderDetails.payment_qr_url && <img className="payment-qr" src={orderDetails.payment_qr_url} alt={t('payment.aba')} />}
        {orderDetails.payment_link && <a className="primary payment-link" href={orderDetails.payment_link} target="_blank" rel="noreferrer">{t('payment.openLink')}</a>}
        {!orderDetails.payment_qr_url && !orderDetails.payment_link && <p className="error">{t('payment.notConfigured')}</p>}
        <p>{t('payment.instruction')}</p>
        <CustomerOrderPaymentProofAction order={orderDetails} onOpen={openTelegramLink} />
      </>}
      {orderDetails.payment_status === 'REJECTED' && <p className="error">{t('payment.resubmit')}</p>}
      {((orderDetails.order_status === 'NEW' && ['UNPAID', 'REJECTED'].includes(orderDetails.payment_status)) || ((orderDetails.issues ?? []).some((issue) => issue.status === 'OPEN') && ['NEW', 'ACCEPTED', 'PREPARING'].includes(orderDetails.order_status))) && <button type="button" className="danger-button" onClick={() => void cancelCustomerOrder()}>{t('order.cancel')}</button>}
      <button type="button" onClick={() => void refreshStatus()}>{t('payment.refreshStatus')}</button>
    </section></div>}

    {languageDialogOpen && <div className="language-modal-backdrop"><section className="language-modal" role="dialog" aria-modal="true" aria-labelledby="language-modal-title"><img src="/tea-cafe-logo.png" alt="Tea Cafe" /><span className="eyebrow">TEA CAFE</span><h2 id="language-modal-title">{t('language.first.title')}</h2><p>{t('language.first.description')}</p><label>{t('common.language')}<select ref={languageSelectRef} value={languageDraft} onChange={(event) => setLanguageDraft(event.target.value as LanguageCode)}><option value="zh-CN">中文</option><option value="en">English</option><option value="km">ខ្មែរ</option></select></label>{languageError && <p className="error" role="alert">{languageError}</p>}<button className="primary" type="button" disabled={languageSaving} onClick={() => void saveLanguage()}>{languageSaving ? t('common.loading') : t('language.first.continue')}</button></section></div>}
    {profileLoaded && !profile && profileError && <div className="language-modal-backdrop"><section className="language-modal" role="alert"><h2>{t('error.network')}</h2><p>{profileError}</p><button type="button" onClick={() => { setProfileLoading(true); setProfileError(''); void loadProfile(); }}>{t('common.retry')}</button></section></div>}
  </main>;
}

function AdminLogin() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { setAdminAuth } = useAuthStore();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const response = await api.post('/api/v1/auth/admin/login', { login_name: login, password });
      setAdminAuth(String(response.data.staff_id), response.data.role, response.data.store_id ?? null, response.data.catalog_write_allowed ?? false);
      navigate('/admin');
    } catch { setError(t('auth.loginFailed')); } finally { setBusy(false); }
  };
  return <main className="auth-shell"><div className="auth-card"><img className="login-logo" src="/tea-cafe-logo.png" alt="Tea Cafe" /><span className="eyebrow">TEA CAFE · STAFF CONSOLE</span><h1>{t('auth.loginTitle')}</h1><p>{t('auth.loginDescription')}</p><form onSubmit={submit}><label htmlFor="login">{t('auth.loginId')}</label><input id="login" value={login} onChange={(event) => setLogin(event.target.value)} autoComplete="username" required /><label htmlFor="password">{t('auth.password')}</label><input id="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required />{error && <p className="error">{error}</p>}<button className="primary" type="submit" disabled={busy}>{busy ? t('common.loading') : t('auth.login')}</button></form><LanguageSwitch /></div></main>;
}


export default function App() {
  return <TelegramProvider><Routes><Route path="/" element={<CustomerPage />} /><Route path="/admin/login" element={<AdminLogin />} /><Route path="/admin/*" element={<AdminApp />} /></Routes></TelegramProvider>;
}
