import React, { useEffect, useState } from 'react';
import WebApp from '@twa-dev/sdk';
import { useAuthStore } from '../store/authStore';
import { useTranslation } from 'react-i18next';
import api from '../api';

function getTelegramLaunchData() {
  const hash = window.location.hash.replace(/^#/, '');
  const hashQuery = hash.includes('?') ? hash.slice(hash.indexOf('?') + 1) : hash;
  const hashParams = new URLSearchParams(hashQuery);
  const searchParams = new URLSearchParams(window.location.search);
  const win = window as Window & {
    Telegram?: { WebApp?: { initDataUnsafe?: { user?: { id?: number } } }; WebView?: { isIframe?: boolean } };
    TelegramWebviewProxy?: unknown;
  };
  const hashData = hashParams.get('tgWebAppData') || '';
  const searchData = searchParams.get('tgWebAppData') || '';

  return {
    initData: WebApp.initData || hashData || searchData,
    diagnostics: {
      webApp: Boolean(win.Telegram?.WebApp),
      platform: String(WebApp.platform || 'unknown'),
      version: String(WebApp.version || 'unknown'),
      bridge: Boolean(win.TelegramWebviewProxy || win.Telegram?.WebView?.isIframe),
      hashParam: hashParams.has('tgWebAppData'),
      queryParam: searchParams.has('tgWebAppData'),
      unsafeUser: Boolean(win.Telegram?.WebApp?.initDataUnsafe?.user?.id),
    },
  };
}

export const TelegramProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { setCustomerAuth } = useAuthStore();
  const { i18n, t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<'telegram' | 'session' | 'network' | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    if (window.location.pathname.startsWith('/admin')) {
      setLoading(false);
      return;
    }
    const authenticate = async () => {
      try {
        try {
          WebApp.ready();
        } catch {
          // The Telegram bridge's ready event is advisory; validated initData is the auth source.
        }
        const { initData, diagnostics } = getTelegramLaunchData();
        
        if (!initData) {
          void api.post('/api/v1/auth/telegram', { initData: '' }, {
            headers: {
              'X-TC-TG-WebApp': diagnostics.webApp ? '1' : '0',
              'X-TC-TG-Platform': diagnostics.platform,
              'X-TC-TG-Version': diagnostics.version,
              'X-TC-TG-Bridge': diagnostics.bridge ? '1' : '0',
              'X-TC-TG-Hash': diagnostics.hashParam ? '1' : '0',
              'X-TC-TG-Query': diagnostics.queryParam ? '1' : '0',
              'X-TC-TG-Unsafe-User': diagnostics.unsafeUser ? '1' : '0',
            },
          }).catch(() => undefined);
          setError('telegram');
          setLoading(false);
          return;
        }

        const res = await api.post('/api/v1/auth/telegram', { initData });

        if (res.data.customer_id) {
          setCustomerAuth(res.data.customer_id);
          if (!localStorage.getItem('teacafe.language')) {
            await i18n.changeLanguage(res.data.language || 'en');
          }
        }
      } catch (requestError) {
        const status = (requestError as { response?: { status?: number } })?.response?.status;
        setError(status === 401 ? 'session' : 'network');
      } finally {
        setLoading(false);
      }
    };

    authenticate();
  }, [setCustomerAuth, i18n, retryCount]);

  if (loading) {
    return <main className="auth-shell"><section className="auth-card" role="status"><img className="login-logo" src="/tea-cafe-logo.png" alt="Tea Cafe" /><p>{t('common.loading')}</p></section></main>;
  }

  if (error) {
    const message = error === 'telegram' ? t('auth.openFromTelegram') : error === 'session' ? t('auth.sessionExpired') : t('error.network');
    return <main className="auth-shell"><section className="auth-card" role="alert"><img className="login-logo" src="/tea-cafe-logo.png" alt="Tea Cafe" /><span className="eyebrow">TEA CAFE</span><h1>{t('brand')}</h1><p>{message}</p><button className="primary" type="button" onClick={() => { setLoading(true); setError(null); setRetryCount((count) => count + 1); }}>{t('common.retry')}</button></section></main>;
  }

  return <>{children}</>;
};
