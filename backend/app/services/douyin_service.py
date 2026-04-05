import json
import logging
import random
import re
import time
from dataclasses import dataclass
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


logger = logging.getLogger(__name__)

DOUYIN_DETAIL_API_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
DOUYIN_TTWID_REGISTER_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
DOUYIN_MS_TOKEN_REPORT_URL = "https://mssdk.bytedance.com/web/report"

DOUYIN_TTWID_REGISTER_BODY = (
    '{"region":"cn","aid":1768,"needFid":false,"service":"www.ixigua.com",'
    '"migrate_info":{"ticket":"","source":"node"},"cbUrlProtocol":"https","union":true}'
)

# 这里沿用公开项目里已经验证过的 Web report 参数，用来申请游客态 msToken。
DOUYIN_MS_TOKEN_STR_DATA = (
    "fWOdJTQR3/jwmZqBBsPO6tdNEc1jX7YTwPg0Z8CT+j3HScLFbj2Zm1XQ7/lqgSutntVKLJWaY3Hc/+vc0h+So9N1"
    "t6EqiImu5jKyUa+S4NPy6cNP0x9CUQQgb4+RRihCgsn4QyV8jivEFOsj3N5zFQbzXRyOV+9aG5B5EAnwpn8C70lls"
    "Wq0zJz1VjN6y2KZiBZRyonAHE8feSGpwMDeUTllvq6BG3AQZz7RrORLWNCLEoGzM6bMovYVPRAJipuUML4Hq/568b"
    "Nb5vqAo0eOFpvTZjQFgbB7f/CtAYYmnOYlvfrHKBKvb0TX6AjYrw2qmNNEer2ADJosmT5kZeBsogDui8rNiI/OOdX"
    "9PVotmcSmHOLRfw1cYXTgwHXr6cJeJveuipgwtUj2FNT4YCdZfUGGyRDz5bR5bdBuYiSRteSX12EktobsKPksdhUP"
    "GGv99SI1QRVmR0ETdWqnKWOj/7ujFZsNnfCLxNfqxQYEZEp9/U01CHhWLVrdzlrJ1v+KJH9EA4P1Wo5/2fuBFVdIz2"
    "upFqEQ11DJu8LSyD43qpTok+hFG3Moqrr81uPYiyPHnUvTFgwA/TIE11mTc/pNvYIb8IdbE4UAlsR90eYvPkI+rK9"
    "KpYN/l0s9ti9sqTth12VAw8tzCQvhKtxevJRQntU3STeZ3coz9Dg8qkvaSNFWuBDuyefZBGVSgILFdMy33//l/eTX"
    "hQpFrVc9OyxDNsG6cvdFwu7trkAENHU5eQEWkFSXBx9Ml54+fa3LvJBoacfPViyvzkJworlHcYYTG392L4q6wuMSS"
    "pYUconb+0c5mwqnnLP6MvRdm/bBTaY2Q6RfJcCxyLW0xsJMO6fgLUEjAg/dcqGxl6gDjUVRWbCcG1NAwPCfmYARTu"
    "XQYbFc8LO+r6WQTWikO9Q7Cgda78pwH07F8bgJ8zFBbWmyrghilNXENNQkyIzBqOQ1V3w0WXF9+Z3vG3aBKCjIENq"
    "AQM9qnC14WMrQkfCHosGbQyEH0n/5R2AaVTE/ye2oPQBWG1m0Gfcgs/96f6yYrsxbDcSnMvsA+okyd6GfWsdZYTIK"
    "1E97PYHlncFeOjxySjPpfy6wJc4UlArJEBZYmgveo1SZAhmXl3pJY3yJa9CmYImWkhbpwsVkSmG3g11JitJXTGLIf"
    "qKXSAhh+7jg4HTKe+5KNir8xmbBI/DF8O/+diFAlD+BQd3cV0G4mEtCiPEhOvVLKV1pE+fv7nKJh0t38wNVdbs3qH"
    "tiQNN7JhY4uWZAosMuBXSjpEtoNUndI+o0cjR8XJ8tSFnrAY8XihiRzLMfeisiZxWCvVwIP3kum9MSHXma75cdCQG"
    "FBfFRj0jPn1JildrTh2vRgwG+KeDZ33BJ2VGw9PgRkztZ2l/W5d32jc7H91FftFFhwXil6sA23mr6nNp6CcrO7rOb"
    "lcm5SzXJ5MA601+WVicC/g3p6A0lAnhjsm37qP+xGT+cbCFOfjexDYEhnqz0QZm94CCSnilQ9B/HBLhWOddp9GK0S"
    "ABIk5i3xAH701Xb4HCcgAulvfO5EK0RL2eN4fb+CccgZQeO1Zzo4qsMHc13UG0saMgBEH8SqYlHz2S0CVHuDY5j1M"
    "SV0nsShjM01vIynw6K0T8kmEyNjt1eRGlleJ5lvE8vonJv7rAeaVRZ06rlYaxrMT6cK3RSHd2liE50Z3ik3xezwWo"
    "aY6zBXvCzljyEmqjNFgAPU3gI+N1vi0MsFmwAwFzYqqWdk3jwRoWLp//FnawQX0g5T64CnfAe/o2e/8o5/bvz83Os"
    "AAwZoR48GZzPu7KCIN9q4GBjyrePNx5Csq2srblifmzSKwF5MP/RLYsk6mEE15jpCMKOVlHcu0zhJybNP3AKMVllF"
    "6pvn+HWvUnLXNkt0A6zsfvjAva/tbLQiiiYi6vtheasIyDz3HpODlI+BCkV6V8lkTt7m8QJ1IcgTfqjQBummyjYTS"
    "wsQji3DdNCnlKYd13ZQa545utqu837FFAzOZQhbnC3bKqeJqO2sE3m7WBUMbRWLflPRqp/PsklN+9jBPADKxKPl8g"
    "6/NZVq8fB1w68D5EJlGExdDhglo4B0aihHhb1u3+zJ2DqkxkPCGBAZ2AcuFIDzD53yS4NssoWb4HJ7YyzPaJro+tgG"
    "9TshWRBtUw8Or3m0OtQtX+rboYn3+GxvD1O8vWInrg5qxnepelRcQzmnor4rHF6ZNhAJZAf18Rjncra00HPJBugY5"
    "rD+EwnN9+mGQo43b01qBBRYEnxy9JJYuvXxNXxe47/MEPOw6qsxN+dmyIWZSuzkw8K+iBM/anE11yfU4qTFt0veCa"
    "VprK6tXaFK0ZhGXDOYJd70sjIP4UrPhatp8hqIXSJ2cwi70B+TvlDk/o19CA3bH6YxrAAVeag1P9hmNlfJ7NxK3Jp"
    "7+Ny1Vd7JHWVF+R6rSJiXXPfsXi3ZEy0klJAjI51NrDAnzNtgIQf0V8OWeEVv7F8Rsm3/GKnjdNOcDKymi9agZUgt"
    "ctENWbCXGFnI40NHuVHtBRZeYAYtwfV7v6U0bP9s7uZGpkp+OETHMv3AyV0MVbZwQvarnjmct4Z3Vma+DvT+Z4VlM"
    "VnkC2x2FLt26K3SIMz+KV2XLv5ocEdPFSn1vMR7zruCWC8XqAG288biHo/soldmb/nlw8o8qlfZj4h296K3hfdFub"
    "GIUtqgsrZCrLCkkRC08Cv1ozEX/y6t2YrQepwiNmwDVk5IufStVvJMj+y2r9TcYLv7UKWXx3P6aySvM2ZHPaZhv+6"
    "Z/A/jIMBSvOizn4qG11iK7Oo6JYhxCSMJZsetjsnL4ecSIAufEmoFlAScWBh6nFArRpVLvkAZ3tej7H2lWFRXIU7x"
    "7mdBfGqU82PpM6znKMMZCpEsvHqpkSPSL+Kwz2z1f5wW7BKcKK4kNZ8iveg9VzY1NNjs91qU8DJpUnGyM04C7KNMpe"
    "ilEmoOxvyelMQdi85ndOVmigVKmy5JYlODNX744sHpeqmMEK/ux3xY5O406lm7dZlyGPSMrFWbm4rzqvSEIskP43+"
    "9xVP8L84GeHE4RpOHg3qh/shx+/WnT1UhKuKpByHCpLoEo144udpzZswCYSMp58uPrlwdVF31//AacTRk8dUP3tBl"
    "nSQPa1eTpXWFCn7vIiqOTXaRL//YQK+e7ssrgSUnwhuGKJ8aqNDgdsL+haVZnV9g5Qrju643adyNixvYFEp0uxzOz"
    "VkekOMh2FYnFVIL2mJYGpZEXlAIC0zQbb54rSP89j0G7soJ2HcOkD0NmMEWj/7hUdTuMin1lRNde/qmHjwhbhqL8Z9"
    "MEO/YG3iLMgFTgSNQQhyE8AZAAKnehmzjORJfbK+qxyiJ07J843EDduzOoYt9p/YLqyTFmAgpdfK0uYrtAJ47cbl5"
    "WWhVXp5/XUxwWdL7TvQB0Xh6ir1/XBRcsVSDrR7cPE221ThmW1EPzD+SPf2L2gS0WromZqj1PhLgk92YnnR9s7/nL"
    "BXZHPKy+fDbJT16QqabFKqAl9G0blyf+R5UGX2kN+iQp4VGXEoH5lXxNNTlgRskzrW7KliQXcac20oimAHUE8Phf+"
    "rXXglpmSv4XN3eiwfXwvOaAMVjMRmRxsKitl5iZnwpcdbsC4jt16g2r/ihlKzLIYju+XZej4dNMlkftEidyNg24IV"
    "imJthXY1H15RZ8Hm7mAM/JZrsxiAVI0A49pWEiUk3cyZcBzq/vVEjHUy4r6IZnKkRvLjqsvqWE95nAGMor+F0GLHW"
    "fBCVkuI51EIOknwSB1eTvLgwgRepV4pdy9cdp6iR8TZndPVCikflXYVMlMEJ2bJ2c0Swiq57ORJW6vQwnkxtPudpF"
    "Rc7tNNDzz4LKEznJxAwGi6pBR7/co2IUgRw1ijLFTHWHQJOjgc7KaduHI0C6a+BJb4Y8IWuIk2u2qCMF1HNKFAUn/"
    "J1gTcqtIJcvK5uykpfJFCYc899TmUc8LMKI9nu57m0S44Y2hPPYeW4XSakScsg8bJHMkcXk3Tbs9b4eqiD+kHUhTS"
    "2BGfsHadR3d5j8lNhBPzA5e+mE=="
)


@dataclass(frozen=True)
class DouyinResolvedMedia:
    title: str
    uploader: str | None
    duration: int | None
    thumbnail: str | None
    direct_url: str
    direct_ext: str
    headers: dict[str, str]


class DouyinService:
    _video_pattern = re.compile(r"/video/(?P<item_id>\d+)")
    _note_pattern = re.compile(r"/note/(?P<item_id>\d+)")
    _share_pattern = re.compile(r"/share/(?:video|note)/(?P<item_id>\d+)")

    def enrich_cookie_header(
        self,
        *,
        url: str,
        cookie_header: str | None,
        user_agent: str,
    ) -> str | None:
        cookies = self._parse_cookie_header(cookie_header)
        changed = False

        if not cookies.get("s_v_web_id"):
            cookies["s_v_web_id"] = self._generate_verify_fp()
            changed = True

        if not cookies.get("ttwid"):
            ttwid = self._request_ttwid(user_agent=user_agent)
            if ttwid:
                cookies["ttwid"] = ttwid
                changed = True

        if not cookies.get("msToken"):
            ms_token = self._request_ms_token(user_agent=user_agent)
            if ms_token:
                cookies["msToken"] = ms_token
                changed = True

        if not cookies.get("__ac_nonce"):
            ac_nonce = self._request_ac_nonce(url=url, user_agent=user_agent)
            if ac_nonce:
                cookies["__ac_nonce"] = ac_nonce
                changed = True

        if not cookies:
            return None
        if not changed and cookie_header:
            return cookie_header
        return self._serialize_cookie_header(cookies)

    def resolve_media(
        self,
        *,
        url: str,
        cookie_header: str | None,
        user_agent: str,
    ) -> DouyinResolvedMedia | None:
        aweme_id = self.extract_aweme_id(url)
        if aweme_id is None:
            return None

        enriched_cookie_header = self.enrich_cookie_header(
            url=url,
            cookie_header=cookie_header,
            user_agent=user_agent,
        )
        cookies = self._parse_cookie_header(enriched_cookie_header)
        verify_fp = cookies.get("s_v_web_id") or self._generate_verify_fp()
        cookies["s_v_web_id"] = verify_fp

        detail_payload = self._request_detail_payload(
            aweme_id=aweme_id,
            cookies=cookies,
            user_agent=user_agent,
        )
        if detail_payload is None:
            return None

        detail = detail_payload.get("aweme_detail")
        if not isinstance(detail, dict):
            return None

        direct_url = self._pick_best_play_url(detail)
        if not direct_url:
            return None

        title = self._normalize_title(detail.get("desc"), aweme_id=aweme_id)
        uploader = self._extract_author_name(detail.get("author"))
        duration = self._normalize_duration(detail.get("duration"))
        thumbnail = self._extract_thumbnail(detail)

        media_headers = {
            "User-Agent": user_agent,
            "Referer": f"https://www.douyin.com/video/{aweme_id}",
        }
        serialized_cookie = self._serialize_cookie_header(cookies)
        if serialized_cookie:
            media_headers["Cookie"] = serialized_cookie

        return DouyinResolvedMedia(
            title=title,
            uploader=uploader,
            duration=duration,
            thumbnail=thumbnail,
            direct_url=direct_url,
            direct_ext="mp4",
            headers=media_headers,
        )

    def extract_aweme_id(self, url: str) -> str | None:
        for pattern in (self._video_pattern, self._note_pattern, self._share_pattern):
            match = pattern.search(url)
            if match is not None:
                return match.group("item_id")

        parsed = urlparse(url)
        query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
        for key in ("modal_id", "aweme_id", "item_id", "vid"):
            value = query.get(key)
            if isinstance(value, str) and value.isdigit():
                return value
        return None

    def _request_detail_payload(
        self,
        *,
        aweme_id: str,
        cookies: dict[str, str],
        user_agent: str,
    ) -> dict[str, object] | None:
        query = self._build_detail_query(aweme_id=aweme_id, cookies=cookies)
        headers = {
            "User-Agent": user_agent,
            "Referer": f"https://www.douyin.com/video/{aweme_id}",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        serialized_cookie = self._serialize_cookie_header(cookies)
        if serialized_cookie:
            headers["Cookie"] = serialized_cookie

        body = self._send_request(
            url=f"{DOUYIN_DETAIL_API_URL}?{urlencode(query)}",
            method="GET",
            headers=headers,
            body=None,
        )
        if not body:
            return None

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            logger.info("douyin detail api returned non-json body")
            return None

        if not isinstance(payload, dict):
            return None
        return payload

    def _build_detail_query(self, *, aweme_id: str, cookies: dict[str, str]) -> dict[str, str]:
        verify_fp = cookies.get("s_v_web_id") or self._generate_verify_fp()
        return {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "pc_client_type": "1",
            "version_code": "290100",
            "version_name": "29.1.0",
            "cookie_enabled": "true",
            "screen_width": "1920",
            "screen_height": "1080",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "146.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "engine_version": "146.0.0.0",
            "os_name": "Windows",
            "os_version": "10",
            "cpu_core_num": "8",
            "device_memory": "8",
            "platform": "PC",
            "downlink": "10",
            "effective_type": "4g",
            "from_user_page": "1",
            "locate_query": "false",
            "need_time_list": "1",
            "pc_libra_divert": "Windows",
            "publish_video_strategy_type": "2",
            "round_trip_time": "0",
            "show_live_replay_strategy": "1",
            "time_list_query": "0",
            "whale_cut_token": "",
            "update_version_code": "170400",
            "verifyFp": verify_fp,
            "fp": verify_fp,
            "aweme_id": aweme_id,
            "msToken": cookies.get("msToken", ""),
        }

    def _request_ttwid(self, *, user_agent: str) -> str | None:
        cookie_jar = CookieJar()
        headers = {
            "User-Agent": user_agent,
            "Content-Type": "application/json",
            "Referer": "https://www.douyin.com/",
        }
        self._send_request(
            url=DOUYIN_TTWID_REGISTER_URL,
            method="POST",
            headers=headers,
            body=DOUYIN_TTWID_REGISTER_BODY.encode("utf-8"),
            cookie_jar=cookie_jar,
        )
        return self._read_cookie_value(cookie_jar, "ttwid")

    def _request_ms_token(self, *, user_agent: str) -> str | None:
        cookie_jar = CookieJar()
        headers = {
            "User-Agent": user_agent,
            "Content-Type": "application/json",
            "Referer": "https://www.douyin.com/",
        }
        payload = {
            "magic": 538969122,
            "version": 1,
            "dataType": 8,
            "strData": DOUYIN_MS_TOKEN_STR_DATA,
            "tspFromClient": int(time.time() * 1000),
        }
        self._send_request(
            url=DOUYIN_MS_TOKEN_REPORT_URL,
            method="POST",
            headers=headers,
            body=json.dumps(payload).encode("utf-8"),
            cookie_jar=cookie_jar,
        )
        return self._read_cookie_value(cookie_jar, "msToken")

    def _request_ac_nonce(self, *, url: str, user_agent: str) -> str | None:
        cookie_jar = CookieJar()
        headers = {
            "User-Agent": user_agent,
            "Referer": "https://www.douyin.com/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        self._send_request(
            url=url,
            method="GET",
            headers=headers,
            body=None,
            cookie_jar=cookie_jar,
        )
        return self._read_cookie_value(cookie_jar, "__ac_nonce")

    def _send_request(
        self,
        *,
        url: str,
        method: str,
        headers: dict[str, str],
        body: bytes | None,
        cookie_jar: CookieJar | None = None,
    ) -> str | None:
        opener = build_opener(HTTPCookieProcessor(cookie_jar or CookieJar()))
        request = Request(url=url, data=body, headers=headers, method=method)
        try:
            with opener.open(request, timeout=20) as response:
                return response.read().decode("utf-8", "ignore")
        except HTTPError as exc:
            logger.info("douyin request failed: status=%s url=%s", exc.code, url)
            try:
                return exc.read().decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                return None
        except (URLError, TimeoutError) as exc:
            logger.info("douyin request failed: url=%s error=%s", url, exc)
            return None

    def _pick_best_play_url(self, detail: dict[str, object]) -> str | None:
        video = detail.get("video")
        if not isinstance(video, dict):
            return None

        candidates: list[tuple[int, str]] = []

        for item in video.get("bit_rate") or []:
            if not isinstance(item, dict):
                continue
            play_addr = item.get("play_addr")
            url = self._pick_first_url(play_addr)
            if not url:
                continue
            quality = int(item.get("bit_rate") or item.get("bitrate") or 0)
            candidates.append((quality, self._normalize_play_url(url)))

        for key in ("play_addr_h264", "play_addr", "download_addr"):
            url = self._pick_first_url(video.get(key))
            if not url:
                continue
            candidates.append((0, self._normalize_play_url(url)))

        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _pick_first_url(self, node: object) -> str | None:
        if not isinstance(node, dict):
            return None
        url_list = node.get("url_list")
        if not isinstance(url_list, list):
            return None
        for item in url_list:
            if isinstance(item, str) and item:
                return item
        return None

    def _extract_thumbnail(self, detail: dict[str, object]) -> str | None:
        video = detail.get("video")
        if not isinstance(video, dict):
            return None
        for key in ("origin_cover", "dynamic_cover", "cover"):
            url = self._pick_first_url(video.get(key))
            if url:
                return url
        return None

    def _extract_author_name(self, author: object) -> str | None:
        if not isinstance(author, dict):
            return None
        for key in ("nickname", "unique_id", "short_id"):
            value = author.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _normalize_duration(self, value: object) -> int | None:
        if isinstance(value, (int, float)):
            duration = int(value)
            return duration // 1000 if duration >= 1000 else duration
        return None

    def _normalize_title(self, value: object, *, aweme_id: str) -> str:
        if isinstance(value, str):
            normalized = " ".join(value.split())
            if normalized:
                return normalized[:160]
        return f"douyin-{aweme_id}"

    def _normalize_play_url(self, url: str) -> str:
        normalized = url.replace("/playwm/", "/play/")
        normalized = normalized.replace("watermark=1", "watermark=0")
        normalized = normalized.replace("ratio=default", "ratio=1080p")
        return normalized

    def _parse_cookie_header(self, cookie_header: str | None) -> dict[str, str]:
        if not isinstance(cookie_header, str) or not cookie_header.strip():
            return {}

        cookies: dict[str, str] = {}
        for part in cookie_header.split(";"):
            item = part.strip()
            if not item or "=" not in item:
                continue
            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or not value:
                continue
            cookies[key] = value
        return cookies

    def _serialize_cookie_header(self, cookies: dict[str, str]) -> str | None:
        parts = [f"{key}={value}" for key, value in cookies.items() if key and value]
        if not parts:
            return None
        return "; ".join(parts)

    def _read_cookie_value(self, cookie_jar: CookieJar, name: str) -> str | None:
        for cookie in cookie_jar:
            if cookie.name == name and cookie.value:
                return cookie.value
        return None

    def _generate_verify_fp(self) -> str:
        charset = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        milliseconds = int(round(time.time() * 1000))
        base36 = ""
        while milliseconds > 0:
            remainder = milliseconds % 36
            if remainder < 10:
                base36 = str(remainder) + base36
            else:
                base36 = chr(ord("a") + remainder - 10) + base36
            milliseconds //= 36

        segments = [""] * 36
        segments[8] = segments[13] = segments[18] = segments[23] = "_"
        segments[14] = "4"
        for index in range(36):
            if segments[index]:
                continue
            value = random.randint(0, len(charset) - 1)
            if index == 19:
                value = (3 & value) | 8
            segments[index] = charset[value]
        return f"verify_{base36}_{''.join(segments)}"


douyin_service = DouyinService()
