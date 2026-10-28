import { useQueryClient } from '@tanstack/react-query';
import { useAppSdk } from '@tonkeeper/uikit/dist/hooks/appSdk';
import {
    KOALGRAM_KEY,
    KoalGramNftDetail,
    koalGramGet,
    koalGramNftToNftItem,
    useKoalGramAccount,
    useLinkKoalGramWallet
} from '@tonkeeper/uikit/dist/state/koalgram';
import { useActiveWallet } from '@tonkeeper/uikit/dist/state/wallet';
import { AppRoute } from '@tonkeeper/uikit/dist/libs/routes';
import { FC, useEffect, useRef } from 'react';
import { useHistory, useLocation } from 'react-router-dom';

/**
 * Syncs the active TON wallet with the KoalGram account and opens NFTs
 * requested through bot deep links (`?nft=<address>`).
 */
export const KoalGramBridge: FC = () => {
    const wallet = useActiveWallet();
    const { data: account, isError: isAccountError } = useKoalGramAccount();
    const { mutate: linkWallet } = useLinkKoalGramWallet();
    const client = useQueryClient();
    const location = useLocation();
    const history = useHistory();
    const sdk = useAppSdk();

    const attemptedAddress = useRef<string | undefined>();
    const openedNft = useRef<string | undefined>();

    const walletAddress = wallet?.rawAddress;

    useEffect(() => {
        if (!account || isAccountError || !walletAddress) {
            return;
        }
        const linkedAddress = (account.address || '').toLowerCase();
        if (account.linked && linkedAddress === walletAddress.toLowerCase()) {
            return;
        }
        if (attemptedAddress.current === walletAddress) {
            return;
        }
        attemptedAddress.current = walletAddress;
        linkWallet(walletAddress, {
            onSuccess: () => client.invalidateQueries([KOALGRAM_KEY]),
            onError: error => {
                console.warn('KoalGram wallet auto-link failed:', error.message);
            }
        });
    }, [account, isAccountError, walletAddress, linkWallet, client]);

    useEffect(() => {
        const params = new URLSearchParams(location.search);
        const nftAddress = params.get('nft');
        const txId = params.get('tx');
        const account = params.get('wallet');
        if (!nftAddress && !txId && !account) {
            return;
        }

        params.delete('nft');
        params.delete('tx');
        params.delete('wallet');
        const search = params.toString();
        window.history.replaceState(
            null,
            '',
            `${window.location.pathname}${search ? `?${search}` : ''}${window.location.hash}`
        );

        if (nftAddress && openedNft.current !== nftAddress) {
            openedNft.current = nftAddress;
            koalGramGet<KoalGramNftDetail>(`/api/koalgram-nft/${encodeURIComponent(nftAddress)}`)
                .then(detail => sdk.openNft(koalGramNftToNftItem(detail.nft, detail.nft.owner_address)))
                .catch(error => {
                    console.warn('KoalGram NFT deep link failed:', error);
                });
        }
        if (txId || account) {
            history.push(AppRoute.activity);
        }
    }, [location.search, sdk, history]);

    return null;
};
