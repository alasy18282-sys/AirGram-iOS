import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Account } from '@tonkeeper/core/dist/entries/account';
import { FiatCurrencies } from '@tonkeeper/core/dist/entries/fiat';
import { localizationText } from '@tonkeeper/core/dist/entries/language';
import { defaultTonendpointConfig } from '@tonkeeper/core/dist/tonkeeperApi/tonendpoint';
import { getApiConfig, setProApiUrl } from '@tonkeeper/core/dist/entries/network';
import { WalletVersion } from '@tonkeeper/core/dist/entries/wallet';
import { CopyNotification } from '@tonkeeper/uikit/dist/components/CopyNotification';
import { FooterGlobalStyle } from '@tonkeeper/uikit/dist/components/Footer';
import { HeaderGlobalStyle } from '@tonkeeper/uikit/dist/components/Header';
import { DarkThemeContext } from '@tonkeeper/uikit/dist/components/Icon';
import { GlobalListStyle } from '@tonkeeper/uikit/dist/components/List';
import { Loading } from '@tonkeeper/uikit/dist/components/Loading';
import { ModalsRoot } from '@tonkeeper/uikit/dist/components/ModalsRoot';
import { SybHeaderGlobalStyle } from '@tonkeeper/uikit/dist/components/SubHeader';
import { AppContext, IAppContext } from '@tonkeeper/uikit/dist/hooks/appContext';
import { AppSdkContext } from '@tonkeeper/uikit/dist/hooks/appSdk';
import { useLock } from '@tonkeeper/uikit/dist/hooks/lock';
import { StorageContext } from '@tonkeeper/uikit/dist/hooks/storage';
import {
    I18nContext,
    TranslationContext,
    useTWithReplaces
} from '@tonkeeper/uikit/dist/hooks/translation';
import { UnlockNotification } from '@tonkeeper/uikit/dist/pages/home/UnlockNotification';
import { UserThemeProvider } from '@tonkeeper/uikit/dist/providers/UserThemeProvider';
import { useUserFiatQuery } from '@tonkeeper/uikit/dist/state/fiat';
import { useUserLanguage } from '@tonkeeper/uikit/dist/state/language';
import { useProApiUrl, useProBackupState } from '@tonkeeper/uikit/dist/state/pro';
import { useTonendpoint, useTonenpointConfig } from '@tonkeeper/uikit/dist/state/tonendpoint';
import {
    useAccountsStateQuery,
    useActiveAccountQuery,
    useMutateActiveAccount
} from '@tonkeeper/uikit/dist/state/wallet';
import { GlobalStyle } from '@tonkeeper/uikit/dist/styles/globalStyle';
import React, { FC, PropsWithChildren, Suspense, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { MobileView } from './AppMobile';
import { BrowserAppSdk } from './libs/appSdk';
import { getInitData } from './libs/koalgram';
import { useAnalytics, useAppHeight, useLayout } from './libs/hooks';
import { useGlobalPreferencesQuery } from '@tonkeeper/uikit/dist/state/global-preferences';
import { useGlobalSetup } from '@tonkeeper/uikit/dist/state/globalSetup';
import { useIsActiveAccountMultisig } from '@tonkeeper/uikit/dist/state/multisig';
import { BrowserRouter } from 'react-router-dom';
import { localesList } from '@tonkeeper/locales/localesList';
import { CryptoStrategyInstaller } from '@tonkeeper/uikit/dist/components/pro/CryptoStrategyInstaller';

const QrScanner = React.lazy(() => import('@tonkeeper/uikit/dist/components/QrScanner'));
const DesktopView = React.lazy(() => import('./AppDesktop'));

class WalletStartupBoundary extends React.Component<
    React.PropsWithChildren,
    { failed: boolean; message?: string }
> {
    state: { failed: boolean; message?: string } = { failed: false };

    static getDerivedStateFromError(error: Error) {
        return { failed: true, message: error?.message || 'Unknown startup error' };
    }

    componentDidCatch(error: Error) {
        console.error('Wallet startup failed', error);
    }

    render() {
        if (this.state.failed) {
            return <div style={{ position: 'fixed', inset: 0, display: 'grid', placeContent: 'center', gap: 12, padding: 24, background: '#10161f', color: '#e6edf5', textAlign: 'center', font: '16px system-ui' }}>
                <div>Не удалось загрузить кошелёк KoalGram.</div>
                <div role="alert" style={{ maxWidth: 420, color: '#aebdce', fontSize: 13, overflowWrap: 'anywhere' }}>{this.state.message}</div>
                <button type="button" onClick={() => window.location.reload()}>Перезагрузить</button>
            </div>;
        }
        return this.props.children;
    }
}

const WalletStartupFallback: FC = () => (
    <div role="status" style={{ position: 'fixed', inset: 0, display: 'grid', placeContent: 'center', gap: 12, padding: 24, background: '#10161f', color: '#e6edf5', textAlign: 'center', font: '16px system-ui' }}>
        <div>Загружаем кошелёк KoalGram…</div>
        <div style={{ color: '#8b98a8', fontSize: 13 }}>Если загрузка затянулась, перезагрузите WebApp.</div>
        <button type="button" onClick={() => window.location.reload()}>Перезагрузить</button>
    </div>
);

const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            staleTime: 30000,
            refetchOnWindowFocus: false
        }
    }
});

const sdk = new BrowserAppSdk();

export const App: FC = () => {
    useEffect(() => {
        // Telegram keeps its native WebApp loading indicator visible until the
        // page announces readiness. Do this even if initData is unavailable so
        // the user can see an actionable in-app state instead of a frozen shell.
        const webApp = window.Telegram?.WebApp;
        webApp?.ready?.();
        webApp?.expand?.();
    }, []);

    const basename = window.location.pathname.startsWith('/wallet') ? '/wallet' : '';

    return (
        <BrowserRouter basename={basename}>
            <Providers />
        </BrowserRouter>
    );
};

const Providers: FC<PropsWithChildren> = () => {
    const { t: tSimple, i18n } = useTranslation();

    const t = useTWithReplaces(tSimple);

    const translation = useMemo(() => {
        const client: I18nContext = {
            t,
            i18n: {
                enable: true,
                reloadResources: i18n.reloadResources,
                changeLanguage: i18n.changeLanguage as any,
                language: i18n.language,
                languages: localesList
            }
        };
        return client;
    }, [t, i18n, i18n.language]);

    return (
        <QueryClientProvider client={queryClient}>
            <WalletStartupBoundary>
                <Suspense fallback={<WalletStartupFallback />}>
                    <AppSdkContext.Provider value={sdk}>
                        <TranslationContext.Provider value={translation}>
                            <StorageContext.Provider value={sdk.storage}>
                                <ThemeAndContent />
                            </StorageContext.Provider>
                        </TranslationContext.Provider>
                    </AppSdkContext.Provider>
                </Suspense>
            </WalletStartupBoundary>
        </QueryClientProvider>
    );
};

const ThemeAndContent = () => {
    const { data } = useProBackupState();
    const isMobile = useLayout() || Boolean(getInitData());

    return (
        <UserThemeProvider
            isPro={Boolean(data?.valid)}
            isProSupported
            proDisplayType="desktop"
            displayType={isMobile ? 'compact' : 'full-width'}
        >
            <DarkThemeContext.Provider value={!data?.valid}>
                <GlobalStyle />
                <HeaderGlobalStyle />
                <FooterGlobalStyle />
                <SybHeaderGlobalStyle />
                <GlobalListStyle />
                <Loader />
                <UnlockNotification sdk={sdk} />
            </DarkThemeContext.Provider>
        </UserThemeProvider>
    );
};

const Loader: FC = () => {
    const { data: activeAccount } = useActiveAccountQuery();
    const { data: accounts } = useAccountsStateQuery();
    const { data: lang } = useUserLanguage();
    const { data: fiat } = useUserFiatQuery();
    useGlobalPreferencesQuery();
    useGlobalSetup();

    const [ios, standalone] = useMemo(() => {
        return [sdk.isIOs(), sdk.isStandalone()] as const;
    }, []);

    const lock = useLock(sdk);
    const { i18n } = useTranslation();

    const tonendpoint = useTonendpoint({
        build: sdk.version,
        lang,
        platform: 'web'
    });
    const { data: fetchedServerConfig } = useTonenpointConfig(tonendpoint);
    // Boot services occasionally time out from embedded browsers. The
    // Tonkeeper defaults keep the wallet usable while the query retries.
    const serverConfig = fetchedServerConfig ?? {
        mainnetConfig: defaultTonendpointConfig,
        testnetConfig: defaultTonendpointConfig
    };
    const { data: proApiUrl } = useProApiUrl(serverConfig?.mainnetConfig);
    const resolvedProApiUrl = proApiUrl ?? serverConfig.mainnetConfig.pro_api_url;

    useAppHeight();

    const { data: tracker } = useAnalytics(
        activeAccount || undefined,
        accounts,
        sdk.version,
        serverConfig?.mainnetConfig
    );

    useEffect(() => {
        if (lang && i18n.language !== localizationText(lang)) {
            void i18n.changeLanguage(localizationText(lang)).catch(error =>
                console.warn('Could not switch wallet language', error)
            );
        }
    }, [activeAccount, lang, i18n]);

    const isMobile = useLayout();

    // set api url synchronously
    setProApiUrl(resolvedProApiUrl);

    const context: IAppContext = {
        mainnetApi: getApiConfig(serverConfig.mainnetConfig),
        testnetApi: getApiConfig(serverConfig.testnetConfig),
        fiat: fiat ?? FiatCurrencies.USD,
        mainnetConfig: serverConfig.mainnetConfig,
        testnetConfig: serverConfig.testnetConfig,
        tonendpoint,
        standalone,
        extension: false,
        proFeatures: true,
        ios,
        defaultWalletVersion: WalletVersion.V5R1,
        hideMultisig: isMobile,
        hideFireblocks: true,
        tracker: tracker?.track
    };

    return (
        <AppContext.Provider value={context}>
            <CryptoStrategyInstaller>
                <Content activeAccount={activeAccount} lock={lock ?? false} standalone={standalone} />
                <CopyNotification hideSimpleCopyNotifications={!standalone} />
                <Suspense>
                    <QrScanner />
                </Suspense>
                <ModalsRoot />
            </CryptoStrategyInstaller>
        </AppContext.Provider>
    );
};

const Content: FC<{
    activeAccount?: Account | null;
    lock: boolean;
    standalone: boolean;
}> = ({ activeAccount, lock, standalone }) => {
    const isMobile = useLayout() || Boolean(getInitData());
    const { data: accounts = [] } = useAccountsStateQuery();
    const isActiveMultisig = useIsActiveAccountMultisig();
    const { mutate: setActiveAccount } = useMutateActiveAccount();

    useEffect(() => {
        if (isMobile && isActiveMultisig) {
            const firstNotMultisig = accounts.filter(a => a.type !== 'ton-multisig')[0];
            if (firstNotMultisig) {
                setActiveAccount(firstNotMultisig.id);
            }
        }
    }, [isMobile, isActiveMultisig, setActiveAccount]);

    return (
        <>
            {isMobile ? (
                <MobileView activeAccount={activeAccount} lock={lock} standalone={standalone} />
            ) : (
                <Suspense fallback={<Loading />}>
                    <DesktopView activeAccount={activeAccount} lock={lock} />
                </Suspense>
            )}
        </>
    );
};
