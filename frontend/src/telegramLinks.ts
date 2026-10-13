type TelegramWebApp = {
  openTelegramLink?: (url: string) => void;
  initData?: string;
  initDataUnsafe?: { start_param?: string };
};

type TelegramWindow = Window & {
  Telegram?: { WebApp?: TelegramWebApp };
};

/** Open a bot deep link in Telegram when the Mini App bridge is available. */
export function openTelegramLink(url: string) {
  const webApp = (window as TelegramWindow).Telegram?.WebApp;
  if (webApp?.openTelegramLink) {
    try {
      webApp.openTelegramLink(url);
      return;
    } catch {
      // Fall back to the normal browser navigation below.
    }
  }
  window.location.assign(url);
}

/** Read the Mini App deep-link parameter from Telegram or its browser fallback. */
export function getTelegramStartParam(): string | null {
  const webApp = (window as TelegramWindow).Telegram?.WebApp;
  const webAppParam = webApp?.initDataUnsafe?.start_param;
  if (webAppParam) return webAppParam;
  const read = (value: string) => {
    try {
      const params = new URLSearchParams(value.replace(/^#/, '').replace(/^\?/, ''));
      return params.get('tgWebAppStartParam') || params.get('startapp') || params.get('start_param');
    }
    catch { return null; }
  };
  return read(webApp?.initData || '') || read(window.location.search) || read(window.location.hash);
}

/** Telegram may inject the bridge/hash parameters just after the first render. */
export async function waitForTelegramStartParam(): Promise<string | null> {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const value = getTelegramStartParam();
    if (value) return value;
    await new Promise((resolve) => window.setTimeout(resolve, 200));
  }
  return null;
}
