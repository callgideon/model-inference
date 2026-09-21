"""media fetch: SSRF-hardened, once, then inline to vLLM.

See the gateway module docstring and README "Security" for the policy. F1 lifted
this unchanged; behavior changes (including the documented DNS-rebinding window)
belong to track M.
"""
import asyncio
import base64
import ipaddress
import os
import socket
import subprocess
import tempfile

import httpx


DENIED_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    # Tunnels and translations whose payload is an address of the caller's choosing:
    # the outer address looks global and the packet arrives at the embedded v4.
    "2002::/16",            # 6to4
    "64:ff9b::/96",         # NAT64, well-known prefix
    "64:ff9b:1::/48",       # NAT64, local use
    "2001::/32",            # Teredo
    "192.88.99.0/24",       # 6to4 relay anycast
    # Special-purpose space that is never a video host.
    "::/96",                # IPv4-compatible (::127.0.0.1, ::169.254.169.254)
    "2001:20::/28",         # ORCHIDv2
    "3fff::/20",            # documentation (RFC 9637)
    "198.18.0.0/15",        # benchmarking (RFC 2544)
    "192.0.0.0/24",         # IETF protocol assignments
))


def address_allowed(ip):
    """False for anything not a routable public address: loopback, private,
    link-local (169.254.0.0/16 and fe80::/10, so the EC2 metadata endpoint),
    CGNAT, multicast, reserved, unspecified — v4 and v6, and v4-mapped v6.

    Track M tightened this (M1 review B1) so the answer does not depend on the
    interpreter's own tables: deprecated site-local `fec0::/10` reports
    `is_global` True, and upstream CPython 3.12.0-3.12.3 does not carry the
    6to4/NAT64/Teredo ranges this host's patched build happens to have. The
    site-local check and `DENIED_NETWORKS` are therefore explicit. Every change
    is in the refusing direction, so nothing this used to allow has stopped
    working.
    """
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    if a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified:
        return False
    if a.version == 6 and a.is_site_local:
        return False
    if any(a in net for net in DENIED_NETWORKS if net.version == a.version):
        return False
    return a.is_global  # also drops 100.64.0.0/10 and the v6 special-purpose ranges


class Media:
    """One per app. The hooks tests swap (resolve_public, fetch_client,
    probe_seconds) are looked up on self, so an instance attribute replaces them
    for that app only."""

    def __init__(self, rt):
        self.rt = rt

    async def resolve_public(self, host):
        """All A/AAAA records for host, or an error class. Every record must pass:
        one private answer among many is a rebinding attempt, not a fallback."""
        if not host:
            return None, "dns"
        try:
            infos = await asyncio.get_event_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except Exception:
            return None, "dns"
        addrs = [i[4][0] for i in infos]
        if not addrs:
            return None, "dns"
        if not all(address_allowed(a) for a in addrs):
            return None, "blocked-address"
        return addrs, None

    def fetch_client(self):
        """Separate from the upstream client: no base_url, no redirects, short connect.
        Tests swap it."""
        return httpx.AsyncClient(timeout=httpx.Timeout(self.rt.settings.fetch_timeout_s, connect=5),
                                 follow_redirects=False)

    def video_mime(self, content_type, url):
        """Allowlisted mime for the response, or None. Falls back to the URL's
        extension only when the server declines to say (octet-stream/empty)."""
        s = self.rt.settings
        ct = (content_type or "").split(";")[0].strip().lower()
        if ct in s.allowed_video_mime:
            return ct
        if ct in ("", "application/octet-stream", "binary/octet-stream"):
            ext = os.path.splitext(httpx.URL(url).path)[1].lower()
            m = s.ext_mime.get(ext)
            return m if m in s.allowed_video_mime else None
        return None

    def probe_seconds(self, path):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)

    def budget_kwargs(self, seconds):
        s = self.rt.settings
        frames = int(min(s.max_frames, max(s.min_frames, round(seconds * s.fps))))
        frames += frames % 2
        return {"fps": s.fps, "min_frames": s.min_frames, "max_frames": s.max_frames,
                "size": {"shortest_edge": 4096, "longest_edge": frames * s.px_per_frame}}

    async def fetch_video(self, url, fh):
        """Stream a caller-supplied URL into fh. Returns (mime, error class).

        Redirects are followed by hand so every hop is re-validated; the size cap is
        enforced on the stream, not after buffering the body.
        ponytail: validation and connection are separate steps, so a DNS rebind
        between them is still possible; pin the resolved IP (Host + sni_hostname on
        an IP URL) if that threat becomes real."""
        s = self.rt.settings
        cap = int(s.max_video_mb * 2**20)
        async with self.fetch_client() as c:
            for _ in range(s.max_redirects + 1):
                u = httpx.URL(url)
                if u.scheme not in ("http", "https"):
                    return None, "unsupported-scheme"
                _, err = await self.resolve_public(u.host)
                if err:
                    return None, err
                async with c.stream("GET", u) as r:
                    if r.is_redirect:  # httpx: 3xx *with* a Location header
                        url = str(u.join(r.headers["location"]))
                        continue
                    if r.status_code != 200:
                        return None, f"http-{r.status_code}"
                    mime = self.video_mime(r.headers.get("content-type"), str(u))
                    if mime is None:
                        return None, "unsupported-type"
                    cl = r.headers.get("content-length", "")
                    if cl.isdigit() and int(cl) > cap:
                        return None, "too-large"
                    n = 0
                    async for chunk in r.aiter_bytes():
                        n += len(chunk)
                        if n > cap:
                            return None, "too-large"   # abort mid-stream; the body is never fully read
                        fh.write(chunk)
                    return mime, None
        return None, "too-many-redirects"

    async def prepare_video(self, part):
        """Duration of the request's video, plus the `data:` URL to send to vLLM in
        place of the caller's URL, so the engine never fetches anything itself.
        Returns (seconds, data_url, client-safe error)."""
        s = self.rt.settings
        url = part.get("video_url", {}).get("url", "") if isinstance(part.get("video_url"), dict) else part.get("video_url", "")
        if not isinstance(url, str) or not url:
            return None, None, "video_url must be an http(s) URL or a data: URL"
        data_url = url if url.startswith("data:") else None
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            try:
                if url.startswith("data:"):
                    try:
                        data = base64.b64decode(url.split(",", 1)[1], validate=False)
                    except Exception:
                        return None, None, "could not read video (bad data: URL)"
                    if len(data) > s.max_video_mb * 2**20:
                        return None, None, f"video larger than {s.max_video_mb} MB"
                    f.write(data)
                    del data
                elif url.startswith(("http://", "https://")):
                    try:
                        mime, why = await asyncio.wait_for(self.fetch_video(url, f), s.fetch_timeout_s)
                    except asyncio.TimeoutError:
                        mime, why = None, "timeout"
                    except httpx.TimeoutException:
                        mime, why = None, "timeout"
                    except Exception as e:  # never echoed: the blind-SSRF oracle
                        print(f"gateway: video fetch failed ({type(e).__name__}: {e})", flush=True)
                        mime, why = None, "fetch-failed"
                    if why:
                        if why != "fetch-failed":
                            print(f"gateway: video fetch rejected ({why})", flush=True)
                        return None, None, f"could not fetch video ({why})"
                else:
                    return None, None, "video_url must be an http(s) URL or a data: URL"
                f.flush()
                try:
                    secs = await asyncio.get_event_loop().run_in_executor(None, self.probe_seconds, f.name)
                except Exception as e:
                    print(f"gateway: ffprobe failed ({type(e).__name__}: {e})", flush=True)
                    return None, None, "could not read video (not a decodable video file)"
                if data_url is None:
                    with open(f.name, "rb") as g:  # raw bytes are gone by now; only the base64 copy lives on
                        data_url = f"data:{mime};base64,{base64.b64encode(g.read()).decode()}"
            finally:
                os.unlink(f.name)
        if secs > s.max_video_seconds:
            return None, None, f"video is {secs:.0f}s; max is {s.max_video_seconds:.0f}s"
        return secs, data_url, None
