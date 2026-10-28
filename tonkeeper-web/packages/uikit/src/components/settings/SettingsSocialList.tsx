import React, { FC, useMemo } from 'react';
import { useAppSdk } from '../../hooks/appSdk';
import { useTranslation } from '../../hooks/translation';
import { SettingsRoute, relative } from '../../libs/routes';
import {
    ContactSupportIcon,
    LegalDocumentsIcon,
    TelegramIcon,
    StarIcon
} from './SettingsIcons';
import { SettingsItem, SettingsList } from './SettingsList';
import { useNavigate } from '../../hooks/router/useNavigate';
import { useSupport } from '../../state/pro';

export const SettingsSocialList: FC = React.memo(() => {
    const navigate = useNavigate();
    const sdk = useAppSdk();
    const { data: support } = useSupport();

    const { t } = useTranslation();
    const items = useMemo(() => {
        const result = [] as SettingsItem[];

        if (sdk.storeUrl) {
            result.push({
                name: t('settings_rate'),
                icon: <StarIcon />,
                action: () => sdk.storeUrl && sdk.openPage(sdk.storeUrl)
            });
        }

        const supportUrl = support.url || 'https://t.me/KoalGramApp';
        return result.concat([
            {
                name: t(support.isPriority ? 'priority_support' : 'settings_support'),
                icon: <TelegramIcon />,
                action: () => sdk.openPage(supportUrl)
            },
            {
                name: t('settings_news'),
                icon: <TelegramIcon />,
                action: () => sdk.openPage('https://t.me/KoalGramNews')
            },
            {
                name: t('settings_contact_support'),
                icon: <ContactSupportIcon />,
                action: () => sdk.openPage('https://t.me/KoalGramApp')
            },
            {
                name: t('settings_legal_documents'),
                icon: <LegalDocumentsIcon />,
                action: () => navigate(relative(SettingsRoute.legal))
            }
        ]);
    }, [t, navigate, sdk, support]);

    return <SettingsList items={items} />;
});
