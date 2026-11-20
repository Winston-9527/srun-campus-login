#!/usr/bin/env python3
"""Log in to an SRun campus network from the command line."""
import argparse
import base64
import errno
import getpass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


DEFAULT_PORTAL_URL = "https://net.szu.edu.cn"
DEFAULT_AC_ID = "12"
MAINTENANCE_LOCK_ADDRESS = b"\0srun-campus-login"


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_jsonp(opener, portal_url, path, params):
    params = dict(params, callback="jsonp", _=str(int(time.time() * 1000)))
    url = portal_url + path + "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(request, timeout=10) as response:
        body = response.read().decode()

    match = re.fullmatch(r"\s*jsonp\((.*)\)\s*;?\s*", body, re.S)
    if not match:
        raise ValueError("校园网网关未返回预期的 JSONP 响应")
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise ValueError("校园网网关返回了无效的响应格式")
    return data


def _s(a, b):
    c = len(a)
    v = [0] * ((c + 3) // 4)
    for i in range(c):
        v[i >> 2] |= ord(a[i]) << ((i & 3) * 8)
    if b:
        v.append(c)
    return v


def _l(a):
    return "".join(chr(x & 0xff) + chr(x >> 8 & 0xff) + chr(x >> 16 & 0xff) + chr(x >> 24 & 0xff) for x in a)


def xencode(msg, key):
    if not msg:
        return ""
    v = _s(msg, True)
    k = _s(key, False)
    k += [0] * (4 - len(k)) if len(k) < 4 else []
    n = len(v) - 1
    z = v[n]
    c = 0x9E3779B9
    q = math.floor(6 + 52 / (n + 1))
    d = 0
    M = 0xFFFFFFFF
    while q > 0:
        d = (d + c) & M
        e = d >> 2 & 3
        p = 0
        while p < n:
            y = v[p + 1]
            m = (z >> 5 ^ (y << 2 & M)) + ((y >> 3 ^ (z << 4 & M)) ^ (d ^ y)) + (k[(p & 3) ^ e] ^ z)
            v[p] = (v[p] + m) & M
            z = v[p]
            p += 1
        y = v[0]
        m = (z >> 5 ^ (y << 2 & M)) + ((y >> 3 ^ (z << 4 & M)) ^ (d ^ y)) + (k[(p & 3) ^ e] ^ z)
        v[n] = (v[n] + m) & M
        z = v[n]
        q -= 1
    return _l(v)


STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


CUS = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"


def b64(s):
    return base64.b64encode(s.encode("latin-1")).decode().translate(str.maketrans(STD, CUS))


def read_credentials(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2 or not lines[0].strip() or not lines[1]:
        raise ValueError(f"{path.name} 需要在前两行分别填写账号和密码")
    return lines[0].strip(), lines[1]


def credentials(reconnect=False):
    if reconnect:
        directory = os.environ.get("CREDENTIALS_DIRECTORY")
        if not directory:
            raise ValueError("自动重连需要通过 systemd 提供 srun_cred 凭据")
        return read_credentials(Path(directory) / "srun_cred")

    username = os.environ.get("SRUN_USER")
    password = os.environ.get("SRUN_PASS")
    if username and password:
        return username, password

    credential_file = Path.home() / ".srun_cred"
    if credential_file.exists():
        return read_credentials(credential_file)

    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    if not username or not password:
        raise ValueError("账号和密码不能为空")
    return username, password


def parse_args(argv):
    parser = argparse.ArgumentParser(description="登录 SRun 深澜校园网")
    command = parser.add_mutually_exclusive_group()
    command.add_argument("--status", action="store_true", help="查询校园网认证状态")
    command.add_argument("--logout", action="store_true", help="退出当前校园网会话")
    command.add_argument("--reconnect", action="store_true", help="使用 systemd 凭据检查并尝试重连一次")
    parser.add_argument("--url", default=os.environ.get("SRUN_URL") or DEFAULT_PORTAL_URL,
                        help=f"校园网 HTTPS 网关地址，优先读取 SRUN_URL，默认 {DEFAULT_PORTAL_URL}")
    parser.add_argument("--ac-id", default=os.environ.get("SRUN_AC_ID") or DEFAULT_AC_ID,
                        help=f"认证入口编号，优先读取 SRUN_AC_ID，默认 {DEFAULT_AC_ID}")
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument("--ca-file", help="额外信任的 CA 证书文件（PEM 格式）")
    tls.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验")
    args = parser.parse_args(argv)
    if args.reconnect and args.insecure:
        parser.error("自动重连需要验证网关的 HTTPS 证书")

    try:
        portal = urlsplit(args.url)
        if portal.port == 0:
            raise ValueError
    except ValueError:
        parser.error("网关地址格式无效")
    if (portal.scheme != "https" or not portal.hostname or portal.username is not None
            or portal.path not in ("", "/") or portal.query or portal.fragment):
        parser.error("网关地址应为 HTTPS 根地址，例如 https://portal.example.edu")
    args.url = args.url.rstrip("/")
    if not args.status and (not args.ac_id or not re.fullmatch(r"[0-9]+", args.ac_id)):
        parser.error("登录或退出需要通过 --ac-id 或 SRUN_AC_ID 设置数字 ac_id")
    return args


def run(args):
    context = ssl.create_default_context()
    if args.ca_file:
        context.load_verify_locations(cafile=args.ca_file)
    if args.insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    handlers = [ProxyHandler({}), HTTPSHandler(context=context)]
    if args.reconnect:
        handlers.append(RejectRedirects())
    opener = build_opener(*handlers)

    def get(path, params):
        return request_jsonp(opener, args.url, path, params)

    info = get("/cgi-bin/rad_user_info", {})
    ip = info.get("online_ip") or info.get("client_ip")
    if args.status:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    if args.reconnect:
        if info.get("error") == "ok":
            return 0
        if info.get("error") != "not_online_error":
            raise ValueError("网关未确认当前会话离线，跳过重新认证")
    if not ip:
        raise ValueError("校园网网关未返回客户端 IP")
    if args.logout:
        result = get("/cgi-bin/srun_portal", {
            "action": "logout",
            "username": info.get("user_name", ""),
            "ip": ip,
            "ac_id": args.ac_id,
        })
        print(result.get("error"), result.get("error_msg") or "")
        return 0 if result.get("error") == "ok" else 1
    if info.get("error") == "ok":
        print("Already online:", info.get("user_name"), ip)
        return 0

    user, password = credentials(args.reconnect)
    token = get("/cgi-bin/get_challenge", {"username": user, "ip": ip}).get("challenge")
    if not isinstance(token, str) or not token:
        raise ValueError("校园网网关未返回登录挑战值")

    hmd5 = hmac.new(token.encode(), password.encode(), hashlib.md5).hexdigest()
    encoded_info = "{SRBX1}" + b64(xencode(json.dumps(
        {"username": user, "password": password, "ip": ip, "acid": args.ac_id, "enc_ver": "srun_bx1"},
        separators=(",", ":"),
    ), token))
    n, typ = "200", "1"
    checksum = hashlib.sha1("".join(
        token + field for field in (user, hmd5, args.ac_id, ip, n, typ, encoded_info)
    ).encode()).hexdigest()
    result = get("/cgi-bin/srun_portal", {
        "action": "login",
        "username": user,
        "password": "{MD5}" + hmd5,
        "os": "Linux",
        "name": "Linux",
        "double_stack": "0",
        "chksum": checksum,
        "info": encoded_info,
        "ac_id": args.ac_id,
        "ip": ip,
        "n": n,
        "type": typ,
    })
    if args.reconnect:
        if result.get("error") != "ok":
            raise ValueError("网关拒绝重新认证，请检查账号、密码和认证入口")
        print("校园网已重新认证")
        return 0
    message = result.get("suc_msg") or result.get("error_msg") or result.get("ploy_msg") or ""
    print("result:", result.get("error"), message)
    return 0 if result.get("error") == "ok" else 1


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.status or sys.platform != "linux":
            return run(args)

        # A Linux abstract socket coordinates distinct users without a shared lock file.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as lock:
            try:
                lock.bind(MAINTENANCE_LOCK_ADDRESS)
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    raise
                print("已有校园网维护任务在运行，本次跳过", file=sys.stderr)
                return 0 if args.reconnect else 1
            return run(args)
    except HTTPError as error:
        print(f"校园网网关返回 HTTP {error.code}", file=sys.stderr)
    except (URLError, OSError, ValueError, EOFError) as error:
        print(f"错误：{error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
