#!/usr/bin/env python3
"""Rewrite fake-codesigning mobileprovision bundle IDs and re-sign with SelfSigned.p12."""

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path


def run(cmd):
    subprocess.check_call(cmd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--certs", required=True)
    parser.add_argument("--old-bundle", required=True)
    parser.add_argument("--new-bundle", required=True)
    args = parser.parse_args()

    profiles_dir = Path(args.profiles)
    certs_dir = Path(args.certs)
    old_bundle = args.old_bundle
    new_bundle = args.new_bundle

    work = profiles_dir.parent / "_profile_rewrite_tmp"
    work.mkdir(exist_ok=True)
    signer_pem = work / "signer.pem"
    signer_key = work / "signer.key"

    run([
        "openssl", "x509", "-inform", "DER",
        "-in", str(certs_dir / "Public.cer"),
        "-out", str(signer_pem),
    ])
    run([
        "openssl", "pkcs12",
        "-in", str(certs_dir / "SelfSigned.p12"),
        "-passin", "pass:",
        "-nocerts", "-nodes",
        "-out", str(signer_key),
        "-legacy",
    ])

    def rewrite(value):
        if isinstance(value, str):
            return value.replace(old_bundle, new_bundle)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    for profile in sorted(profiles_dir.glob("*.mobileprovision")):
        decoded = subprocess.check_output([
            "openssl", "smime",
            "-inform", "der",
            "-verify", "-noverify",
            "-in", str(profile),
        ])
        plist = plistlib.loads(decoded)
        entitlements = plist.get("Entitlements", {})
        for key in list(entitlements.keys()):
            entitlements[key] = rewrite(entitlements[key])
        plist["Entitlements"] = entitlements
        if isinstance(plist.get("Name"), str):
            plist["Name"] = plist["Name"].replace(old_bundle, new_bundle)
        plist["AppIDName"] = "AirGram"

        xml_path = work / "profile.plist"
        der_path = work / "profile.der"
        xml_path.write_bytes(plistlib.dumps(plist, fmt=plistlib.FMT_XML))
        run([
            "openssl", "smime", "-sign",
            "-in", str(xml_path),
            "-signer", str(signer_pem),
            "-inkey", str(signer_key),
            "-outform", "DER",
            "-nodetach",
            "-out", str(der_path),
        ])
        profile.write_bytes(der_path.read_bytes())
        print(f"{profile.name} -> {entitlements.get('application-identifier')}")

    for path in work.iterdir():
        path.unlink()
    work.rmdir()
    return 0


if __name__ == "__main__":
    sys.exit(main())
