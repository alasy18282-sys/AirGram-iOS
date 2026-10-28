export type TelegramWebApp = {
    initData?: string;
    initDataUnsafe?: { user?: { id?: number; first_name?: string; username?: string } };
    ready?: () => void;
    expand?: () => void;
    openTelegramLink?: (url: string) => void;
    openLink?: (url: string) => void;
};

declare global {
    interface Window {
        Telegram?: { WebApp?: TelegramWebApp };
    }
}

export const KOALGRAM_BOT_URL = 'https://t.me/KoalGramApp';

export const getTelegramWebApp = (): TelegramWebApp | undefined => window.Telegram?.WebApp;

export const getInitData = (): string => getTelegramWebApp()?.initData ?? '';
