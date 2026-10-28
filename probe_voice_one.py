"""Send one voice note from QA 888908 to the emulator user (888906)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import probe_voice_stall as pv
from probe_layer228 import Client

RECIPIENT_ID = 888906


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2] if len(sys.argv) > 2 else 443)
    sender = pv.Probe(host, port)
    sender.sign_in(pv.SENDER_PHONE, 'sender')
    recv_id, recv_hash = pv.db_user('99944444244')

    pv.upload_voice(sender, 'audio/ogg', pv.audio_attrs(), 'voice.ogg')
    pv.send_media(sender, recv_id, recv_hash, kind='voice')
    print('[ok] voice sent to', recv_id)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
