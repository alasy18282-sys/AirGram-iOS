#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from shop_webapp import bridge


def main() -> None:
    parser = argparse.ArgumentParser(description='Shop WebApp bridge')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('catalog')

    top_donors = sub.add_parser('top-donors')
    top_donors.add_argument('--telegram-id', type=int, default=0)

    sub.add_parser('rebuild-top-donors')

    phone = sub.add_parser('get-assigned-phone')
    phone.add_argument('--telegram-id', type=int, required=True)

    login_send = sub.add_parser('send-login-code')
    login_send.add_argument('--phone', required=True)
    login_send.add_argument('--telegram-id', type=int, default=0)

    login = sub.add_parser('login')
    login.add_argument('--phone', required=True)
    login.add_argument('--code', required=True)

    sess = sub.add_parser('session')
    sess.add_argument('--token', required=True)

    star_order = sub.add_parser('create-star-order')
    star_order.add_argument('--user-id', type=int, required=True)
    star_order.add_argument('--stars', type=int, required=True)
    star_order.add_argument('--promo', default='')
    star_order.add_argument('--target-user-id', type=int, default=0)

    anon_order = sub.add_parser('create-anon-order')
    anon_order.add_argument('--user-id', type=int, required=True)
    anon_order.add_argument('--kind', required=True)
    anon_order.add_argument('--digits', default='')
    anon_order.add_argument('--promo', default='')
    anon_order.add_argument('--target-user-id', type=int, default=0)

    username_order = sub.add_parser('create-username-order')
    username_order.add_argument('--user-id', type=int, required=True)
    username_order.add_argument('--username', required=True)
    username_order.add_argument('--promo', default='')
    username_order.add_argument('--target-user-id', type=int, default=0)

    lookup_username = sub.add_parser('lookup-username')
    lookup_username.add_argument('--username', required=True)

    lookup_premium = sub.add_parser('lookup-premium-recipient')
    lookup_premium.add_argument('--username', required=True)

    premium_order = sub.add_parser('create-premium-order')
    premium_order.add_argument('--user-id', type=int, required=True)
    premium_order.add_argument('--months', type=int, required=True)
    premium_order.add_argument('--recipient-username', required=True)

    resolve_target = sub.add_parser('resolve-target')
    resolve_target.add_argument('--user-id', type=int, required=True)
    resolve_target.add_argument('--target', default='')
    resolve_target.add_argument('--telegram-id', type=int, default=0)

    targets = sub.add_parser('list-purchase-targets')
    targets.add_argument('--token', required=True)

    promo = sub.add_parser('validate-promo')
    promo.add_argument('--promo', default='')
    promo.add_argument('--stars', type=int, default=0)
    promo.add_argument('--kind', default='')
    promo.add_argument('--username', default='')

    val = sub.add_parser('validate-payment')
    val.add_argument('--payload', required=True)
    val.add_argument('--xtr', type=int, required=True)

    pay = sub.add_parser('complete-payment')
    pay.add_argument('--payload', required=True)
    pay.add_argument('--payment-id', required=True)
    pay.add_argument('--buyer-telegram-id', type=int, default=0)

    anon_list = sub.add_parser('list-anonymous')
    anon_list.add_argument('--token', required=True)

    admin_lookup = sub.add_parser('admin-lookup')
    admin_lookup.add_argument('--username', required=True)

    admin_label = sub.add_parser('admin-set-user-label')
    admin_label.add_argument('--username', required=True)
    admin_label.add_argument('--label', choices=('none', 'scam', 'fake'), required=True)

    admin_stars = sub.add_parser('admin-grant-stars')
    admin_stars.add_argument('--username', required=True)
    admin_stars.add_argument('--amount', type=int, required=True)

    admin_revoke_stars = sub.add_parser('admin-revoke-stars')
    admin_revoke_stars.add_argument('--username', required=True)
    admin_revoke_stars.add_argument('--amount', type=int, required=True)

    admin_gram = sub.add_parser('admin-grant-gram')
    admin_gram.add_argument('--username', required=True)
    admin_gram.add_argument('--nanoton', type=int, required=True)

    admin_premium = sub.add_parser('admin-grant-premium')
    admin_premium.add_argument('--username', required=True)
    admin_premium.add_argument('--days', type=int, default=30)

    admin_revoke_premium = sub.add_parser('admin-revoke-premium')
    admin_revoke_premium.add_argument('--username', required=True)

    admin_ban = sub.add_parser('admin-ban')
    admin_ban.add_argument('--username', required=True)
    admin_ban.add_argument('--block-recreate', action='store_true')

    admin_unban = sub.add_parser('admin-unban')
    admin_unban.add_argument('--query', required=True)

    admin_lookup_unban = sub.add_parser('admin-lookup-unban')
    admin_lookup_unban.add_argument('--query', required=True)

    admin_number = sub.add_parser('admin-give-number')
    admin_number.add_argument('--telegram-id', type=int, required=True)
    admin_number.add_argument('--phone', required=True)

    admin_take_number = sub.add_parser('admin-take-number')
    admin_take_number.add_argument('--telegram-id', type=int, required=True)
    admin_take_number.add_argument('--phone', required=True)

    freeze_get = sub.add_parser('get-freeze-settings')
    freeze_set = sub.add_parser('set-freeze-settings')
    freeze_set.add_argument('--emoji', default='')
    freeze_set.add_argument('--emoji-id', default='')
    freeze_set.add_argument('--appeal-url', default='')
    freeze_set.add_argument('--freeze-days', type=int, default=30)

    admin_freeze = sub.add_parser('admin-freeze')
    admin_freeze.add_argument('--username', required=True)

    admin_unfreeze = sub.add_parser('admin-unfreeze')
    admin_unfreeze.add_argument('--username', required=True)

    banner_get = sub.add_parser('get-banner')
    banner_set = sub.add_parser('set-banner')
    banner_set.add_argument('--enabled', type=int, default=0)
    banner_set.add_argument('--emoji', default='')
    banner_set.add_argument('--emoji-id', default='')
    banner_set.add_argument('--title', default='')
    banner_set.add_argument('--description', default='')
    banner_set.add_argument('--url', default='')
    banner_set.add_argument('--suggestion-id', default='airgram_banner')

    gifts_list = sub.add_parser('admin-list-gifts')

    gifts_update = sub.add_parser('admin-update-gift')
    gifts_update.add_argument('--gift-id', type=int, required=True)
    gifts_update.add_argument('--title', default='')
    gifts_update.add_argument('--stars', type=int, default=None)
    gifts_update.add_argument('--convert-stars', type=int, default=None)
    gifts_update.add_argument('--upgrade-stars', type=int, default=None)
    gifts_update.add_argument('--limited', type=int, default=None)
    gifts_update.add_argument('--availability-total', type=int, default=None)
    gifts_update.add_argument('--availability-remains', type=int, default=None)
    gifts_update.add_argument('--sold-out', type=int, default=None)
    gifts_update.add_argument('--require-premium', type=int, default=None)
    gifts_update.add_argument('--birthday', type=int, default=None)
    gifts_update.add_argument('--peer-color-available', type=int, default=None)
    gifts_update.add_argument('--sort-order', type=int, default=None)

    gifts_delete = sub.add_parser('admin-delete-gift')
    gifts_delete.add_argument('--gift-id', type=int, required=True)

    gifts_import = sub.add_parser('admin-import-gifts')
    gifts_import.add_argument('--query', default='')
    gifts_import.add_argument('--all', action='store_true')
    gifts_import.add_argument('--include-upgrades', action='store_true')
    gifts_import.add_argument('--title', default='')
    gifts_import.add_argument('--stars', type=int, default=None)
    gifts_import.add_argument('--convert-stars', type=int, default=None)
    gifts_import.add_argument('--upgrade-stars', type=int, default=None)
    gifts_import.add_argument('--limited', type=int, default=None)
    gifts_import.add_argument('--availability-total', type=int, default=None)
    gifts_import.add_argument('--availability-remains', type=int, default=None)
    gifts_import.add_argument('--gift-count', type=int, default=None)
    gifts_import.add_argument('--require-premium', type=int, default=None)
    gifts_import.add_argument('--birthday', type=int, default=None)
    gifts_import.add_argument('--peer-color-available', type=int, default=None)

    crafts_import = sub.add_parser('admin-import-crafts')
    crafts_import.add_argument('--gift-id', type=int, required=True)

    sub.add_parser('admin-list-telegram-gifts')

    upgraded_list = sub.add_parser('admin-list-upgraded-gifts')
    upgraded_list.add_argument('--query', default='')
    upgraded_list.add_argument('--gift-id', type=int, default=0)
    upgraded_list.add_argument('--offset', type=int, default=0)
    upgraded_list.add_argument('--limit', type=int, default=60)

    upgraded_get = sub.add_parser('admin-get-upgraded-gift')
    upgraded_get.add_argument('--saved-id', type=int, default=0)
    upgraded_get.add_argument('--unique-num', type=int, default=0)
    upgraded_get.add_argument('--gift-id', type=int, default=0)

    attrs_list = sub.add_parser('admin-list-gift-attrs')
    attrs_list.add_argument('--gift-id', type=int, required=True)

    attr_update = sub.add_parser('admin-update-gift-attr')
    attr_update.add_argument('--attr-id', type=int, required=True)
    attr_update.add_argument('--name', default=None)
    attr_update.add_argument('--rarity-permille', type=int, default=None)
    attr_update.add_argument('--document-id', type=int, default=None)
    attr_update.add_argument('--backdrop-id', type=int, default=None)
    attr_update.add_argument('--center-color', type=int, default=None)
    attr_update.add_argument('--edge-color', type=int, default=None)
    attr_update.add_argument('--pattern-color', type=int, default=None)
    attr_update.add_argument('--text-color', type=int, default=None)
    attr_update.add_argument('--crafted', type=int, default=None)

    upgraded_update = sub.add_parser('admin-update-upgraded-gift')
    upgraded_update.add_argument('--saved-id', type=int, required=True)
    upgraded_update.add_argument('--model-attr-id', type=int, default=None)
    upgraded_update.add_argument('--pattern-attr-id', type=int, default=None)
    upgraded_update.add_argument('--backdrop-attr-id', type=int, default=None)

    cases_list = sub.add_parser('admin-list-cases')
    cases_save = sub.add_parser('admin-save-case')
    cases_save.add_argument('--case-id', default='')
    cases_save.add_argument('--title', required=True)
    cases_save.add_argument('--cover-path', required=True)
    cases_save.add_argument('--price-xtr', type=int, required=True)
    cases_save.add_argument('--price-airgram', type=int, default=0)
    cases_save.add_argument('--sort-order', type=int, default=0)
    cases_save.add_argument('--admin-only', type=int, default=0)
    cases_save.add_argument('--open-limit', type=int, default=0)
    cases_save.add_argument('--items-json', required=True)
    cases_delete = sub.add_parser('admin-delete-case')
    cases_delete.add_argument('--case-id', required=True)
    cases_auto = sub.add_parser('cases-auto-weights')
    cases_auto.add_argument('--items-json', required=True)
    cases_stats = sub.add_parser('admin-case-stats')
    cases_stats.add_argument('--period', default='month')
    cases_batch_get = sub.add_parser('admin-get-case-batch')
    cases_batch_get.add_argument('--telegram-id', type=int, required=True)
    cases_batch_set = sub.add_parser('admin-set-case-batch')
    cases_batch_set.add_argument('--telegram-id', type=int, required=True)
    cases_batch_set.add_argument('--max-batch', type=int, required=True)
    cases_public = sub.add_parser('list-cases')
    cases_public.add_argument('--user-id', type=int, default=0)
    cases_public.add_argument('--telegram-id', type=int, default=0)
    cases_get = sub.add_parser('get-case')
    cases_get.add_argument('--case-id', required=True)
    cases_get.add_argument('--user-id', type=int, default=0)
    cases_get.add_argument('--telegram-id', type=int, default=0)
    cases_demo = sub.add_parser('demo-case-roll')
    cases_demo.add_argument('--case-id', required=True)
    cases_order = sub.add_parser('create-case-order')
    cases_order.add_argument('--user-id', type=int, required=True)
    cases_order.add_argument('--case-id', required=True)
    cases_order.add_argument('--count', type=int, default=1)
    cases_order.add_argument('--telegram-id', type=int, default=0)
    cases_order.add_argument('--currency', default='auto')
    cases_pay = sub.add_parser('pay-case-order')
    cases_pay.add_argument('--user-id', type=int, required=True)
    cases_pay.add_argument('--nonce', required=True)
    cases_pay.add_argument('--buyer-telegram-id', type=int, default=0)
    cases_wins = sub.add_parser('list-case-wins')
    cases_wins.add_argument('--user-id', type=int, required=True)
    cases_live = sub.add_parser('list-case-live-drops')
    cases_live.add_argument('--viewer-key', default='')
    cases_live.add_argument('--limit', type=int, default=0)
    cases_sell = sub.add_parser('sell-case-win')
    cases_sell.add_argument('--user-id', type=int, required=True)
    cases_sell.add_argument('--win-id', type=int, required=True)
    cases_withdraw = sub.add_parser('withdraw-case-win')
    cases_withdraw.add_argument('--user-id', type=int, required=True)
    cases_withdraw.add_argument('--win-id', type=int, required=True)
    cases_withdraw.add_argument('--username', required=True)
    cases_lookup = sub.add_parser('lookup-case-recipient')
    cases_lookup.add_argument('--username', required=True)
    cases_queue = sub.add_parser('process-case-delivery-queue')

    args = parser.parse_args()
    argv = sys.argv[2:]
    result = bridge.dispatch(args.command, argv)
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == '__main__':
    main()
