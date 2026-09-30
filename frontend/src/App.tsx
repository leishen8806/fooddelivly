import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { Routes, Route, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from './api';
import { TelegramProvider } from './components/TelegramProvider';
import { useAuthStore } from './store/authStore';
import './index.css';

type Product = { id: number; category_id: number; name: Record<string, string>; description?: Record<string, string>; price_minor: number; currency: string; image_key?: string; available: boolean; sweetness_enabled: boolean };
type Category = { id: number; name: Record<string, string>; products?: Product[]; sort_order?: number; active?: boolean };
type CartLine = { product: Product; quantity: number; sweetness: number | null };
type PlacedOrder = { public_code: string; total_minor: number; currency: string; status: string; payment_status: string; payment_link?: string | null; payment_qr_url?: string | null; bot_deeplink?: string | null };
type Order = { id: number; public_code: string; room_number: string; order_status: string; payment_status: string; currency: string; total_minor: number; items?: Array<{ name: Record<string, string> | string; quantity: number; line_total_minor: number; options?: { sweetness?: number } }> };
type StoreSettings = { currency: string; timezone: string; aba_qr_asset_key: string | null; payment_link: string | null; telegram_staff_group_id: string | null; staff_group_language: string; open_hours: string | null };
type Staff = { id: number; login_name: string; role: string; active: boolean; telegram_user_id: string | null };
type Customer = { id: number; telegram_user_id: string; display_name: string | null; username: string | null; telegram_chat_url: string; order_count: number; completed_order_count: number; created_at: string };
type AuditEntry = { id: number; entity_type: string; entity_id: string; action: string; operator_name: string; operator_telegram_id: string | null; source: string; details: Record<string, any>; created_at: string };

const label = (value: Record<string, string> | undefined, language: string) => value?.[language] || value?.en || '';
const amount = (minor: number, currency: string, locale = 'en') => {
  try {
    const digits = new Intl.NumberFormat(locale, { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2;
    return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(minor / (10 ** digits));
  } catch {
    return `${minor} ${currency}`;
  }
};
const toMinor = (value: number, currency: string) => {
  try { return Math.round(value * (10 ** (new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2))); }
  catch { return Math.round(value * 100); }
};

function ImageUpload({ label: fieldLabel, value, onChange }: {
  label: string;
  value: string | null;
  onChange: (url: string | null) => void;
}) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');

  const selectImage = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setError('');
    if (file.size > 5 * 1024 * 1024) {
      setError(t('admin.uploadTooLarge'));
      event.target.value = '';
      return;
    }
    const form = new FormData();
    form.append('file', file);
    setUploading(true);
    try {
      const response = await api.post('/api/v1/admin/uploads', form);
      onChange(response.data.url);
    } catch {
      setError(t('admin.uploadFailed'));
    } finally {
      setUploading(false);
      event.target.value = '';
    }
  };

  return <div className="image-upload">
    <span className="image-upload-label">{fieldLabel}</span>
    <div className="image-upload-row">
      {value && <img className="image-upload-preview" src={value} alt={fieldLabel} />}
      <input ref={inputRef} className="image-upload-input" type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void selectImage(event)} />
      <button type="button" disabled={uploading} onClick={() => inputRef.current?.click()}>{uploading ? t('admin.uploadingImage') : t('admin.chooseImage')}</button>
      {value && <button type="button" disabled={uploading} onClick={() => onChange(null)}>{t('admin.removeImage')}</button>}
    </div>
    <small>{t('admin.imageUploadHint')}</small>
    {error && <span className="error" role="alert">{error}</span>}
  </div>;
}

function LanguageSwitch() {
  const { i18n } = useTranslation();
  return <select aria-label="Language" value={i18n.language} onChange={(event) => {
    const language = event.target.value;
    localStorage.setItem('teacafe.language', language);
    void i18n.changeLanguage(language);
  }}><option value="en">English</option><option value="zh-CN">中文</option><option value="km">ខ្មែរ</option></select>;
}

function CustomerPage() {
  const { t, i18n } = useTranslation();
  const [categories, setCategories] = useState<Category[]>([]);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [sweetnessByProduct, setSweetnessByProduct] = useState<Record<number, number>>({});
  const [room, setRoom] = useState('');
  const [order, setOrder] = useState<PlacedOrder | null>(null);
  const [orderStatus, setOrderStatus] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const idempotencyKey = useRef<string | null>(null);
  const total = useMemo(() => cart.reduce((sum, line) => sum + line.product.price_minor * line.quantity, 0), [cart]);
  const itemCount = useMemo(() => cart.reduce((count, line) => count + line.quantity, 0), [cart]);

  const loadMenu = useCallback(() => api.get('/api/v1/menu').then((response) => setCategories(response.data)).catch(() => setError(t('error.network'))).finally(() => setLoading(false)), [t]);
  useEffect(() => { void loadMenu(); }, [loadMenu]);

  const cartLineKey = (line: CartLine) => `${line.product.id}:${line.sweetness ?? 'default'}`;
  const add = (product: Product) => {
    if (cart.length && cart[0].product.currency !== product.currency) {
      setStatus(t('cart.mixedCurrency'));
      return;
    }
    if (product.sweetness_enabled && sweetnessByProduct[product.id] === undefined) {
      setStatus(t('menu.selectSweetnessFirst'));
      return;
    }
    setStatus('');
    idempotencyKey.current = null;
    setCart((current) => {
      const selectedSweetness = product.sweetness_enabled ? sweetnessByProduct[product.id] : null;
      const key = `${product.id}:${selectedSweetness ?? 'default'}`;
      const found = current.find((line) => cartLineKey(line) === key);
      return found ? current.map((line) => cartLineKey(line) === key ? { ...line, quantity: Math.min(99, line.quantity + 1) } : line) : [...current, { product, quantity: 1, sweetness: selectedSweetness }];
    });
  };
  const setQuantity = (key: string, quantity: number) => {
    idempotencyKey.current = null;
    setCart((current) => quantity < 1 ? current.filter((line) => cartLineKey(line) !== key) : current.map((line) => cartLineKey(line) === key ? { ...line, quantity } : line));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!room.trim()) { setStatus(t('checkout.roomRequired')); return; }
    if (!idempotencyKey.current) idempotencyKey.current = crypto.randomUUID();
    try {
      setStatus(t('checkout.submitting'));
      const response = await api.post('/api/v1/orders', {
        room_number: room.trim(), items: cart.map((line) => ({ product_id: line.product.id, quantity: line.quantity, ...(line.sweetness === null ? {} : { options: { sweetness: line.sweetness } }) })),
      }, { headers: { 'Idempotency-Key': idempotencyKey.current } });
      setOrder(response.data);
      setOrderStatus(null);
      setCart([]);
      idempotencyKey.current = null;
      setStatus('');
    } catch (requestError: any) {
      if (requestError?.response?.status === 409) idempotencyKey.current = null;
      setStatus(requestError?.response?.status === 409 ? t('cart.priceChanged') : t('checkout.failed'));
    }
  };

  const refreshStatus = async () => {
    if (!order) return;
    try {
      const response = await api.get(`/api/v1/orders/${order.public_code}`);
      setOrderStatus(response.data);
    } catch { setStatus(t('error.network')); }
  };

  return <main className="customer-shell">
    <header className="topbar"><div className="brand-title"><img className="brand-logo" src="/tea-cafe-logo.png" alt="Tea Cafe" /><div><span className="eyebrow">TEA CAFE</span><h1>{t('menu.title')}</h1></div></div><div className="customer-header-actions">{itemCount > 0 && <a className="cart-shortcut" href="#cart" aria-label={`${t('cart.title')}: ${itemCount}`}><span>{t('cart.title')}</span><strong>{itemCount}</strong></a>}<LanguageSwitch /></div></header>
    <section className="hero"><p className="eyebrow">{t('menu.popular')}</p><h2>{t('brand')}</h2><p>{t('checkout.roomOnly')}</p></section>
    {loading && <p className="state">{t('common.loading')}</p>}{error && <p className="error">{error}</p>}
    {!loading && !error && categories.length === 0 && <section className="state empty-menu" role="status"><h2>{t('menu.emptyTitle')}</h2><p>{t('menu.emptyDescription')}</p></section>}
    <section id="menu" className="menu-grid">{categories.map((category) => <div className="category" key={category.id}><h2>{label(category.name, i18n.language)}</h2><div className="product-grid">{(category.products || []).map((product) => <article className="product-card" key={product.id}>{product.image_key ? <img className="product-image" src={product.image_key} alt={label(product.name, i18n.language)} /> : <div className="product-art">{label(product.name, i18n.language).slice(0, 1)}</div>}<div className="product-copy"><h3>{label(product.name, i18n.language)}</h3><p>{label(product.description, i18n.language)}</p>{product.sweetness_enabled && <label className="sweetness-picker">{t('menu.sweetness')}<select value={sweetnessByProduct[product.id] ?? ''} onChange={(event) => setSweetnessByProduct((current) => ({ ...current, [product.id]: Number(event.target.value) }))}><option value="" disabled>{t('menu.chooseSweetness')}</option><option value={0}>{t('sweetness.0')}</option><option value={25}>{t('sweetness.25')}</option><option value={50}>{t('sweetness.50')}</option><option value={75}>{t('sweetness.75')}</option><option value={100}>{t('sweetness.100')}</option></select></label>}<div className="product-foot"><strong>{amount(product.price_minor, product.currency, i18n.language)}</strong><button type="button" disabled={!product.available || (product.sweetness_enabled && sweetnessByProduct[product.id] === undefined)} onClick={() => add(product)}>{product.available ? t('menu.addToCart') : t('common.soldOut')}</button></div></div></article>)}</div></div>)}</section>
    <section id="cart" className="checkout-card"><div><span className="eyebrow">{t('cart.title')}</span><h2>{itemCount ? `${itemCount} ${t('common.quantity')}` : t('cart.empty')}</h2></div>
      {cart.length > 0 && <form onSubmit={submit}>{cart.map((line) => <div className="cart-line" key={cartLineKey(line)}><span>{label(line.product.name, i18n.language)}{line.sweetness !== null && <small>{t('menu.sweetness')}: {line.sweetness}%</small>}</span><span><button type="button" onClick={() => setQuantity(cartLineKey(line), line.quantity - 1)} aria-label={t('common.delete')}>−</button> {line.quantity} <button type="button" onClick={() => setQuantity(cartLineKey(line), line.quantity + 1)}>+</button></span></div>)}
        <button className="continue-shopping" type="button" onClick={() => document.getElementById('menu')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>{t('menu.addMore')}</button>
        <label htmlFor="room">{t('checkout.roomNumber')}</label><input id="room" value={room} onChange={(event) => { idempotencyKey.current = null; setRoom(event.target.value); }} autoComplete="off" maxLength={32} required />
        <div className="total-row"><span>{t('common.total')}</span><strong>{amount(total, cart[0].product.currency, i18n.language)}</strong></div><button className="primary" type="submit">{t('checkout.placeOrder')}</button></form>}
      {status && <p className="status" role="status">{status}</p>}
    </section>
    {order && <section className="checkout-card payment-card"><span className="eyebrow">{t('payment.title')}</span><h2>{t('payment.order')} {order.public_code}</h2><p>{t('payment.amountDue')}: <strong>{amount(order.total_minor, order.currency, i18n.language)}</strong></p>
      {order.payment_qr_url && <img className="payment-qr" src={order.payment_qr_url} alt={t('payment.aba')} />}
      {order.payment_link && <a className="primary payment-link" href={order.payment_link} target="_blank" rel="noreferrer">{t('payment.openLink')}</a>}
      {!order.payment_qr_url && !order.payment_link && <p className="error">{t('payment.notConfigured')}</p>}
      <p>{t('payment.instruction')}</p><p>{t('payment.notConfirmed')}</p>
      {order.bot_deeplink ? <a className="primary payment-link" href={order.bot_deeplink}>{t('payment.sendProof')}</a> : <p>{t('payment.botNotConfigured')}</p>}
      <button type="button" onClick={refreshStatus}>{t('payment.refreshStatus')}</button>
      {orderStatus && <div className="status" role="status"><strong>{t(`order.status.${orderStatus.order_status.toLowerCase()}`)}</strong><p>{orderStatus.payment_status === 'PAID_CONFIRMED' ? t('payment.confirmed') : orderStatus.payment_status === 'PROOF_SUBMITTED' ? t('payment.pending') : orderStatus.payment_status === 'REJECTED' ? t('payment.rejected') : t('order.status.unpaid')}</p>{orderStatus.payment_status === 'REJECTED' && <p>{t('payment.resubmit')}</p>}</div>}
    </section>}
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
      setAdminAuth(String(response.data.staff_id), response.data.role);
      navigate('/admin');
    } catch { setError(t('auth.loginFailed')); } finally { setBusy(false); }
  };
  return <main className="auth-shell"><div className="auth-card"><img className="login-logo" src="/tea-cafe-logo.png" alt="Tea Cafe" /><span className="eyebrow">TEA CAFE · STAFF CONSOLE</span><h1>{t('auth.loginTitle')}</h1><p>{t('auth.loginDescription')}</p><form onSubmit={submit}><label htmlFor="login">{t('auth.loginId')}</label><input id="login" value={login} onChange={(event) => setLogin(event.target.value)} autoComplete="username" required /><label htmlFor="password">{t('auth.password')}</label><input id="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required />{error && <p className="error">{error}</p>}<button className="primary" type="submit" disabled={busy}>{busy ? t('common.loading') : t('auth.login')}</button></form><LanguageSwitch /></div></main>;
}

function AdminPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { isAdminAuthenticated, setAdminAuth, logoutAdmin, role } = useAuthStore();
  const [authLoading, setAuthLoading] = useState(true);
  const [orders, setOrders] = useState<Order[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [section, setSection] = useState<'overview' | 'orders' | 'products' | 'finance' | 'settings' | 'staff' | 'customers' | 'audit'>('overview');
  const [orderFilter, setOrderFilter] = useState('ALL');
  const [error, setError] = useState('');
  const [from, setFrom] = useState(() => new Date(Date.now() - 6 * 86400000).toISOString().slice(0, 10));
  const [to, setTo] = useState(() => new Date().toISOString().slice(0, 10));
  const emptyProduct = { nameEn: '', nameZh: '', nameKm: '', descriptionEn: '', descriptionZh: '', descriptionKm: '', price: '', categoryId: '', imageUrl: '', currency: '', sweetnessEnabled: false };
  const [newProduct, setNewProduct] = useState(emptyProduct);
  const [editingProductId, setEditingProductId] = useState<number | null>(null);
  const [newCategory, setNewCategory] = useState({ en: '', zh: '', km: '' });
  const [editingCategoryId, setEditingCategoryId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [storeSettings, setStoreSettings] = useState<StoreSettings>({ currency: 'USD', timezone: 'Asia/Phnom_Penh', aba_qr_asset_key: null, payment_link: null, telegram_staff_group_id: null, staff_group_language: 'en', open_hours: null });
  const [staff, setStaff] = useState<Staff[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [customerSearch, setCustomerSearch] = useState('');
  const [imageErrors, setImageErrors] = useState<Set<number>>(() => new Set());
  const [auditLogs, setAuditLogs] = useState<AuditEntry[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditOffset, setAuditOffset] = useState(0);
  const [auditLoading, setAuditLoading] = useState(false);
  const [telegramIdEdits, setTelegramIdEdits] = useState<Record<number, string>>({});
  const [busyStaffId, setBusyStaffId] = useState<number | null>(null);
  const [newStaff, setNewStaff] = useState({ login: '', password: '', telegramId: '', role: 'STAFF' });

  const fetchData = useCallback(async () => {
    setError('');
    const [orderResult, statsResult, productResult, categoryResult, settingsResult, staffResult, customerResult] = await Promise.allSettled([
      api.get('/api/v1/admin/orders'), api.get('/api/v1/admin/analytics/', { params: { from, to } }),
      api.get('/api/v1/admin/products'), api.get('/api/v1/admin/categories'),
      api.get('/api/v1/admin/settings/'), api.get('/api/v1/admin/staff'),
      role === 'MANAGER' ? api.get('/api/v1/admin/customers') : Promise.resolve({ data: [] }),
    ]);
    if (orderResult.status === 'fulfilled') setOrders(orderResult.value.data); else setError(t('error.network'));
    if (statsResult.status === 'fulfilled') setStats(statsResult.value.data);
    if (productResult.status === 'fulfilled') setProducts(productResult.value.data);
    if (categoryResult.status === 'fulfilled') setCategories(categoryResult.value.data);
    if (settingsResult.status === 'fulfilled') setStoreSettings(settingsResult.value.data);
    if (staffResult.status === 'fulfilled') setStaff(staffResult.value.data);
    if (customerResult.status === 'fulfilled') setCustomers(customerResult.value.data);
  }, [from, to, role, t]);

  useEffect(() => {
    let active = true;
    api.get('/api/v1/auth/admin/me').then((response) => {
      if (active) setAdminAuth(String(response.data.staff_id), response.data.role);
    }).catch(() => { if (active) logoutAdmin(); }).finally(() => { if (active) setAuthLoading(false); });
    return () => { active = false; };
  }, [setAdminAuth, logoutAdmin]);
  useEffect(() => { if (isAdminAuthenticated) void fetchData(); }, [isAdminAuthenticated, fetchData]);
  const fetchAuditLogs = useCallback(async () => {
    if (role !== 'MANAGER') return;
    setAuditLoading(true);
    try {
      const response = await api.get('/api/v1/admin/audit-logs', { params: { limit: 25, offset: auditOffset } });
      setAuditLogs(response.data.items);
      setAuditTotal(response.data.total);
    } catch { setError(t('error.network')); }
    finally { setAuditLoading(false); }
  }, [auditOffset, role, t]);
  useEffect(() => { if (isAdminAuthenticated && section === 'audit') void fetchAuditLogs(); }, [isAdminAuthenticated, section, fetchAuditLogs]);
  useEffect(() => {
    if (!authLoading && !isAdminAuthenticated) navigate('/admin/login', { replace: true });
  }, [authLoading, isAdminAuthenticated, navigate]);

  const signOut = async () => { try { await api.post('/api/v1/auth/admin/logout'); } finally { logoutAdmin(); navigate('/admin/login'); } };
  const reviewPayment = async (order: Order, decision: 'APPROVED' | 'REJECTED') => {
    const reason = decision === 'REJECTED' ? window.prompt(t('payment.rejectReasonInput'))?.trim() : undefined;
    if (decision === 'REJECTED' && !reason) return;
    setBusyId(order.id);
    try { await api.post(`/api/v1/admin/orders/${order.id}/payment-review`, { decision, reason }); await fetchData(); }
    catch { setError(t('error.generic')); } finally { setBusyId(null); }
  };
  const changeStatus = async (order: Order, status: string) => {
    const reason = status === 'CANCELLED' ? window.prompt(t('order.cancelReason'))?.trim() : undefined;
    if (status === 'CANCELLED' && !reason) return;
    setBusyId(order.id);
    try { await api.post(`/api/v1/admin/orders/${order.id}/status`, { status, reason }); await fetchData(); }
    catch { setError(t('error.generic')); } finally { setBusyId(null); }
  };
  const saveProduct = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const currency = newProduct.currency || storeSettings.currency || 'USD';
      const payload = {
        category_id: Number(newProduct.categoryId),
        name: { en: newProduct.nameEn, 'zh-CN': newProduct.nameZh, km: newProduct.nameKm },
        description: newProduct.descriptionEn || newProduct.descriptionZh || newProduct.descriptionKm
          ? { en: newProduct.descriptionEn || undefined, 'zh-CN': newProduct.descriptionZh || undefined, km: newProduct.descriptionKm || undefined }
          : null,
        price_minor: toMinor(Number(newProduct.price), currency), currency,
        image_key: newProduct.imageUrl || null,
        sweetness_enabled: newProduct.sweetnessEnabled,
      };
      if (editingProductId) await api.patch(`/api/v1/admin/products/${editingProductId}`, payload);
      else await api.post('/api/v1/admin/products', payload);
      setNewProduct(emptyProduct); setEditingProductId(null); await fetchData();
    } catch { setError(t('error.generic')); }
  };
  const createCategory = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const payload = { name: { en: newCategory.en, 'zh-CN': newCategory.zh, km: newCategory.km }, sort_order: categories.length };
      if (editingCategoryId) await api.patch(`/api/v1/admin/categories/${editingCategoryId}`, payload);
      else await api.post('/api/v1/admin/categories', payload);
      setNewCategory({ en: '', zh: '', km: '' }); setEditingCategoryId(null); await fetchData();
    }
    catch { setError(t('error.generic')); }
  };
  const startCategoryEdit = (category: Category) => {
    setEditingCategoryId(category.id);
    setNewCategory({ en: category.name.en || '', zh: category.name['zh-CN'] || '', km: category.name.km || '' });
  };
  const toggleCategory = async (category: Category) => {
    try { await api.patch(`/api/v1/admin/categories/${category.id}`, { active: !category.active }); await fetchData(); }
    catch { setError(t('error.generic')); }
  };
  const startProductEdit = (product: Product) => {
    setEditingProductId(product.id);
    setNewProduct({
      nameEn: product.name.en || '', nameZh: product.name['zh-CN'] || '', nameKm: product.name.km || '',
      descriptionEn: product.description?.en || '', descriptionZh: product.description?.['zh-CN'] || '', descriptionKm: product.description?.km || '',
      price: String(product.price_minor / (10 ** (new Intl.NumberFormat('en', { style: 'currency', currency: product.currency }).resolvedOptions().maximumFractionDigits ?? 2))),
      categoryId: String(product.category_id), imageUrl: product.image_key || '', currency: product.currency,
      sweetnessEnabled: product.sweetness_enabled,
    });
  };
  const toggleProduct = async (product: Product) => {
    try { await api.patch(`/api/v1/admin/products/${product.id}`, { available: !product.available }); await fetchData(); }
    catch { setError(t('error.generic')); }
  };
  const saveSettings = async (event: FormEvent) => {
    event.preventDefault();
    try { await api.patch('/api/v1/admin/settings/', storeSettings); await fetchData(); }
    catch { setError(t('error.generic')); }
  };
  const addStaff = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await api.post('/api/v1/admin/staff', { login_name: newStaff.login, password: newStaff.password, role: newStaff.role, telegram_user_id: newStaff.telegramId || null });
      setNewStaff({ login: '', password: '', telegramId: '', role: 'STAFF' }); await fetchData();
    } catch { setError(t('error.generic')); }
  };
  const toggleStaff = async (member: Staff) => {
    try { await api.patch(`/api/v1/admin/staff/${member.id}`, { active: !member.active }); await fetchData(); }
    catch { setError(t('error.generic')); }
  };
  const saveStaffTelegramId = async (member: Staff) => {
    setBusyStaffId(member.id);
    try {
      const telegram_user_id = telegramIdEdits[member.id]?.trim() || null;
      await api.patch(`/api/v1/admin/staff/${member.id}`, { telegram_user_id });
      setTelegramIdEdits((current) => { const next = { ...current }; delete next[member.id]; return next; });
      await fetchData();
    } catch { setError(t('error.generic')); }
    finally { setBusyStaffId(null); }
  };
  const changeStaffRole = async (member: Staff, roleValue: string) => {
    try { await api.patch(`/api/v1/admin/staff/${member.id}`, { role: roleValue }); await fetchData(); }
    catch { setError(t('error.generic')); }
  };

  if (authLoading) return <main className="auth-shell"><p>{t('common.loading')}</p></main>;
  if (!isAdminAuthenticated) return <main className="auth-shell"><p>{t('common.loading')}</p></main>;

  return <main className="admin-shell"><div className="admin-layout">
    <aside className="admin-sidebar"><div className="admin-sidebar-brand"><img className="brand-logo" src="/tea-cafe-logo.png" alt="Tea Cafe" /><div><strong>Tea Cafe</strong><small>STORE CONSOLE</small></div></div>
      <nav className="admin-nav" aria-label={t('admin.navigation')}>
        <button className={section === 'overview' ? 'active' : ''} onClick={() => setSection('overview')}><span aria-hidden="true">▦</span>{t('nav.overview')}</button>
        <button className={section === 'orders' ? 'active' : ''} onClick={() => setSection('orders')}><span aria-hidden="true">▤</span>{t('nav.orders')}</button>
        {role === 'MANAGER' && <button className={section === 'customers' ? 'active' : ''} onClick={() => setSection('customers')}><span aria-hidden="true">♧</span>{t('nav.customers')}</button>}
        {role === 'MANAGER' && <button className={section === 'products' ? 'active' : ''} onClick={() => setSection('products')}><span aria-hidden="true">▧</span>{t('nav.products')}</button>}
        <button className={section === 'finance' ? 'active' : ''} onClick={() => setSection('finance')}><span aria-hidden="true">▥</span>{t('nav.finance')}</button>
        {role === 'MANAGER' && <button className={section === 'audit' ? 'active' : ''} onClick={() => { setAuditOffset(0); setSection('audit'); }}><span aria-hidden="true">◷</span>{t('nav.auditLogs')}</button>}
        {role === 'MANAGER' && <button className={section === 'staff' ? 'active' : ''} onClick={() => setSection('staff')}><span aria-hidden="true">⚙</span>{t('admin.staffAccess')}</button>}
        {role === 'MANAGER' && <button className={section === 'settings' ? 'active' : ''} onClick={() => setSection('settings')}><span aria-hidden="true">⌘</span>{t('nav.settings')}</button>}
      </nav><div className="sidebar-footer"><span>{role === 'MANAGER' ? t('admin.manager') : t('admin.waiter')}</span><span>TEA CAFE</span></div>
    </aside>
    <div className="admin-main"><header className="admin-header"><div><span className="eyebrow">FOOD.WORKLINE.INK/ADMIN</span><h1>{t(section === 'audit' ? 'nav.auditLogs' : section === 'staff' ? 'admin.staffAccess' : `nav.${section}`)}</h1></div><div className="header-actions"><LanguageSwitch /><button onClick={() => void signOut()}>{t('auth.logout')}</button></div></header>
    <div className="admin-content">{error && <p className="error" role="alert">{error}</p>}
    {section === 'overview' && <section className="overview-grid"><div className="metric"><span>{t('admin.orderVolume')}</span><strong>{stats?.order_volume ?? '—'}</strong></div><button className="metric metric-action warning" onClick={() => { setOrderFilter('ALL'); setSection('orders'); }}><span>{t('admin.needsReview')}</span><strong>{orders.filter((order) => order.payment_status === 'PROOF_SUBMITTED').length}</strong></button><div className="metric"><span>{t('admin.confirmedPaid')}</span><strong>{orders.filter((order) => order.payment_status === 'PAID_CONFIRMED').length}</strong></div><div className="metric"><span>{t('admin.cancelledCount')}</span><strong>{stats?.cancelled_count ?? '—'}</strong></div><section className="panel overview-recent"><div className="panel-heading"><div><span className="eyebrow">{t('admin.orders')}</span><h2>{t('admin.recentOrders')}</h2></div><button onClick={() => setSection('orders')}>{t('admin.viewOrders')}</button></div>{orders.slice(0, 6).map((order) => <div className="overview-order" key={order.id}><strong>{order.public_code}</strong><span>{t('checkout.roomNumber')} {order.room_number}</span><span>{t(`order.status.${order.order_status.toLowerCase()}`)}</span><b>{amount(order.total_minor, order.currency, i18n.language)}</b></div>)}</section></section>}
    {section === 'orders' && <>
      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">{t('admin.orders')}</span><h2>{t('nav.orders')}</h2></div><label>{t('admin.allStatuses')}<select value={orderFilter} onChange={(event) => setOrderFilter(event.target.value)}><option value="ALL">{t('admin.allStatuses')}</option>{['NEW', 'ACCEPTED', 'PREPARING', 'READY', 'DELIVERED', 'COMPLETED', 'CANCELLED'].map((value) => <option key={value} value={value}>{t(`order.status.${value.toLowerCase()}`)}</option>)}</select></label><button onClick={() => void fetchData()}>{t('common.retry')}</button></div>
        {orders.length === 0 ? <p className="state">{t('admin.noOrders')}</p> : <div className="order-list">{orders.filter((order) => orderFilter === 'ALL' || order.order_status === orderFilter).map((order) => <article className="order-row" key={order.id}>
          <strong>{order.public_code}</strong><span>{t('checkout.roomNumber')} {order.room_number}</span><span>{t(`order.status.${order.order_status.toLowerCase()}`)}</span><span>{order.payment_status === 'PAID_CONFIRMED' ? t('payment.confirmed') : order.payment_status === 'PROOF_SUBMITTED' ? t('payment.pending') : order.payment_status === 'REJECTED' ? t('payment.rejected') : t('order.status.unpaid')}</span><b>{amount(order.total_minor, order.currency, i18n.language)}</b>
          {order.items?.length ? <div className="order-items">{order.items.map((item, index) => <span key={`${order.id}-${index}`}>{typeof item.name === 'string' ? item.name : label(item.name, i18n.language)} × {item.quantity}{item.options?.sweetness !== undefined ? ` · ${t('order.sweetnessValue', { value: item.options.sweetness })}` : ''} · {amount(item.line_total_minor, order.currency, i18n.language)}</span>)}</div> : null}
          {order.payment_status === 'PROOF_SUBMITTED' && <details><summary>{t('admin.openPaymentProof')}</summary>{imageErrors.has(order.id) ? <p className="error">{t('admin.paymentImageUnavailable')} <button type="button" onClick={() => setImageErrors((current) => { const next = new Set(current); next.delete(order.id); return next; })}>{t('common.retry')}</button></p> : <img className="payment-proof" src={`/api/v1/admin/orders/${order.id}/payment-proof`} alt={t('admin.paymentImage')} onError={() => setImageErrors((current) => new Set(current).add(order.id))} />}<p>{t('admin.checkActualPayment')}</p>{order.order_status === 'ACCEPTED' && <div className="actions"><button disabled={busyId === order.id} onClick={() => void reviewPayment(order, 'APPROVED')}>{t('order.confirmPayment')}</button><button disabled={busyId === order.id} onClick={() => void reviewPayment(order, 'REJECTED')}>{t('order.reject')}</button></div>}</details>}
          <div className="actions">{order.order_status === 'NEW' && <button onClick={() => void changeStatus(order, 'ACCEPTED')}>{t('order.accept')}</button>}{order.order_status === 'PREPARING' && order.payment_status === 'PAID_CONFIRMED' && <button onClick={() => void changeStatus(order, 'READY')}>{t('order.markReady')}</button>}{order.order_status === 'READY' && <button onClick={() => void changeStatus(order, 'DELIVERED')}>{t('order.markDelivered')}</button>}{order.order_status === 'DELIVERED' && <button onClick={() => void changeStatus(order, 'COMPLETED')}>{t('order.complete')}</button>}{!['CANCELLED', 'COMPLETED'].includes(order.order_status) && order.payment_status !== 'PAID_CONFIRMED' && <button onClick={() => void changeStatus(order, 'CANCELLED')}>{t('order.cancel')}</button>}</div>
        </article>)}</div>}
      </section>
    </>}
    {section === 'customers' && role === 'MANAGER' && <section className="panel"><div className="panel-heading"><div><span className="eyebrow">{t('admin.customerList')}</span><h2>{t('nav.customers')}</h2></div><label>{t('common.search')}<input type="search" value={customerSearch} onChange={(event) => setCustomerSearch(event.target.value)} placeholder={t('admin.searchCustomers')} /></label></div>
      {customers.length === 0 ? <p className="state">{t('admin.noCustomers')}</p> : <div className="customer-table-wrap"><table className="customer-table"><thead><tr><th>{t('admin.telegramName')}</th><th>{t('admin.telegramId')}</th><th>{t('admin.telegramChat')}</th><th>{t('admin.orderCount')}</th><th>{t('admin.completedOrderCount')}</th></tr></thead><tbody>{customers.filter((customer) => `${customer.display_name || ''} ${customer.username || ''} ${customer.telegram_user_id}`.toLowerCase().includes(customerSearch.trim().toLowerCase())).map((customer) => <tr key={customer.id}><td>{customer.display_name || customer.username || '—'}{customer.username && <small>@{customer.username}</small>}</td><td>{customer.telegram_user_id}</td><td><a href={customer.telegram_chat_url}>{t('admin.telegramChat')}</a></td><td>{customer.order_count}</td><td>{customer.completed_order_count}</td></tr>)}</tbody></table></div>}
    </section>}
    {section === 'audit' && role === 'MANAGER' && <section className="panel audit-panel" aria-busy={auditLoading}><div className="panel-heading"><div><span className="eyebrow">{t('admin.auditDescription')}</span><h2>{t('nav.auditLogs')}</h2></div><button onClick={() => void fetchAuditLogs()} disabled={auditLoading}>{auditLoading ? t('common.loading') : t('common.retry')}</button></div>
      {auditLogs.length === 0 && !auditLoading ? <p className="state">{t('admin.noAuditLogs')}</p> : <div className="customer-table-wrap"><table className="customer-table audit-table"><thead><tr><th>{t('admin.logTime')}</th><th>{t('admin.logAction')}</th><th>{t('admin.logOrder')}</th><th>{t('admin.logOperator')}</th><th>{t('admin.logChannel')}</th><th>{t('admin.logState')}</th></tr></thead><tbody>{auditLogs.map((entry) => {
        const groupAction = entry.source === 'telegram_group' && entry.details.action ? `audit.groupAction.${entry.details.action}` : null;
        const actionKey = groupAction || `audit.action.${entry.action}`;
        const actionLabel = t(actionKey, { defaultValue: entry.action });
        const orderLabel = entry.details.public_code || `${t(`audit.entity.${entry.entity_type}`, { defaultValue: entry.entity_type })} #${entry.entity_id}`;
        let timeLabel = entry.created_at;
        try { timeLabel = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short', timeZone: storeSettings.timezone }).format(new Date(entry.created_at)); } catch { /* retain server timestamp */ }
        return <tr key={entry.id}><td>{timeLabel}</td><td>{actionLabel}{entry.details.reason && <small>{entry.details.reason}</small>}</td><td>{orderLabel}</td><td>{entry.operator_name}<small>{entry.operator_telegram_id ? `${t('admin.telegramId')}: ${entry.operator_telegram_id}` : ''}</small></td><td>{t(`audit.source.${entry.source}`, { defaultValue: entry.source })}{entry.source === 'telegram_group' && entry.details.group_id && <small>{entry.details.group_id}</small>}</td><td><span>{entry.details.from_state || '—'}</span>{entry.details.to_state && <small>→ {entry.details.to_state}</small>}</td></tr>;
      })}</tbody></table></div>}
      {auditTotal > 0 && <div className="audit-pagination"><span>{t('admin.logRange', { from: auditOffset + 1, to: Math.min(auditOffset + auditLogs.length, auditTotal), total: auditTotal })}</span><div><button disabled={auditOffset === 0 || auditLoading} onClick={() => setAuditOffset((offset) => Math.max(0, offset - 25))}>{t('common.back')}</button><button disabled={auditOffset + 25 >= auditTotal || auditLoading} onClick={() => setAuditOffset((offset) => offset + 25)}>{t('admin.nextPage')}</button></div></div>}
    </section>}
    {section === 'products' && role === 'MANAGER' && <section className="panel"><div className="panel-heading"><h2>{t('nav.products')}</h2></div>
      <form className="product-form" onSubmit={createCategory}><label>{t('admin.newCategory')} (EN)<input value={newCategory.en} onChange={(event) => setNewCategory({ ...newCategory, en: event.target.value })} required /></label><label>{t('admin.newCategory')} (中文)<input value={newCategory.zh} onChange={(event) => setNewCategory({ ...newCategory, zh: event.target.value })} required /></label><label>{t('admin.newCategory')} (ខ្មែរ)<input value={newCategory.km} onChange={(event) => setNewCategory({ ...newCategory, km: event.target.value })} required /></label><button type="submit">{editingCategoryId ? t('common.save') : t('common.add')}</button>{editingCategoryId && <button type="button" onClick={() => { setEditingCategoryId(null); setNewCategory({ en: '', zh: '', km: '' }); }}>{t('common.cancel')}</button>}</form>
      <div className="order-list">{categories.map((category) => <article className="order-row" key={category.id}><strong>{label(category.name, i18n.language)}</strong><span>{category.active ? t('common.available') : t('common.soldOut')}</span><button onClick={() => startCategoryEdit(category)}>{t('common.edit')}</button><button onClick={() => void toggleCategory(category)}>{category.active ? t('admin.deactivate') : t('admin.activate')}</button></article>)}</div>
      <form className="product-form" onSubmit={saveProduct}><label>{t('admin.category')}<select value={newProduct.categoryId} onChange={(event) => setNewProduct({ ...newProduct, categoryId: event.target.value })} required><option value="">—</option>{categories.map((category) => <option key={category.id} value={category.id}>{label(category.name, i18n.language)}</option>)}</select></label><label>{t('admin.productName')} (EN)<input value={newProduct.nameEn} onChange={(event) => setNewProduct({ ...newProduct, nameEn: event.target.value })} required /></label><label>{t('admin.productName')} (中文)<input value={newProduct.nameZh} onChange={(event) => setNewProduct({ ...newProduct, nameZh: event.target.value })} required /></label><label>{t('admin.productName')} (ខ្មែរ)<input value={newProduct.nameKm} onChange={(event) => setNewProduct({ ...newProduct, nameKm: event.target.value })} required /></label><label>{t('admin.description')} (EN)<input value={newProduct.descriptionEn} onChange={(event) => setNewProduct({ ...newProduct, descriptionEn: event.target.value })} /></label><label>{t('admin.description')} (中文)<input value={newProduct.descriptionZh} onChange={(event) => setNewProduct({ ...newProduct, descriptionZh: event.target.value })} /></label><label>{t('admin.description')} (ខ្មែរ)<input value={newProduct.descriptionKm} onChange={(event) => setNewProduct({ ...newProduct, descriptionKm: event.target.value })} /></label><ImageUpload label={t('admin.imageUrl')} value={newProduct.imageUrl || null} onChange={(imageUrl) => setNewProduct((current) => ({ ...current, imageUrl: imageUrl || '' }))} /><label>{t('admin.price')}<input type="number" min="0.01" step="0.01" value={newProduct.price} onChange={(event) => setNewProduct({ ...newProduct, price: event.target.value })} required /></label><label>{t('common.currency')}<input value={newProduct.currency || storeSettings.currency} readOnly /></label><label className="product-sweetness-toggle"><input type="checkbox" checked={newProduct.sweetnessEnabled} onChange={(event) => setNewProduct({ ...newProduct, sweetnessEnabled: event.target.checked })} /><span><strong>{t('admin.enableSweetness')}</strong><small>{t('admin.enableSweetnessHint')}</small></span></label><button className="primary" type="submit" disabled={!categories.length}>{editingProductId ? t('common.save') : t('admin.createProduct')}</button>{editingProductId && <button type="button" onClick={() => { setEditingProductId(null); setNewProduct(emptyProduct); }}>{t('common.cancel')}</button>}</form>
      <div className="order-list">{products.map((product) => <article className="order-row" key={product.id}><strong>{label(product.name, i18n.language)}</strong><span>{amount(product.price_minor, product.currency, i18n.language)}</span><span>{product.sweetness_enabled ? t('admin.sweetnessOn') : t('admin.sweetnessOff')}</span><span>{product.available ? t('common.available') : t('common.soldOut')}</span><button onClick={() => startProductEdit(product)}>{t('common.edit')}</button><button onClick={() => void toggleProduct(product)}>{product.available ? t('common.soldOut') : t('common.available')}</button></article>)}</div>
    </section>}
    {section === 'finance' && <section className="panel"><div className="panel-heading"><h2>{t('admin.finance')}</h2><div><label>{t('admin.from')} <input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label><label>{t('admin.to')} <input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label></div></div><p>{t('admin.timezone')}: {stats?.timezone}</p><p>{t('admin.manualReviewBasis')}</p><div className="metric-row"><div className="metric"><span>{t('admin.orderVolume')}</span><strong>{stats?.order_volume ?? '—'}</strong></div><div className="metric"><span>{t('admin.cancelledCount')}</span><strong>{stats?.cancelled_count ?? '—'}</strong></div></div>{(stats?.by_currency || []).map((row: any) => <div className="metric-row" key={row.currency}><div className="metric"><span>{t('admin.orderTotal')}</span><strong>{amount(row.order_total_minor, row.currency, i18n.language)}</strong></div><div className="metric"><span>{t('admin.confirmedReceipts')}</span><strong>{amount(row.confirmed_receipts_minor, row.currency, i18n.language)}</strong></div><div className="metric warning"><span>{t('admin.inReview')}</span><strong>{amount(row.in_review_minor, row.currency, i18n.language)}</strong></div><div className="metric"><span>{t('admin.cancelledAmount')}</span><strong>{amount(row.cancelled_total_minor, row.currency, i18n.language)}</strong></div></div>)}</section>}
    {section === 'settings' && role === 'MANAGER' && <section className="panel"><div className="panel-heading"><h2>{t('nav.settings')}</h2></div><form className="product-form" onSubmit={saveSettings}>
      <label>{t('common.currency')}<input maxLength={3} value={storeSettings.currency} onChange={(event) => setStoreSettings({ ...storeSettings, currency: event.target.value.toUpperCase() })} required /></label>
      <label>{t('admin.timezone')}<input value={storeSettings.timezone} onChange={(event) => setStoreSettings({ ...storeSettings, timezone: event.target.value })} required /></label>
      <label>{t('admin.paymentLink')}<input type="url" value={storeSettings.payment_link || ''} onChange={(event) => setStoreSettings({ ...storeSettings, payment_link: event.target.value || null })} /></label>
      <ImageUpload label={t('admin.qrImageUrl')} value={storeSettings.aba_qr_asset_key} onChange={(aba_qr_asset_key) => setStoreSettings((current) => ({ ...current, aba_qr_asset_key }))} />
      <label>{t('admin.groupId')}<input inputMode="numeric" value={storeSettings.telegram_staff_group_id || ''} onChange={(event) => setStoreSettings({ ...storeSettings, telegram_staff_group_id: event.target.value || null })} /><small>{t('admin.groupIdHelp')}</small></label>
      <label>{t('admin.groupLanguage')}<select value={storeSettings.staff_group_language} onChange={(event) => setStoreSettings({ ...storeSettings, staff_group_language: event.target.value })}><option value="en">English</option><option value="zh-CN">中文</option><option value="km">ខ្មែរ</option></select></label>
      <label>{t('admin.openHours')}<input value={storeSettings.open_hours || ''} onChange={(event) => setStoreSettings({ ...storeSettings, open_hours: event.target.value || null })} /></label>
      <button className="primary" type="submit">{t('admin.updateSettings')}</button>
    </form></section>}
    {section === 'staff' && role === 'MANAGER' && <section className="panel"><div className="panel-heading"><h2>{t('admin.staffList')}</h2></div><form className="product-form" onSubmit={addStaff}>
      <label>{t('admin.loginName')}<input value={newStaff.login} onChange={(event) => setNewStaff({ ...newStaff, login: event.target.value })} required minLength={3} /></label>
      <label>{t('admin.password')}<input type="password" value={newStaff.password} onChange={(event) => setNewStaff({ ...newStaff, password: event.target.value })} required minLength={12} /></label>
      <label>{t('admin.telegramUserId')}<input inputMode="numeric" value={newStaff.telegramId} onChange={(event) => setNewStaff({ ...newStaff, telegramId: event.target.value })} /></label>
      <label>{t('admin.staffRole')}<select value={newStaff.role} onChange={(event) => setNewStaff({ ...newStaff, role: event.target.value })}><option value="STAFF">{t('admin.waiter')}</option><option value="MANAGER">{t('admin.manager')}</option></select></label>
      <button className="primary" type="submit">{t('admin.addStaff')}</button>
    </form><div className="order-list">{staff.map((member) => <article className="order-row" key={member.id}><strong>{member.login_name}</strong><input inputMode="numeric" aria-label={t('admin.telegramUserId')} placeholder={t('admin.telegramUserId')} value={telegramIdEdits[member.id] ?? member.telegram_user_id ?? ''} onChange={(event) => setTelegramIdEdits((current) => ({ ...current, [member.id]: event.target.value }))} /><button type="button" disabled={busyStaffId === member.id} onClick={() => void saveStaffTelegramId(member)}>{t('admin.linkTelegramId')}</button><select value={member.role} onChange={(event) => void changeStaffRole(member, event.target.value)}><option value="STAFF">{t('admin.waiter')}</option><option value="MANAGER">{t('admin.manager')}</option></select><span>{member.active ? t('admin.active') : t('admin.deactivate')}</span><button onClick={() => void toggleStaff(member)}>{member.active ? t('admin.deactivate') : t('admin.activate')}</button></article>)}</div></section>}
    </div></div></div></main>;
}

export default function App() {
  return <TelegramProvider><Routes><Route path="/" element={<CustomerPage />} /><Route path="/admin/login" element={<AdminLogin />} /><Route path="/admin" element={<AdminPage />} /></Routes></TelegramProvider>;
}
