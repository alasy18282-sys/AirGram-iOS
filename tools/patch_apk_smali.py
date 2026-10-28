#!/usr/bin/env python3
"""Inject AirGram smali patches into a decompiled Telegram-Android tree.

Two additions to org.telegram.messenger.ApplicationLoader:

1. ``airgramResetStaleDc`` — on startup, wipe the native connection cache
   (tgnet.dat + dc{1..5}conf.dat) when it still points at official Telegram
   DCs.  The 12.7.0 version of this patch shipped with inverted conditions
   (wiped OUR cache, kept the stale official one) and a flipped exists()
   check; this rewrite only deletes caches that carry official-DC markers
   and none of our IP.

2. ``airgramSetupProxy`` — registers the built-in fakeTLS MTProto proxy
   (dd-secret) and enables it on first launch, so the app tunnels its
   MTProto inside TLS-lookalike records and passes DPI without a VPN.

Called from ApplicationLoader.onCreate right after super.onCreate().

Usage: python3 tools/patch_apk_smali.py <apktool-dir>
"""
import sys
from pathlib import Path

SERVER_IP = '84.21.173.237'
PROXY_SECRET = 'dd8193e2e2e30d0428b52a602e5e836f90projectre.work'

MARKERS = [
    '149.154.', '91.108.', '95.161.76', '185.76.151', '169.58.61',
]


def _marker_checks() -> str:
    parts = []
    for i, marker in enumerate(MARKERS):
        parts.append(
            f'    const-string v4, "{marker}"\n'
            '\n'
            '    invoke-virtual {v2, v4}, Ljava/lang/String;->contains('
            'Ljava/lang/CharSequence;)Z\n'
            '\n'
            '    move-result v4\n'
            '\n'
            '    if-eqz v4, :cond_m{}\n'.replace('{}', str(i)) +
            '\n'
            '    const/4 v6, 0x1\n'
            '\n'
            f'    :cond_m{i}\n')
    return '\n'.join(parts)


def _reset_stale_dc_method() -> str:
    checks = _marker_checks()
    body = f'''
.method public static airgramResetStaleDc(Landroid/content/Context;)V
    .locals 8

    :try_start_0
    invoke-virtual {{p0}}, Landroid/content/Context;->getFilesDir()Ljava/io/File;

    move-result-object v0

    if-eqz v0, :cond_ret

    new-instance v1, Ljava/io/File;

    const-string v2, "tgnet.dat"

    invoke-direct {{v1, v0, v2}}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    invoke-virtual {{v1}}, Ljava/io/File;->exists()Z

    move-result v2

    if-eqz v2, :cond_ret

    invoke-virtual {{v1}}, Ljava/io/File;->length()J

    move-result-wide v2

    const-wide/16 v4, 0x0

    cmp-long v6, v2, v4

    if-lez v6, :cond_wipe

    new-instance v6, Ljava/io/FileInputStream;

    invoke-direct {{v6, v1}}, Ljava/io/FileInputStream;-><init>(Ljava/io/File;)V

    long-to-int v2, v2

    new-array v7, v2, [B

    invoke-virtual {{v6, v7}}, Ljava/io/FileInputStream;->read([B)I

    invoke-virtual {{v6}}, Ljava/io/FileInputStream;->close()V

    new-instance v2, Ljava/lang/String;

    const-string v3, "ISO-8859-1"

    invoke-direct {{v2, v7, v3}}, Ljava/lang/String;-><init>([BLjava/lang/String;)V

    const/4 v6, 0x0

    const/4 v7, 0x0

{checks}
    const-string v4, "{SERVER_IP}"

    invoke-virtual {{v2, v4}}, Ljava/lang/String;->contains(Ljava/lang/CharSequence;)Z

    move-result v4

    if-eqz v4, :cond_check

    const/4 v7, 0x1

    :cond_check
    if-eqz v6, :cond_ret

    if-nez v7, :cond_ret

    :cond_wipe
    invoke-virtual {{v1}}, Ljava/io/File;->delete()Z

    const/4 v2, 0x1

    :goto_loop
    const/4 v3, 0x5

    if-gt v2, v3, :cond_ret

    new-instance v4, Ljava/io/File;

    new-instance v5, Ljava/lang/StringBuilder;

    invoke-direct {{v5}}, Ljava/lang/StringBuilder;-><init>()V

    const-string v6, "dc"

    invoke-virtual {{v5, v6}}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {{v5, v2}}, Ljava/lang/StringBuilder;->append(I)Ljava/lang/StringBuilder;

    const-string v6, "conf.dat"

    invoke-virtual {{v5, v6}}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {{v5}}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v5

    invoke-direct {{v4, v0, v5}}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    invoke-virtual {{v4}}, Ljava/io/File;->delete()Z

    add-int/lit8 v2, v2, 0x1

    goto :goto_loop

    :cond_ret
    :try_end_0
    .catchall {{:try_start_0 .. :try_end_0}} :catchall_0

    return-void

    :catchall_0
    move-exception v0

    return-void
.end method
'''
    return body


def _setup_proxy_method() -> str:
    return f'''
.method public static airgramSetupProxy()V
    .locals 8

    :try_start_0
    invoke-static {{}}, Lorg/telegram/messenger/MessagesController;->getGlobalMainSettings()Landroid/content/SharedPreferences;

    move-result-object v0

    const-string v1, "airgram_proxy_layer229_v2_ok"

    const/4 v2, 0x0

    invoke-interface {{v0, v1, v2}}, Landroid/content/SharedPreferences;->getBoolean(Ljava/lang/String;Z)Z

    move-result v1

    if-eqz v1, :cond_setup

    return-void

    :cond_setup
    const-string v2, "{SERVER_IP}"

    const/16 v3, 0x1bb

    const-string v4, ""

    const-string v5, ""

    const-string v6, "{PROXY_SECRET}"

    new-instance v7, Lorg/telegram/messenger/SharedConfig$ProxyInfo;

    invoke-direct/range {{v2 .. v7}}, Lorg/telegram/messenger/SharedConfig$ProxyInfo;-><init>(Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;)V

    invoke-static {{v7}}, Lorg/telegram/messenger/SharedConfig;->addProxy(Lorg/telegram/messenger/SharedConfig$ProxyInfo;)Lorg/telegram/messenger/SharedConfig$ProxyInfo;

    sput-object v7, Lorg/telegram/messenger/SharedConfig;->currentProxy:Lorg/telegram/messenger/SharedConfig$ProxyInfo;

    const/4 v1, 0x1

    invoke-static/range {{v1 .. v6}}, Lorg/telegram/tgnet/ConnectionsManager;->setProxySettings(ZLjava/lang/String;ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;)V

    invoke-interface {{v0}}, Landroid/content/SharedPreferences;->edit()Landroid/content/SharedPreferences$Editor;

    move-result-object v0

    const-string v1, "airgram_proxy_layer229_v2_ok"

    const/4 v2, 0x1

    invoke-interface {{v0, v1, v2}}, Landroid/content/SharedPreferences$Editor;->putBoolean(Ljava/lang/String;Z)Landroid/content/SharedPreferences$Editor;

    invoke-interface {{v0}}, Landroid/content/SharedPreferences$Editor;->apply()V

    const-string v1, "airgramSetupProxy: fakeTLS proxy configured"

    invoke-static {{v1}}, Lorg/telegram/messenger/FileLog;->d(Ljava/lang/String;)V
    :try_end_0
    .catchall {{:try_start_0 .. :try_end_0}} :catchall_0

    return-void

    :catchall_0
    move-exception v0

    return-void
.end method
'''


HOOK = (
    '    invoke-static {p0}, Lorg/telegram/messenger/ApplicationLoader;'
    '->airgramResetStaleDc(Landroid/content/Context;)V\n'
    '\n'
    '    invoke-static {}, Lorg/telegram/messenger/ApplicationLoader;'
    '->airgramSetupProxy()V\n\n'
)


def main() -> int:
    tree = Path(sys.argv[1])
    loader = tree / 'smali_classes3' / 'org' / 'telegram' / 'messenger' / \
        'ApplicationLoader.smali'
    text = loader.read_text(encoding='utf-8')
    if 'airgramSetupProxy' in text:
        updated = text.replace('"airgram_proxy_ok"',
                               '"airgram_proxy_layer229_ok"')
        if updated != text:
            loader.write_text(updated, encoding='utf-8')
            print(f'updated proxy bootstrap marker in {loader}')
        else:
            print('already patched')
        return 0
    text = text.replace(
        '.method public onCreate()V',
        _reset_stale_dc_method().strip() + '\n\n' +
        _setup_proxy_method().strip() + '\n\n' +
        '.method public onCreate()V', 1)
    hook_at = ('.line 292\n'
               '    sget-boolean v3, Lorg/telegram/messenger/BuildVars;'
               '->LOGS_ENABLED:Z')
    if hook_at not in text:
        raise SystemExit('onCreate hook point not found')
    text = text.replace(hook_at, HOOK + hook_at, 1)
    loader.write_text(text, encoding='utf-8')
    print(f'patched {loader}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
