#!/usr/bin/env python3
"""Rebuild and sign the layer-229 client while retaining original resources.

The bundled apktool version cannot re-link Telegram 12.10's API-35 manifest.
It still assembles the patched smali before that step fails, so this script
replaces the patched DEX files in the known layer-229 APK, preserving its
resource and native-library tables.
"""
from __future__ import annotations

import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT_APK = ROOT.parent / "clients" / "Telegram-12.10.1-airgram-layer229.apk"
DEX_DIR = ROOT / "apk_layer229" / "build" / "apk"
PATCHED_DEX_NAMES = ("classes3.dex", "classes4.dex")
KEYSTORE = ROOT / "keys" / "projectre2.jks"
KEY_INFO = ROOT / "keys" / "KEYSTORE_INFO.txt"
OUTPUT = ROOT / "MyTelegram-layer229.apk"


def _key_info() -> tuple[str, str]:
    fields = {}
    for line in KEY_INFO.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    password = fields.get("store/key password")
    alias = fields.get("alias")
    if not password or not alias:
        raise RuntimeError("Signing key metadata is incomplete")
    return password, alias


def main() -> None:
    if not INPUT_APK.is_file():
        raise FileNotFoundError("Layer-229 source APK is missing")
    assembled = subprocess.run(
        ["apktool", "b", "--use-aapt2", str(ROOT / "apk_layer229"),
         "-o", str(ROOT / "build" / "layer229-link-step.apk")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if assembled.returncode and "unexpected element <queries>" not in assembled.stdout:
        raise RuntimeError("Smali assembly failed; check the MTG build output")
    for dex_name in PATCHED_DEX_NAMES:
        if not (DEX_DIR / dex_name).is_file():
            raise FileNotFoundError(f"apktool did not produce {dex_name}")
    password, alias = _key_info()
    with tempfile.TemporaryDirectory(prefix="mtg-apk-") as tmp:
        tmp = Path(tmp)
        unsigned = tmp / "client-unsigned.apk"
        aligned = tmp / "client-aligned.apk"
        with zipfile.ZipFile(INPUT_APK, "r") as source, \
                zipfile.ZipFile(unsigned, "w") as target:
            for info in source.infolist():
                upper = info.filename.upper()
                if upper.startswith("META-INF/") and upper.endswith(
                        (".SF", ".RSA", ".DSA", ".EC")):
                    continue
                if info.filename in PATCHED_DEX_NAMES:
                    target.writestr(
                        info, (DEX_DIR / info.filename).read_bytes(),
                        compress_type=info.compress_type)
                else:
                    target.writestr(info, source.read(info.filename),
                                    compress_type=info.compress_type)
        subprocess.run(["zipalign", "-f", "-p", "4", str(unsigned),
                        str(aligned)], check=True)
        sign = subprocess.run([
            "apksigner", "sign", "--ks", str(KEYSTORE),
            "--ks-key-alias", alias,
            "--ks-pass", f"pass:{password}",
            "--key-pass", f"pass:{password}",
            "--out", str(OUTPUT), str(aligned),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if sign.returncode:
            raise RuntimeError("APK signing failed; check the signing key metadata")
        subprocess.run(["apksigner", "verify", "--verbose", str(OUTPUT)],
                       check=True, stdout=subprocess.DEVNULL)
    print(f"Built and signed {OUTPUT}")


if __name__ == "__main__":
    main()
