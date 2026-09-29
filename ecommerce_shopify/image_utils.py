# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import hashlib
import io
import logging

_logger = logging.getLogger(__name__)

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None


def decode_image_bytes(image_data):
    """Safely decode image data from bytes, base64 str, or base64 bytes into raw bytes.

    :param image_data: bytes, str, or None
    :return: raw bytes or None
    """
    if not image_data:
        return None
    if isinstance(image_data, bytes):
        # Could be raw image bytes (e.g. starting with \x89PNG or \xff\xd8) or base64 bytes
        if image_data.startswith(b'\x89PNG') or image_data.startswith(b'\xff\xd8') or image_data.startswith(b'GIF') or image_data.startswith(b'RIFF'):
            return image_data
        try:
            decoded = base64.b64decode(image_data)
            if decoded.startswith(b'\x89PNG') or decoded.startswith(b'\xff\xd8') or decoded.startswith(b'GIF') or decoded.startswith(b'RIFF'):
                return decoded
            return decoded or image_data
        except Exception:
            return image_data
    if isinstance(image_data, str):
        try:
            return base64.b64decode(image_data)
        except Exception:
            return image_data.encode('utf-8')
    return None


def compute_image_sha256(image_data):
    """Compute hex SHA-256 of the raw image bytes.

    :param image_data: raw bytes or base64
    :return: hex SHA-256 string or None
    """
    raw_bytes = decode_image_bytes(image_data)
    if not raw_bytes:
        return None
    return hashlib.sha256(raw_bytes).hexdigest()


def get_normalized_image_bytes(image_data, size=(16, 16)):
    """Convert image into normalized RGB raw bytes at fixed dimensions for perceptual comparison.

    Uses PIL to open the image, converts to 'RGB' mode (stripping alpha/palette differences),
    and resizes to ``size`` with bilinear interpolation.

    :param image_data: raw bytes or base64
    :param tuple size: (width, height), defaults to (16, 16)
    :return: raw bytes of normalized pixels or None if invalid
    """
    if PILImage is None:
        return None
    raw_bytes = decode_image_bytes(image_data)
    if not raw_bytes:
        return None
    try:
        img = PILImage.open(io.BytesIO(raw_bytes))
        img = img.convert('RGB')
        resample = getattr(PILImage.Resampling, 'BILINEAR', PILImage.BILINEAR if hasattr(PILImage, 'BILINEAR') else None)
        if resample is not None:
            img = img.resize(size, resample)
        else:
            img = img.resize(size)
        return img.tobytes()
    except Exception as exc:
        _logger.debug("Could not compute normalized image bytes: %s", exc)
        return None


def byte_diff_ratio(b1, b2):
    """Compute the mean absolute difference ratio between two byte sequences.

    Returns a float between 0.0 (exact match) and 1.0 (completely opposite).
    """
    if not b1 or not b2 or len(b1) != len(b2):
        return 1.0
    total_diff = sum(abs(x - y) for x, y in zip(b1, b2))
    return total_diff / (len(b1) * 255.0)


def compute_image_fingerprint(image_data):
    """Compute a cached fingerprint dict containing raw SHA-256 and normalized pixel bytes.

    :param image_data: raw bytes or base64
    :return: dict with 'sha256', 'norm', and 'raw_bytes' or None
    """
    raw_bytes = decode_image_bytes(image_data)
    if not raw_bytes:
        return None
    return {
        'sha256': hashlib.sha256(raw_bytes).hexdigest(),
        'norm': get_normalized_image_bytes(raw_bytes),
        'raw_bytes': raw_bytes,
        'len': len(raw_bytes),
    }


def fingerprints_match(fp1, fp2, threshold=0.06):
    """Compare two precomputed image fingerprints.

    1. If raw SHA-256 matches -> True (exact byte match).
    2. If normalized visual bytes match within threshold -> True (re-encoded/compressed match).
    3. Otherwise -> False.

    :param dict fp1: fingerprint dict
    :param dict fp2: fingerprint dict
    :param float threshold: difference ratio threshold (default 0.06 = 6%)
    :return: bool
    """
    if not fp1 or not fp2:
        return False
    if fp1.get('sha256') and fp1.get('sha256') == fp2.get('sha256'):
        return True
    norm1 = fp1.get('norm')
    norm2 = fp2.get('norm')
    if norm1 and norm2 and len(norm1) == len(norm2):
        diff = byte_diff_ratio(norm1, norm2)
        return diff <= threshold
    return False


def images_match(img1, img2, threshold=0.06):
    """Check whether two images represent the same visual content.

    Accepts raw bytes, base64 strings, or precomputed fingerprint dicts.
    Tolerant to Shopify CDN re-encoding, format changes (e.g. PNG vs JPEG),
    scaling, and compression artifacts.

    :param img1: raw bytes, base64 str, or fingerprint dict
    :param img2: raw bytes, base64 str, or fingerprint dict
    :param float threshold: difference ratio threshold (default 0.06 = 6%)
    :return: bool
    """
    if img1 is None or img2 is None:
        return False
    fp1 = img1 if isinstance(img1, dict) and 'sha256' in img1 else compute_image_fingerprint(img1)
    fp2 = img2 if isinstance(img2, dict) and 'sha256' in img2 else compute_image_fingerprint(img2)
    return fingerprints_match(fp1, fp2, threshold=threshold)
