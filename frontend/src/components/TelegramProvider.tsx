import React, { useEffect, useState } from 'react';
import WebApp from '@twa-dev/sdk';
import { useAuthStore } from '../store/authStore';
import { useTranslation } from 'react-i18next';
import api from '../api';

export const TelegramProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { setCustomerAuth } = useAuthStore();
  const { i18n, t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<'telegram' | 'session' | null>(null);

  useEffect(() => {
    if (window.location.pathname.startsWith('/admin')) {
      setLoading(false);
      return;
    }
    WebApp.ready();

    const authenticate = async () => {
      try {
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
      } catch {
        setError('session');
      } finally {
        setLoading(false);
      }
    };

    authenticate();
  }, [setCustomerAuth, i18n]);

  if (error) {
    return <div style={{ padding: 20, textAlign: 'center', color: 'red' }}>{error === 'telegram' ? t('auth.openFromTelegram') : t('auth.sessionExpired')}</div>;
  }

  if (loading) {
    return <div style={{ padding: 20, textAlign: 'center' }}>{t('common.loading')}</div>;
  }

  return <>{children}</>;
};
