"""Optional console filter: log only iOS client connections (--logios)."""
from __future__ import annotations

import logging
import re
from typing import Optional

LOG_IOS_ONLY = False

_ios_peers: set[str] = set()
_non_ios_peers: set[str] = set()
_ios_ips: set[str] = set()
_non_ios_ips: set[str] = set()
_ios_auth_keys: set[int] = set()
_non_ios_auth_keys: set[int] = set()
_pending_auth_keys: set[int] = set()
_encrypted_auth_keys: set[int] = set()
_auth_key_peers: dict[int, str] = {}
_active_pending_ips: set[str] = set()
_active_pending_peers: set[str] = set()

_PEER_RE = re.compile(r'\[([0-9a-fA-F.:]+:\d+)\]')
_AUTH_KEY_RE = re.compile(
    r'(?:auth_key_id=|Auth key created: )(-?0x[0-9a-fA-F]+|\d+)')

_HANDSHAKE_HINTS = (
    'New connection',
    'Handshake:',
    'Auth key created:',
    'bad_server_salt',
)

_ACTIVE_HINTS = _HANDSHAKE_HINTS + (
    'Encrypted packet',
    'Decrypted',
    'New session',
    'Unknown auth_key_id',
    'Decrypt failed',
    'Identified iOS client',
    'initConnection',
    'PHONE CODE',
    'RPC ',
    'Error in handler',
    'Handshake error',
)


def peer_ip(peer: str) -> str:
    if peer.startswith('['):
        host = peer[1:].split(']', 1)[0]
    elif peer.count(':') > 1 and ']' not in peer:
        host = peer.rsplit(':', 1)[0]
    else:
        host = peer.rsplit(':', 1)[0]
    return host.strip('[]')


def _parse_auth_key_id(msg: str) -> Optional[int]:
    m = _AUTH_KEY_RE.search(msg)
    if not m:
        return None
    try:
        return int(m.group(1), 0)
    except ValueError:
        return None


def _ip_blocked(ip: str, peer: str) -> bool:
    return ip in _non_ios_ips or peer in _non_ios_peers


def _ip_allowed(ip: str, peer: str) -> bool:
    return ip in _ios_ips or peer in _ios_peers


def _clear_pending(peer: str, auth_key_id: Optional[int] = None) -> None:
    ip = peer_ip(peer)
    _active_pending_peers.discard(peer)
    _active_pending_ips.discard(ip)
    if auth_key_id is not None:
        ak = int(auth_key_id)
        _pending_auth_keys.discard(ak)
        _encrypted_auth_keys.discard(ak)
        _auth_key_peers.pop(ak, None)


def mark_peer_ios(peer: str, auth_key_id: Optional[int] = None) -> None:
    ip = peer_ip(peer)
    _ios_peers.add(peer)
    _ios_ips.add(ip)
    _non_ios_peers.discard(peer)
    _non_ios_ips.discard(ip)
    _clear_pending(peer, auth_key_id)
    if auth_key_id is not None:
        ak = int(auth_key_id)
        _ios_auth_keys.add(ak)
        _non_ios_auth_keys.discard(ak)


def mark_peer_non_ios(peer: str, auth_key_id: Optional[int] = None) -> None:
    ip = peer_ip(peer)
    _non_ios_peers.add(peer)
    _non_ios_ips.add(ip)
    _ios_peers.discard(peer)
    _ios_ips.discard(ip)
    _clear_pending(peer, auth_key_id)
    if auth_key_id is not None:
        ak = int(auth_key_id)
        _non_ios_auth_keys.add(ak)
        _ios_auth_keys.discard(ak)

def mark_auth_key_pending(auth_key_id: int, peer: str) -> None:
    if _ip_blocked(peer_ip(peer), peer):
        return
    ak = int(auth_key_id)
    _pending_auth_keys.add(ak)
    _auth_key_peers[ak] = peer


def mark_auth_key_encrypted(auth_key_id: int) -> None:
    ak = int(auth_key_id)
    if ak in _non_ios_auth_keys:
        return
    _encrypted_auth_keys.add(ak)
    peer = _auth_key_peers.get(ak)
    if peer and not _ip_blocked(peer_ip(peer), peer):
        _active_pending_peers.add(peer)
        _active_pending_ips.add(peer_ip(peer))


def update_client_from_session(session) -> None:
    """Call after initConnection metadata is captured on the session."""
    if not LOG_IOS_ONLY:
        return
    peer = getattr(session, 'connection_peer', None)
    if not peer:
        return
    from .client_info import client_label, format_client_log, fork_ios_api_ids

    label = client_label(session)
    auth_key_id = int(getattr(session, 'auth_key_id', 0) or 0)
    api_id = int(getattr(session, 'init_api_id', 0) or 0)
    log = logging.getLogger('mtproto.client_info')

    if label == 'iOS':
        mark_peer_ios(peer, auth_key_id or None)
        log.info('[%s] Identified iOS client %s', peer, format_client_log(session))
        return

    if label == 'Unknown' and api_id in fork_ios_api_ids():
        mark_peer_ios(peer, auth_key_id or None)
        log.info('[%s] Identified iOS client by api_id=%s %s',
                 peer, api_id, format_client_log(session))
        return

    if label != 'Unknown':
        mark_peer_non_ios(peer, auth_key_id or None)
        return

    log.info('[%s] Unknown client initConnection api_id=%s lang_pack=%s %s',
             peer, api_id,
             getattr(session, 'init_lang_pack', ''),
             format_client_log(session))


def should_log_record(record: logging.LogRecord) -> bool:
    if not LOG_IOS_ONLY:
        return True

    if record.name == 'server':
        return True

    msg = record.getMessage()

    if 'client=iOS' in msg or 'Identified iOS client' in msg:
        return True
    if 'Unknown client initConnection' in msg:
        return True
    if any(token in msg for token in ('client=Desktop', 'client=Android')):
        return False

    auth_key_id = _parse_auth_key_id(msg)
    if auth_key_id is not None:
        if auth_key_id in _non_ios_auth_keys:
            return False
        if auth_key_id in _ios_auth_keys:
            return True
        if auth_key_id in _pending_auth_keys:
            if auth_key_id in _encrypted_auth_keys:
                return True
            return any(hint in msg for hint in _HANDSHAKE_HINTS)

    m = _PEER_RE.search(msg)
    if m:
        peer = m.group(1)
        ip = peer_ip(peer)
        if _ip_blocked(ip, peer):
            return False
        if _ip_allowed(ip, peer):
            return True
        if ip in _active_pending_ips or peer in _active_pending_peers:
            return True
        return any(hint in msg for hint in _HANDSHAKE_HINTS)

    if any(hint in msg for hint in (
        'PHONE CODE',
        'RPC ',
        'Error in handler',
        'Unknown client initConnection',
        'Identified iOS client',
        'bad_server_salt',
        'msg cid=',
    )):
        return True

    return False


class IOSOnlyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return should_log_record(record)
