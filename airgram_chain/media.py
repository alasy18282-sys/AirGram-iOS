"""Compose gift backdrop + pattern + model into static NFT artwork."""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
GIFTS_DIR = ROOT / 'data' / 'star_gifts' / 'upgrade'
MEDIA_DIR = Path(config.AIRGRAM_CHAIN_MEDIA_DIR)


def _rgb(value: Optional[int], fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return fallback
    if number < 0 or number > 0xFFFFFF:
        return fallback
    return ((number >> 16) & 0xFF, (number >> 8) & 0xFF, number & 0xFF)


def _render_tgs(path: Path, frame_ratio: float = 0.5) -> Optional['object']:
    try:
        from rlottie_python import LottieAnimation
        from PIL import Image
    except ImportError:
        return None
    try:
        anim = LottieAnimation.from_tgs(str(path))
        total = int(anim.lottie_animation_get_totalframe() or 1)
        frame = max(0, min(total - 1, int(total * frame_ratio)))
        image = anim.render_pillow_frame(frame_num=frame).convert('RGBA')
        if image.width < 8 or image.height < 8:
            return None
        return image
    except Exception as exc:
        log.warning('gift TGS render failed for %s: %s', path, exc)
        return None


def _tgs_frames(path: Path, side: int, max_frames: int = 64):
    """Render a TGS into evenly sampled square frames plus a frame delay.

    Gift models are short loops (a few seconds at 60 fps). Sampling keeps
    the animated WebP small enough to load quickly while preserving motion.
    """
    try:
        from rlottie_python import LottieAnimation
        from PIL import Image
    except ImportError:
        return [], 0
    try:
        anim = LottieAnimation.from_tgs(str(path))
        total = int(anim.lottie_animation_get_totalframe() or 1)
        fps = float(anim.lottie_animation_get_framerate() or 30)
        step = max(1, math.ceil(total / max_frames))
        frames = []
        for number in range(0, total, step):
            art = anim.render_pillow_frame(frame_num=number).convert('RGBA')
            canvas = Image.new('RGBA', (side, side), (0, 0, 0, 0))
            art.thumbnail((side, side), Image.LANCZOS)
            canvas.alpha_composite(
                art, ((side - art.width) // 2, (side - art.height) // 2))
            frames.append(canvas)
        duration = max(20, int(round(1000 * step / fps)))
        return frames, duration
    except Exception as exc:
        log.warning('gift TGS animation failed for %s: %s', path, exc)
        return [], 0


def _gradient(size: int, center: tuple[int, int, int],
              edge: tuple[int, int, int]) -> 'object':
    from PIL import Image

    small = 320
    canvas = Image.new('RGB', (small, small), edge)
    pixels = canvas.load()
    cx = small * 0.5
    cy = small * 0.38
    max_dist = math.hypot(small * 0.5, small * 0.62)
    for y in range(small):
        for x in range(small):
            dist = math.hypot(x - cx, y - cy) / max_dist
            t = min(1.0, dist) ** 1.05
            pixels[x, y] = (
                int(center[0] + (edge[0] - center[0]) * t),
                int(center[1] + (edge[1] - center[1]) * t),
                int(center[2] + (edge[2] - center[2]) * t),
            )
    return canvas.resize((size, size), Image.LANCZOS).convert('RGBA')


def _tile_pattern(pattern: 'object', size: int,
                  color: tuple[int, int, int], opacity: float = 0.18) -> 'object':
    """Lay the gift motif out as a symmetric, seamless wallpaper.

    The grid is centred with a single tile step and identical margins on
    every side, so masks are never cropped unevenly and overlap.
    """
    from PIL import Image

    layer = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    tile_side = max(32, int(size * 0.26))
    tile = pattern.resize((tile_side, tile_side), Image.LANCZOS)
    alpha = tile.getchannel('A').point(lambda value: int(value * opacity))
    tinted = Image.new('RGBA', tile.size, (*color, 0))
    tinted.putalpha(alpha)
    step = tile_side
    cols = size // step + 2
    rows = size // step + 2
    start_x = (size - cols * step) // 2
    start_y = (size - rows * step) // 2
    for row in range(rows):
        for col in range(cols):
            layer.alpha_composite(
                tinted, (start_x + col * step, start_y + row * step))
    return layer


def _shadow_layer(size: int) -> 'object':
    from PIL import Image, ImageDraw, ImageFilter

    layer = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    side = int(size * 0.68)
    x = (size - side) // 2
    y = int(size * 0.5) - side // 2 + int(size * 0.02)
    draw = ImageDraw.Draw(layer)
    ellipse_w = int(side * 0.62)
    ellipse_h = max(10, int(side * 0.09))
    draw.ellipse(
        (x + (side - ellipse_w) // 2, y + side - ellipse_h // 2,
         x + (side + ellipse_w) // 2, y + side + ellipse_h // 2),
        fill=(0, 0, 0, 70))
    return layer.filter(ImageFilter.GaussianBlur(radius=max(4, size // 90)))


def _model_layer(art: 'object', size: int) -> 'object':
    from PIL import Image

    layer = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    side = int(size * 0.68)
    if art.size != (side, side):
        art = art.resize((side, side), Image.LANCZOS)
    x = (size - side) // 2
    y = int(size * 0.5) - side // 2 + int(size * 0.02)
    layer.alpha_composite(art, (x, y))
    return layer


_FONT_CANDIDATES = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
)
_FONT_REGULAR_CANDIDATES = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
)

# Bump when the artwork layout changes so cached covers are re-rendered.
_VIRTUAL_CARD_VERSION = '3'


def _font(size: int) -> 'object':
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _regular_font(size: int) -> 'object':
    from PIL import ImageFont

    for path in _FONT_REGULAR_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return _font(size)


def _fit_text(text: str, max_width: int, start: int, minimum: int = 34,
              bold: bool = True):
    from PIL import Image, ImageDraw

    probe = ImageDraw.Draw(Image.new('RGB', (8, 8)))
    factory = _font if bold else _regular_font
    for size in range(start, minimum - 1, -2):
        font = factory(size)
        width = probe.textbbox((0, 0), text, font=font)[2]
        if width <= max_width:
            return font
    return factory(minimum)


_TELEGRAM_PLANE = (
    'M9.417 15.181l-.397 5.584c.568 0 .814-.244 1.109-.537l2.663-2.545 '
    '5.518 4.041c1.012.564 1.725.267 1.998-.931L23.93 3.821c.321-1.496'
    '-.541-2.081-1.527-1.714L2.62 9.85c-1.458.564-1.439 1.378-.251 '
    '1.744l5.067 1.58L19.1 6.555c.57-.375 1.089-.167.663.208z'
)


def _telegram_badge(side: int) -> 'object':
    """White squircle with the Telegram blue disc and paper plane."""
    from PIL import Image

    radius = int(side * 0.26)
    circle = side * 0.72
    center = side / 2
    scale = (circle * 0.84) / 24.0
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}" '
        f'height="{side}" viewBox="0 0 {side} {side}">'
        f'<rect x="0" y="0" width="{side}" height="{side}" rx="{radius}" '
        f'fill="#ffffff"/>'
        f'<circle cx="{center}" cy="{center}" r="{circle / 2}" '
        f'fill="#2AABEE"/>'
        f'<g transform="translate({center - 12 * scale},'
        f'{center - 12 * scale}) scale({scale})">'
        f'<path d="{_TELEGRAM_PLANE}" fill="#ffffff"/></g></svg>'
    )
    try:
        import io

        import cairosvg
        png = cairosvg.svg2png(
            bytestring=svg.encode('utf-8'),
            output_width=side * 4, output_height=side * 4)
        image = Image.open(io.BytesIO(png)).convert('RGBA')
        return image.resize((side, side), Image.LANCZOS)
    except Exception as exc:
        log.warning('Telegram badge render failed: %s', exc)
        image = Image.new('RGBA', (side, side), (255, 255, 255, 255))
        return image


def _virtual_card(address: str, title: str, subtitle: str, kind: str,
                  size: int = 900) -> 'object':
    """Cover art matching Telegram collectible username / anonymous number."""
    from PIL import Image, ImageDraw, ImageFilter

    canvas = _gradient(size, (59, 72, 87), (24, 31, 40))

    badge_side = int(size * 0.30)
    badge = _telegram_badge(badge_side)
    bx = (size - badge_side) // 2
    by = int(size * 0.185)

    shadow = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (bx, by + int(size * 0.012), bx + badge_side,
         by + badge_side + int(size * 0.012)),
        radius=int(badge_side * 0.26), fill=(0, 0, 0, 90))
    canvas.alpha_composite(
        shadow.filter(ImageFilter.GaussianBlur(radius=max(4, size // 70))))
    canvas.alpha_composite(badge, (bx, by))

    is_phone = (kind or '').lower() == 'phone'
    label = (title or '').strip()
    if is_phone:
        caption = 'Anonymous Telegram Number'
    else:
        caption = f"{label.lstrip('@')}.t.me"

    plate_fill = (42, 160, 150, 255)
    title_font = _fit_text(label, int(size * 0.70), int(size * 0.145),
                           minimum=int(size * 0.048))
    probe = ImageDraw.Draw(Image.new('RGB', (8, 8)))
    bbox = probe.textbbox((0, 0), label, font=title_font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x = int(size * 0.055)
    pad_y = int(size * 0.034)
    plate_w = text_w + pad_x * 2
    plate_h = text_h + pad_y * 2
    plate_x = (size - plate_w) // 2
    plate_y = int(size * 0.605) - plate_h // 2
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (plate_x, plate_y, plate_x + plate_w, plate_y + plate_h),
        radius=int(plate_h * 0.30), fill=plate_fill)
    draw.text(
        (plate_x + pad_x - bbox[0], plate_y + pad_y - bbox[1]),
        label, font=title_font, fill=(255, 255, 255, 255))

    caption_font = _fit_text(caption, int(size * 0.80), int(size * 0.055),
                             minimum=int(size * 0.036), bold=False)
    cap_bbox = draw.textbbox((0, 0), caption, font=caption_font)
    cap_w = cap_bbox[2] - cap_bbox[0]
    draw.text(
        ((size - cap_w) // 2, int(size * 0.745) - cap_bbox[1]),
        caption, font=caption_font, fill=(143, 152, 164, 255))
    return canvas


def ensure_virtual_media(address: str, *, title: str, subtitle: str,
                         kind: str = 'username') -> dict:
    """Render and cache artwork for a virtual (username/phone) NFT."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    hex_part = (address or '').split(':', 1)[-1]
    if not hex_part:
        return {'image_path': '', 'thumb_path': ''}
    image_name = f'{hex_part}.webp'
    thumb_name = f'{hex_part}_thumb.webp'
    image_path = MEDIA_DIR / image_name
    thumb_path = MEDIA_DIR / thumb_name
    stamp = MEDIA_DIR / f'.virtual-cards-{_VIRTUAL_CARD_VERSION}'
    if image_path.is_file() and thumb_path.is_file() and stamp.is_file():
        return {'image_path': image_name, 'thumb_path': thumb_name}
    try:
        from PIL import Image

        canvas = _virtual_card(address, title, subtitle, kind)
        main = canvas.convert('RGB')
        main.save(image_path, 'WEBP', quality=88, method=4)
        thumb = main.copy()
        thumb.thumbnail((main.width // 2, main.height // 2), Image.LANCZOS)
        thumb.save(thumb_path, 'WEBP', quality=82, method=4)
        stamp.write_text(_VIRTUAL_CARD_VERSION, encoding='utf-8')
    except Exception as exc:
        log.warning('virtual NFT artwork failed for %s: %s', address, exc)
        return {'image_path': '', 'thumb_path': ''}
    return {'image_path': image_name, 'thumb_path': thumb_name}


def ensure_media(nft_address: str, *, gift_id: int,
                 model_document_id: int, pattern_document_id: int,
                 backdrop: dict, size: int = 900,
                 force: bool = False) -> dict:
    """Render and cache NFT artwork; always returns relative media names."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    hex_part = nft_address.split(':', 1)[-1]
    image_name = f'{hex_part}.webp'
    thumb_name = f'{hex_part}_thumb.webp'
    image_path = MEDIA_DIR / image_name
    thumb_path = MEDIA_DIR / thumb_name
    if image_path.is_file() and thumb_path.is_file() and not force:
        return {'image_path': image_name, 'thumb_path': thumb_name}

    from PIL import Image

    render_side = min(int(size), 512)
    center = _rgb(backdrop.get('center_color'), (40, 74, 120))
    edge = _rgb(backdrop.get('edge_color'), (7, 13, 26))
    pattern_color = _rgb(backdrop.get('pattern_color'), (150, 200, 255))

    try:
        canvas = _gradient(render_side, center, edge)
        model_file = GIFTS_DIR / str(int(gift_id)) / 'models' / f'{int(model_document_id)}.tgs'
        pattern_file = GIFTS_DIR / str(int(gift_id)) / 'patterns' / f'{int(pattern_document_id)}.tgs'
        pattern = _render_tgs(pattern_file) if pattern_document_id else None
        if pattern is not None:
            canvas.alpha_composite(
                _tile_pattern(pattern, render_side, pattern_color))
        frames, duration = (
            _tgs_frames(model_file, render_side, max_frames=64)
            if model_document_id else ([], 0))
        canvas.alpha_composite(_shadow_layer(render_side))
        still = canvas
        if frames:
            still = canvas.copy()
            still.alpha_composite(
                _model_layer(frames[len(frames) // 2], render_side))
            rendered = []
            for art in frames:
                frame = canvas.copy()
                frame.alpha_composite(_model_layer(art, render_side))
                rendered.append(frame.convert('RGB'))
            rendered[0].save(
                image_path, 'WEBP', save_all=True,
                append_images=rendered[1:], duration=duration, loop=0,
                quality=70, method=4)
        else:
            canvas.convert('RGB').save(
                image_path, 'WEBP', quality=88, method=4)
    except Exception as exc:
        log.warning('NFT artwork composition failed for %s: %s', nft_address, exc)
        try:
            canvas = _gradient(render_side, center, edge)
            still = canvas
        except Exception:
            canvas = Image.new('RGBA', (render_side, render_side), (*center, 255))
            still = canvas
        still.convert('RGB').save(image_path, 'WEBP', quality=88, method=4)

    thumb = still.convert('RGB').copy()
    thumb.thumbnail((render_side // 2, render_side // 2), Image.LANCZOS)
    thumb.save(thumb_path, 'WEBP', quality=82, method=4)
    return {'image_path': image_name, 'thumb_path': thumb_name}


def media_file(name: str) -> Optional[Path]:
    if not name or '/' in name or '\\' in name:
        return None
    candidate = (MEDIA_DIR / name).resolve()
    if MEDIA_DIR.resolve() not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None
