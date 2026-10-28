import { QueryClient, useQuery, useQueryClient } from '@tanstack/react-query';
import { CryptoCurrency } from '@tonkeeper/core/dist/entries/crypto';
import { FiatCurrencies } from '@tonkeeper/core/dist/entries/fiat';
import { RatesApi, TokenRates } from '@tonkeeper/core/dist/tonApiV2';
import { shiftedDecimals } from '@tonkeeper/core/dist/utils/balance';
import BigNumber from 'bignumber.js';
import { useMemo } from 'react';
import { AssetData } from '../components/home/Jettons';
import { useAppContext } from '../hooks/appContext';
import { formatFiatCurrency } from '../hooks/balance';
import { QueryKey } from '../libs/queryKey';
import { useActiveApi } from './wallet';
import {
    TON_USDT_ASSET,
    TRON_USDT_ASSET
} from '@tonkeeper/core/dist/entries/crypto/asset/constants';
import {
    jettonToTonAssetAmount,
    tonAssetAddressToString
} from '@tonkeeper/core/dist/entries/crypto/asset/ton-asset';
import { KoalGramRate, koalGramPost } from './koalgram';

export interface TokenRate {
    diff7d: string;
    diff24h: string;
    prices: number;
}

export const toTokenRate = (rate: TokenRates, fiat: FiatCurrencies): TokenRate => {
    return Object.entries(rate).reduce((acc, [key, value]) => {
        acc[key] = value[fiat];
        return acc;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }, {} as Record<string, any>) as TokenRate;
};

const fetchKoalGramRate = async (fiat: FiatCurrencies): Promise<TokenRate | undefined> => {
    const gateway = await koalGramPost<KoalGramRate>('gram-rate');
    if (!gateway.ok || !gateway.prices) return undefined;
    const price = gateway.prices[fiat.toLowerCase()] ?? gateway.prices.usd;
    if (price === undefined || !Number.isFinite(price)) return undefined;
    return { prices: price, diff24h: '0', diff7d: '0' };
};

export const usePreFetchRates = () => {
    const { tonApiV2 } = useActiveApi();
    const { fiat } = useAppContext();
    const client = useQueryClient();

    return useQuery(
        [QueryKey.rate],
        async () => {
            try {
                const value = await new RatesApi(tonApiV2).getRates({
                    tokens: [CryptoCurrency.TON, CryptoCurrency.USDT],
                    currencies: [fiat]
                });

                if (value?.rates) {
                    for (const [token, rate] of Object.entries<TokenRates>(value.rates)) {
                        try {
                            const tokenRate = toTokenRate(rate, fiat);
                            if (tokenRate) {
                                client.setQueryData(getRateKey(fiat, token), tokenRate);
                            }
                        } catch (e) {
                            console.error(e);
                        }
                    }
                }
            } catch (e) {
                console.error('TonAPI rates are unavailable', e);
            }

            // GRAM is the KoalGram native coin; its market price comes from the
            // same gateway the wallet data does, and wins over the indexed rate.
            try {
                const gatewayRate = await fetchKoalGramRate(fiat);
                if (gatewayRate) {
                    client.setQueryData(getRateKey(fiat, CryptoCurrency.TON), gatewayRate);
                }
            } catch (e) {
                console.error('KoalGram GRAM rate is unavailable', e);
            }

            setTimeout(() => client.invalidateQueries([QueryKey.total]));
            return 'ok';
        },
        { retry: 0 }
    );
};

export const getRateKey = (fiat: FiatCurrencies, token: string) => [QueryKey.rate, fiat, token];

export const useRate = (token: string) => {
    const { tonApiV2 } = useActiveApi();
    const { fiat } = useAppContext();
    const client = useQueryClient();
    return useQuery<TokenRate, Error>(
        getRateKey(fiat, token),
        async () => {
            if (token === CryptoCurrency.TON) {
                try {
                    const gatewayRate = await fetchKoalGramRate(fiat);
                    if (gatewayRate) {
                        setTimeout(() => client.invalidateQueries([QueryKey.total]));
                        return gatewayRate;
                    }
                } catch (e) {
                    console.error('KoalGram GRAM rate is unavailable, falling back to TonAPI', e);
                }
            }

            const value = await new RatesApi(tonApiV2).getRates({
                tokens: [token],
                currencies: [fiat]
            });

            const tokenRate = toTokenRate(value.rates[token], fiat);
            if (!tokenRate || !tokenRate.prices) {
                throw new Error(`Missing price for token: ${token}`);
            }

            // TODO need refactoring -- two reactive sources of the tokens rates
            setTimeout(() => client.invalidateQueries([QueryKey.total]));
            return tokenRate;
        },
        { retry: 0 }
    );
};

export const useUSDTRate = () => {
    const { tonApiV2 } = useActiveApi();
    const { fiat } = useAppContext();

    return useQuery<TokenRate, Error>(
        getRateKey(fiat, TRON_USDT_ASSET.address),
        async () => {
            const token = tonAssetAddressToString(TON_USDT_ASSET.address);
            const value = await new RatesApi(tonApiV2).getRates({
                tokens: [token],
                currencies: [fiat]
            });

            try {
                const tokenRate = toTokenRate(value.rates[token], fiat);
                if (!tokenRate || !tokenRate.prices) {
                    throw new Error(`Missing price for token: ${token}`);
                }

                return tokenRate;
            } catch (e) {
                throw e;
            }
        },
        { retry: 0 }
    );
};

export const useFormatFiat = (rate: TokenRate | undefined, tokenAmount?: BigNumber.Value) => {
    const { fiat } = useAppContext();

    const [fiatPrice, fiatAmount] = useMemo(() => {
        if (!rate || !tokenAmount) return [undefined, undefined] as const;

        const fiatValue = new BigNumber(rate.prices).multipliedBy(tokenAmount);

        return [
            formatFiatCurrency(fiat, rate.prices),
            formatFiatCurrency(fiat, fiatValue)
        ] as const;
    }, [rate, fiat, tokenAmount]);
    return {
        fiatPrice,
        fiatAmount
    };
};

export const tokenRate = (client: QueryClient, fiat: FiatCurrencies, token: string) => {
    const key = getRateKey(fiat, token);

    return client.getQueryCache().find(key)?.state.data as TokenRate | undefined;
};

export const getTonFiatAmount = (client: QueryClient, fiat: FiatCurrencies, assets: AssetData) => {
    const rate = tokenRate(client, fiat, CryptoCurrency.TON);

    if (!rate) {
        return new BigNumber(0);
    }

    return shiftedDecimals(assets.ton.info.balance).multipliedBy(rate.prices);
};

export const getJettonsFiatAmount = (fiat: FiatCurrencies, assets: AssetData) => {
    return assets.ton.jettons.balances.reduce((total, b) => {
        const rate = b.price ? toTokenRate(b.price, fiat) : undefined;
        if (!rate) {
            return total;
        }
        return total.plus(jettonToTonAssetAmount(b).relativeAmount.multipliedBy(rate.prices));
    }, new BigNumber(0));
};
