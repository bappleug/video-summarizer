import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bili  # noqa: E402


class ResolveBvidTest(unittest.TestCase):
    def test_bv_number(self):
        self.assertEqual(bili.BV_RE.search("BV1AU421o7ob").group(1), "BV1AU421o7ob")

    def test_full_url_with_page(self):
        url = "https://www.bilibili.com/video/BV1AU421o7ob/?p=2&spm_id_from=x"
        self.assertEqual(bili.BV_RE.search(url).group(1), "BV1AU421o7ob")

    def test_av_number(self):
        self.assertEqual(bili.AV_RE.search("av80433022").group(1), "80433022")


class PickTrackTest(unittest.TestCase):
    def test_prefers_human_zh_over_ai(self):
        tracks = [
            {"lan": "ai-zh", "lan_doc": "中文（自动生成）"},
            {"lan": "zh-CN", "lan_doc": "中文（中国）"},
        ]
        self.assertEqual(bili.pick_track(tracks)["lan"], "zh-CN")

    def test_falls_back_to_ai_when_only_ai(self):
        tracks = [{"lan": "ai-zh", "lan_doc": "中文（自动生成）"}]
        self.assertEqual(bili.pick_track(tracks)["lan"], "ai-zh")

    def test_explicit_language(self):
        tracks = [{"lan": "ai-zh"}, {"lan": "en"}]
        self.assertEqual(bili.pick_track(tracks, "en")["lan"], "en")

    def test_empty(self):
        self.assertIsNone(bili.pick_track([]))


class TextRenderTest(unittest.TestCase):
    def test_join_drops_consecutive_duplicates(self):
        segs = [
            {"from": 0, "to": 1, "text": "你好"},
            {"from": 1, "to": 2, "text": "你好"},
            {"from": 2, "to": 3, "text": "世界"},
            {"from": 3, "to": 4, "text": ""},
        ]
        self.assertEqual(bili.join_text(segs), "你好\n世界")

    def test_srt_layout(self):
        segs = [{"from": 1.5, "to": 3.25, "text": "第一句"}]
        self.assertEqual(
            bili.render_srt(segs),
            "1\n00:00:01,500 --> 00:00:03,250\n第一句",
        )

    def test_timed_render(self):
        segs = [{"from": 65, "to": 70, "text": "第二段"}]
        self.assertEqual(bili.render_timed(segs), "[01:05] 第二段")


class WbiTest(unittest.TestCase):
    def test_mixin_key_length(self):
        key = bili.mixin_key("7cd084941338484aae1ad9425b84077c",
                             "493c850383e832b48cd4f0d5d0bd56b3")
        self.assertEqual(len(key), 32)

    def test_sign_is_deterministic_and_filters_chars(self):
        key = bili.mixin_key("7cd084941338484aae1ad9425b84077c",
                             "493c850383e832b48cd4f0d5d0bd56b3")
        a = bili.wbi_sign({"bvid": "BV1x", "name": "a!b(c)"}, key, now=1700000000)
        b = bili.wbi_sign({"bvid": "BV1x", "name": "a!b(c)"}, key, now=1700000000)
        self.assertEqual(a, b)
        self.assertIn("w_rid=", a)
        self.assertIn("name=abc", a)


class CredentialParseTest(unittest.TestCase):
    def test_poll_url_keeps_percent_encoding(self):
        data = {
            "url": "https://passport.biligame.com/crossdomain?DedeUserID=123"
                   "&SESSDATA=abc%2Cdef%2Cg&bili_jct=csrf&Expires=1",
            "refresh_token": "tok",
        }
        cred = bili.build_cred_from_poll(data)
        self.assertEqual(cred["cookies"]["SESSDATA"], "abc%2Cdef%2Cg")
        self.assertEqual(cred["cookies"]["DedeUserID"], "123")
        self.assertEqual(cred["refresh_token"], "tok")

    def test_cookie_header_skips_internal_fields(self):
        header = bili.cookie_header({"SESSDATA": "s", "bili_jct": "j", "_x": "no"})
        self.assertEqual(header, "SESSDATA=s; bili_jct=j")

    def test_sessdata_from_set_cookie_header(self):
        from email.message import Message

        headers = Message()
        headers.add_header("Set-Cookie", "SESSDATA=abc%2Cdef; Path=/; HttpOnly")
        headers.add_header("Set-Cookie", "bili_jct=csrf; Path=/")
        cred = bili.build_cred_from_poll({"url": "", "refresh_token": "tok"}, headers)
        self.assertEqual(cred["cookies"]["SESSDATA"], "abc%2Cdef")
        self.assertEqual(cred["cookies"]["bili_jct"], "csrf")

    def test_missing_sessdata_raises(self):
        with self.assertRaises(bili.CliError):
            bili.build_cred_from_poll({"url": "https://x/?DedeUserID=1"})


class QrPaintTest(unittest.TestCase):
    def test_swaps_to_white_background(self):
        raw = "\x1b[40;37;1m██\x1b[0m"
        self.assertEqual(bili.paint_qr_white(raw), "\x1b[47;30;1m██\x1b[0m")


class CorrespondPathTest(unittest.TestCase):
    def test_hex_length_and_format(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            original = bili.PUBKEY_PATH
            bili.PUBKEY_PATH = os.path.join(tmp, "pub.pem")
            try:
                path = bili.correspond_path(1750000000000)
            finally:
                bili.PUBKEY_PATH = original
        self.assertEqual(len(path), 256)
        self.assertTrue(all(c in "0123456789abcdef" for c in path))


if __name__ == "__main__":
    unittest.main()
