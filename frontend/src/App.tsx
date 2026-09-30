import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Routes, Route, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import api from './api';
import { TelegramProvider } from './components/TelegramProvider';
import { useAuthStore } from './store/authStore';
import './index.css';

type Product = { id: number; category_id: number; name: Record<string, string>; description?: Record<string, string>; price_minor: number; currency: string; image_key?: string; available: boolean };
type Category = { id: number; name: Record<string, string>; products?: Product[]; sort_order?: number; active?: boolean };
type CartLine = { product: Product; quantity: number };
type PlacedOrder = { public_code: string; total_minor: number; currency: string; status: string; payment_status: string; payment_link?: string | null; payment_qr_url?: string | null; bot_deeplink?: string | null };
type Order = { id: number; public_code: string; room_number: string; order_status: string; payment_status: string; currency: string; total_minor: number; items?: Array<{ name: Record<string, string> | string; quantity: number; line_total_minor: number }> };
type StoreSettings = { currency: string; timezone: string; aba_qr_asset_key: string | null; payment_link: string | null; telegram_staff_group_id: string | null; staff_group_language: string; open_hours: string | null };
type Staff = { id: number; login_name: string; role: string; active: boolean; telegram_user_id: string | null };

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
  const [room, setRoom] = useState('');
  const [order, setOrder] = useState<PlacedOrder | null>(null);
  const [orderStatus, setOrderStatus] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const idempotencyKey = useRef<string | null>(null);
  const total = useMemo(() => cart.reduce((sum, line) => sum + line.product.price_minor * line.quantity, 0), [cart]);

  const loadMenu = useCallback(() => api.get('/api/v1/menu').then((response) => setCategories(response.data)).catch(() => setError(t('error.network'))).finally(() => setLoading(false)), [t]);
  useEffect(() => { void loadMenu(); }, [loadMenu]);

  const add = (product: Product) => setCart((current) => {
    const found = current.find((line) => line.product.id === product.id);
    return found ? current.map((line) => line.product.id === product.id ? { ...line, quantity: Math.min(99, line.quantity + 1) } : line) : [...current, { product, quantity: 1 }];
  });
  const setQuantity = (productId: number, quantity: number) => setCart((current) => quantity < 1 ? current.filter((line) => line.product.id !== productId) : current.map((line) => line.product.id === productId ? { ...line, quantity } : line));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!room.trim()) { setStatus(t('checkout.roomRequired')); return; }
    if (!idempotencyKey.current) idempotencyKey.current = crypto.randomUUID();
    try {
      setStatus(t('checkout.submitting'));
      const response = await api.post('/api/v1/orders', {
        room_number: room.trim(), items: cart.map((line) => ({ product_id: line.product.id, quantity: line.quantity })),
      }, { headers: { 'Idempotency-Key': idempotencyKey.current } });
      setOrder(response.data);
      setOrderStatus(null);
      setCart([]);
      idempotencyKey.current = null;
      setStatus('');
    } catch (requestError: any) {
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
    <header className="topbar"><div className="brand-title"><img className="brand-logo" src="/tea-cafe-logo.png" alt="Tea Cafe" /><div><span className="eyebrow">TEA CAFE</span><h1>{t('menu.title')}</h1></div></div><LanguageSwitch /></header>
    <section className="hero"><p className="eyebrow">{t('menu.popular')}</p><h2>{t('brand')}</h2><p>{t('checkout.roomOnly')}</p></section>
    {loading && <p className="state">{t('common.loading')}</p>}{error && <p className="error">{error}</p>}
    <section className="menu-grid">{categories.map((category) => <div className="category" key={category.id}><h2>{label(category.name, i18n.language)}</h2><div className="product-grid">{(category.products || []).map((product) => <article className="product-card" key={product.id}>{product.image_key ? <img className="product-image" src={product.image_key} alt={label(product.name, i18n.language)} /> : <div className="product-art">{label(product.name, i18n.language).slice(0, 1)}</div>}<div className="product-copy"><h3>{label(product.name, i18n.language)}</h3><p>{label(product.description, i18n.language)}</p><div className="product-foot"><strong>{amount(product.price_minor, product.currency, i18n.language)}</strong><button disabled={!product.available} onClick={() => add(product)}>{product.available ? t('menu.addToCart') : t('common.soldOut')}</button></div></div></article>)}</div></div>)}</section>
    <section className="checkout-card"><div><span className="eyebrow">{t('cart.title')}</span><h2>{cart.length ? `${cart.reduce((count, line) => count + line.quantity, 0)} ${t('common.quantity')}` : t('cart.empty')}</h2></div>
      {cart.length > 0 && <form onSubmit={submit}>{cart.map((line) => <div className="total-row" key={line.product.id}><span>{label(line.product.name, i18n.language)}</span><span><button type="button" onClick={() => setQuantity(line.product.id, line.quantity - 1)} aria-label={t('common.delete')}>−</button> {line.quantity} <button type="button" onClick={() => setQuantity(line.product.id, line.quantity + 1)}>+</button></span></div>)}
        <label htmlFor="room">{t('checkout.roomNumber')}</label><input id="room" value={room} onChange={(event) => setRoom(event.target.value)} autoComplete="off" maxLength={32} required />
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
  const [section, setSection] = useState<'orders' | 'products' | 'finance' | 'settings' | 'staff'>('orders');
  const [orderFilter, setOrderFilter] = useState('ALL');
  const [error, setError] = useState('');
  const [from, setFrom] = useState(() => new Date(Date.now() - 6 * 86400000).toISOString().slice(0, 10));
  const [to, setTo] = useState(() => new Date().toISOString().slice(0, 10));
  const emptyProduct = { nameEn: '', nameZh: '', nameKm: '', descriptionEn: '', descriptionZh: '', descriptionKm: '', price: '', categoryId: '', imageUrl: '', currency: '' };
  const [newProduct, setNewProduct] = useState(emptyProduct);
  const [editingProductId, setEditingProductId] = useState<number | null>(null);
  const [newCategory, setNewCategory] = useState({ en: '', zh: '', km: '' });
  const [editingCategoryId, setEditingCategoryId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [storeSettings, setStoreSettings] = useState<StoreSettings>({ currency: 'USD', timezone: 'Asia/Phnom_Penh', aba_qr_asset_key: null, payment_link: null, telegram_staff_group_id: null, staff_group_language: 'en', open_hours: null });
  const [staff, setStaff] = useState<Staff[]>([]);
  const [newStaff, setNewStaff] = useState({ login: '', password: '', telegramId: '', role: 'STAFF' });

  const fetchData = useCallback(async () => {
    setError('');
    const [orderResult, statsResult, productResult, categoryResult, settingsResult, staffResult] = await Promise.allSettled([
      api.get('/api/v1/admin/orders'), api.get('/api/v1/admin/analytics/', { params: { from, to } }),
      api.get('/api/v1/admin/products'), api.get('/api/v1/admin/categories'),
      api.get('/api/v1/admin/settings/'), api.get('/api/v1/admin/staff'),
    ]);
    if (orderResult.status === 'fulfilled') setOrders(orderResult.value.data); else setError(t('error.network'));
    if (statsResult.status === 'fulfilled') setStats(statsResult.value.data);
    if (productResult.status === 'fulfilled') setProducts(productResult.value.data);
    if (categoryResult.status === 'fulfilled') setCategories(categoryResult.value.data);
    if (settingsResult.status === 'fulfilled') setStoreSettings(settingsResult.value.data);
    if (staffResult.status === 'fulfilled') setStaff(staffResult.value.data);
  }, [from, to, t]);

  useEffect(() => {
    let active = true;
    api.get('/api/v1/auth/admin/me').then((response) => {
      if (active) setAdminAuth(String(response.data.staff_id), response.data.role);
    }).catch(() => { if (active) logoutAdmin(); }).finally(() => { if (active) setAuthLoading(false); });
    return () => { active = false; };
  }, [setAdminAuth, logoutAdmin]);
  useEffect(() => { if (isAdminAuthenticated) void fetchData(); }, [isAdminAuthenticated, fetchData]);
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
  const changeStaffRole = async (member: Staff, roleValue: string) => {
    try { await api.patch(`/api/v1/admin/staff/${member.id}`, { role: roleValue }); await fetchData(); }
    catch { setError(t('error.generic')); }
  };

  if (authLoading) return <main className="auth-shell"><p>{t('common.loading')}</p></main>;
  if (!isAdminAuthenticated) return <main className="auth-shell"><p>{t('common.loading')}</p></main>;

  return <main className="admin-shell">
    <header className="admin-header"><div className="brand-title"><img className="brand-logo" src="/tea-cafe-logo.png" alt="Tea Cafe" /><div><span className="eyebrow">FOOD.WORKLINE.INK/ADMIN</span><h1>{t('nav.overview')}</h1></div></div><div className="header-actions"><LanguageSwitch /><button onClick={() => void signOut()}>{t('auth.logout')}</button></div></header>
    <nav className="admin-tabs"><button onClick={() => setSection('orders')}>{t('nav.orders')}</button>{role === 'MANAGER' && <button onClick={() => setSection('products')}>{t('nav.products')}</button>}<button onClick={() => setSection('finance')}>{t('nav.finance')}</button>{role === 'MANAGER' && <button onClick={() => setSection('settings')}>{t('nav.settings')}</button>}{role === 'MANAGER' && <button onClick={() => setSection('staff')}>{t('admin.staffAccess')}</button>}</nav>
    {error && <p className="error" role="alert">{error}</p>}
    {section === 'orders' && <>
      <section className="metric-row"><div className="metric"><span>{t('admin.orderVolume')}</span><strong>{stats?.order_volume ?? '—'}</strong></div><div className="metric warning"><span>{t('admin.needsReview')}</span><strong>{orders.filter((order) => order.payment_status === 'PROOF_SUBMITTED').length}</strong></div><div className="metric"><span>{t('admin.confirmedPaid')}</span><strong>{orders.filter((order) => order.payment_status === 'PAID_CONFIRMED').length}</strong></div></section>
      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">{t('admin.orders')}</span><h2>{t('nav.orders')}</h2></div><label>{t('admin.allStatuses')}<select value={orderFilter} onChange={(event) => setOrderFilter(event.target.value)}><option value="ALL">{t('admin.allStatuses')}</option>{['NEW', 'ACCEPTED', 'PREPARING', 'READY', 'DELIVERED', 'COMPLETED', 'CANCELLED'].map((value) => <option key={value} value={value}>{t(`order.status.${value.toLowerCase()}`)}</option>)}</select></label><button onClick={() => void fetchData()}>{t('common.retry')}</button></div>
        {orders.length === 0 ? <p className="state">{t('admin.noOrders')}</p> : <div className="order-list">{orders.filter((order) => orderFilter === 'ALL' || order.order_status === orderFilter).map((order) => <article className="order-row" key={order.id}>
          <strong>{order.public_code}</strong><span>{t('checkout.roomNumber')} {order.room_number}</span><span>{t(`order.status.${order.order_status.toLowerCase()}`)}</span><span>{order.payment_status === 'PAID_CONFIRMED' ? t('payment.confirmed') : order.payment_status === 'PROOF_SUBMITTED' ? t('payment.pending') : order.payment_status === 'REJECTED' ? t('payment.rejected') : t('order.status.unpaid')}</span><b>{amount(order.total_minor, order.currency, i18n.language)}</b>
          {order.items?.length ? <div className="order-items">{order.items.map((item, index) => <span key={`${order.id}-${index}`}>{typeof item.name === 'string' ? item.name : label(item.name, i18n.language)} × {item.quantity} · {amount(item.line_total_minor, order.currency, i18n.language)}</span>)}</div> : null}
          {order.payment_status === 'PROOF_SUBMITTED' && <details><summary>{t('admin.openPaymentProof')}</summary><img className="payment-proof" src={`/api/v1/admin/orders/${order.id}/payment-proof`} alt={t('admin.paymentImage')} /><p>{t('admin.checkActualPayment')}</p>{order.order_status === 'ACCEPTED' && <div className="actions"><button disabled={busyId === order.id} onClick={() => void reviewPayment(order, 'APPROVED')}>{t('order.confirmPayment')}</button><button disabled={busyId === order.id} onClick={() => void reviewPayment(order, 'REJECTED')}>{t('order.reject')}</button></div>}</details>}
          <div className="actions">{order.order_status === 'NEW' && <button onClick={() => void changeStatus(order, 'ACCEPTED')}>{t('order.accept')}</button>}{order.order_status === 'PREPARING' && order.payment_status === 'PAID_CONFIRMED' && <button onClick={() => void changeStatus(order, 'READY')}>{t('order.markReady')}</button>}{order.order_status === 'READY' && <button onClick={() => void changeStatus(order, 'DELIVERED')}>{t('order.markDelivered')}</button>}{order.order_status === 'DELIVERED' && <button onClick={() => void changeStatus(order, 'COMPLETED')}>{t('order.complete')}</button>}{!['CANCELLED', 'COMPLETED'].includes(order.order_status) && order.payment_status !== 'PAID_CONFIRMED' && <button onClick={() => void changeStatus(order, 'CANCELLED')}>{t('order.cancel')}</button>}</div>
        </article>)}</div>}
      </section>
    </>}
    {section === 'products' && role === 'MANAGER' && <section className="panel"><div className="panel-heading"><h2>{t('nav.products')}</h2></div>
      <form className="product-form" onSubmit={createCategory}><label>{t('admin.newCategory')} (EN)<input value={newCategory.en} onChange={(event) => setNewCategory({ ...newCategory, en: event.target.value })} required /></label><label>{t('admin.newCategory')} (中文)<input value={newCategory.zh} onChange={(event) => setNewCategory({ ...newCategory, zh: event.target.value })} required /></label><label>{t('admin.newCategory')} (ខ្មែរ)<input value={newCategory.km} onChange={(event) => setNewCategory({ ...newCategory, km: event.target.value })} required /></label><button type="submit">{editingCategoryId ? t('common.save') : t('common.add')}</button>{editingCategoryId && <button type="button" onClick={() => { setEditingCategoryId(null); setNewCategory({ en: '', zh: '', km: '' }); }}>{t('common.cancel')}</button>}</form>
      <div className="order-list">{categories.map((category) => <article className="order-row" key={category.id}><strong>{label(category.name, i18n.language)}</strong><span>{category.active ? t('common.available') : t('common.soldOut')}</span><button onClick={() => startCategoryEdit(category)}>{t('common.edit')}</button><button onClick={() => void toggleCategory(category)}>{category.active ? t('admin.deactivate') : t('admin.activate')}</button></article>)}</div>
      <form className="product-form" onSubmit={saveProduct}><label>{t('admin.category')}<select value={newProduct.categoryId} onChange={(event) => setNewProduct({ ...newProduct, categoryId: event.target.value })} required><option value="">—</option>{categories.map((category) => <option key={category.id} value={category.id}>{label(category.name, i18n.language)}</option>)}</select></label><label>{t('admin.productName')} (EN)<input value={newProduct.nameEn} onChange={(event) => setNewProduct({ ...newProduct, nameEn: event.target.value })} required /></label><label>{t('admin.productName')} (中文)<input value={newProduct.nameZh} onChange={(event) => setNewProduct({ ...newProduct, nameZh: event.target.value })} required /></label><label>{t('admin.productName')} (ខ្មែរ)<input value={newProduct.nameKm} onChange={(event) => setNewProduct({ ...newProduct, nameKm: event.target.value })} required /></label><label>{t('admin.description')} (EN)<input value={newProduct.descriptionEn} onChange={(event) => setNewProduct({ ...newProduct, descriptionEn: event.target.value })} /></label><label>{t('admin.description')} (中文)<input value={newProduct.descriptionZh} onChange={(event) => setNewProduct({ ...newProduct, descriptionZh: event.target.value })} /></label><label>{t('admin.description')} (ខ្មែរ)<input value={newProduct.descriptionKm} onChange={(event) => setNewProduct({ ...newProduct, descriptionKm: event.target.value })} /></label><label>{t('admin.imageUrl')}<input type="url" value={newProduct.imageUrl} onChange={(event) => setNewProduct({ ...newProduct, imageUrl: event.target.value })} /></label><label>{t('admin.price')}<input type="number" min="0.01" step="0.01" value={newProduct.price} onChange={(event) => setNewProduct({ ...newProduct, price: event.target.value })} required /></label><label>{t('common.currency')}<input value={newProduct.currency || storeSettings.currency} readOnly /></label><button className="primary" type="submit" disabled={!categories.length}>{editingProductId ? t('common.save') : t('admin.createProduct')}</button>{editingProductId && <button type="button" onClick={() => { setEditingProductId(null); setNewProduct(emptyProduct); }}>{t('common.cancel')}</button>}</form>
      <div className="order-list">{products.map((product) => <article className="order-row" key={product.id}><strong>{label(product.name, i18n.language)}</strong><span>{amount(product.price_minor, product.currency, i18n.language)}</span><span>{product.available ? t('common.available') : t('common.soldOut')}</span><button onClick={() => startProductEdit(product)}>{t('common.edit')}</button><button onClick={() => void toggleProduct(product)}>{product.available ? t('common.soldOut') : t('common.available')}</button></article>)}</div>
    </section>}
    {section === 'finance' && <section className="panel"><div className="panel-heading"><h2>{t('admin.finance')}</h2><div><label>{t('admin.from')} <input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label><label>{t('admin.to')} <input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label></div></div><p>{t('admin.timezone')}: {stats?.timezone}</p><p>{t('admin.manualReviewBasis')}</p><div className="metric-row"><div className="metric"><span>{t('admin.orderVolume')}</span><strong>{stats?.order_volume ?? '—'}</strong></div><div className="metric"><span>{t('admin.cancelledCount')}</span><strong>{stats?.cancelled_count ?? '—'}</strong></div></div>{(stats?.by_currency || []).map((row: any) => <div className="metric-row" key={row.currency}><div className="metric"><span>{t('admin.orderTotal')}</span><strong>{amount(row.order_total_minor, row.currency, i18n.language)}</strong></div><div className="metric"><span>{t('admin.confirmedReceipts')}</span><strong>{amount(row.confirmed_receipts_minor, row.currency, i18n.language)}</strong></div><div className="metric warning"><span>{t('admin.inReview')}</span><strong>{amount(row.in_review_minor, row.currency, i18n.language)}</strong></div><div className="metric"><span>{t('admin.cancelledAmount')}</span><strong>{amount(row.cancelled_total_minor, row.currency, i18n.language)}</strong></div></div>)}</section>}
    {section === 'settings' && role === 'MANAGER' && <section className="panel"><div className="panel-heading"><h2>{t('nav.settings')}</h2></div><form className="product-form" onSubmit={saveSettings}>
      <label>{t('common.currency')}<input maxLength={3} value={storeSettings.currency} onChange={(event) => setStoreSettings({ ...storeSettings, currency: event.target.value.toUpperCase() })} required /></label>
      <label>{t('admin.timezone')}<input value={storeSettings.timezone} onChange={(event) => setStoreSettings({ ...storeSettings, timezone: event.target.value })} required /></label>
      <label>{t('admin.paymentLink')}<input type="url" value={storeSettings.payment_link || ''} onChange={(event) => setStoreSettings({ ...storeSettings, payment_link: event.target.value || null })} /></label>
      <label>{t('admin.qrImageUrl')}<input value={storeSettings.aba_qr_asset_key || ''} onChange={(event) => setStoreSettings({ ...storeSettings, aba_qr_asset_key: event.target.value || null })} /></label>
      <label>{t('admin.groupId')}<input value={storeSettings.telegram_staff_group_id || ''} onChange={(event) => setStoreSettings({ ...storeSettings, telegram_staff_group_id: event.target.value || null })} /></label>
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
    </form><div className="order-list">{staff.map((member) => <article className="order-row" key={member.id}><strong>{member.login_name}</strong><span>{member.telegram_user_id || '—'}</span><select value={member.role} onChange={(event) => void changeStaffRole(member, event.target.value)}><option value="STAFF">{t('admin.waiter')}</option><option value="MANAGER">{t('admin.manager')}</option></select><span>{member.active ? t('admin.active') : t('admin.deactivate')}</span><button onClick={() => void toggleStaff(member)}>{member.active ? t('admin.deactivate') : t('admin.activate')}</button></article>)}</div></section>}
  </main>;
}

export default function App() {
  return <TelegramProvider><Routes><Route path="/" element={<CustomerPage />} /><Route path="/admin/login" element={<AdminLogin />} /><Route path="/admin" element={<AdminPage />} /></Routes></TelegramProvider>;
}
