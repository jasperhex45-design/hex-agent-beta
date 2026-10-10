"""Small HTTPS fetcher for explicit public sources only.

DNS addresses are validated AND pinned to the connection. Redirects are checked
again; no cookies, login, JavaScript, external commands, or credential headers.
"""
from __future__ import annotations
import http.client
import ipaddress
import re
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
from .io import AgentError

USER_AGENT = "LIUAestheticAgent/0.2 (personal research; bounded requests)"

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(text: str) -> str:
    parser = TextExtractor()
    parser.feed(text)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def check_url(url: str, allowed_hosts: set[str]):
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.port not in (None, 443):
        raise AgentError("外部资料只允许无凭据的 HTTPS:443 地址")
    host = u.hostname.lower()
    if host not in allowed_hosts:
        raise AgentError("来源不在本次允许列表：" + host)
    return u


def public_addresses(host: str) -> list[str]:
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(x).is_global for x in addresses):
        raise AgentError("拒绝访问内网、回环、保留地址或混合 DNS 结果")
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str):
        super().__init__(host, timeout=20, context=ssl.create_default_context())
        self.ip = ip
    def connect(self):
        raw = socket.create_connection((self.ip, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class PublicFetcher:
    def __init__(self, allowed_hosts: set[str], *, consent: bool):
        if not consent:
            raise AgentError("联网抓取需要 --allow-network")
        self.allowed_hosts = allowed_hosts
        self.robot_cache: dict[str, RobotFileParser | None] = {}

    def _raw(self, url: str, max_bytes: int, redirects: int = 0, robots_required: bool = False) -> dict:
        u = check_url(url, self.allowed_hosts)
        addresses = public_addresses(u.hostname)
        conn = PinnedHTTPS(u.hostname, addresses[0])
        try:
            path = u.path or "/"
            if u.query:
                path += "?" + u.query
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                if redirects >= 3:
                    raise AgentError("重定向超过3次")
                new = urljoin(url, response.getheader("Location", ""))
                check_url(new, self.allowed_hosts)
                if robots_required:
                    self._check_robots(new)
                return self._raw(new, max_bytes, redirects+1, robots_required)
            if response.status != 200:
                return {"status": response.status, "url": url, "body": b"", "mime": ""}
            length = response.getheader("Content-Length", "")
            if length.isdigit() and int(length) > max_bytes:
                raise AgentError("来源内容超过下载大小限制")
            data = response.read(max_bytes+1)
            if len(data) > max_bytes:
                raise AgentError("来源内容超过下载大小限制")
            return {"status": 200, "url": url, "body": data, "mime": response.getheader("Content-Type", "").split(";")[0]}
        except (OSError, http.client.HTTPException) as exc:
            raise AgentError("公共来源连接失败；未绕过限制或自动无限重试") from exc
        finally:
            conn.close()

    def _check_robots(self, url: str) -> None:
        u = check_url(url, self.allowed_hosts)
        base = f"https://{u.hostname}"
        if base not in self.robot_cache:
            robots = self._raw(base + "/robots.txt", 256_000)
            if robots["status"] == 404:
                self.robot_cache[base] = None
            elif robots["status"] == 200:
                parser = RobotFileParser()
                parser.parse(robots["body"].decode("utf-8", "replace").splitlines())
                self.robot_cache[base] = parser
            else:
                raise AgentError("robots.txt 无法确认；停止自动抓取此站点")
        parser = self.robot_cache[base]
        if parser and not parser.can_fetch(USER_AGENT, url):
            raise AgentError("站点 robots.txt 不允许此自动抓取路径")

    def get(self, url: str, max_bytes: int = 2_000_000, respect_robots: bool = True) -> dict:
        check_url(url, self.allowed_hosts)
        if respect_robots:
            self._check_robots(url)
        result = self._raw(url, max_bytes, robots_required=respect_robots)
        if result["status"] != 200:
            raise AgentError(f"来源 HTTP {result['status']}，未取得内容")
        return result
