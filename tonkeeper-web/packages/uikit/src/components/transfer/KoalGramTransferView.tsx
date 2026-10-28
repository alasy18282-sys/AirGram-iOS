import { AssetAmount } from '@tonkeeper/core/dist/entries/crypto/asset/asset-amount';
import { TON_ASSET } from '@tonkeeper/core/dist/entries/crypto/asset/constants';
import { RecipientData, isTonRecipientData } from '@tonkeeper/core/dist/entries/send';
import { useMutation, useQuery } from '@tanstack/react-query';
import React, { FC, PropsWithChildren, useMemo } from 'react';
import styled from 'styled-components';
import { koalGramGet, koalGramPost, useKoalGramAccount } from '../../state/koalgram';
import { ConfirmView, ConfirmViewAdditionalBottomSlot } from './ConfirmView';
import { ConfirmTransferView } from './ConfirmTransferView';

/** Mirrors airgram_chain.service.GRAM_SEND_FEE_NANO (0.0001 GRAM). */
export const KOALGRAM_SEND_FEE_NANO = 100_000;

const KOALGRAM_FEE = new AssetAmount({
    asset: TON_ASSET,
    weiAmount: KOALGRAM_SEND_FEE_NANO
});

const RecipientNotice = styled.div`
    margin: 0 16px 8px;
    padding: 10px 12px;
    border-radius: 12px;
    background: ${props => props.theme.backgroundContentTint};
    color: ${props => props.theme.textSecondary};
    font-size: 13px;
    line-height: 18px;
`;

type TransferViewProps = PropsWithChildren<{
    recipient: RecipientData;
    assetAmount: AssetAmount;
    isMax: boolean;
    onBack?: () => void;
    onClose: (confirmed?: boolean) => void;
    fitContent?: boolean;
}>;

const KoalGramTransferView: FC<TransferViewProps> = ({
    recipient,
    assetAmount,
    isMax,
    onBack,
    onClose,
    fitContent,
    children
}) => {
    const sendAmount = useMemo(() => {
        if (!isMax) {
            return assetAmount;
        }
        const rest = assetAmount.weiAmount.minus(KOALGRAM_SEND_FEE_NANO);
        return new AssetAmount({
            asset: assetAmount.asset,
            weiAmount: rest.isGreaterThan(0) ? rest : 0
        });
    }, [assetAmount, isMax]);

    const mutation = useMutation<boolean, Error>(async () => {
        const comment = isTonRecipientData(recipient) ? recipient.comment ?? '' : '';
        await koalGramPost('koalgram-send-gram', {
            to: recipient.address.address,
            amountNano: sendAmount.weiAmount.toFixed(0),
            comment
        });
        return true;
    });

    const recipientAddress = isTonRecipientData(recipient)
        ? recipient.address.address
        : '';
    const { data: recipientWallet } = useQuery(
        ['koalgram-recipient-wallet', recipientAddress],
        () =>
            koalGramGet<{ ownerUserId?: number }>(
                `/api/koalgram-nfts?address=${encodeURIComponent(recipientAddress)}`
            ),
        { enabled: !!recipientAddress }
    );
    const recipientUnlinked = !!recipientWallet && !recipientWallet.ownerUserId;

    const estimation = useMemo(
        () => ({
            data: { fee: { type: 'ton-asset' as const, extra: KOALGRAM_FEE } },
            isLoading: false,
            error: null
        }),
        []
    );

    return (
        <ConfirmView
            estimation={estimation}
            {...mutation}
            recipient={recipient}
            assetAmount={sendAmount}
            onBack={onBack}
            onClose={onClose}
            fitContent={fitContent}
        >
            {recipientUnlinked && (
                <ConfirmViewAdditionalBottomSlot>
                    <RecipientNotice>
                        Этот адрес ещё не привязан к KoalGram. GRAM будет зачислен
                        получателю сразу после привязки адреса в кошельке KoalGram.
                    </RecipientNotice>
                </ConfirmViewAdditionalBottomSlot>
            )}
            {children}
        </ConfirmView>
    );
};

export const ConfirmTransferViewSwitch: FC<TransferViewProps> = props => {
    const { data: account } = useKoalGramAccount();
    if (account?.linked && props.assetAmount.asset.id === TON_ASSET.id) {
        return <KoalGramTransferView {...props} />;
    }
    return <ConfirmTransferView {...props} />;
};
