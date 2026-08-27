from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from PIL import Image

try:
    from scripts import fetch_dcbbs_external_news_media as external_media
except (ImportError, ModuleNotFoundError):
    external_media = None  # type: ignore[assignment]


class ImplementationContractTests(unittest.TestCase):
    def test_implementation_module_exists(self) -> None:
        self.assertIsNotNone(
            external_media,
            "scripts.fetch_dcbbs_external_news_media must be implemented",
        )


@unittest.skipIf(external_media is None, "implementation module is not present yet")
class UrlAllowlistTests(unittest.TestCase):
    def test_qpic_url_loses_fragment_but_preserves_query(self) -> None:
        raw = (
            "https://mmecoa.qpic.cn/mmecoa_jpg/token/640"
            "?wx_fmt=jpeg&watermark=1#imgIndex=6"
        )

        self.assertEqual(
            external_media.canonicalize_url(raw),
            "https://mmecoa.qpic.cn/mmecoa_jpg/token/640"
            "?wx_fmt=jpeg&watermark=1",
        )

    def test_only_exact_hosts_and_public_media_paths_are_allowed(self) -> None:
        allowed = (
            "https://mmbiz.qpic.cn/sz_mmbiz_png/token/640?wx_fmt=png",
            "https://mmecoa.qpic.cn/mmbiz_svg/token/640?wx_fmt=svg",
            "https://mmbiz.qpic.cn/mmbiz/token/640?wx_fmt=gif&wx_lazy=1",
            "https://mmbiz.qpic.cn/sz_mmbiz_png/token0008.png",
            "https://res.wx.qq.com/t/wx_fed/we-emoji/res/"
            "v1.3.10/assets/Expression/Expression_82@2x.png?tp=webp",
            "https://res.wx.qq.com/t/wx_fed/we-emoji/res/"
            "assets/newemoji/Party.png",
        )
        for url in allowed:
            with self.subTest(url=url):
                self.assertEqual(external_media.canonicalize_url(url), url)

        rejected = (
            "http://mmecoa.qpic.cn/mmecoa_jpg/token/640?wx_fmt=jpeg",
            "https://evil.mmecoa.qpic.cn/mmecoa_jpg/token/640?wx_fmt=jpeg",
            "https://mmecoa.qpic.cn:443/mmecoa_jpg/token/640?wx_fmt=jpeg",
            "https://wx.qlogo.cn/mmopen/token/96?wx_lazy=1",
            "https://res.wx.qq.com/t/wx_fed/webwx/res/static/img/login.png",
            "https://res.wx.qq.com/t/wx_fed/we-emoji/res/assets/newemoji/../secret.png",
        )
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaises(external_media.UrlNotAllowedError):
                    external_media.canonicalize_url(url)


@unittest.skipIf(external_media is None, "implementation module is not present yet")
class TaskCollectionTests(unittest.TestCase):
    def test_database_is_read_only_and_references_keep_article_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "source.sqlite3"
            connection = sqlite3.connect(db_path)
            connection.execute(
                "CREATE TABLE news(article_id INTEGER PRIMARY KEY, images_json TEXT NOT NULL)"
            )
            shared_a = (
                "https://mmecoa.qpic.cn/mmecoa_jpg/token/640"
                "?wx_fmt=jpeg#imgIndex=1"
            )
            shared_b = (
                "https://mmecoa.qpic.cn/mmecoa_jpg/token/640"
                "?wx_fmt=jpeg#imgIndex=9"
            )
            connection.executemany(
                "INSERT INTO news(article_id,images_json) VALUES (?,?)",
                (
                    (
                        101,
                        json.dumps(
                            [
                                "https://www.dcbbs.com/fileroot1/inside.jpg",
                                shared_a,
                                "https://wx.qlogo.cn/mmopen/private/96",
                            ]
                        ),
                    ),
                    (102, json.dumps([shared_b])),
                ),
            )
            connection.commit()
            connection.close()
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()

            tasks, exclusions, snapshot = external_media.collect_media_tasks(db_path)

            after = hashlib.sha256(db_path.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertEqual(snapshot["articles_scanned"], 2)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(
                tasks[0].canonical_url,
                "https://mmecoa.qpic.cn/mmecoa_jpg/token/640?wx_fmt=jpeg",
            )
            self.assertEqual(
                [(item.article_id, item.image_index) for item in tasks[0].references],
                [(101, 2), (102, 1)],
            )
            self.assertEqual(
                [item.original_url for item in tasks[0].references],
                [shared_a, shared_b],
            )
            self.assertEqual(exclusions["dcbbs_internal"], 1)
            self.assertEqual(exclusions["explicitly_excluded_host"], 1)
            self.assertEqual(exclusions["not_allowlisted"], 0)


@unittest.skipIf(external_media is None, "implementation module is not present yet")
class MediaValidationTests(unittest.TestCase):
    @staticmethod
    def animated_gif() -> bytes:
        stream = io.BytesIO()
        frames = [Image.new("RGB", (5, 5), color) for color in ("red", "green", "blue")]
        frames[0].save(
            stream,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=80,
            loop=0,
        )
        return stream.getvalue()

    @staticmethod
    def jpeg() -> bytes:
        stream = io.BytesIO()
        Image.new("RGB", (4, 3), (10, 20, 30)).save(stream, format="JPEG")
        return stream.getvalue()

    def test_all_animation_frames_must_decode(self) -> None:
        content = self.animated_gif()
        info = external_media.validate_media_bytes(content, "image/gif")
        self.assertEqual(info.format, "gif")
        self.assertEqual(info.frame_count, 3)

        with self.assertRaises(external_media.MediaValidationError):
            external_media.validate_media_bytes(content[:-20], "image/gif")

    def test_svg_rejects_active_or_external_content(self) -> None:
        safe = (
            b'<svg xmlns="http://www.w3.org/2000/svg"><defs><path id="p"/></defs>'
            b'<use href="#p"/></svg>'
        )
        info = external_media.validate_media_bytes(safe, "image/svg+xml")
        self.assertEqual(info.format, "svg")
        self.assertEqual(info.extension, ".svg")

        unsafe = (
            b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b'<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<image href="https://tracker.invalid/a.png"/></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<rect style="fill:url(https://tracker.invalid/a.svg)"/></svg>',
        )
        for content in unsafe:
            with self.subTest(content=content):
                with self.assertRaises(external_media.MediaValidationError):
                    external_media.validate_media_bytes(content, "image/svg+xml")


@unittest.skipIf(external_media is None, "implementation module is not present yet")
class RobotsPolicyTests(unittest.TestCase):
    URL = "https://mmecoa.qpic.cn/mmecoa_jpg/token/640?wx_fmt=jpeg"

    def test_robots_4xx_is_unavailable_and_not_a_disallow(self) -> None:
        status = external_media.classify_robots_response(
            "mmecoa.qpic.cn", 400, "text/plain", b"", [self.URL]
        )
        self.assertTrue(status["allowed"])
        self.assertEqual(status["classification"], "unavailable_4xx")

    def test_no_parseable_group_has_no_applicable_rules(self) -> None:
        status = external_media.classify_robots_response(
            "mmecoa.qpic.cn",
            200,
            "text/html; charset=utf-8",
            b"<html><body>ordinary page</body></html>",
            [self.URL],
        )
        self.assertTrue(status["allowed"])
        self.assertEqual(status["classification"], "success_no_rules")

    def test_explicit_disallow_or_server_error_blocks_the_host(self) -> None:
        disallowed = external_media.classify_robots_response(
            "mmecoa.qpic.cn",
            200,
            "text/plain",
            b"User-agent: *\nDisallow: /\n",
            [self.URL],
        )
        unreachable = external_media.classify_robots_response(
            "mmecoa.qpic.cn", 503, "text/plain", b"", [self.URL]
        )
        self.assertFalse(disallowed["allowed"])
        self.assertEqual(disallowed["classification"], "success_disallowed")
        self.assertFalse(unreachable["allowed"])
        self.assertEqual(unreachable["classification"], "unreachable_5xx")

    def test_valid_robots_read_floor_covers_current_large_no_rules_response(self) -> None:
        class Response:
            status_code = 200
            headers = {"Content-Type": "text/html; charset=utf-8"}

            def __init__(self) -> None:
                self.closed = False

            def iter_content(self, _chunk_size: int) -> Any:
                body = b"<html>" + b"x" * 90_000 + b"</html>"
                for start in range(0, len(body), 8192):
                    yield body[start : start + 8192]

            def close(self) -> None:
                self.closed = True

        class Session:
            def __init__(self) -> None:
                self.headers: dict[str, str] = {}
                self.response = Response()

            def get(self, _url: str, **_kwargs: Any) -> Response:
                return self.response

        session = Session()
        status = external_media.fetch_robots_status(
            "mmecoa.qpic.cn", [self.URL], session=session, timeout=1
        )

        self.assertTrue(status["allowed"])
        self.assertEqual(status["classification"], "success_no_rules")
        self.assertTrue(session.response.closed)


@unittest.skipIf(external_media is None, "implementation module is not present yet")
class DownloadBoundaryTests(unittest.TestCase):
    class Response:
        def __init__(
            self,
            status: int,
            chunks: list[bytes],
            headers: dict[str, str] | None = None,
        ) -> None:
            self.status_code = status
            self.headers = headers or {}
            self._chunks = chunks
            self.closed = False

        def iter_content(self, _chunk_size: int) -> Any:
            yield from self._chunks

        def close(self) -> None:
            self.closed = True

    class Session:
        def __init__(self, response: "DownloadBoundaryTests.Response") -> None:
            self.response = response
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def get(self, url: str, **kwargs: Any) -> "DownloadBoundaryTests.Response":
            self.calls.append((url, kwargs))
            return self.response

    def test_redirect_is_not_followed(self) -> None:
        response = self.Response(302, [], {"Location": "https://evil.invalid/file"})
        session = self.Session(response)

        with self.assertRaisesRegex(external_media.DownloadError, "redirect"):
            external_media.download_url(
                session,
                "https://mmecoa.qpic.cn/mmecoa_jpg/token/640?wx_fmt=jpeg",
                max_bytes=1024,
                timeout=1,
            )

        self.assertIs(session.calls[0][1]["allow_redirects"], False)
        self.assertIs(session.calls[0][1]["stream"], True)
        self.assertTrue(response.closed)

    def test_declared_or_streamed_body_over_limit_is_rejected(self) -> None:
        url = "https://mmecoa.qpic.cn/mmecoa_jpg/token/640?wx_fmt=jpeg"
        declared = self.Response(
            200,
            [],
            {"Content-Type": "image/jpeg", "Content-Length": "1025"},
        )
        streamed = self.Response(200, [b"a" * 600, b"b" * 600], {"Content-Type": "image/jpeg"})

        for response in (declared, streamed):
            with self.subTest(response=response):
                with self.assertRaisesRegex(external_media.DownloadError, "size limit"):
                    external_media.download_url(
                        self.Session(response), url, max_bytes=1024, timeout=1
                    )
                self.assertTrue(response.closed)


@unittest.skipIf(external_media is None, "implementation module is not present yet")
class ArchiveAndResumeTests(unittest.TestCase):
    @staticmethod
    def jpeg() -> bytes:
        stream = io.BytesIO()
        Image.new("RGB", (4, 3), (10, 20, 30)).save(stream, format="JPEG")
        return stream.getvalue()

    def test_archive_writes_mapping_manifest_and_resumes_only_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "source.sqlite3"
            output = root / "archive"
            connection = sqlite3.connect(db_path)
            connection.execute(
                "CREATE TABLE news(article_id INTEGER PRIMARY KEY, images_json TEXT NOT NULL)"
            )
            url_a = (
                "https://mmecoa.qpic.cn/mmecoa_jpg/token/640"
                "?wx_fmt=jpeg#imgIndex=1"
            )
            url_b = (
                "https://mmecoa.qpic.cn/mmecoa_jpg/token/640"
                "?wx_fmt=jpeg#imgIndex=7"
            )
            connection.executemany(
                "INSERT INTO news(article_id,images_json) VALUES (?,?)",
                ((1, json.dumps([url_a])), (2, json.dumps([url_b]))),
            )
            connection.commit()
            connection.close()
            database_before = hashlib.sha256(db_path.read_bytes()).hexdigest()
            robots = {
                "mmecoa.qpic.cn": {
                    "host": "mmecoa.qpic.cn",
                    "status_code": 400,
                    "content_type": "text/plain",
                    "classification": "unavailable_4xx",
                    "allowed": True,
                    "checked_at": "2026-07-13T00:00:00+00:00",
                }
            }
            calls: list[str] = []

            def fetch(task: Any) -> Any:
                calls.append(task.canonical_url)
                return external_media.DownloadPayload(self.jpeg(), "image/jpeg")

            first = external_media.archive_external_media(
                db_path,
                output,
                fetch,
                robots,
                workers=1,
                checkpoint_every=1,
            )

            self.assertEqual(len(calls), 1)
            self.assertEqual(first["counts"]["saved"], 1)
            self.assertEqual(first["usage_scope"], "restricted/internal-research")
            self.assertIn("no public redistribution", first["rights_boundary"])
            self.assertEqual(database_before, hashlib.sha256(db_path.read_bytes()).hexdigest())

            jsonl = output / "external_news_media" / "external_news_media.jsonl"
            manifest_path = output / "external_news_media" / "manifest.json"
            records = external_media.load_jsonl(jsonl)
            self.assertEqual(len(records), 1)
            self.assertEqual(
                [(item["article_id"], item["image_index"]) for item in records[0]["references"]],
                [(1, 1), (2, 1)],
            )
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(external_media.stored_record_matches(output, records[0], records[0]["canonical_url"]))

            def must_not_fetch(_task: Any) -> Any:
                raise AssertionError("a verified resume record must not be fetched again")

            second = external_media.archive_external_media(
                db_path,
                output,
                must_not_fetch,
                robots,
                workers=1,
                checkpoint_every=1,
            )
            self.assertEqual(second["counts"]["resumed"], 1)

            media_path = output / records[0]["local_path"]
            media_path.write_bytes(b"\xff\xd8\xff")
            self.assertFalse(
                external_media.stored_record_matches(
                    output, records[0], records[0]["canonical_url"]
                )
            )


if __name__ == "__main__":
    unittest.main()
