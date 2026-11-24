#!/usr/bin/env python3
import os
import json
import time
import logging
import threading
import urllib.request
import urllib.parse
import urllib.error
import re
import ctypes
import hmac
import pwd
import uuid
import ipaddress
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LOGS = os.path.join(ROOT, "logs")
CONFIG_FILE = os.path.join(DATA, "config.json")
MONITOR_STATE_FILE = os.path.join(DATA, "monitor-state.json")
MODE_MARKER = "/usr/data/opencreator/tunneled/tunneled.sh"
LANG_DIR = os.path.join(ROOT, "web", "languages")

MOONRAKER = os.environ.get("OC_MOONRAKER_URL", "http://127.0.0.1:7125").rstrip("/")
WEB_HOST = "0.0.0.0"
WEB_PORT = 8767

SESSION_TTL = 30 * 60
_sessions = {}
_sessions_lock = threading.RLock()

LIBCRYPT = ctypes.CDLL("libcrypt.so.1")
LIBCRYPT.crypt.argtypes = (ctypes.c_char_p, ctypes.c_char_p)
LIBCRYPT.crypt.restype = ctypes.c_char_p


def discover_allowed_networks():
    networks = [ipaddress.ip_network("127.0.0.0/8")]
    try:
        output = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=3
        )
        for line in output.splitlines():
            match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
            if match:
                network = ipaddress.ip_interface(match.group(1)).network
                if network not in networks:
                    networks.append(network)
    except Exception:
        logger.exception("Could not discover local IPv4 networks")
    return networks


def verify_credentials(username, password):
    if (not username or len(username) > 64 or
            not password or len(password) > 1024):
        return None

    try:
        account = pwd.getpwnam(username)
    except KeyError:
        return None

    if account.pw_uid < 0 or account.pw_shell in (
        "/sbin/nologin", "/usr/sbin/nologin", "/bin/false"
    ):
        return None

    stored_hash = ""
    try:
        with open("/etc/shadow", "r", encoding="utf-8") as f:
            for line in f:
                fields = line.rstrip("\n").split(":")
                if fields and fields[0] == username and len(fields) > 1:
                    stored_hash = fields[1]
                    break
    except OSError:
        pass

    if not stored_hash:
        stored_hash = account.pw_passwd or ""

    if not stored_hash or stored_hash.startswith(("!", "*", "x")):
        return None

    try:
        result = LIBCRYPT.crypt(
            password.encode("utf-8"), stored_hash.encode("utf-8")
        )
        if result and hmac.compare_digest(result.decode("utf-8"), stored_hash):
            return account
    except Exception:
        logger.exception("SSH credential verification failed")
    return None


def get_session(cookie_header):
    for item in (cookie_header or "").split(";"):
        key, sep, value = item.strip().partition("=")
        if sep and key == "oc_telegram_notify":
            token = value
            break
    else:
        return None

    now = time.time()
    with _sessions_lock:
        session = _sessions.get(token)
        if not session:
            return None
        if now - session["last"] > SESSION_TTL:
            _sessions.pop(token, None)
            return None
        session["last"] = now
        return session["username"]

os.makedirs(DATA, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)

logger = logging.getLogger("telegram_notify")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(
    os.path.join(LOGS, "telegram-notify.log"),
    maxBytes=1024 * 1024,
    backupCount=3
)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(handler)

config_lock = threading.RLock()
config = {
    "bot_token": "",
    "chat_ids": "",
    "name": "Creator 5",
    "silent": False,
    "snapshot_url": "http://127.0.0.1:8080/?action=snapshot",
    "send_photos": True,
    "enable_service": True,
    "progress_percent": 0,
    "poll_interval": 3,
    "language": "en"
}

state_lock = threading.Lock()
printer_state = {
    "connection": "Starting",
    "state": "unknown",
    "filename": "",
    "progress": 0,
    "message": "",
    "last_event": "None"
}


def load_config():
    global config
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        with config_lock:
            config.update(saved)
    except FileNotFoundError:
        save_config()
    except Exception:
        logger.exception("Could not load configuration")


def save_config():
    with config_lock:
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG_FILE)


def get_config():
    with config_lock:
        return dict(config)


def service_runtime_state(cfg=None):
    cfg = get_config() if cfg is None else cfg
    if not bool(cfg.get("enable_service", True)):
        return "disabled"
    try:
        return "tunneled" if (os.stat(MODE_MARKER).st_mode & 0o111) else "soc"
    except OSError:
        return "unknown"


def moonraker_query():
    url = MOONRAKER + (
        "/printer/objects/query?"
        "print_stats&display_status&virtual_sdcard&extruder&heater_bed"
    )
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["result"]["status"]


def moonraker_server_info():
    req = urllib.request.Request(MOONRAKER + "/server/info")
    with urllib.request.urlopen(req, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["result"]


def current_boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        logger.exception("Could not read system boot ID")
        return ""


def local_ip_address():
    try:
        output = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=3
        )
        for line in output.splitlines():
            fields = line.split()
            if "inet" in fields:
                address = fields[fields.index("inet") + 1].split("/", 1)[0]
                if not address.startswith("127."):
                    return address
    except Exception:
        logger.exception("Could not detect local IPv4 address")
    return "Unavailable"


def load_monitor_boot_id():
    try:
        with open(MONITOR_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        value = state.get("boot_id", "")
        return value if isinstance(value, str) else ""
    except FileNotFoundError:
        return ""
    except Exception:
        logger.exception("Could not read monitor state")
        return ""


def save_monitor_boot_id(boot_id):
    if not boot_id:
        return

    tmp = MONITOR_STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"boot_id": boot_id}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, MONITOR_STATE_FILE)
    except Exception:
        logger.exception("Could not save monitor state")
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass



def telegram_chats(cfg):
    result = []
    for item in cfg.get("chat_ids", "").replace("\n", ",").split(","):
        item = item.strip()
        if not item:
            continue
        chat_id, separator, topic = item.rpartition(":")
        if separator and topic.isdigit():
            result.append((chat_id, int(topic)))
        else:
            result.append((item, None))
    return result


def telegram_request(method, fields, photo=None):
    cfg = get_config()
    token = cfg.get("bot_token", "").strip()
    if not token:
        raise RuntimeError("Telegram bot token is not configured")

    url = "https://api.telegram.org/bot%s/%s" % (token, method)

    if photo is None:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(url, data=body)
    else:
        boundary = "----OpenCreatorTelegramBoundary"
        chunks = []

        for key, value in fields.items():
            chunks.append(
                ("--" + boundary + "\r\n"
                 'Content-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                 % (key, value)).encode("utf-8")
            )

        chunks.append(
            ("--" + boundary + "\r\n"
             'Content-Disposition: form-data; name="photo"; filename="snapshot.jpg"\r\n'
             "Content-Type: image/jpeg\r\n\r\n").encode("utf-8")
        )
        chunks.append(photo)
        chunks.append(("\r\n--" + boundary + "--\r\n").encode("utf-8"))

        request = urllib.request.Request(
            url,
            data=b"".join(chunks),
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary}
        )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return result



def load_language_pack(code):
    if not isinstance(code, str) or not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", code):
        code = "en"

    path = os.path.join(LANG_DIR, code + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            pack = json.load(f)

        if (pack.get("version") != 2 or
                not isinstance(pack.get("interface"), dict) or
                not isinstance(pack.get("notifications"), dict)):
            raise ValueError("Invalid language pack schema")

        return pack

    except Exception:
        if code != "en":
            return load_language_pack("en")
        raise


def notification_text(key, **values):
    cfg = get_config()
    try:
        pack = load_language_pack(cfg.get("language", "en"))
        template = pack["notifications"].get(key)
        if not isinstance(template, str):
            raise KeyError(key)
        return template.format(**values)
    except Exception:
        pack = load_language_pack("en")
        return pack["notifications"][key].format(**values)


def send_notification(message, photo=False):
    cfg = get_config()
    runtime_state = service_runtime_state(cfg)
    if runtime_state != "soc":
        logger.info("Notification suppressed; service state: %s", runtime_state)
        return
    if not cfg.get("bot_token") or not cfg.get("chat_ids"):
        logger.warning("Notification skipped: Telegram is not configured")
        return

    chats = telegram_chats(cfg)
    if not chats:
        logger.warning("Notification skipped: no valid chat IDs")
        return

    image_data = None
    if photo and cfg.get("send_photos") and cfg.get("snapshot_url"):
        try:
            req = urllib.request.Request(cfg["snapshot_url"])
            with urllib.request.urlopen(req, timeout=8) as response:
                content_type = response.headers.get("Content-Type", "")
                data = response.read(2 * 1024 * 1024 + 1)
            if content_type.startswith("image/") and len(data) <= 2 * 1024 * 1024:
                image_data = data
            else:
                logger.warning("Snapshot ignored: invalid type or size")
        except Exception:
            logger.exception("Could not retrieve webcam snapshot")

    prefix = cfg.get("name", "").strip()
    if prefix:
        message = prefix + "\n" + message

    for chat_id, topic in chats:
        fields = {
            "chat_id": chat_id,
            "disable_notification": str(bool(cfg.get("silent"))).lower()
        }
        if topic is not None:
            fields["message_thread_id"] = str(topic)

        try:
            if image_data:
                fields["caption"] = message[:1024]
                telegram_request("sendPhoto", fields, image_data)
            else:
                fields["text"] = message[:4096]
                telegram_request("sendMessage", fields)
            logger.info("Telegram notification sent to %s", chat_id)
        except Exception:
            logger.exception("Telegram delivery failed for chat %s", chat_id)


def set_status(**values):
    with state_lock:
        printer_state.update(values)


def monitor():
    previous_state = None
    previous_filename = ""
    last_progress = 0

    previous_moonraker_connected = None
    previous_klippy_state = None

    boot_id = current_boot_id()
    stored_boot_id = load_monitor_boot_id()
    boot_changed = bool(stored_boot_id and boot_id and stored_boot_id != boot_id)

    if not stored_boot_id and boot_id:
        save_monitor_boot_id(boot_id)

    was_dormant = False
    mode_check_failed = False

    while True:
        cfg = get_config()
        interval = max(2, min(30, int(cfg.get("poll_interval", 3))))

        runtime_state = service_runtime_state(cfg)

        if runtime_state == "unknown":
            if not mode_check_failed:
                logger.warning(
                    "Could not detect printer mode; monitoring kept dormant"
                )
            mode_check_failed = True
        else:
            mode_check_failed = False

        if runtime_state != "soc":
            if not was_dormant:
                logger.info("Monitoring suspended; service state: %s", runtime_state)
            was_dormant = True

            previous_state = None
            previous_filename = ""
            last_progress = 0
            previous_moonraker_connected = None
            previous_klippy_state = None

            set_status(
                connection="Disabled" if runtime_state == "disabled" else "Dormant",
                state="unknown",
                filename="",
                progress=0,
                message=""
            )
            time.sleep(interval)
            continue

        if was_dormant:
            logger.info("SOC mode available; monitoring resumed")
            was_dormant = False

        try:
            server_info = moonraker_server_info()
            klippy_state = str(
                server_info.get("klippy_state", "unknown")
            ).lower()
            klippy_connected = bool(server_info.get("klippy_connected"))
            ready = klippy_connected and klippy_state == "ready"

            if ready:
                startup_event = (
                    boot_changed
                    or previous_moonraker_connected is False
                    or (
                        previous_klippy_state is not None
                        and previous_klippy_state != "ready"
                    )
                )

                if startup_event:
                    ip_address = local_ip_address()
                    send_notification(
                        notification_text("printer_started", ip=ip_address)
                    )
                    set_status(last_event="Printer started")
                    logger.info(
                        "Printer ready notification triggered; IP: %s",
                        ip_address
                    )

                    if boot_changed:
                        save_monitor_boot_id(boot_id)
                        boot_changed = False

                status = moonraker_query()
                stats = status.get("print_stats", {})
                display = status.get("display_status", {})
                sdcard = status.get("virtual_sdcard", {})

                current = str(stats.get("state", "unknown")).lower()
                filename = stats.get("filename") or ""
                progress = int(float(
                    display.get("progress") or sdcard.get("progress") or 0
                ) * 100)
                message = stats.get("message") or ""

                set_status(
                    connection="Connected",
                    state=current,
                    filename=filename,
                    progress=progress,
                    message=message
                )

                if previous_state is None:
                    previous_state = current
                    previous_filename = filename
                    last_progress = progress

                elif current == "printing" and (
                    previous_state != "printing" or filename != previous_filename
                ):
                    if previous_state == "paused":
                        send_notification(
                            notification_text("print_resumed", filename=filename),
                            True
                        )
                        set_status(last_event="Resumed")
                    else:
                        send_notification(
                            notification_text("print_started", filename=filename),
                            True
                        )
                        set_status(last_event="Print started")
                    last_progress = progress

                elif current == "paused" and previous_state != "paused":
                    send_notification(
                        notification_text("print_paused", filename=filename),
                        True
                    )
                    set_status(last_event="Paused")

                elif current == "complete" and previous_state != "complete":
                    send_notification(
                        notification_text("print_completed", filename=filename),
                        True
                    )
                    set_status(last_event="Completed")

                elif current == "error" and previous_state != "error":
                    send_notification(
                        notification_text(
                            "print_error",
                            filename=filename,
                            error=str(message)
                        ),
                        True
                    )
                    set_status(last_event="Error")

                elif current == "cancelled" and previous_state != "cancelled":
                    send_notification(
                        notification_text("print_cancelled", filename=filename),
                        True
                    )
                    set_status(last_event="Cancelled")

                threshold = int(cfg.get("progress_percent", 0))
                if (
                    current == "printing"
                    and threshold > 0
                    and progress > 0
                    and progress >= last_progress + threshold
                ):
                    send_notification(
                        notification_text(
                            "print_progress",
                            progress=progress,
                            filename=filename
                        ),
                        True
                    )
                    last_progress = (progress // threshold) * threshold

                previous_state = current
                previous_filename = filename

            else:
                set_status(connection="Connected", state="unknown")

            previous_moonraker_connected = True
            previous_klippy_state = klippy_state

        except Exception as exc:
            set_status(connection="Disconnected")
            previous_moonraker_connected = False
            logger.warning("Moonraker polling failed: %s", exc)

        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    def parse_request(self):
        if not super().parse_request():
            return False
        try:
            address = ipaddress.ip_address(
                self.client_address[0].split("%", 1)[0]
            )
            allowed = any(
                address in network for network in discover_allowed_networks()
            )
        except ValueError:
            allowed = False

        if not allowed:
            self.send_error(403, "LAN access only")
            logger.warning("Blocked non-LAN client: %s", self.client_address[0])
            return False
        return True

    def log_message(self, fmt, *args):
        logger.info("Web %s - %s", self.address_string(), fmt % args)

    def send_json(self, data, status=200, headers=None):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def current_user(self):
        return get_session(self.headers.get("Cookie"))

    def require_auth(self):
        if not self.current_user():
            self.send_json({"error": "SSH login required"}, 401)
            return False
        return True

    def check_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            return urllib.parse.urlparse(origin).netloc.lower() == (
                self.headers.get("Host", "").lower()
            )
        except Exception:
            return False

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 16384:
            raise ValueError("Request too large")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        route = urllib.parse.urlsplit(self.path).path

        if route == "/api/session":
            username = self.current_user()
            if not username:
                return self.send_json({"error": "SSH login required"}, 401)
            return self.send_json({"ok": True, "username": username})

        if route != "/" and not self.require_auth():
            return

        if route == "/":
            path = os.path.join(ROOT, "web", "index.html")
            try:
                with open(path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                self.send_error(404)
            return

        if route == "/api/token":
            cfg = get_config()
            self.send_json({"token": cfg.get("bot_token", "")})
            return

        if self.path == "/api/status":
            with state_lock:
                self.send_json(dict(printer_state))
            return

        if self.path == "/api/config":
            cfg = get_config()
            self.send_json({
                "token_configured": bool(cfg.get("bot_token")),
                "chat_ids": cfg.get("chat_ids", ""),
                "name": cfg.get("name", ""),
                "silent": bool(cfg.get("silent")),
                "snapshot_url": cfg.get("snapshot_url", ""),
                "send_photos": bool(cfg.get("send_photos")),
                "enable_service": bool(cfg.get("enable_service", True)),
                "progress_percent": int(cfg.get("progress_percent", 0)),
                "poll_interval": int(cfg.get("poll_interval", 3)),
            "language": cfg.get("language", "en")
            })
            return

        if self.path == "/api/languages":
            items = []
            os.makedirs(LANG_DIR, exist_ok=True)
            for filename in sorted(os.listdir(LANG_DIR)):
                if not filename.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(LANG_DIR, filename), encoding="utf-8") as f:
                        pack = json.load(f)
                    if (pack.get("version") != 2 or
                            not isinstance(pack.get("interface"), dict) or
                            not isinstance(pack.get("notifications"), dict)):
                        raise ValueError("Unsupported language pack schema")
                    items.append({
                        "code": pack["code"],
                        "name": pack["name"],
                        "native_name": pack["native_name"]
                    })
                except Exception:
                    logger.warning("Ignoring invalid language pack: %s", filename)
            self.send_json({"languages": items})
            return

        if self.path.startswith("/api/languages/") and self.path.endswith(".json"):
            code = self.path[len("/api/languages/"):-5]
            if not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", code):
                self.send_error(400)
                return
            path = os.path.join(LANG_DIR, code + ".json")
            try:
                with open(path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="' + code + '.json"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                self.send_error(404)
            return

        self.send_error(404)

    def do_POST(self):
        route = urllib.parse.urlsplit(self.path).path

        if not self.check_origin():
            return self.send_json({"error": "Invalid request origin"}, 403)

        if route == "/api/login":
            try:
                data = self.read_json()
                username = str(data.get("username", ""))
                password = str(data.get("password", ""))
            except Exception:
                return self.send_json({"error": "Invalid request"}, 400)

            account = verify_credentials(username, password)
            if not account:
                time.sleep(0.5)
                logger.warning("Failed login from %s for user %s",
                               self.client_address[0], username[:64])
                return self.send_json(
                    {"error": "Invalid SSH username or password"}, 401
                )

            token = uuid.uuid4().hex + uuid.uuid4().hex
            with _sessions_lock:
                _sessions[token] = {
                    "username": username,
                    "last": time.time()
                }

            logger.info("Login accepted from %s as %s",
                        self.client_address[0], username)
            return self.send_json(
                {"ok": True, "username": username},
                200,
                {"Set-Cookie":
                 "oc_telegram_notify=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d"
                 % (token, SESSION_TTL)}
            )

        if route == "/api/logout":
            token = ""
            for item in (self.headers.get("Cookie") or "").split(";"):
                key, sep, value = item.strip().partition("=")
                if sep and key == "oc_telegram_notify":
                    token = value
                    break
            with _sessions_lock:
                _sessions.pop(token, None)
            return self.send_json(
                {"ok": True},
                200,
                {"Set-Cookie":
                 "oc_telegram_notify=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"}
            )

        if not self.require_auth():
            return

        try:
            if self.path == "/api/languages":
                pack = self.read_json()
                code = pack.get("code", "")
                if not isinstance(code, str) or not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", code):
                    raise ValueError("Invalid language code")
                if not isinstance(pack.get("name"), str) or not pack["name"].strip():
                    raise ValueError("Language name is required")
                if not isinstance(pack.get("native_name"), str) or not pack["native_name"].strip():
                    raise ValueError("Native language name is required")
                if pack.get("version") != 2:
                    raise ValueError("Language pack version must be 2")

                interface = pack.get("interface")
                notifications = pack.get("notifications")

                if not isinstance(interface, dict) or not isinstance(notifications, dict):
                    raise ValueError("Interface and notifications objects are required")

                if any(not isinstance(k, str) or not isinstance(v, str)
                       for section in (interface, notifications)
                       for k, v in section.items()):
                    raise ValueError("Translation keys and values must be strings")

                with open(os.path.join(LANG_DIR, "en.json"), "r", encoding="utf-8") as f:
                    reference = json.load(f)

                if set(interface) != set(reference["interface"]):
                    raise ValueError("Interface keys do not match the English template")

                if set(notifications) != set(reference["notifications"]):
                    raise ValueError("Notification keys do not match the English template")

                for key in reference["notifications"]:
                    expected = sorted(re.findall(
                        r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}",
                        reference["notifications"][key]
                    ))
                    actual = sorted(re.findall(
                        r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}",
                        notifications[key]
                    ))
                    if expected != actual:
                        raise ValueError("Invalid notification placeholders: " + key)
                os.makedirs(LANG_DIR, exist_ok=True)
                target = os.path.join(LANG_DIR, code + ".json")
                temporary = target + ".tmp"
                with open(temporary, "w", encoding="utf-8") as f:
                    json.dump(pack, f, ensure_ascii=False, indent=2)
                os.chmod(temporary, 0o644)
                os.replace(temporary, target)
                self.send_json({"ok": True, "code": code})
                return

            if self.path == "/api/config":
                values = self.read_json()
                with config_lock:
                    if values.get("bot_token"):
                        config["bot_token"] = str(values["bot_token"]).strip()
                    if values.get("clear_token"):
                        config["bot_token"] = ""

                    config["chat_ids"] = str(values.get("chat_ids", config["chat_ids"]))
                    config["name"] = str(values.get("name", config["name"]))[:80]
                    config["silent"] = bool(values.get("silent", config["silent"]))
                    config["snapshot_url"] = str(
                        values.get("snapshot_url", config["snapshot_url"])
                    )[:500]
                    config["send_photos"] = bool(
                        values.get("send_photos", config["send_photos"])
                    )
                    config["enable_service"] = bool(
                        values.get("enable_service", config.get("enable_service", True))
                    )
                    config["progress_percent"] = max(
                        0, min(100, int(values.get("progress_percent", config["progress_percent"])))
                    )
                    config["poll_interval"] = max(
                        2, min(30, int(values.get("poll_interval", config["poll_interval"])))
                    )
                    selected_language = str(values.get("language", config.get("language", "en")))
                    if not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", selected_language):
                        raise ValueError("Invalid language code")
                    if not os.path.isfile(os.path.join(LANG_DIR, selected_language + ".json")):
                        raise ValueError("Language pack not found")
                    config["language"] = selected_language
                    save_config()
                self.send_json({"ok": True})
                return

            if self.path == "/api/test":
                runtime_state = service_runtime_state()
                if runtime_state != "soc":
                    self.send_json({
                        "ok": False,
                        "error": "Service disabled" if runtime_state == "disabled"
                                 else "Telegram monitoring is available only in SOC mode"
                    })
                    return
                threading.Thread(
                    target=send_notification,
                    args=(notification_text("test_message"),),
                    daemon=True
                ).start()
                self.send_json({"ok": True, "message": "Test queued"})
                return

            self.send_error(404)

        except Exception as exc:
            logger.exception("Web request failed")
            self.send_json({"ok": False, "error": str(exc)}, 400)


def main():
    load_config()
    threading.Thread(target=monitor, daemon=True, name="moonraker-monitor").start()

    server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), Handler)
    logger.info("Telegram Notify started; Moonraker: %s; Web: %s:%d",
                MOONRAKER, WEB_HOST, WEB_PORT)
    print("Telegram Notify listening on port %d" % WEB_PORT, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
