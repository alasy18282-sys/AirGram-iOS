import { QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AccountAddress, NftItem, TrustType } from '@tonkeeper/core/dist/tonApiV2';

/**
 * Bridge between the KoalGram chain backend (same-origin /api) and uikit models.
 */

export const KOALGRAM_KEY = 'koalgram';

const KOALGRAM_SYSTEM_ADDRESS = `0:${'0'.repeat(64)}`;

export type KoalGramNftAttribute = {
    trait_type: string;
    value: string | number;
    rarity?: number;
};

export type KoalGramNft = {
    address: string;
    item_index: number;
    owner_address: string;
    title: string;
    collection: string;
    model_name?: string;
    pattern_name?: string;
    backdrop_name?: string;
    attributes?: KoalGramNftAttribute[];
    imageUrl?: string;
    thumbUrl?: string;
    metadataUrl?: string;
    friendlyAddress?: string;
    created_at?: number;
};

export type KoalGramTransaction = {
    tx_id: string;
    user_id?: number;
    amount: number;
    date: number;
    title?: string | null;
    peer_user_id?: number | null;
    gift?: number;
    stargift_resale?: number;
    offer?: number;
};

export type KoalGramEvent = {
    id: number;
    height: number;
    ts: number;
    kind: string;
    address: string;
    nft_address: string;
    tx_hash: string;
    data?: {
        title?: string;
        slug?: string;
        collection?: string;
        image?: string;
    };
};

export type KoalGramWallet = {
    ok: boolean;
    linked?: boolean;
    address?: string;
    friendlyAddress?: string;
    ownerUserId?: number;
    gramBalance?: string;
    gramBalanceNano?: number;
    nftCount?: number;
    nfts?: KoalGramNft[];
    transactions?: KoalGramTransaction[];
    activity?: KoalGramEvent[];
    error?: string;
};

export type KoalGramNftDetail = {
    ok: boolean;
    nft: KoalGramNft;
    error?: string;
};

export type KoalGramRate = {
    ok: boolean;
    symbol?: string;
    prices?: Record<string, number>;
    provider?: string;
    updatedAt?: number;
    stale?: boolean;
    error?: string;
};

export type KoalGramLinkResult = {
    ok: boolean;
    address?: string;
    friendlyAddress?: string;
    unchanged?: boolean;
    error?: string;
};

export const getKoalGramInitData = (): string => {
    const webApp = (window as { Telegram?: { WebApp?: { initData?: string } } }).Telegram
        ?.WebApp;
    return typeof webApp?.initData === 'string' ? webApp.initData : '';
};

async function readJson(response: Response): Promise<any> {
    try {
        return await response.json();
    } catch {
        throw new Error('KoalGram вернул некорректный ответ. Попробуйте ещё раз.');
    }
}

export async function koalGramPost<T>(
    command: string,
    extra: Record<string, unknown> = {},
    initData: string = getKoalGramInitData()
): Promise<T> {
    const response = await fetch(`/api/${command}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ initData, ...extra })
    });
    const result = await readJson(response);
    if (!response.ok || !result?.ok) {
        throw new Error(result?.error || `KoalGram API ${command}: ошибка`);
    }
    return result as T;
}

export async function koalGramGet<T>(path: string): Promise<T> {
    const response = await fetch(path);
    const result = await readJson(response);
    if (!response.ok || !result?.ok) {
        throw new Error(result?.error || 'KoalGram API: ошибка');
    }
    return result as T;
}

export const useKoalGramWallet = (address: string | undefined) => {
    return useQuery<KoalGramWallet, Error>(
        [KOALGRAM_KEY, 'wallet', address],
        () =>
            koalGramGet<KoalGramWallet>(
                `/api/koalgram-nfts?address=${encodeURIComponent(address!)}`
            ),
        {
            enabled: Boolean(address),
            staleTime: 10000,
            refetchInterval: 20000,
            retry: 1
        }
    );
};

export const useKoalGramAccount = () => {
    const initData = getKoalGramInitData();
    return useQuery<KoalGramWallet, Error>(
        [KOALGRAM_KEY, 'account', initData],
        () => koalGramPost<KoalGramWallet>('koalgram-wallet', {}, initData),
        {
            enabled: Boolean(initData),
            staleTime: 15000,
            refetchInterval: 30000,
            retry: 1
        }
    );
};

export const useKoalGramLinkedAddress = (fallback?: string): string | undefined => {
    const { data: account } = useKoalGramAccount();
    return account?.address || fallback;
};

export const useLinkKoalGramWallet = () => {
    const client = useQueryClient();
    return useMutation<KoalGramLinkResult, Error, string>(
        address => koalGramPost<KoalGramLinkResult>('link-ton-wallet', { address }),
        {
            onSuccess: () => client.invalidateQueries([KOALGRAM_KEY])
        }
    );
};

export const useKoalGramNftDetail = (address: string | null | undefined) =>
    useQuery<KoalGramNftDetail, Error>(
        [KOALGRAM_KEY, 'nft', address],
        () => koalGramGet<KoalGramNftDetail>(`/api/koalgram-nft/${encodeURIComponent(address!)}`),
        {
            enabled: Boolean(address),
            staleTime: 30000,
            retry: 1
        }
    );

export const useKoalGramRate = () =>
    useQuery<KoalGramRate, Error>(
        [KOALGRAM_KEY, 'rate'],
        () => koalGramPost<KoalGramRate>('gram-rate'),
        {
            staleTime: 60000,
            retry: 1
        }
    );

export const isKoalGramNft = (nft?: { metadata?: Record<string, any> } | null): boolean =>
    nft?.metadata?._koalgram === '1';

export const koalGramAccount = (address: string, name?: string): AccountAddress => ({
    address,
    name,
    isScam: false,
    isWallet: true
});

export const koalGramSystemAccount = (): AccountAddress => ({
    address: KOALGRAM_SYSTEM_ADDRESS,
    name: 'KoalGram',
    isScam: false,
    isWallet: true
});

export const koalGramNftToNftItem = (nft: KoalGramNft, ownerAddress?: string): NftItem => {
    const image = nft.imageUrl || nft.thumbUrl || '';
    const thumb = nft.thumbUrl || nft.imageUrl || '';
    const previews = [
        { resolution: '100x100', url: thumb },
        { resolution: '500x500', url: image },
        { resolution: '1500x1500', url: image }
    ].filter(preview => Boolean(preview.url));

    return {
        address: nft.address,
        index: nft.item_index,
        owner: ownerAddress ? koalGramAccount(ownerAddress) : undefined,
        collection: nft.collection
            ? { address: '', name: nft.collection, description: '' }
            : undefined,
        verified: true,
        metadata: {
            _koalgram: '1',
            name: nft.title,
            description: nft.collection
                ? `NFT-подарок KoalGram · ${nft.collection}`
                : 'NFT-подарок KoalGram',
            image,
            attributes: (nft.attributes || []).map(attribute => ({
                trait_type: attribute.trait_type,
                value: String(attribute.value)
            }))
        },
        previews,
        approvedBy: [],
        trust: TrustType.Whitelist
    };
};

export const findCachedKoalGramNft = (
    client: QueryClient,
    address: string
): NftItem | undefined => {
    const cached = client.getQueriesData<KoalGramWallet>([KOALGRAM_KEY, 'wallet']);
    for (const [, data] of cached) {
        const nft = data?.nfts?.find(item => item.address === address);
        if (nft) {
            return koalGramNftToNftItem(nft, nft.owner_address);
        }
    }
    return undefined;
};
