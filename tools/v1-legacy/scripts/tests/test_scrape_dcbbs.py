from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.scrape_dcbbs import (  # noqa: E402
    Fetcher,
    extension_for_preview,
    image_magic_matches,
    parse_news_html,
    stored_image_record_matches,
)


class NewsNavigationRegressionTests(unittest.TestCase):
    def test_navigation_reparented_outside_article_is_still_parsed(self) -> None:
        html = b"""<!doctype html>
<html>
  <head>
    <link rel="canonical" href="https://www.dcbbs.com/i-131429.html">
    <meta name="keywords" content="sample">
  </head>
  <body>
    <article class="article-wrapper">
      <div class="article-header"><h1 class="article-title">Sample</h1></div>
      <div class="post-meta-items">
        <div class="post-meta-item"><i class="fa-clock-o"></i>2025-12-19 12:00:00</div>
      </div>
      <div class="grap"><div class="P_conten">Body</div></div>
    </article>
    <!-- A malformed source article can make html.parser reparent this block. -->
    <div>
      \xe4\xb8\x8a\xe4\xb8\x80\xe7\xaf\x87\xef\xbc\x9a<a href="https://www.dcbbs.com/i-131428.html">Previous</a>
      \xe4\xb8\x8b\xe4\xb8\x80\xe7\xaf\x87\xef\xbc\x9a<a href="https://www.dcbbs.com/i-131430.html">Next</a>
    </div>
  </body>
</html>"""

        record = parse_news_html(131429, html, {})

        self.assertEqual(record["previous_id"], 131428)
        self.assertEqual(record["next_id"], 131430)


class FetcherSafetyRegressionTests(unittest.TestCase):
    def test_redirect_target_is_never_followed_automatically(self) -> None:
        class Guard:
            def __init__(self) -> None:
                self.checked: list[str] = []

            def ensure_allowed(self, url: str) -> None:
                self.checked.append(url)

        class Session:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def get(self, url: str, **kwargs: object) -> SimpleNamespace:
                self.calls.append((url, kwargs))
                return SimpleNamespace(
                    status_code=302,
                    headers={"Location": "/View.aspx?restricted=1"},
                    url=url,
                )

        guard = Guard()
        session = Session()
        fetcher = Fetcher(guard, rate=0, timeout=1, retries=0)
        fetcher.local.session = session

        with self.assertRaisesRegex(RuntimeError, "redirect blocked"):
            fetcher.get("https://www.dcbbs.com/fileroot1/example.gif")

        self.assertEqual(guard.checked, ["https://www.dcbbs.com/fileroot1/example.gif"])
        self.assertEqual(len(session.calls), 1)
        self.assertIs(session.calls[0][1]["allow_redirects"], False)


class ImageIntegrityRegressionTests(unittest.TestCase):
    @staticmethod
    def jpeg_bytes() -> bytes:
        stream = io.BytesIO()
        Image.new("RGB", (2, 2), (12, 34, 56)).save(stream, format="JPEG")
        return stream.getvalue()

    @staticmethod
    def animated_gif_bytes() -> bytes:
        stream = io.BytesIO()
        frames = [Image.new("RGB", (4, 4), color) for color in ("red", "green", "blue")]
        frames[0].save(
            stream,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
        )
        return stream.getvalue()

    def test_header_only_payload_is_not_a_usable_image(self) -> None:
        self.assertFalse(image_magic_matches(b"\xff\xd8\xff"))
        self.assertFalse(image_magic_matches(b"\x89PNG\r\n\x1a\n"))
        content = self.jpeg_bytes()
        self.assertTrue(image_magic_matches(content))
        self.assertFalse(image_magic_matches(content[:-1]))

    def test_truncated_later_animation_frame_is_rejected(self) -> None:
        content = self.animated_gif_bytes()
        self.assertTrue(image_magic_matches(content))
        self.assertFalse(image_magic_matches(content[:-20]))

    def test_extension_uses_detected_bytes_not_mislabelled_gif_url(self) -> None:
        content = self.jpeg_bytes()
        self.assertEqual(
            extension_for_preview(
                "https://www.dcbbs.com/fileroot1/example.gif",
                "image/gif",
                content,
            ),
            ".jpg",
        )

    def test_resume_requires_current_url_file_size_hash_and_valid_decode(self) -> None:
        content = self.jpeg_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            local = output / "news_media" / "image.jpg"
            local.parent.mkdir(parents=True)
            local.write_bytes(content)
            row = {
                "status": "ok",
                "source_url": "https://www.dcbbs.com/fileroot1/example.gif",
                "local_path": "news_media/image.jpg",
                "byte_count": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }

            self.assertTrue(stored_image_record_matches(output, row, row["source_url"]))

            row["source_url"] = "https://www.dcbbs.com/fileroot1/changed.gif"
            self.assertFalse(
                stored_image_record_matches(
                    output,
                    row,
                    "https://www.dcbbs.com/fileroot1/example.gif",
                )
            )
            row["source_url"] = "https://www.dcbbs.com/fileroot1/example.gif"
            local.write_bytes(b"\xff\xd8\xff")
            self.assertFalse(stored_image_record_matches(output, row, row["source_url"]))


if __name__ == "__main__":
    unittest.main()
