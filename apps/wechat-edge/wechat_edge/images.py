"""Three-tier image retrieval on top of wechatauto's MediaDownloader.

Tiers, cheapest first — UI automation is only touched when the local cache
holds nothing better than a thumbnail:

1. ``<md5>.dat``   normal image already on disk (no UI)
2. ``<md5>_h.dat`` original image already on disk (no UI)
3. ``<md5>_t.dat`` only a thumbnail exists → download_image_original()
   clicks the image in the client to make WeChat fetch the original
   (UIA; serialized through the library's LockManager)

Every produced file is magic-byte verified against the ingress media
whitelist (JPEG/PNG/WebP/GIF) and stored as a content-addressed copy, so
``.wxgf`` artifacts that ffmpeg could not transcode degrade instead of
being rejected with a 415 server-side.

All decryption calls pass the keys detected at startup explicitly:
``decrypt_image(aes_key=None)`` would fall into a 120-second blocking
memory scan (wechatauto media.py ``_scan_aes_key(monitor=True)``).
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from wechatauto.media import MediaDownloader
from wechatauto.utils.lock import LockManager

log = logging.getLogger("wechat_edge")

# Mirrors the /v1/ingress/media magic-byte whitelist.
def _sniff(data: bytes) -> Optional[str]:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class EdgeMediaDownloader(MediaDownloader):
    """download() returns a content-addressed artifact plus the tier used."""

    def __init__(self, db, save_dir: Path):
        super().__init__(db, save_dir=str(save_dir))
        self.save_root = Path(save_dir)
        self.save_root.mkdir(parents=True, exist_ok=True)
        self._keys: Optional[Tuple[str, int]] = None

    def refresh_keys(self) -> Optional[Tuple[str, int]]:
        try:
            self._keys = self.detect_image_key()
        except Exception as exc:
            log.warning("image key detection failed: %r", exc)
            self._keys = None
        if self._keys is None:
            log.warning("no image AES key; image collection degrades to placeholders")
        return self._keys

    def _explicit(self) -> dict:
        if not self._keys:
            return {}
        return {"aes_key": self._keys[0], "xor_key": self._keys[1]}

    def _store(self, data: bytes) -> Optional[Path]:
        """Magic-verify then write a content-addressed copy; None on reject.

        wxgf (HEVC) payloads — common for WeChat 4.x normal images — are
        transcoded to jpg first via the library's bundled ffmpeg."""
        ext = _sniff(data)
        if ext is None and data[:4] == b"wxgf":
            jpg = self._wxgf_to_jpg(data)
            if jpg is not None:
                data = jpg
                ext = "jpg"
        if ext is None:
            return None
        target = self.save_root / (hashlib.sha256(data).hexdigest() + "." + ext)
        if not target.exists():
            target.write_bytes(data)
        return target

    def _store_file(self, path: Path) -> Optional[Path]:
        try:
            return self._store(Path(path).read_bytes())
        except OSError as exc:
            log.warning("reading %s failed: %r", path, exc)
            return None

    def _download_h_dat(self, username: str, local_id: int) -> Optional[Path]:
        try:
            row = self.db.get_message_row(username, local_id)
            if not row or row["local_type"] != 3:
                return None
            md5 = self._img_md5(row)
            if not md5:
                return None
            h_dat = self._find_h_dat(username, md5)
            if not h_dat or os.path.getsize(h_dat) <= 102400:
                return None
            data = self.decrypt_image(h_dat, **self._explicit())
            return self._store(data)
        except Exception as exc:
            log.warning("tier2 _h.dat download failed: %r", exc)
            return None

    def _ui_original(self, username: str, local_id: int,
                     chat_display_name: str) -> Optional[Path]:
        if not chat_display_name:
            return None
        try:
            with LockManager.acquire():
                path = self.download_image_original(
                    username, local_id, str(self.save_root),
                    chat_name=chat_display_name, **self._explicit())
        except Exception as exc:
            log.warning("tier3 UI original download failed: %r", exc)
            return None
        if path:
            return self._store_file(Path(path))
        # The upstream check (sleep 3s → size>100KB) races WeChat's download
        # of multi-MB originals: the click may have succeeded while _h.dat is
        # still in flight. Poll for the artifact before giving up.
        return self._await_triggered_download(username, local_id)

    def _await_triggered_download(self, username: str, local_id: int,
                                  wait_seconds: float = 120.0) -> Optional[Path]:
        import time as _time
        try:
            row = self.db.get_message_row(username, local_id)
            md5 = self._img_md5(row) if row else None
        except Exception:
            md5 = None
        if not md5:
            return None
        best: Optional[Path] = None
        deadline = _time.monotonic() + wait_seconds
        while _time.monotonic() < deadline:
            try:
                h_dat = self._find_h_dat(username, md5)
                if h_dat and os.path.getsize(h_dat) > 102400:
                    return self._store(
                        self.decrypt_image(h_dat, **self._explicit()))
                dat = self._find_dat(username, md5, 0)
                if dat and os.path.getsize(dat) > 1024 and best is None:
                    # the click already landed the normal image; keep polling
                    # for the original, but remember this beats a thumbnail
                    best = self._store(
                        self.decrypt_image(dat, **self._explicit()))
            except Exception:
                pass
            _time.sleep(3)
        return best


    def download(self, username: str, local_id: int,
                 chat_display_name: str) -> Tuple[Optional[Path], str]:
        """Return (content-addressed artifact, tier); (None, 'failed') on miss."""
        if self._keys is None:
            # Without keys decryption would block on a 120s memory scan.
            return None, "failed"

        # Tier 1: super() resolves <md5>.dat and falls back to a *_thumb file.
        try:
            path = super().download_image(
                username, local_id, str(self.save_root), **self._explicit())
        except Exception as exc:
            log.warning("tier1 .dat download failed: %r", exc)
            path = None
        thumb_path = Path(path) if (
            path and "_thumb" in os.path.basename(path)) else None
        if path and thumb_path is None:
            out = self._store_file(Path(path))
            if out:
                return out, "dat"

        # Tier 2: an original already fetched earlier lives in <md5>_h.dat.
        out = self._download_h_dat(username, local_id)
        if out:
            return out, "h_dat"

        # Tier 3: only a thumbnail locally → drive the UI to fetch the
        # original (clicks the image and the "图片原始大小" button).
        out = self._ui_original(username, local_id, chat_display_name)
        if out:
            return out, "ui_original"

        if thumb_path:
            out = self._store_file(thumb_path)
            if out:
                return out, "thumb"

        return None, "failed"
