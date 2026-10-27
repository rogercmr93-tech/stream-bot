"""Bot de Telegram que avisa cuando un streamer entra en directo
en Twitch, YouTube o Kick.

Uso:
  python bot.py          -> bucle continuo (VPS, Raspberry Pi, PC)
  python bot.py --once   -> una sola revisión (GitHub Actions)
"""

import asyncio
import html
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).parent
STREAMERS_FILE = BASE / "streamers.json"
STATE_FILE = BASE / "state.json"

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
INTERVAL = int(os.getenv("CHECK_INTERVAL") or "60")

TWITCH_ID = os.getenv("TWITCH_CLIENT_ID", "")
TWITCH_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "")
KICK_ID = os.getenv("KICK_CLIENT_ID", "")
KICK_SECRET = os.getenv("KICK_CLIENT_SECRET", "")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("streambot")


class OAuthToken:
    """Token de aplicación (client credentials) con caché."""

    def __init__(self, url, client_id, client_secret):
        self.url, self.cid, self.secret = url, client_id, client_secret
        self.token, self.expires = None, 0.0

    async def get(self, client: httpx.AsyncClient) -> str:
        if self.token and time.time() < self.expires - 60:
            return self.token
        r = await client.post(
            self.url,
            data={
                "client_id": self.cid,
                "client_secret": self.secret,
                "grant_type": "client_credentials",
            },
        )
        r.raise_for_status()
        data = r.json()
        self.token = data["access_token"]
        self.expires = time.time() + data.get("expires_in", 3600)
        return self.token


twitch_token = OAuthToken("https://id.twitch.tv/oauth2/token", TWITCH_ID, TWITCH_SECRET)
kick_token = OAuthToken("https://id.kick.com/oauth/token", KICK_ID, KICK_SECRET)


# ---------- Comprobación por plataforma ----------
# Cada función devuelve {clave_del_streamer: info} con los que están en directo,
# o None si la consulta falló (así no se marca a nadie como "offline" por error).


async def check_twitch(client, logins):
    if not logins:
        return {}
    if not TWITCH_ID:
        log.warning("Hay streamers de Twitch pero faltan las credenciales de Twitch.")
        return None
    try:
        token = await twitch_token.get(client)
        live = {}
        for i in range(0, len(logins), 100):
            params = [("user_login", l) for l in logins[i : i + 100]]
            r = await client.get(
                "https://api.twitch.tv/helix/streams",
                params=params,
                headers={"Client-Id": TWITCH_ID, "Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            for s in r.json()["data"]:
                if s.get("type") != "live":
                    continue
                login = s["user_login"].lower()
                live[login] = {
                    "name": s["user_name"],
                    "title": s.get("title", ""),
                    "game": s.get("game_name", ""),
                    "viewers": s.get("viewer_count", 0),
                    "url": f"https://twitch.tv/{login}",
                }
        return live
    except Exception as e:
        log.warning("Twitch falló: %s", e)
        return None


async def check_kick(client, slugs):
    if not slugs:
        return {}
    if not KICK_ID:
        log.warning("Hay streamers de Kick pero faltan las credenciales de Kick.")
        return None
    try:
        token = await kick_token.get(client)
        live = {}
        for i in range(0, len(slugs), 50):
            params = [("slug", s) for s in slugs[i : i + 50]]
            r = await client.get(
                "https://api.kick.com/public/v1/channels",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            for ch in r.json().get("data", []):
                stream = ch.get("stream") or {}
                if not stream.get("is_live"):
                    continue
                slug = ch["slug"].lower()
                live[slug] = {
                    "name": ch["slug"],
                    "title": ch.get("stream_title", ""),
                    "game": (ch.get("category") or {}).get("name", ""),
                    "viewers": stream.get("viewer_count", 0),
                    "url": f"https://kick.com/{slug}",
                }
        return live
    except Exception as e:
        log.warning("Kick falló: %s", e)
        return None


async def _youtube_one(client, ident):
    """ident: @handle o ID de canal (UC...)."""
    path = f"@{ident.lstrip('@')}" if not ident.startswith("UC") else f"channel/{ident}"
    r = await client.get(
        f"https://www.youtube.com/{path}/live",
        headers={
            "Accept-Language": "es,en;q=0.8",
            "Cookie": "CONSENT=YES+1; SOCS=CAI",
        },
        follow_redirects=True,
    )
    r.raise_for_status()
    page = r.text
    if '"isLive":true' not in page:
        return None
    vid = re.search(
        r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"',
        page,
    )
    title = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    return {
        "name": ident.lstrip("@"),
        "title": html.unescape(title.group(1)) if title else "",
        "game": "",
        "viewers": None,
        "url": (
            f"https://www.youtube.com/watch?v={vid.group(1)}"
            if vid
            else f"https://www.youtube.com/{path}/live"
        ),
    }


async def check_youtube(client, idents):
    live, failed = {}, 0
    for ident in idents:
        try:
            info = await _youtube_one(client, ident)
            if info:
                live[ident.lower()] = info
        except Exception as e:
            failed += 1
            log.warning("YouTube (%s) falló: %s", ident, e)
    return None if idents and failed == len(idents) else live


# ---------- Telegram ----------


async def notify(client, platform, info):
    icons = {"twitch": "🟣", "youtube": "🔴", "kick": "🟢"}
    lines = [
        f"{icons[platform]} <b>{html.escape(info['name'])}</b> "
        f"está en directo en {platform.capitalize()}"
    ]
    if info["title"]:
        lines.append(html.escape(info["title"]))
    extra = []
    if info["game"]:
        extra.append(f"🎮 {html.escape(info['game'])}")
    if info["viewers"] is not None:
        extra.append(f"👥 {info['viewers']}")
    if extra:
        lines.append("  ".join(extra))
    lines.append(info["url"])
    r = await client.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": "\n".join(lines), "parse_mode": "HTML"},
    )
    if r.status_code != 200:
        log.error("Telegram respondió %s: %s", r.status_code, r.text)


# ---------- Comandos de Telegram (/add, /remove, /list) ----------

PLATFORMS = ("twitch", "youtube", "kick")
ICONS = {"twitch": "🟣", "youtube": "🔴", "kick": "🟢"}
HELP = (
    "Comandos:\n"
    "/add twitch ibai\n"
    "/add kick xqc\n"
    "/add youtube @ibai\n"
    "/remove twitch ibai\n"
    "/list\n\n"
    "También vale pegar el enlace del canal, por ejemplo:\n"
    "/add https://twitch.tv/ibai"
)
URL_PATTERNS = [
    ("twitch", re.compile(r"twitch\.tv/([A-Za-z0-9_]+)", re.I)),
    ("kick", re.compile(r"kick\.com/([A-Za-z0-9_-]+)", re.I)),
    ("youtube", re.compile(r"youtube\.com/(@[\w.-]+|channel/UC[\w-]+)", re.I)),
]


def load_cfg():
    try:
        return json.loads(STREAMERS_FILE.read_text())
    except FileNotFoundError:
        return {}


def save_cfg(cfg):
    STREAMERS_FILE.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def normalize(platform, name):
    name = name.strip()
    if platform == "youtube":
        if re.fullmatch(r"UC[\w-]{20,}", name):
            return name
        return "@" + name.lstrip("@")
    return name.lstrip("@").lower()


def parse_target(args):
    """Devuelve (plataforma, nombre) a partir de '/add twitch ibai' o de un enlace."""
    if not args:
        return None
    joined = " ".join(args)
    for platform, rx in URL_PATTERNS:
        m = rx.search(joined)
        if m:
            name = m.group(1)
            if name.lower().startswith("channel/"):
                name = name.split("/", 1)[1]
            return platform, normalize(platform, name)
    if len(args) >= 2 and args[0].lower() in PLATFORMS:
        platform = args[0].lower()
        return platform, normalize(platform, args[1])
    return None


def command_reply(text):
    """Ejecuta un comando y devuelve el texto de respuesta (o None si se ignora)."""
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    if cmd in ("/start", "/help"):
        return HELP

    cfg = load_cfg()

    if cmd == "/list":
        lines = []
        for p in PLATFORMS:
            names = cfg.get(p, [])
            lines.append(f"{ICONS[p]} {p.capitalize()}: " + (", ".join(names) or "—"))
        return "\n".join(lines)

    if cmd in ("/add", "/remove"):
        target = parse_target(args)
        if not target:
            return "No te entendí. Ejemplo: /add twitch ibai\n\n" + HELP
        platform, name = target
        current = cfg.get(platform, [])
        key = name.lower()
        exists = any(x.lower() == key for x in current)
        if cmd == "/add":
            if exists:
                return f"{name} ya estaba en {platform.capitalize()}."
            cfg[platform] = current + [name]
            save_cfg(cfg)
            return f"✅ Añadido {name} a {platform.capitalize()}."
        if not exists:
            return f"{name} no estaba en {platform.capitalize()}."
        cfg[platform] = [x for x in current if x.lower() != key]
        save_cfg(cfg)
        return f"🗑️ Quitado {name} de {platform.capitalize()}."

    return None


async def send_text(client, text):
    r = await client.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True},
    )
    if r.status_code != 200:
        log.error("Telegram respondió %s: %s", r.status_code, r.text)


async def handle_commands(client, state):
    """Lee los mensajes nuevos del chat y ejecuta los comandos."""
    try:
        r = await client.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={
                "offset": state.get("tg_offset", 0),
                "timeout": 0,
                "allowed_updates": json.dumps(["message"]),
            },
        )
        r.raise_for_status()
        updates = r.json().get("result", [])
    except Exception as e:
        log.warning("No pude leer los mensajes de Telegram: %s", e)
        return

    for u in updates:
        state["tg_offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        # Solo obedece al chat configurado: nadie más puede cambiar la lista.
        if str((msg.get("chat") or {}).get("id")) != str(TG_CHAT):
            continue
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        try:
            reply = command_reply(text)
        except Exception as e:
            log.error("Error en el comando %r: %s", text, e)
            reply = "⚠️ Hubo un error con ese comando."
        if reply:
            await send_text(client, reply)


# ---------- Lógica principal ----------


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    # Formato estable para que git solo registre cambios reales.
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


async def cycle(client, state):
    """Una revisión completa: primero los comandos, luego las tres plataformas."""
    await handle_commands(client, state)
    try:
        cfg = json.loads(STREAMERS_FILE.read_text())
    except Exception as e:
        log.error("No pude leer streamers.json: %s", e)
        return

    checks = {
        "twitch": check_twitch(client, [s.lower() for s in cfg.get("twitch", [])]),
        "kick": check_kick(client, [s.lower() for s in cfg.get("kick", [])]),
        "youtube": check_youtube(client, cfg.get("youtube", [])),
    }
    results = dict(zip(checks, await asyncio.gather(*checks.values())))

    for platform, live in results.items():
        if live is None:  # fallo temporal: conservar el estado anterior
            continue
        before = set(state.get(platform, []))
        for key, info in live.items():
            if key not in before:
                log.info("%s en directo en %s", info["name"], platform)
                await notify(client, platform, info)
        state[platform] = sorted(live.keys())

    save_state(state)


async def main():
    once = "--once" in sys.argv or os.getenv("RUN_ONCE") == "1"
    state = load_state()  # {"twitch": ["ibai", ...], ...} = quién estaba en directo
    async with httpx.AsyncClient(timeout=20) as client:
        if once:
            await cycle(client, state)
            return
        log.info("Bot iniciado. Revisando cada %s s.", INTERVAL)
        while True:
            await cycle(client, state)
            await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
