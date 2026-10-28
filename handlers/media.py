"""Upload, photo, and general media RPC handlers."""
from __future__ import annotations

import json
import logging
import os
import random
import struct
import threading
import time
import io
from collections import OrderedDict
from pathlib import Path
from typing import List, Optional, Tuple

import config
from db.storage import Storage
from handlers import packers
from mtproto import tl

log = logging.getLogger(__name__)

UPLOAD_SAVE_FILE_PART = 0xb304a621
UPLOAD_SAVE_BIG_FILE_PART = 0xde7b673d
MESSAGES_UPLOAD_MEDIA = 0x14967978
PHOTOS_UPLOAD_PROFILE_PHOTO = 0x388a3b5
PHOTOS_UPDATE_PROFILE_PHOTO = 0x09e82039
PHOTOS_DELETE_PHOTOS = 0x87cf7f2f
PHOTOS_GET_USER_PHOTOS = 0x91cd32a8
PHOTO_EMPTY = 0x2331b22d

INPUT_FILE = 0xf52ff27f
INPUT_FILE_BIG = 0xfa4f0bb5
INPUT_MEDIA_UPLOADED_DOCUMENT = 0x37c9330
INPUT_MEDIA_UPLOADED_PHOTO = 0x7d8375da
INPUT_MEDIA_UPLOADED_PHOTO_LEGACY = 0x1e287d04
INPUT_MEDIA_DOCUMENT = 0xa8763ab5
INPUT_MEDIA_PHOTO = 0xe3af4434
INPUT_MEDIA_PHOTO_LEGACY = 0xb3ba0635
INPUT_MEDIA_POLL = 0xf94e5f1
INPUT_MEDIA_POLL_ALT = 0x883a4108
# inputMediaDice#e66fbf7b (layer 112+) / legacy #e66f6b26
INPUT_MEDIA_DICE = 0xe66fbf7b
INPUT_MEDIA_DICE_LEGACY = 0xe66f6b26
# messageMediaDice#08cbec07 flags:# value:int emoticon:string ...
MESSAGE_MEDIA_DICE = 0x08cbec07
MESSAGE_MEDIA_DICE_LEGACY = 0x3f7ee58b
INPUT_PHOTO = 0x3bb3b94a
INPUT_PHOTO_EMPTY = 0x1cd7bf0d

# value ranges per https://core.telegram.org/api/dice
DICE_VALUE_MAX = {
    '🎲': 6,
    '🎯': 6,
    '🏀': 5,
    '⚽': 5,
    '⚽️': 5,
    '🎰': 64,
    '🎳': 6,
}

DOCUMENT_ATTR_IMAGE_SIZE = 0x6c37c15c
DOCUMENT_ATTR_VIDEO = 0x43c57c48
DOCUMENT_ATTR_AUDIO = 0x9852f9c6
DOCUMENT_ATTR_FILENAME = 0x15590068
DOCUMENT_ATTR_ANIMATED = 0x11b58939
DOCUMENT_ATTR_STICKER = 0x6319d612
DOCUMENT_ATTR_CUSTOM_EMOJI = 0xfd149899
DOCUMENT_ATTR_HAS_STICKERS = 0x9801d2f7
INPUT_STICKER_SET_EMPTY = 0xffb62b95
INPUT_STICKER_SET_ID = 0x9de7a269
INPUT_STICKER_SET_SHORT_NAME = 0x861cc8a0
INPUT_STICKER_SET_DICE = 0xe67f520e
INPUT_STICKER_SET_ANIMATED_EMOJI = 0x028703c8
INPUT_STICKER_SET_ANIMATED_EMOJI_ANIMATIONS = 0x0cde3739
INPUT_STICKER_SET_PREMIUM_GIFTS = 0xc88b3b02
INPUT_STICKER_SET_EMOJI_GENERIC_ANIMATIONS = 0x04c4d4ce
INPUT_STICKER_SET_EMOJI_DEFAULT_STATUSES = 0x29d0f5ee
INPUT_STICKER_SET_EMOJI_DEFAULT_TOPIC_ICONS = 0x44c1f8e9
INPUT_STICKER_SET_EMOJI_CHANNEL_DEFAULT_STATUSES = 0x49748553
INPUT_STICKER_SET_TON_GIFTS = 0x1cf671a0
MASK_COORDS = 0xaed6dbb2

PHOTOS_PHOTO = 0x20212ca8
USER_PROFILE_PHOTO = 0x82d1f706
UPDATE_USER_PHOTO = 0x564fe691
MESSAGE_MEDIA_PHOTO_LEGACY = 0x695150d7  # layer < 224
MESSAGE_MEDIA_PHOTO = 0xe216eb63        # layer 224+

INPUT_PHOTO_FILE_LOCATION = 0x40181ffe
INPUT_PEER_PHOTO_FILE_LOCATION = 0x37257e99
INPUT_PHOTO_LEGACY_FILE_LOCATION = 0xd83466f3

PHOTO_SIZE = 0x75c78e60
PHOTO_CACHED_SIZE = 0x21e1ad6
PHOTO_PATH_SIZE = 0xd8214d41
PHOTO_STRIPPED_SIZE = 0xe0b0bc2e

STORAGE_FILE_UNKNOWN = 0xaa963b05
STORAGE_FILE_PARTIAL = 0x40bc6f52
STORAGE_FILE_JPEG = 0x007efe0e
STORAGE_FILE_GIF = 0xcae1aadf
STORAGE_FILE_PNG = 0x0a4f63c0
STORAGE_FILE_PDF = 0xae1e508d
STORAGE_FILE_MP3 = 0x528a0677
STORAGE_FILE_MOV = 0x4b09ebbc
STORAGE_FILE_MP4 = 0xb3cea0e4
STORAGE_FILE_WEBP = 0x1081464c


def _media_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_media_path(file_path: str) -> Optional[Path]:
    if not file_path:
        return None
    path = Path(file_path)
    if not path.is_absolute():
        path = _media_root() / path
    return path if path.is_file() else None


def _jpg_to_stripped(jpeg: bytes, width: int = 0, height: int = 0) -> Optional[bytes]:
    """Build Telegram stripped-thumb bytes (version + h + w + JPEG scan).

    Desktop reconstructs thumbs by prepending a *fixed* JPEG header (see
    tdesktop image.cpp / Telethon stripped_photo_to_jpg). Arbitrary JPEG bodies
    (wrong DQT/DHT) produce corrupt images; decode/scale then hits
    SafeRound(NaN) → Assertion Failed "!std::isnan(value)".

    We omit stripped thumbs until we can emit header-compatible payloads.
    Profile photos still work via photoSize + download.
    """
    return None


def _photo_inline_bytes(path: Path, *, max_bytes: int = 65536) -> Optional[bytes]:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    if data[:2] == b'\xff\xd8':
        return data[:max_bytes]
    return None


def pack_photo_size(typ: str, width: int, height: int, size: int) -> bytes:
    w = tl.Writer()
    w.u32(PHOTO_SIZE)
    w.string(typ)
    w.i32(int(width))
    w.i32(int(height))
    w.i32(int(size))
    return w.bytes_out()


def pack_photo_cached_size(typ: str, width: int, height: int,
                           data: bytes) -> bytes:
    w = tl.Writer()
    w.u32(PHOTO_CACHED_SIZE)
    w.string(typ)
    w.i32(int(width))
    w.i32(int(height))
    w.tl_bytes(data)
    return w.bytes_out()


def pack_photo_path_size(typ: str, data: bytes) -> bytes:
    w = tl.Writer()
    w.u32(PHOTO_PATH_SIZE)
    w.string(typ)
    w.tl_bytes(data)
    return w.bytes_out()


def pack_photo_stripped_size(typ: str, data: bytes) -> bytes:
    w = tl.Writer()
    w.u32(PHOTO_STRIPPED_SIZE)
    w.string(typ)
    w.tl_bytes(data)
    return w.bytes_out()


def pack_photo_sizes(photo: dict, *, inline_preview: bool = False) -> List[bytes]:
    width = int(photo.get('width') or 320)
    height = int(photo.get('height') or 320)
    size = int(photo.get('size') or 0)
    sizes: List[bytes] = []
    path = _resolve_media_path(photo.get('file_path') or '')
    if path is not None:
        inline = _photo_inline_bytes(path, max_bytes=4096)
        if inline:
            stripped = _jpg_to_stripped(inline, width, height)
            if stripped:
                sizes.append(pack_photo_stripped_size('i', stripped))
            if inline_preview and len(inline) <= 16 * 1024:
                sizes.append(pack_photo_cached_size('i', width, height, inline))
    sizes.append(pack_photo_size('m', min(width, 320), min(height, 320), size or 1))
    sizes.append(pack_photo_size('x', width, height, size or 1))
    if width > 800 or height > 800:
        sizes.append(pack_photo_size('y', width, height, size or 1))
    return sizes


def _tgs_thumb_jpeg(path: Path, alt: str = '') -> Optional[bytes]:
    """Render a small static preview for .tgs stickers (used in emoji pickers)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    try:
        img = Image.new('RGBA', (128, 128), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((10, 10, 118, 118), fill=(42, 171, 238, 255))
        label = (alt or '✓').strip()[:1] or '✓'
        font = None
        for font_path in (
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        ):
            try:
                font = ImageFont.truetype(font_path, 56)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((128 - tw) // 2, (128 - th) // 2 - 4), label,
                  fill=(255, 255, 255, 255), font=font)
        buf = io.BytesIO()
        img.convert('RGB').save(buf, format='JPEG', quality=85)
        return buf.getvalue()
    except Exception:
        return None


def render_tgs_thumbnail(path: Path, out_path: Path, *, size: int = 128) -> bool:
    """Render a static WebP preview of a TGS animation for document thumbs."""
    try:
        from PIL import Image
        from rlottie_python import LottieAnimation
    except ImportError:
        return False
    try:
        anim = LottieAnimation.from_tgs(str(path))
        total = int(anim.lottie_animation_get_totalframe() or 1)
        frame = max(0, min(total - 1, total // 5))
        img = anim.render_pillow_frame(frame_num=frame).convert('RGBA')
        img.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path, 'WEBP', quality=85, method=4)
        return True
    except Exception as exc:
        log.warning('TGS thumb render failed for %s: %s', path, exc)
        return False


def pack_document_thumbs(doc: dict) -> Tuple[int, bytes]:
    """Return (document flags bits, serialized thumbs vector) for layer 214+."""
    mime = (doc.get('mime_type') or '').lower()
    width = int(doc.get('width') or 512)
    height = int(doc.get('height') or 512)
    size = int(doc.get('size') or 0)
    path = _resolve_media_path(doc.get('file_path') or '')
    thumbs: List[bytes] = []
    if path is not None and mime.startswith('image/'):
        inline = _photo_inline_bytes(path, max_bytes=32768)
        if inline:
            thumbs.append(pack_photo_cached_size('m', width, height, inline))
    elif 'tgsticker' in mime:
        path_bytes = doc.get('thumb_path_bytes') or b''
        if isinstance(path_bytes, str):
            path_bytes = bytes.fromhex(path_bytes) if path_bytes else b''
        if path_bytes:
            thumbs.append(pack_photo_path_size('j', path_bytes))
        thumb_path = _resolve_media_path(doc.get('thumb_path') or '')
        if thumb_path is not None:
            try:
                thumb_size = thumb_path.stat().st_size
            except OSError:
                thumb_size = 0
            if thumb_size > 0:
                thumbs.append(pack_photo_size('m', 128, 128, thumb_size))
    if not thumbs:
        # Never advertise the main file as a thumbnail: clients expect JPEG/WebP
        # bytes there and loop on decode errors when they receive a TGS.
        return 0, b''
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(thumbs))
    for item in thumbs:
        w.raw(item)
    return 1, w.bytes_out()


def stripped_thumb_for_photo(photo: dict) -> Optional[bytes]:
    path = _resolve_media_path(photo.get('file_path') or '')
    if path is None:
        return None
    inline = _photo_inline_bytes(path)
    if not inline:
        return None
    return _jpg_to_stripped(
        inline,
        int(photo.get('width') or 320),
        int(photo.get('height') or 320),
    )


def _rpc_error(code: int, message: str) -> bytes:
    w = tl.Writer()
    w.u32(tl.RPC_ERROR)
    w.i32(code)
    w.string(message)
    return w.bytes_out()


def _guess_mime(name: str, fallback: str = 'application/octet-stream') -> str:
    ext = Path(name).suffix.lower()
    return {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
        '.gif': 'image/gif',
        '.mp4': 'video/mp4',
        '.mov': 'video/quicktime',
        '.webm': 'video/webm',
        '.tgs': 'application/x-tgsticker',
        '.ogg': 'audio/ogg',
        '.oga': 'audio/ogg',
        '.opus': 'audio/ogg',
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
        '.pdf': 'application/pdf',
        '.zip': 'application/zip',
        '.txt': 'text/plain',
    }.get(ext, fallback)


def _storage_file_type(path: Optional[Path], mime_type: str = '') -> int:
    mime = (mime_type or '').lower()
    ext = path.suffix.lower() if path is not None else ''
    if mime == 'image/jpeg' or ext in ('.jpg', '.jpeg'):
        return STORAGE_FILE_JPEG
    if mime == 'image/png' or ext == '.png':
        return STORAGE_FILE_PNG
    if mime == 'image/gif' or ext == '.gif':
        return STORAGE_FILE_GIF
    if mime == 'image/webp' or ext == '.webp':
        return STORAGE_FILE_WEBP
    if mime == 'audio/mpeg' or ext == '.mp3':
        return STORAGE_FILE_MP3
    if mime == 'video/mp4' or ext == '.mp4':
        return STORAGE_FILE_MP4
    if mime == 'video/quicktime' or ext == '.mov':
        return STORAGE_FILE_MOV
    if mime == 'application/pdf' or ext == '.pdf':
        return STORAGE_FILE_PDF
    return STORAGE_FILE_UNKNOWN


def _image_size(path: str) -> Tuple[int, int]:
    try:
        with open(path, 'rb') as f:
            head = f.read(64)
        if head[:8] == b'\x89PNG\r\n\x1a\n' and len(head) >= 24:
            w, h = struct.unpack('>II', head[16:24])
            return int(w), int(h)
        if head[:2] == b'\xff\xd8':
            with open(path, 'rb') as f:
                data = f.read(256 * 1024)
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h = struct.unpack('>H', data[i + 5:i + 7])[0]
                    w = struct.unpack('>H', data[i + 7:i + 9])[0]
                    return int(w), int(h)
                seg_len = struct.unpack('>H', data[i + 2:i + 4])[0]
                i += 2 + seg_len
        if head[:4] == b'RIFF' and head[8:12] == b'WEBP' and len(head) >= 30:
            if head[12:16] == b'VP8 ':
                w = struct.unpack('<H', head[26:28])[0] & 0x3FFF
                h = struct.unpack('<H', head[28:30])[0] & 0x3FFF
                return int(w), int(h)
    except Exception:
        pass
    return 512, 512


def _build_attr_image_size(w: int, h: int) -> bytes:
    bw = tl.Writer()
    bw.u32(DOCUMENT_ATTR_IMAGE_SIZE)
    bw.i32(int(w))
    bw.i32(int(h))
    return bw.bytes_out()


def _build_attr_filename(name: str) -> bytes:
    bw = tl.Writer()
    bw.u32(DOCUMENT_ATTR_FILENAME)
    bw.string(name or 'file')
    return bw.bytes_out()


def _parse_input_sticker_set_ref(r: tl.Reader):
    cid = r.u32()
    if cid == INPUT_STICKER_SET_EMPTY:
        return None
    if cid == INPUT_STICKER_SET_ID:
        return {'kind': 'id', 'id': r.i64(), 'access_hash': r.i64()}
    if cid == INPUT_STICKER_SET_SHORT_NAME:
        return {'kind': 'short_name', 'short_name': r.string()}
    if cid == INPUT_STICKER_SET_DICE:
        return {'kind': 'dice', 'emoticon': r.string()}
    if cid in {
        INPUT_STICKER_SET_ANIMATED_EMOJI,
        INPUT_STICKER_SET_ANIMATED_EMOJI_ANIMATIONS,
        INPUT_STICKER_SET_PREMIUM_GIFTS,
        INPUT_STICKER_SET_EMOJI_GENERIC_ANIMATIONS,
        INPUT_STICKER_SET_EMOJI_DEFAULT_STATUSES,
        INPUT_STICKER_SET_EMOJI_DEFAULT_TOPIC_ICONS,
        INPUT_STICKER_SET_EMOJI_CHANNEL_DEFAULT_STATUSES,
        INPUT_STICKER_SET_TON_GIFTS,
    }:
        return {'kind': 'special', 'cid': cid}
    raise ValueError(f'unsupported InputStickerSet {cid:#010x}')


def _pack_input_sticker_set_ref(ref) -> bytes:
    w = tl.Writer()
    if ref is None:
        w.u32(INPUT_STICKER_SET_EMPTY)
        return w.bytes_out()
    kind = ref.get('kind')
    if kind == 'id':
        w.u32(INPUT_STICKER_SET_ID)
        w.i64(int(ref.get('id') or 0))
        w.i64(int(ref.get('access_hash') or 0))
        return w.bytes_out()
    if kind == 'short_name':
        w.u32(INPUT_STICKER_SET_SHORT_NAME)
        w.string(ref.get('short_name') or '')
        return w.bytes_out()
    if kind == 'dice':
        w.u32(INPUT_STICKER_SET_DICE)
        w.string(ref.get('emoticon') or '')
        return w.bytes_out()
    if kind == 'special':
        w.u32(int(ref.get('cid') or INPUT_STICKER_SET_EMPTY))
        return w.bytes_out()
    w.u32(INPUT_STICKER_SET_EMPTY)
    return w.bytes_out()


def _pack_mask_coords(n: int, x: float, y: float, zoom: float) -> bytes:
    bw = tl.Writer()
    bw.u32(MASK_COORDS)
    bw.i32(int(n))
    bw.f64(float(x))
    bw.f64(float(y))
    bw.f64(float(zoom))
    return bw.bytes_out()


def _build_attr_sticker(alt: str, stickerset_ref, *, mask: bool = False,
                        mask_coords: Optional[tuple[int, float, float, float]] = None) -> bytes:
    flags = 0
    if mask_coords is not None:
        flags |= 1
    if mask:
        flags |= 2
    bw = tl.Writer()
    bw.u32(DOCUMENT_ATTR_STICKER)
    bw.i32(flags)
    bw.string(alt or '')
    bw.raw(_pack_input_sticker_set_ref(stickerset_ref))
    if flags & 1 and mask_coords is not None:
        bw.raw(_pack_mask_coords(*mask_coords))
    return bw.bytes_out()


def _build_attr_custom_emoji(alt: str, stickerset_ref, *, free: bool = False,
                             text_color: bool = False) -> bytes:
    flags = 0
    if free:
        flags |= 1
    if text_color:
        flags |= 2
    bw = tl.Writer()
    bw.u32(DOCUMENT_ATTR_CUSTOM_EMOJI)
    bw.i32(flags)
    bw.string(alt or '')
    bw.raw(_pack_input_sticker_set_ref(stickerset_ref))
    return bw.bytes_out()


def parse_input_photo(r: tl.Reader) -> Optional[int]:
    cid = r.u32()
    if cid == INPUT_PHOTO_EMPTY:
        return None
    if cid == INPUT_PHOTO:
        photo_id = r.i64()
        r.i64()
        r.tl_bytes()
        return int(photo_id)
    raise ValueError(f'unsupported InputPhoto {cid:#010x}')


def parse_input_file(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid == INPUT_FILE:
        file_id = r.i64()
        parts = r.i32()
        name = r.string()
        _md5 = r.string()
        return {'file_id': file_id, 'parts': parts, 'name': name, 'big': False}
    if cid == INPUT_FILE_BIG:
        file_id = r.i64()
        parts = r.i32()
        name = r.string()
        return {'file_id': file_id, 'parts': parts, 'name': name, 'big': True}
    raise ValueError(f'unsupported InputFile {cid:#010x}')


def _skip_input_document_vector(r: tl.Reader) -> None:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    for _ in range(count):
        packers.parse_input_document(r)


def parse_document_attributes(r: tl.Reader) -> Tuple[List[str], int, int]:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    attrs_hex: List[str] = []
    width = height = 0
    for _ in range(count):
        cid = r.u32()
        if cid == DOCUMENT_ATTR_IMAGE_SIZE:
            width = r.i32()
            height = r.i32()
            attrs_hex.append(_build_attr_image_size(width, height).hex())
        elif cid == DOCUMENT_ATTR_VIDEO:
            flags = r.i32()
            duration = r.f64()
            width = r.i32()
            height = r.i32()
            if flags & (1 << 2):
                r.i32()
            if flags & (1 << 4):
                r.f64()
            if flags & (1 << 5):
                r.string()
            bw = tl.Writer()
            bw.u32(DOCUMENT_ATTR_VIDEO)
            bw.i32(flags)
            bw.f64(duration)
            bw.i32(width)
            bw.i32(height)
            if flags & (1 << 2):
                bw.i32(0)
            if flags & (1 << 4):
                bw.f64(0.0)
            if flags & (1 << 5):
                bw.string('')
            attrs_hex.append(bw.bytes_out().hex())
        elif cid == DOCUMENT_ATTR_AUDIO:
            flags = r.i32()
            duration = r.i32()
            title = performer = None
            waveform = None
            if flags & 1:
                title = r.string()
            if flags & 2:
                performer = r.string()
            if flags & 4:
                waveform = r.tl_bytes()
            bw = tl.Writer()
            bw.u32(DOCUMENT_ATTR_AUDIO)
            bw.i32(flags)
            bw.i32(duration)
            if flags & 1:
                bw.string(title or '')
            if flags & 2:
                bw.string(performer or '')
            if flags & 4:
                bw.tl_bytes(waveform or b'')
            attrs_hex.append(bw.bytes_out().hex())
        elif cid == DOCUMENT_ATTR_FILENAME:
            name = r.string()
            attrs_hex.append(_build_attr_filename(name).hex())
        elif cid == DOCUMENT_ATTR_ANIMATED:
            bw = tl.Writer()
            bw.u32(DOCUMENT_ATTR_ANIMATED)
            attrs_hex.append(bw.bytes_out().hex())
        elif cid == DOCUMENT_ATTR_STICKER:
            flags = r.i32()
            alt = r.string()
            stickerset_ref = _parse_input_sticker_set_ref(r)
            mask_coords = None
            if flags & 1:
                if r.u32() != MASK_COORDS:
                    raise ValueError('MASK_COORDS_INVALID')
                mask_coords = (r.i32(), r.f64(), r.f64(), r.f64())
            attrs_hex.append(_build_attr_sticker(
                alt,
                stickerset_ref,
                mask=bool(flags & 2),
                mask_coords=mask_coords,
            ).hex())
        elif cid == DOCUMENT_ATTR_CUSTOM_EMOJI:
            flags = r.i32()
            alt = r.string()
            stickerset_ref = _parse_input_sticker_set_ref(r)
            attrs_hex.append(_build_attr_custom_emoji(
                alt,
                stickerset_ref,
                free=bool(flags & 1),
                text_color=bool(flags & 2),
            ).hex())
        elif cid == DOCUMENT_ATTR_HAS_STICKERS:
            bw = tl.Writer()
            bw.u32(DOCUMENT_ATTR_HAS_STICKERS)
            attrs_hex.append(bw.bytes_out().hex())
        else:
            raise ValueError(f'unsupported DocumentAttribute {cid:#010x}')
    return attrs_hex, width, height


def _assemble_input_file(storage: Storage, file_info: dict,
                         subdir: str) -> Tuple[str, int]:
    file_id = int(file_info['file_id'])
    parts = int(file_info['parts'])
    name = file_info.get('name') or 'file.bin'
    safe_name = Path(name).name.replace('/', '_').replace('\\', '_') or 'file.bin'
    root = Path(__file__).resolve().parent.parent
    dest = Path(config.UPLOADS_DATA_DIR) / subdir / f'{file_id}_{safe_name}'
    size = storage.assemble_upload_file(file_id, parts, str(dest))
    try:
        rel = dest.relative_to(root)
    except ValueError:
        rel = dest
    return str(rel), size


def create_document_from_input_file(storage: Storage, file_info: dict,
                                    mime_type: str,
                                    attributes_hex: List[str],
                                    width: int, height: int) -> dict:
    path, size = _assemble_input_file(storage, file_info, 'documents')
    if not width or not height:
        if (mime_type or '').startswith('image/'):
            width, height = _image_size(
                str(Path(__file__).resolve().parent.parent / path))
        else:
            width = width or 512
            height = height or 512
    attrs = list(attributes_hex or [])
    has_filename = any(
        len(a) >= 8 and int(a[:8], 16) == DOCUMENT_ATTR_FILENAME
        for a in attrs)
    if not has_filename:
        attrs.append(_build_attr_filename(file_info.get('name') or 'file').hex())
    if (mime_type or '').startswith('image/'):
        has_size = any(
            len(a) >= 8 and int(a[:8], 16) == DOCUMENT_ATTR_IMAGE_SIZE
            for a in attrs)
        if not has_size:
            attrs.insert(0, _build_attr_image_size(width, height).hex())
    return storage.create_uploaded_document(
        file_path=path,
        mime_type=mime_type or _guess_mime(file_info.get('name') or ''),
        size=size,
        attributes_json=json.dumps(attrs),
        width=width,
        height=height,
    )


def create_photo_from_input_file(storage: Storage, owner_id: int,
                                 file_info: dict, *,
                                 is_profile: bool = False) -> dict:
    file_id = int(file_info['file_id'])
    parts = int(file_info['parts'])
    name = file_info.get('name') or 'photo.jpg'
    safe_name = Path(name).name.replace('/', '_').replace('\\', '_') or 'photo.jpg'
    root = Path(__file__).resolve().parent.parent
    dest = Path(config.PHOTOS_DATA_DIR) / f'{file_id}_{safe_name}'
    size = storage.assemble_upload_file(file_id, parts, str(dest))
    try:
        rel = dest.relative_to(root)
    except ValueError:
        rel = dest
    abs_path = root / rel if not Path(rel).is_absolute() else Path(rel)
    width, height = _image_size(str(abs_path))
    return storage.create_photo(
        owner_id=owner_id,
        file_path=str(rel),
        width=width,
        height=height,
        size=size,
        is_profile=is_profile,
    )


def parse_input_media_uploaded(r: tl.Reader, storage: Storage,
                             owner_id: int) -> dict:
    """Resolve InputMedia to stored media reference."""
    cid = r.u32()
    if cid == INPUT_MEDIA_UPLOADED_DOCUMENT:
        flags = r.i32()
        file_info = parse_input_file(r)
        if flags & (1 << 2):
            parse_input_file(r)  # thumb
        mime_type = r.string()
        attrs_hex, width, height = parse_document_attributes(r)
        if flags & 1:
            _skip_input_document_vector(r)
        if flags & (1 << 6):
            packers.skip_input_photo(r)
        if flags & (1 << 7):
            r.i32()
        if flags & (1 << 1):
            r.i32()
        doc = create_document_from_input_file(
            storage, file_info, mime_type, attrs_hex, width, height)
        return {'type': 'document', 'id': int(doc['id']), 'doc': doc}
    if cid in (INPUT_MEDIA_UPLOADED_PHOTO, INPUT_MEDIA_UPLOADED_PHOTO_LEGACY):
        flags = r.i32()
        file_info = parse_input_file(r)
        if flags & 1:
            _skip_input_document_vector(r)
        if flags & 2:
            r.i32()  # ttl_seconds
        if cid == INPUT_MEDIA_UPLOADED_PHOTO and flags & (1 << 3):
            r.bool()  # live_photo
            packers.parse_input_document(r)  # attached video for live photo
        photo = create_photo_from_input_file(storage, owner_id, file_info)
        return {'type': 'photo', 'id': int(photo['id']), 'photo': photo}
    if cid == INPUT_MEDIA_DOCUMENT:
        flags = r.i32()
        doc_id = packers.parse_input_document(r)
        if flags & 1:
            r.i32()
        if flags & 2:
            r.string()
        if flags & 8:
            packers.skip_input_photo(r)
        if flags & 16:
            r.i32()
        if not doc_id:
            raise ValueError('MEDIA_EMPTY')
        return {'type': 'document', 'id': int(doc_id)}
    if cid in (INPUT_MEDIA_PHOTO, INPUT_MEDIA_PHOTO_LEGACY):
        flags = r.i32()
        photo_id = parse_input_photo(r)
        if not photo_id:
            raise ValueError('MEDIA_EMPTY')
        if flags & 1:
            r.i32()  # ttl_seconds
        if cid == INPUT_MEDIA_PHOTO and flags & 4:
            packers.parse_input_document(r)  # live photo video
        elif cid == INPUT_MEDIA_PHOTO_LEGACY and flags & 2:
            pass  # legacy spoiler flag only
        return {'type': 'photo', 'id': int(photo_id)}
    if cid in (INPUT_MEDIA_POLL, INPUT_MEDIA_POLL_ALT):
        from handlers.polls import parse_input_media_poll_body
        poll_start = r.pos
        try:
            return parse_input_media_poll_body(r, cid)
        except Exception as exc:
            tail = r.data[r.pos:r.pos + 48].hex()
            chunk = r.data[max(0, poll_start - 4):min(len(r.data), poll_start + 96)].hex()
            raise ValueError(
                f'POLL_MEDIA_INVALID: {exc} pos={r.pos} remaining={r.remaining()} '
                f'tail={tail} chunk={chunk}'
            ) from exc
    if cid in (INPUT_MEDIA_DICE, INPUT_MEDIA_DICE_LEGACY):
        emoticon = normalize_dice_emoticon(r.string())
        if emoticon not in DICE_VALUE_MAX:
            raise ValueError(f'DICE_EMOTICON_INVALID: {emoticon!r}')
        return {
            'type': 'dice',
            'emoticon': emoticon,
            'value': roll_dice_value(emoticon),
        }
    raise ValueError(f'unsupported InputMedia {cid:#010x}')


def normalize_dice_emoticon(emoticon: str) -> str:
    emo = (emoticon or '').strip()
    if emo in DICE_VALUE_MAX:
        return emo
    stripped = emo.replace('\ufe0f', '')
    for known in DICE_VALUE_MAX:
        if known.replace('\ufe0f', '') == stripped:
            return known
    return emo


def roll_dice_value(emoticon: str) -> int:
    return random.randint(1, int(DICE_VALUE_MAX[emoticon]))


def build_dice_json(media: dict) -> str:
    emoticon = normalize_dice_emoticon(media.get('emoticon') or '')
    value = int(media.get('value') or 0)
    if value < 1 or value > int(DICE_VALUE_MAX.get(emoticon) or 0):
        value = roll_dice_value(emoticon)
    return json.dumps({
        'kind': 'dice',
        'emoticon': emoticon,
        'value': value,
    }, ensure_ascii=False)


def parse_dice_json(raw) -> Optional[dict]:
    if not raw:
        return None
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(data, dict) or data.get('kind') != 'dice':
        return None
    emoticon = normalize_dice_emoticon(data.get('emoticon') or '')
    if emoticon not in DICE_VALUE_MAX:
        return None
    value = int(data.get('value') or 0)
    if value < 1 or value > int(DICE_VALUE_MAX[emoticon]):
        return None
    return {'emoticon': emoticon, 'value': value}


def pack_message_media_dice(emoticon: str, value: int) -> bytes:
    w = tl.Writer()
    if int(getattr(config, 'LAYER', 224)) >= 200:
        w.u32(MESSAGE_MEDIA_DICE)
        w.i32(0)  # flags — no game_outcome
    else:
        w.u32(MESSAGE_MEDIA_DICE_LEGACY)
    w.i32(int(value))
    w.string(emoticon)
    return w.bytes_out()


def validate_media_ref(storage: Storage, media: dict) -> bool:
    if media['type'] == 'poll':
        return bool(media.get('poll'))
    if media['type'] == 'dice':
        emo = normalize_dice_emoticon(media.get('emoticon') or '')
        return emo in DICE_VALUE_MAX
    if media['type'] == 'document':
        return storage.get_document_by_id(int(media['id'])) is not None
    if media['type'] == 'photo':
        return storage.get_photo_by_id(int(media['id'])) is not None
    return False


def pack_photo(photo: dict, *, inline_preview: bool = False) -> bytes:
    w = tl.Writer()
    w.u32(0xfb197a65)  # photo
    w.i32(0)
    w.i64(int(photo['id']))
    w.i64(int(photo['access_hash']))
    ref = photo.get('file_reference') or b'\x00' * 8
    if isinstance(ref, str):
        ref = bytes.fromhex(ref) if ref else b'\x00' * 8
    w.tl_bytes(ref)
    w.i32(int(photo.get('date') or time.time()))
    sizes = pack_photo_sizes(photo, inline_preview=inline_preview)
    w.u32(tl.VECTOR)
    w.i32(len(sizes))
    for item in sizes:
        w.raw(item)
    w.i32(int(photo.get('dc_id') or config.DC_ID))
    return w.bytes_out()


def pack_user_profile_photo(photo: dict) -> bytes:
    w = tl.Writer()
    w.u32(USER_PROFILE_PHOTO)
    flags = 0
    stripped = stripped_thumb_for_photo(photo)
    if stripped:
        flags |= 1 << 1
    w.i32(flags)
    w.i64(int(photo['id']))
    if flags & (1 << 1):
        w.tl_bytes(stripped)
    w.i32(int(photo.get('dc_id') or config.DC_ID))
    return w.bytes_out()


def _message_media_photo_ctor() -> int:
    import config
    if int(getattr(config, 'LAYER', 224)) >= 224:
        return MESSAGE_MEDIA_PHOTO
    return MESSAGE_MEDIA_PHOTO_LEGACY


def pack_message_media_photo(photo: dict, *, spoiler: bool = False,
                             inline_preview: bool = False) -> bytes:
    w = tl.Writer()
    w.u32(_message_media_photo_ctor())
    flags = 1  # photo present
    if spoiler:
        flags |= 1 << 3
    w.i32(flags)
    w.raw(pack_photo(photo, inline_preview=inline_preview))
    return w.bytes_out()


def message_media_document_flags(doc: dict) -> int:
    """Layer 227 messageMediaDocument flags from stored document attributes."""
    flags = 1  # document present
    try:
        attrs = json.loads(doc.get('attributes_json') or '[]')
    except json.JSONDecodeError:
        return flags
    has_video = False
    is_round = False
    has_voice = False
    for item in attrs:
        try:
            raw = bytes.fromhex(item)
        except (ValueError, TypeError):
            continue
        if len(raw) < 8:
            continue
        cid = int.from_bytes(raw[:4], 'little')
        aflags = int.from_bytes(raw[4:8], 'little', signed=True)
        if cid == DOCUMENT_ATTR_AUDIO and (aflags & (1 << 10)):
            has_voice = True
        elif cid == DOCUMENT_ATTR_VIDEO:
            has_video = True
            if aflags & 1:
                is_round = True
        elif cid == DOCUMENT_ATTR_ANIMATED:
            has_video = True
    if has_video:
        flags |= 1 << 6
    if is_round:
        flags |= 1 << 7
    if has_voice:
        flags |= 1 << 8
    return flags


def pack_photos_photo(photo: Optional[dict], users: Optional[List[dict]] = None,
                      storage=None,
                      viewer_id: Optional[int] = None,
                      *, photo_id: int = 0) -> bytes:
    users = users or []
    w = tl.Writer()
    w.u32(PHOTOS_PHOTO)
    if photo:
        w.raw(pack_photo(photo))
    else:
        w.u32(PHOTO_EMPTY)
        w.i64(int(photo_id or 0))
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(
            u,
            is_self=viewer_id is not None and int(u['id']) == int(viewer_id),
            viewer_id=viewer_id,
            storage=storage))
    return w.bytes_out()


def pack_update_user_photo(user_id: int, photo: Optional[dict],
                           date: Optional[int] = None) -> bytes:
    w = tl.Writer()
    w.u32(UPDATE_USER_PHOTO)
    w.i64(int(user_id))
    w.i32(int(date or time.time()))
    if photo:
        w.raw(pack_user_profile_photo(photo))
    else:
        w.u32(0x4f11bae1)  # userProfilePhotoEmpty
    w.i64(int(photo['id']) if photo else 0)
    return w.bytes_out()


def _push_profile_photo_update(storage, push_queue, user: dict,
                               photo: Optional[dict],
                               exclude_auth_key_id=None) -> None:
    if push_queue is None or not user:
        return
    upd = packers.pack_updates(
        [pack_update_user_photo(int(user['id']), photo)],
        [user],
        user_id=int(user['id']),
        storage=storage,
        increment_seq=False,
    )
    for uid in storage.get_users_who_know(int(user['id'])):
        if int(uid) >= config.CHANNEL_ID_MIN:
            continue
        push_queue.push_to_user(
            uid, upd, exclude_auth_key_id=exclude_auth_key_id)


def pack_message_media_from_ref(media: dict, storage: Storage,
                                message_date: Optional[int] = None) -> bytes:
    if media['type'] == 'photo':
        photo = media.get('photo') or storage.get_photo_by_id(int(media['id']))
        if not photo:
            return b''
        return pack_message_media_photo(photo)
    doc = media.get('doc') or storage.get_document_by_id(int(media['id']))
    if not doc:
        return b''
    ss = storage.get_sticker_set_by_id(doc['set_id'])
    return packers.pack_message_media_document(
        doc, ss, message_date)


def handle_upload_save_file_part(session, req_msg_id, r, storage, push_queue,
                                 registry):
    file_id = r.i64()
    file_part = r.i32()
    data = r.tl_bytes()
    if not data:
        return _rpc_error(400, 'FILE_PART_EMPTY')
    if file_part < 0:
        return _rpc_error(400, 'FILE_PART_INVALID')
    storage.save_upload_file_part(file_id, file_part, data)
    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_upload_save_big_file_part(session, req_msg_id, r, storage,
                                     push_queue, registry):
    return handle_upload_save_file_part(
        session, req_msg_id, r, storage, push_queue, registry)


def handle_messages_upload_media(session, req_msg_id, r, storage, push_queue,
                                 registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    first = r.u32()
    if first in (
            tl.INPUT_PEER_EMPTY, tl.INPUT_PEER_SELF, tl.INPUT_PEER_CHAT,
            tl.INPUT_PEER_USER, tl.INPUT_PEER_CHANNEL,
            tl.INPUT_PEER_USER_FROM_MESSAGE, tl.INPUT_PEER_CHANNEL_FROM_MESSAGE):
        r.pos -= 4
    else:
        flags = first
        if flags & 1:
            r.string()  # business_connection_id
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    try:
        media = parse_input_media_uploaded(r, storage, viewer['id'])
    except (ValueError, AssertionError) as exc:
        log.warning(
            'uploadMedia MEDIA_INVALID pos=%s remaining=%s error=%s',
            getattr(r, 'pos', '?'), getattr(r, 'remaining', lambda: '?')(), exc)
        return _rpc_error(400, str(exc) or 'MEDIA_INVALID')
    if not validate_media_ref(storage, media):
        log.warning('uploadMedia MEDIA_INVALID validate failed type=%s',
                    media.get('type'))
        return _rpc_error(400, 'MEDIA_INVALID')
    if media['type'] == 'poll':
        from handlers.polls import pack_message_media_poll
        return pack_message_media_poll(
            storage,
            {'poll': media['poll'], 'correct_answers': media.get('correct_answers') or []},
            int(peer_id or viewer['id']), 0, viewer_id=int(viewer['id']))
    if media['type'] == 'dice':
        return pack_message_media_dice(
            media['emoticon'], int(media.get('value') or roll_dice_value(media['emoticon'])))
    body = pack_message_media_from_ref(media, storage)
    if not body:
        log.warning('uploadMedia MEDIA_INVALID empty body type=%s',
                    media.get('type'))
        return _rpc_error(400, 'MEDIA_INVALID')
    return body


def handle_photos_upload_profile_photo(session, req_msg_id, r, storage,
                                     push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    photo = None
    if flags & 1:
        file_info = parse_input_file(r)
        photo = create_photo_from_input_file(
            storage, viewer['id'], file_info, is_profile=True)
    if flags & 2:
        parse_input_file(r)  # video
    if flags & 4:
        r.f64()  # video_start_ts
    # flags & 8: fallback — no extra field
    if flags & 16:
        packers.skip_video_size(r)  # video_emoji_markup
    if flags & 32:
        packers.parse_input_user(r, storage, self_user_id=viewer['id'])  # bot
    if not photo:
        return _rpc_error(400, 'PHOTO_CROP_FILE_MISSING')
    user = storage.set_user_profile_photo(viewer['id'], photo['id'])
    _push_profile_photo_update(
        storage, push_queue, user, photo,
        exclude_auth_key_id=getattr(session, 'auth_key_id', None))
    return pack_photos_photo(
        photo, [user], storage=storage, viewer_id=viewer['id'])


def handle_photos_update_profile_photo(session, req_msg_id, r, storage,
                                       push_queue, registry):
    """photos.updateProfilePhoto — set existing profile photo or clear it."""
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    flags = r.i32()
    if flags & 2:
        packers.parse_input_user(r, storage, self_user_id=viewer['id'])  # bot
    photo_id = parse_input_photo(r)
    if photo_id is None:
        user = storage.clear_user_profile_photo(viewer['id'])
        _push_profile_photo_update(
            storage, push_queue, user, None,
            exclude_auth_key_id=getattr(session, 'auth_key_id', None))
        return pack_photos_photo(
            None, [user], storage=storage, viewer_id=viewer['id'])
    photo = storage.get_photo_by_id(photo_id)
    if (not photo or int(photo.get('owner_id') or 0) != int(viewer['id'])
            or not int(photo.get('is_profile') or 0)):
        return _rpc_error(400, 'PHOTO_ID_INVALID')
    user = storage.set_user_profile_photo(viewer['id'], photo_id)
    _push_profile_photo_update(
        storage, push_queue, user, photo,
        exclude_auth_key_id=getattr(session, 'auth_key_id', None))
    return pack_photos_photo(
        photo, [user], storage=storage, viewer_id=viewer['id'])


def handle_photos_delete_photos(session, req_msg_id, r, storage,
                                push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    assert r.u32() == tl.VECTOR
    count = r.i32()
    photo_ids = []
    for _ in range(max(0, count)):
        pid = parse_input_photo(r)
        if pid:
            photo_ids.append(int(pid))
    before = int(viewer.get('profile_photo_id') or 0)
    deleted = storage.delete_user_photos(viewer['id'], photo_ids)
    user = storage.get_user_by_id(viewer['id'])
    after = int((user or {}).get('profile_photo_id') or 0)
    if before and after == 0:
        _push_profile_photo_update(
            storage, push_queue, user, None,
            exclude_auth_key_id=getattr(session, 'auth_key_id', None))
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(deleted))
    for pid in deleted:
        w.i64(int(pid))
    return w.bytes_out()


def handle_photos_get_user_photos(session, req_msg_id, r, storage, push_queue,
                                  registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    target = packers.parse_input_user(r, storage, self_user_id=viewer['id'])
    _offset = r.i32()
    _max_id = r.i64()
    _limit = r.i32()
    if not target:
        return _rpc_error(400, 'USER_ID_INVALID')
    photos = storage.list_user_photos(target['id'])
    w = tl.Writer()
    w.u32(0x8dca6aa5)  # photos.photos
    w.u32(tl.VECTOR)
    w.i32(len(photos))
    for photo in photos:
        w.raw(pack_photo(photo))
    w.u32(tl.VECTOR)
    w.i32(1)
    is_self = int(target['id']) == int(viewer['id'])
    w.raw(packers.pack_user(
        target, is_self=is_self, viewer_id=viewer['id'], storage=storage))
    return w.bytes_out()


_FILE_CACHE: 'OrderedDict[int, tuple[float, int, bytes]]' = OrderedDict()
_FILE_CACHE_LOCK = threading.Lock()
_FILE_CACHE_MAX_BYTES = 192 * 1024 * 1024
_FILE_CACHE_MAX_ENTRY = 4 * 1024 * 1024
_FILE_CACHE_STATE = {'bytes': 0}
_DOC_CACHE: dict[int, tuple[float, Optional[dict]]] = {}
_DOC_CACHE_TTL = 30.0


def _cached_document(storage: Storage, doc_id: int) -> Optional[dict]:
    """Document row with a short TTL — gift/emoji docs are immutable."""
    now = time.monotonic()
    hit = _DOC_CACHE.get(int(doc_id))
    if hit and now - hit[0] < _DOC_CACHE_TTL:
        return hit[1]
    doc = storage.get_document_by_id(int(doc_id))
    _DOC_CACHE[int(doc_id)] = (now, doc)
    if len(_DOC_CACHE) > 4096:
        for stale in [k for k, (t, _d) in _DOC_CACHE.items()
                      if now - t >= _DOC_CACHE_TTL][:1024]:
            _DOC_CACHE.pop(stale, None)
    return doc


def _cached_file_slice(path: Path, offset: int, limit: int) -> Optional[bytes]:
    """Serve small immutable media from an in-memory LRU.

    Gift models, patterns and covers are read over and over (shop open,
    profile open, upgrades); without the cache every chunk is a blocking
    disk read inside the asyncio loop.
    """
    if offset != 0 or limit > _FILE_CACHE_MAX_ENTRY:
        return None
    key = str(path)
    now = time.time()
    with _FILE_CACHE_LOCK:
        hit = _FILE_CACHE.get(key)
        if hit and now - hit[0] < 3600:
            _FILE_CACHE.move_to_end(key)
            data = hit[2]
            return data if len(data) <= limit else data[:limit]
    try:
        st = path.stat()
    except OSError:
        return None
    if st.st_size > _FILE_CACHE_MAX_ENTRY:
        return None
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return None
    with _FILE_CACHE_LOCK:
        if key not in _FILE_CACHE:
            _FILE_CACHE[key] = (now, st.st_size, data)
            _FILE_CACHE_STATE['bytes'] += len(data)
            while (_FILE_CACHE_STATE['bytes'] > _FILE_CACHE_MAX_BYTES
                   and len(_FILE_CACHE) > 1):
                _old_key, (_t, old_size, old_data) = _FILE_CACHE.popitem(
                    last=False)
                _FILE_CACHE_STATE['bytes'] -= len(old_data)
        else:
            _FILE_CACHE.move_to_end(key)
    return data if len(data) <= limit else data[:limit]


def warm_star_gift_media(storage: Storage) -> tuple[int, int]:
    """Warm small gift models, patterns and covers into the media LRU."""
    rows = storage._conn.execute(
        '''SELECT DISTINCT sd.file_path, sd.thumb_path
           FROM sticker_documents sd
           WHERE sd.id IN (
               SELECT document_id FROM star_gifts
               WHERE document_id IS NOT NULL AND document_id>0
               UNION
               SELECT document_id FROM star_gift_upgrade_attrs
               WHERE document_id IS NOT NULL AND document_id>0
           )'''
    ).fetchall()
    count = total_bytes = 0
    for row in rows:
        for field in ('file_path', 'thumb_path'):
            raw_path = row[field]
            if not raw_path:
                continue
            path = _resolve_media_path(raw_path)
            if path is None:
                continue
            data = _cached_file_slice(path, 0, _FILE_CACHE_MAX_ENTRY)
            if data is not None:
                count += 1
                total_bytes += len(data)
    log.info('Warmed %d star-gift media files (%.2f MiB) into memory',
             count, total_bytes / (1024 * 1024))
    return count, total_bytes


def read_upload_file_result(storage: Storage, location_cid: int,
                            r: tl.Reader) -> Optional[Tuple[bytes, int]]:
    path: Optional[Path] = None
    file_type = STORAGE_FILE_UNKNOWN
    detail = f'loc={location_cid:#x}'
    if location_cid == 0xbad07584:  # inputDocumentFileLocation
        doc_id = r.i64()
        r.i64()
        r.tl_bytes()
        thumb_size = r.string()
        detail += f' doc={doc_id} thumb={thumb_size!r}'
        doc = _cached_document(storage, doc_id)
        if not doc or not doc.get('file_path'):
            try:
                from shop_webapp.emoji_import import ensure_custom_emoji_document
                ensure_custom_emoji_document(storage, int(doc_id))
                _DOC_CACHE.pop(int(doc_id), None)
            except Exception:
                pass
            doc = _cached_document(storage, doc_id)
        if not doc or not doc.get('file_path'):
            log.warning('uploadFile missing document %s', detail)
            return None
        if thumb_size:
            thumb_path = (_resolve_media_path(doc.get('thumb_path') or '')
                          if doc.get('thumb_path') else None)
            if thumb_path is None and 'tgsticker' in (doc.get('mime_type') or '').lower():
                main_path = _resolve_media_path(doc['file_path'])
                if main_path is not None:
                    candidate = main_path.with_name(f'{main_path.stem}_thumb.webp')
                    if candidate.is_file() or render_tgs_thumbnail(main_path, candidate):
                        storage.set_sticker_document_thumb(doc_id, str(candidate))
                        thumb_path = candidate
            if thumb_path is not None:
                path = thumb_path
                file_type = _storage_file_type(thumb_path, 'image/webp')
            else:
                path = _resolve_media_path(doc['file_path'])
                file_type = _storage_file_type(path, doc.get('mime_type') or '')
        else:
            path = _resolve_media_path(doc['file_path'])
            file_type = _storage_file_type(path, doc.get('mime_type') or '')
    elif location_cid == INPUT_PHOTO_FILE_LOCATION:
        photo_id = r.i64()
        r.i64()
        r.tl_bytes()
        r.string()
        detail += f' photo={photo_id}'
        photo = storage.get_photo_by_id(photo_id)
        if not photo or not photo.get('file_path'):
            return None
        path = _resolve_media_path(photo['file_path'])
        file_type = _storage_file_type(path, 'image/jpeg')
    elif location_cid == INPUT_PHOTO_LEGACY_FILE_LOCATION:
        photo_id = r.i64()
        r.i64()
        r.tl_bytes()
        r.i64()
        r.i32()
        r.i64()
        photo = storage.get_photo_by_id(photo_id)
        if not photo or not photo.get('file_path'):
            return None
        path = _resolve_media_path(photo['file_path'])
        file_type = _storage_file_type(path, 'image/jpeg')
    elif location_cid == INPUT_PEER_PHOTO_FILE_LOCATION:
        r.i32()  # flags
        packers.parse_input_peer(r)
        photo_id = r.i64()
        photo = storage.get_photo_by_id(photo_id)
        if not photo or not photo.get('file_path'):
            return None
        path = _resolve_media_path(photo['file_path'])
        file_type = _storage_file_type(path, 'image/jpeg')
    else:
        log.debug('uploadFile unsupported %s', detail)
        return None
    if path is None:
        log.warning('uploadFile missing path %s', detail)
        return None
    offset = r.i64()
    limit = r.i32()
    # MTProto caps a single upload.getFile response at 1 MB; clamping also
    # bounds the memory a single request can pin.
    limit = max(1, min(int(limit), _FILE_CACHE_MAX_ENTRY))
    if offset == 0:
        cached = _cached_file_slice(path, 0, limit)
        if cached is not None:
            return cached, file_type
    with open(path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        if offset < 0:
            log.warning('uploadFile negative offset %s off=%d', detail, offset)
            return None
        if offset >= size:
            # Past EOF is a normal end-of-download, not an error: clients with a
            # stale (larger) thumb size would otherwise loop on LOCATION_INVALID.
            log.debug('uploadFile eof %s off=%d size=%d', detail, offset, size)
            return b'', file_type
        f.seek(offset)
        data = f.read(limit)
        log.debug('uploadFile %s off=%d lim=%d size=%d sent=%d',
                  detail, offset, limit, size, len(data))
        return data, file_type


def read_upload_file(storage: Storage, location_cid: int,
                     r: tl.Reader) -> Optional[bytes]:
    result = read_upload_file_result(storage, location_cid, r)
    if result is None:
        return None
    return result[0]


MEDIA_HANDLERS = {
    UPLOAD_SAVE_FILE_PART: handle_upload_save_file_part,
    UPLOAD_SAVE_BIG_FILE_PART: handle_upload_save_big_file_part,
    MESSAGES_UPLOAD_MEDIA: handle_messages_upload_media,
    PHOTOS_UPLOAD_PROFILE_PHOTO: handle_photos_upload_profile_photo,
    PHOTOS_UPDATE_PROFILE_PHOTO: handle_photos_update_profile_photo,
    PHOTOS_DELETE_PHOTOS: handle_photos_delete_photos,
    PHOTOS_GET_USER_PHOTOS: handle_photos_get_user_photos,
}
