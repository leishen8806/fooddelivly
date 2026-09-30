import React, { useEffect, useState } from 'react';
import WebApp from '@twa-dev/sdk';
import { useAuthStore } from '../store/authStore';
import { useTranslation } from 'react-i18next';
import api from '../api';

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
        const initData = WebApp.initData;
        
        if (!initData) {
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
