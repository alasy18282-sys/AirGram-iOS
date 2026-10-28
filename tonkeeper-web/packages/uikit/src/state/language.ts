import { useAppSdk } from '../hooks/appSdk';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AppKey } from '@tonkeeper/core/dist/Keys';
import { Language, localizationFrom, localizationText } from '@tonkeeper/core/dist/entries/language';
import { QueryKey } from '../libs/queryKey';
import { useTranslation } from '../hooks/translation';
import { DeprecatedAccountState } from '@tonkeeper/core/dist/entries/account';
import { IStorage } from '@tonkeeper/core/dist/Storage';
import { DeprecatedWalletState } from '@tonkeeper/core/dist/entries/wallet';

const detectWebAppLanguage = (): Language => {
    let raw = '';
    try {
        const webApp = (window as { Telegram?: { WebApp?: { initDataUnsafe?: { user?: { language_code?: string } } } } }).Telegram?.WebApp;
        raw = String(webApp?.initDataUnsafe?.user?.language_code || '');
    } catch {
        raw = '';
    }
    if (!raw) {
        try {
            raw = navigator.language || '';
        } catch {
            raw = '';
        }
    }
    const value = raw.trim().toLowerCase().replace(/-/g, '_');
    if (!value) {
        return Language.EN;
    }
    if (value.startsWith('zh')) {
        return /(tw|hant|hk|mo)/.test(value) ? Language.ZH_TW : Language.ZH_CN;
    }
    return localizationFrom(value.split('_')[0]);
};

export const useUserLanguage = () => {
    const sdk = useAppSdk();
    return useQuery<Language>(
        [QueryKey.language],
        async () => {
            let lang = await sdk.storage.get<Language | undefined>(AppKey.LANGUAGE);

            if (!lang) {
                lang = await migrateLanguage(sdk.storage);
            }

            if (!lang || !Object.values(Language).includes(lang)) {
                return detectWebAppLanguage();
            }

            return lang;
        },
        {
            keepPreviousData: true
        }
    );
};

async function migrateLanguage(storage: IStorage): Promise<Language | undefined> {
    const state = await storage.get<DeprecatedAccountState>(AppKey.DEPRECATED_ACCOUNT);

    if (!state || !state.activePublicKey) {
        return;
    }

    const wallet = await storage.get<DeprecatedWalletState>(
        `${AppKey.DEPRECATED_WALLET}_${state.activePublicKey}`
    );
    return wallet?.lang;
}

export const useMutateUserLanguage = () => {
    const { i18n } = useTranslation();

    const sdk = useAppSdk();
    const client = useQueryClient();
    return useMutation<void, Error, Language>(async lang => {
        const locale = localizationText(lang);
        await i18n.changeLanguage(locale);
        await sdk.storage.set(AppKey.LANGUAGE, lang);
        await client.invalidateQueries([QueryKey.language]);
    });
};
