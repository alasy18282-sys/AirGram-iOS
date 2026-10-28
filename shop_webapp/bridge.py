"""Shared command dispatch for shop CLI and persistent worker."""
from __future__ import annotations

import json
from argparse import Namespace
from typing import Any

from shop_webapp import cases, crash, service


_INT_FIELDS = {
    'telegram_id', 'user_id', 'target_user_id', 'stars', 'xtr', 'months',
    'amount', 'nanoton', 'days', 'freeze_days', 'enabled', 'gift_id',
    'convert_stars', 'upgrade_stars', 'limited', 'availability_total',
    'availability_remains', 'sold_out', 'require_premium', 'birthday',
    'peer_color_available', 'sort_order', 'gift_count', 'offset', 'limit',
    'saved_id', 'unique_num', 'attr_id', 'rarity_permille', 'document_id',
    'backdrop_id', 'center_color', 'edge_color', 'pattern_color',
    'text_color', 'crafted', 'price_xtr', 'price_airgram', 'admin_only', 'count', 'win_id',
    'model_attr_id', 'pattern_attr_id', 'backdrop_attr_id',
    'buyer_telegram_id', 'max_batch',
}
_BOOL_FLAGS = {'block_recreate', 'all', 'include_upgrades'}


class _Args(Namespace):
    def __getattr__(self, name: str) -> Any:
        if name in _INT_FIELDS:
            return 0
        if name in _BOOL_FLAGS:
            return False
        return ''


def _argv_to_namespace(argv: list[str]) -> _Args:
    kwargs: dict[str, Any] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if not token.startswith('--'):
            i += 1
            continue
        key = token[2:].replace('-', '_')
        if i + 1 < len(argv) and not argv[i + 1].startswith('--'):
            kwargs[key] = argv[i + 1]
            i += 2
        else:
            kwargs[key] = True
            i += 1
    return _Args(**kwargs)


def _coerce_args(command: str, args: _Args) -> _Args:
    int_fields = _INT_FIELDS
    bool_flags = _BOOL_FLAGS
    data = vars(args).copy()
    for key, value in list(data.items()):
        if value is None:
            continue
        if key in int_fields and value is not True and value is not False:
            try:
                data[key] = int(value)
            except (TypeError, ValueError):
                pass
        elif key in bool_flags:
            if value is True:
                data[key] = True
            else:
                data[key] = bool(int(value)) if str(value).isdigit() else bool(value)
    return _Args(**data)


def dispatch(command: str, argv: list[str] | None = None) -> Any:
    args = _coerce_args(command, _argv_to_namespace(argv or []))
    handlers = {
        'catalog': lambda: service.catalog(),
        'top-donors': lambda: service.get_top_donors(args.telegram_id),
        'rebuild-top-donors': lambda: service.rebuild_top_donors(),
        'get-assigned-phone': lambda: service.get_assigned_phone(args.telegram_id),
        'send-login-code': lambda: service.send_login_code(
            args.phone, args.telegram_id,
        ),
        'login': lambda: service.complete_login(args.phone, args.code),
        'session': lambda: service.get_session(args.token),
        'create-star-order': lambda: service.create_star_order(
            args.user_id, args.stars, args.promo,
            target_user_id=args.target_user_id,
        ),
        'create-anon-order': lambda: service.create_anon_order(
            args.user_id, args.kind, args.digits, args.promo,
            target_user_id=args.target_user_id,
        ),
        'create-username-order': lambda: service.create_username_order(
            args.user_id, args.username, args.promo,
            target_user_id=args.target_user_id,
        ),
        'lookup-username': lambda: service.lookup_collectible_username(args.username),
        'lookup-premium-recipient': lambda: service.lookup_premium_recipient(
            args.username,
        ),
        'create-premium-order': lambda: service.create_premium_order(
            args.user_id, args.months, args.recipient_username,
        ),
        'resolve-target': lambda: service.resolve_purchase_target(
            args.user_id, args.target,
            telegram_user_id=args.telegram_id,
        ),
        'list-purchase-targets': lambda: service.list_purchase_targets(args.token),
        'validate-promo': lambda: service.validate_promo(
            args.promo, stars=args.stars, kind=args.kind,
            username=args.username,
        ),
        'validate-payment': lambda: service.validate_payment(args.payload, args.xtr),
        'complete-payment': lambda: service.complete_payment(
            args.payload, args.payment_id,
            buyer_telegram_id=args.buyer_telegram_id,
        ),
        'list-anonymous': lambda: service.list_anonymous_numbers(args.token),
        'admin-lookup': lambda: service.admin_lookup_user(args.username),
        'admin-set-user-label': lambda: service.admin_set_user_label(
            args.username, args.label,
        ),
        'admin-grant-stars': lambda: service.admin_grant_stars(
            args.username, args.amount,
        ),
        'admin-revoke-stars': lambda: service.admin_revoke_stars(
            args.username, args.amount,
        ),
        'admin-grant-gram': lambda: service.admin_grant_gram(
            args.username, args.nanoton,
        ),
        'admin-grant-premium': lambda: service.admin_grant_premium_username(
            args.username, days=int(args.days or 30),
        ),
        'admin-revoke-premium': lambda: service.admin_revoke_premium_username(
            args.username,
        ),
        'admin-ban': lambda: service.admin_ban_user(
            args.username, block_recreate=bool(args.block_recreate),
        ),
        'admin-unban': lambda: service.admin_unban_user(args.query),
        'admin-lookup-unban': lambda: service.admin_lookup_unban(args.query),
        'admin-give-number': lambda: service.admin_give_number(
            args.telegram_id, args.phone,
        ),
        'admin-take-number': lambda: service.admin_take_number(
            args.telegram_id, args.phone,
        ),
        'get-freeze-settings': lambda: service.admin_get_freeze_settings(),
        'set-freeze-settings': lambda: service.admin_set_freeze_settings(
            emoji=args.emoji,
            emoji_id=args.emoji_id,
            appeal_url=args.appeal_url,
            freeze_days=args.freeze_days,
        ),
        'admin-freeze': lambda: service.admin_freeze_user(args.username),
        'admin-unfreeze': lambda: service.admin_unfreeze_user(args.username),
        'get-banner': lambda: service.admin_get_banner(),
        'set-banner': lambda: service.admin_set_banner(
            enabled=bool(args.enabled),
            emoji=args.emoji,
            emoji_id=args.emoji_id,
            title=args.title,
            description=args.description,
            url=args.url,
            suggestion_id=args.suggestion_id,
        ),
        'admin-list-gifts': lambda: service.admin_list_star_gifts(),
        'admin-update-gift': lambda: service.admin_update_star_gift(
            args.gift_id,
            title=args.title,
            stars=args.stars,
            convert_stars=args.convert_stars,
            upgrade_stars=args.upgrade_stars,
            limited=(bool(args.limited) if args.limited is not None else None),
            availability_total=args.availability_total,
            availability_remains=args.availability_remains,
            sold_out=(bool(args.sold_out) if args.sold_out is not None else None),
            require_premium=(bool(args.require_premium)
                             if args.require_premium is not None else None),
            birthday=(bool(args.birthday) if args.birthday is not None else None),
            peer_color_available=(bool(args.peer_color_available)
                                  if args.peer_color_available is not None else None),
            sort_order=args.sort_order,
        ),
        'admin-delete-gift': lambda: service.admin_delete_star_gift(args.gift_id),
        'admin-import-gifts': lambda: service.admin_import_star_gifts(
            query=args.query,
            import_all=bool(args.all),
            include_upgrades=bool(args.include_upgrades),
            settings={
                'title': args.title,
                'stars': args.stars,
                'convertStars': args.convert_stars,
                'upgradeStars': args.upgrade_stars,
                'limited': (bool(args.limited) if args.limited is not None else None),
                'availabilityTotal': args.availability_total,
                'availabilityRemains': args.availability_remains,
                'giftCount': args.gift_count,
                'requirePremium': (bool(args.require_premium)
                                   if args.require_premium is not None else None),
                'birthday': (bool(args.birthday)
                             if args.birthday is not None else None),
                'peerColorAvailable': (bool(args.peer_color_available)
                                       if args.peer_color_available is not None else None),
            },
        ),
        'admin-import-crafts': lambda: service.admin_import_craft_models(
            gift_id=int(args.gift_id),
        ),
        'admin-list-telegram-gifts': lambda: service.admin_list_telegram_star_gifts(),
        'admin-list-upgraded-gifts': lambda: service.admin_list_upgraded_gifts(
            query=args.query,
            gift_id=args.gift_id,
            offset=args.offset,
            limit=args.limit,
        ),
        'admin-get-upgraded-gift': lambda: service.admin_get_upgraded_gift(
            saved_id=args.saved_id,
            unique_num=args.unique_num,
            gift_id=args.gift_id,
        ),
        'admin-list-gift-attrs': lambda: service.admin_list_gift_upgrade_attrs(args.gift_id),
        'admin-update-gift-attr': lambda: service.admin_update_gift_upgrade_attr(
            args.attr_id,
            name=args.name,
            rarity_permille=args.rarity_permille,
            document_id=args.document_id,
            backdrop_id=args.backdrop_id,
            center_color=args.center_color,
            edge_color=args.edge_color,
            pattern_color=args.pattern_color,
            text_color=args.text_color,
            crafted=(bool(args.crafted) if args.crafted is not None else None),
        ),
        'admin-update-upgraded-gift': lambda: service.admin_update_upgraded_gift_attrs(
            args.saved_id,
            model_attr_id=args.model_attr_id,
            pattern_attr_id=args.pattern_attr_id,
            backdrop_attr_id=args.backdrop_attr_id,
        ),
        'admin-list-cases': lambda: cases.admin_list_cases(),
        'admin-case-stats': lambda: cases.admin_case_stats(period=args.period),
        'admin-get-case-batch': lambda: cases.admin_get_case_batch_limit(
            telegram_user_id=args.telegram_id,
        ),
        'admin-set-case-batch': lambda: cases.admin_set_case_batch_limit(
            telegram_user_id=args.telegram_id,
            max_batch=args.max_batch,
        ),
        'admin-save-case': lambda: cases.admin_save_case(
            case_id=args.case_id,
            title=args.title,
            cover_path=args.cover_path,
            price_xtr=args.price_xtr,
            price_airgram=args.price_airgram,
            items=json.loads(args.items_json),
            sort_order=args.sort_order,
            admin_only=bool(args.admin_only),
            open_limit=args.open_limit,
        ),
        'admin-delete-case': lambda: cases.admin_delete_case(args.case_id),
        'cases-auto-weights': lambda: cases.admin_auto_weights(
            json.loads(args.items_json),
        ),
        'list-cases': lambda: cases.list_cases_public(
            user_id=args.user_id,
            telegram_user_id=args.telegram_id,
        ),
        'get-case': lambda: cases.get_case_public(
            args.case_id,
            user_id=args.user_id,
            telegram_user_id=args.telegram_id,
        ),
        'demo-case-roll': lambda: cases.demo_roll(args.case_id),
        'create-case-order': lambda: cases.create_case_order(
            args.user_id, args.case_id, args.count,
            telegram_user_id=args.telegram_id,
            currency=args.currency,
        ),
        'pay-case-order': lambda: cases.pay_case_order(
            args.user_id, args.nonce,
            buyer_telegram_id=args.buyer_telegram_id,
        ),
        'list-case-wins': lambda: cases.list_user_wins(args.user_id),
        'list-case-live-drops': lambda: cases.list_live_drops(
            viewer_key=str(getattr(args, 'viewer_key', '') or ''),
            limit=int(getattr(args, 'limit', 0) or 0) or cases.LIVE_DROPS_LIMIT,
        ),
        'sell-case-win': lambda: cases.sell_win(args.user_id, args.win_id),
        'withdraw-case-win': lambda: cases.withdraw_win(
            args.user_id, args.win_id, args.username,
        ),
        'lookup-case-recipient': lambda: cases.lookup_airgram_recipient(
            args.username,
        ),
        'process-case-delivery-queue': lambda: cases.process_delivery_queue(),
        'crash-state': lambda: crash.get_state(
            args.user_id,
            telegram_user_id=args.telegram_id,
        ),
        'crash-bet': lambda: crash.place_bet(
            args.user_id,
            args.amount,
            telegram_user_id=args.telegram_id,
        ),
        'crash-cashout': lambda: crash.cashout(
            args.user_id,
            telegram_user_id=args.telegram_id,
            client_mult=(
                float(args.multiplier)
                if str(getattr(args, 'multiplier', '') or '').strip()
                else None
            ),
        ),
        'crash-plane-lottie': lambda: crash.get_plane_lottie(),
    }
    if command not in handlers:
        raise ValueError(f'unknown command: {command}')
    return handlers[command]()
