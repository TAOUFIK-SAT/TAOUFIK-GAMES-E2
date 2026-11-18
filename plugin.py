# -*- coding: utf-8 -*-
import os
import json
import time
import glob
import fcntl
import queue
import select
import shutil
import signal
import struct
import socket
import threading
import urllib.request
import urllib.error
from collections import OrderedDict

from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Tools.LoadPixmap import LoadPixmap
from enigma import eTimer, eConsoleAppContainer, eRCInput, eDVBVolumecontrol, gRGB, getDesktop

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ==============================================================================
# 0. توليد أيقونة البلوغين تلقائياً (Plugin Icon Generator)
# ==============================================================================
def create_plugin_icon():
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(plugin_dir, "plugin.png")
    if os.path.exists(icon_path):
        return
    if not HAS_PIL:
        return
    try:
        img = Image.new("RGBA", (100, 60), (20, 26, 41, 255))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((2, 2, 97, 57), radius=10, outline=(61, 220, 132, 255), width=2)
        draw.rectangle((40, 22, 60, 38), fill=(61, 220, 132, 255))
        draw.ellipse((44, 26, 56, 34), fill=(20, 26, 41, 255))
        img.save(icon_path, "PNG")
    except Exception:
        pass

create_plugin_icon()

# ==============================================================================
# 1. الإعدادات والمسارات والمحاكيات (Neo Geo, Arcade, PS2, Dreamcast)
# ==============================================================================
PLUGIN_PATH = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(PLUGIN_PATH, "assets")
ROMS_BASE = "/media/hdd/roms"
COVERS_BASE = os.path.join(PLUGIN_PATH, "covers")
SETTINGS_FILE = os.path.join(PLUGIN_PATH, "settings.json")
OVERRIDE_CFG = "/tmp/taoufikgames_override.cfg"
V_KBD_NAME = "TAOUFIK GAMES Remote Kbd"

COLS = 4
ROWS = 2
PAGE_CARDS = COLS * ROWS

BIOS_FILES = {
    "neogeo.zip", "pgm.zip", "qsound.zip", "skns.zip",
    "awbios.zip", "naomi.zip", "dc_boot.bin", "dc_flash.bin", "ps2-rom.bin", "scph39001.bin",
}

CORE_DIRS = (
    "/usr/lib/libretro",
    "/usr/lib/retroarch/cores",
    "/usr/share/libretro/cores",
    "/home/root/.config/retroarch/cores",
    "/media/hdd/cores",
)

RETRO_CONFIGS = (
    "/etc/retrogaming/retroarch.cfg",
    "/home/root/.config/retroarch/retroarch.cfg",
    "/etc/retroarch.cfg",
)

SYSTEMS = {
    "neogeo": {
        "name": "SNK Neo Geo",
        "exts": (".zip", ".7z"),
        "cores": ("fbneo_libretro.so", "fbalpha2012_neogeo_libretro.so", "mame2003_plus_libretro.so"),
        "libretro_name": "SNK - Neo Geo",
    },
    "arcade": {
        "name": "Arcade (MAME)",
        "exts": (".zip", ".7z", ".chd"),
        "cores": ("fbneo_libretro.so", "mame2003_plus_libretro.so", "mame2003_libretro.so"),
        "libretro_name": "MAME",
    },
    "ps2": {
        "name": "PlayStation 2",
        "exts": (".iso", ".chd", ".bin", ".img", ".zso"),
        "cores": ("play_libretro.so", "pcsx2_libretro.so"),
        "libretro_name": "Sony - PlayStation 2",
    },
    "dreamcast": {
        "name": "Sega Dreamcast",
        "exts": (".chd", ".cdi", ".gdi"),
        "cores": ("flycast_libretro.so", "reicast_libretro.so"),
        "libretro_name": "Sega - Dreamcast",
    },
}

DEFAULT_SETTINGS = {
    "resolution": "1280x720",
    "aspect": "auto",
    "smooth": False,
    "fps": False,
    "audio_mute": False,
    "audio_db": "0.0",
    "audio_sync": True,
    "hard_sync": True,
    "remote_pad": True,
    "remote_layout": "colors",
    "swap_ab": False,
    "btn_ok": None,
    "btn_exit": None,
    "btn_menu": None,
}


def load_settings():
    cfg = dict(DEFAULT_SETTINGS)
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    cfg.update(data)
    except Exception:
        pass
    return cfg


def save_settings(cfg):
    try:
        os.makedirs(PLUGIN_PATH, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def check_uinput_ready():
    if os.path.exists("/dev/uinput"):
        return True
    os.system("modprobe uinput 2>/dev/null")
    return os.path.exists("/dev/uinput")


def write_retro_override(cfg):
    lines = ['video_fullscreen = "true"']
    res = cfg.get("resolution", "1280x720")
    if res == "1920x1080":
        lines += ['video_fullscreen_x = "1920"', 'video_fullscreen_y = "1080"']
    elif res == "1280x720":
        lines += ['video_fullscreen_x = "1280"', 'video_fullscreen_y = "720"']
    elif res == "640x480":
        lines += ['video_fullscreen_x = "640"', 'video_fullscreen_y = "480"']
    else:
        lines += ['video_fullscreen_x = "0"', 'video_fullscreen_y = "0"']

    aspect = cfg.get("aspect", "auto")
    if aspect == "4:3":
        lines += ['video_aspect_ratio_auto = "false"', 'aspect_ratio_index = "0"']
    elif aspect == "16:9":
        lines += ['video_aspect_ratio_auto = "false"', 'aspect_ratio_index = "1"']
    else:
        lines += ['video_aspect_ratio_auto = "true"']

    lines.append(f'video_smooth = "{"true" if cfg.get("smooth") else "false"}"')
    lines.append(f'fps_show = "{"true" if cfg.get("fps") else "false"}"')
    lines.append(f'audio_mute_enable = "{"true" if cfg.get("audio_mute") else "false"}"')
    lines.append(f'audio_volume = "{cfg.get("audio_db", "0.0")}"')
    lines.append(f'audio_sync = "{"true" if cfg.get("audio_sync", True) else "false"}"')
    lines.append(f'video_hard_sync = "{"true" if cfg.get("hard_sync", True) else "false"}"')
    lines.append('video_threaded = "true"')

    if cfg.get("remote_pad", True):
        if check_uinput_ready():
            lines += [
                'input_player1_up = "up"',
                'input_player1_down = "down"',
                'input_player1_left = "left"',
                'input_player1_right = "right"',
                'input_player1_a = "x"',
                'input_player1_b = "z"',
                'input_player1_x = "s"',
                'input_player1_y = "a"',
                'input_player1_l = "q"',
                'input_player1_r = "w"',
                'input_player1_start = "enter"',
                'input_player1_select = "rshift"',
                'input_menu_toggle = "f1"',
            ]
        else:
            lines += [
                'input_player1_up = "up"',
                'input_player1_down = "down"',
                'input_player1_left = "left"',
                'input_player1_right = "right"',
                'input_player1_a = "num1"',
                'input_player1_b = "num2"',
                'input_player1_x = "num3"',
                'input_player1_y = "num4"',
                'input_player1_l = "num7"',
                'input_player1_r = "num8"',
                'input_player1_start = "num5"',
                'input_player1_select = "num0"',
                'input_menu_toggle = "num9"',
            ]

    try:
        with open(OVERRIDE_CFG, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


# ==============================================================================
# 2. مولّد واجهة أندرويد الخفيفة
# ==============================================================================
def ensure_android_assets(scale=1.0):
    os.makedirs(ASSET_DIR, exist_ok=True)
    if not HAS_PIL:
        return False

    def s(v):
        return max(1, int(round(v * scale)))

    tag = f"{s(1920)}x{s(1080)}"
    files = {
        "rail": os.path.join(ASSET_DIR, f"rail_{tag}.png"),
        "nav_pill": os.path.join(ASSET_DIR, f"nav_pill_{tag}.png"),
        "hero": os.path.join(ASSET_DIR, f"hero_{tag}.png"),
        "card_n": os.path.join(ASSET_DIR, f"card_n_{tag}.png"),
        "card_f": os.path.join(ASSET_DIR, f"card_f_{tag}.png"),
        "cover": os.path.join(ASSET_DIR, f"cover_v2_{tag}.png"),
        "set_n": os.path.join(ASSET_DIR, f"set_n_{tag}.png"),
        "set_f": os.path.join(ASSET_DIR, f"set_f_{tag}.png"),
    }

    if not os.path.exists(files["rail"]):
        w, h = s(290), s(930)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=s(28), fill=(20, 25, 38, 255), outline=(255, 255, 255, 24), width=max(1, s(2)))
        img.save(files["rail"], "PNG")

    if not os.path.exists(files["nav_pill"]):
        w, h = s(260), s(56)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=s(16), fill=(30, 74, 58, 255), outline=(61, 220, 132, 220), width=max(1, s(2)))
        img.save(files["nav_pill"], "PNG")

    if not os.path.exists(files["hero"]):
        w, h = s(1500), s(215)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=s(26), fill=(24, 31, 48, 255), outline=(255, 255, 255, 24), width=max(1, s(2)))
        img.save(files["hero"], "PNG")

    if not os.path.exists(files["card_n"]):
        w, h = s(350), s(248)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((s(4), s(4), w - s(5), h - s(5)), radius=s(22), fill=(22, 28, 44, 255), outline=(255, 255, 255, 22), width=max(1, s(2)))
        img.save(files["card_n"], "PNG")

    if not os.path.exists(files["card_f"]):
        w, h = s(350), s(248)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=s(24), fill=(34, 45, 70, 255), outline=(61, 220, 132, 255), width=max(2, s(4)))
        img.save(files["card_f"], "PNG")

    if not os.path.exists(files["cover"]):
        w, h = s(318), s(152)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=s(16), fill=(32, 41, 66, 255))
        img.save(files["cover"], "PNG")

    if not os.path.exists(files["set_n"]):
        w, h = s(1560), s(74)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=s(18), fill=(20, 26, 41, 255), outline=(255, 255, 255, 18), width=max(1, s(2)))
        img.save(files["set_n"], "PNG")

    if not os.path.exists(files["set_f"]):
        w, h = s(1560), s(74)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=s(18), fill=(32, 46, 68, 255), outline=(61, 220, 132, 255), width=max(2, s(3)))
        img.save(files["set_f"], "PNG")

    return True


# ==============================================================================
# 3. نظام التحميل التلقائي لصور الألعاب (Auto Cover Downloader)
# ==============================================================================
def download_game_cover(base_name, sys_key, target_path):
    if os.path.exists(target_path):
        return True
    
    info = SYSTEMS.get(sys_key)
    if not info or "libretro_name" not in info:
        return False
        
    lib_name = info["libretro_name"].replace(" ", "%20")
    
    urls = [
        f"https://raw.githubusercontent.com/libretro-thumbnails/{info['libretro_name'].replace(' ', '_')}/master/Named_Boxarts/{urllib.parse.quote(base_name)}.png",
        f"https://thumbnails.libretro.com/{lib_name}/Named_Boxarts/{urllib.parse.quote(base_name)}.png"
    ]
    
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=4) as response:
                if response.status == 200:
                    with open(target_path, 'wb') as f:
                        f.write(response.read())
                    return True
        except Exception:
            continue
    return False


# ==============================================================================
# 4. جسر الريموت الافتراضي
# ==============================================================================
class VirtualRemoteKbd:
    UI_SET_EVBIT = 0x40045564
    UI_SET_KEYBIT = 0x40045565
    UI_DEV_CREATE = 0x5501
    UI_DEV_DESTROY = 0x5502
    EV_FMT = "llHHi"
    TARGET_KEYS = (103, 108, 105, 106, 45, 44, 31, 30, 16, 17, 28, 54, 59)

    def __init__(self):
        self.fd = None

    def open(self):
        if not check_uinput_ready():
            return False
        try:
            self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
            fcntl.ioctl(self.fd, self.UI_SET_EVBIT, 1)
            for k in self.TARGET_KEYS:
                fcntl.ioctl(self.fd, self.UI_SET_KEYBIT, k)
            dev_struct = struct.pack(
                "80sHHHHI" + "i" * 256,
                V_KBD_NAME.encode("utf-8"),
                0x03, 0x1234, 0x5678, 1, 0,
                *([0] * 256),
            )
            os.write(self.fd, dev_struct)
            fcntl.ioctl(self.fd, self.UI_DEV_CREATE)
            return True
        except Exception:
            self.close()
            return False

    def send_key(self, code, value):
        if self.fd is None:
            return
        try:
            now = time.time()
            sec = int(now)
            usec = int((now - sec) * 1000000)
            ev_key = struct.pack(self.EV_FMT, sec, usec, 1, code, value)
            ev_syn = struct.pack(self.EV_FMT, sec, usec, 0, 0, 0)
            os.write(self.fd, ev_key + ev_syn)
        except Exception:
            pass

    def close(self):
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, self.UI_DEV_DESTROY)
            except Exception:
                pass
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None


class RemoteBridgeWatcher:
    EV_FMT = "llHHi"
    EV_SIZE = struct.calcsize(EV_FMT)
    EXIT_CODES = {1, 116, 128, 158, 174}

    MAP_COLORS = {
        103: 103, 108: 108, 105: 105, 106: 106,
        398: 45, 399: 44, 400: 31, 401: 30,
        2: 45, 3: 44, 4: 31, 5: 30,
        352: 28, 28: 28, 358: 54, 365: 54, 11: 54,
        402: 16, 104: 16, 403: 17, 109: 17, 139: 59,
    }

    MAP_NUMBERS = {
        103: 103, 108: 108, 105: 105, 106: 106,
        2: 45, 3: 44, 4: 31, 5: 30, 6: 28,
        352: 28, 28: 28, 7: 54, 11: 54, 8: 16, 9: 17, 10: 59, 139: 59,
    }

    def __init__(self, get_pid_fn, enable_pad=True, layout="colors"):
        self.get_pid_fn = get_pid_fn
        self.enable_pad = enable_pad
        self.key_map = self.MAP_NUMBERS if layout == "numbers" else self.MAP_COLORS
        self.running = False
        self._thread = None
        self._vkbd = VirtualRemoteKbd() if enable_pad else None

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def _terminate_retroarch(self):
        try:
            pid = self.get_pid_fn()
            if pid and pid > 1:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        os.system("killall -TERM retroarch 2>/dev/null")
        time.sleep(0.1)
        os.system("killall -KILL retroarch 2>/dev/null")

    def _is_physical_remote(self, ev_dev):
        base = os.path.basename(ev_dev)
        p = f"/sys/class/input/{base}/device/name"
        try:
            if os.path.exists(p):
                name = open(p, "r", encoding="utf-8", errors="ignore").read().strip()
                if name == V_KBD_NAME:
                    return False
                nl = name.lower()
                if any(k in nl for k in ("gamepad", "joystick", "xbox", "dualshock", "dualsense", "8bitdo")):
                    return False
        except Exception:
            pass
        return True

    def _loop(self):
        fds = []
        vkbd_ok = False
        try:
            if self._vkbd:
                vkbd_ok = self._vkbd.open()

            for ev in sorted(glob.glob("/dev/input/event[0-9]*")):
                if not self._is_physical_remote(ev):
                    continue
                try:
                    fds.append(os.open(ev, os.O_RDONLY | os.O_NONBLOCK))
                except Exception:
                    pass
            if not fds:
                return

            start_t = time.monotonic()
            for fd in fds:
                try:
                    while os.read(fd, self.EV_SIZE * 16):
                        pass
                except Exception:
                    pass

            while self.running:
                try:
                    readable, _, _ = select.select(fds, [], [], 0.25)
                except Exception:
                    break
                for fd in readable:
                    while self.running:
                        try:
                            data = os.read(fd, self.EV_SIZE)
                            if not data or len(data) < self.EV_SIZE:
                                break
                            _, _, ev_t, code, val = struct.unpack(self.EV_FMT, data)
                            if time.monotonic() - start_t < 0.7:
                                continue
                            if ev_t == 1:
                                if val == 1 and code in self.EXIT_CODES:
                                    self._terminate_retroarch()
                                    return
                                if vkbd_ok and code in self.key_map and val in (0, 1, 2):
                                    self._vkbd.send_key(self.key_map[code], val)
                        except BlockingIOError:
                            break
                        except OSError:
                            break
        finally:
            if self._vkbd:
                self._vkbd.close()
            for fd in fds:
                try:
                    os.close(fd)
                except Exception:
                    pass


# ==============================================================================
# 5. محرك أذرع التحكم الفيزيائية (Gamepad)
# ==============================================================================
class GamepadReader:
    JS_FMT = "IhBB"
    JS_SIZE = struct.calcsize(JS_FMT)
    EV_FMT = "llHHi"
    EV_SIZE = struct.calcsize(EV_FMT)

    def __init__(self, action_queue, settings):
        self.action_queue = action_queue
        self.settings = settings
        self.running = False
        self.paused = False
        self.connected = False
        self.device_name = ""
        self.capture_mode = False
        self._thread = None
        self._last_time = {}

    def apply_settings(self, settings):
        self.settings = settings

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def _emit(self, action, cooldown=0.17):
        now = time.monotonic()
        if now - self._last_time.get(action, 0) >= cooldown:
            self._last_time[action] = now
            self.action_queue.put(action)

    def _handle_button(self, btn_id, is_evdev=False):
        if self.capture_mode:
            self.capture_mode = False
            self.action_queue.put(("captured_btn", btn_id))
            return

        c_ok = self.settings.get("btn_ok")
        c_ex = self.settings.get("btn_exit")
        c_mn = self.settings.get("btn_menu")

        if c_ok is not None and btn_id == c_ok:
            self._emit("ok", 0.20)
            return
        if c_ex is not None and btn_id == c_ex:
            self._emit("exit", 0.25)
            return
        if c_mn is not None and btn_id == c_mn:
            self._emit("menu", 0.30)
            return

        swap = self.settings.get("swap_ab", False)
        if not is_evdev:
            ok_set = (1, 7, 9) if swap else (0, 7, 9)
            ex_set = (0, 6, 8) if swap else (1, 6, 8)
            mn_set = (2, 3)
        else:
            ok_set = (305, 315, 289, 297) if swap else (304, 315, 288, 297)
            ex_set = (304, 314, 288, 296) if swap else (305, 314, 289, 296)
            mn_set = (307, 308, 290, 291)

        if btn_id in ok_set:
            self._emit("ok", 0.20)
        elif btn_id in ex_set:
            self._emit("exit", 0.25)
        elif btn_id in mn_set:
            self._emit("menu", 0.30)

    def _sys_name(self, dev_base):
        p = f"/sys/class/input/{dev_base}/device/name"
        try:
            if os.path.exists(p):
                return open(p, "r", encoding="utf-8", errors="ignore").read().strip()
        except Exception:
            pass
        return ""

    def _is_gamepad_ev(self, ev_base, name):
        if name == V_KBD_NAME:
            return False
        nl = name.lower()
        if any(k in nl for k in ("gamepad", "joystick", "controller", "xbox", "playstation", "dualshock", "dualsense", "8bitdo", "logitech", "retro")):
            return True
        try:
            kp = f"/sys/class/input/{ev_base}/device/capabilities/key"
            ap = f"/sys/class/input/{ev_base}/device/capabilities/abs"
            if os.path.exists(kp) and os.path.exists(ap):
                return open(ap).read().strip() != "0" and open(kp).read().strip() != "0"
        except Exception:
            pass
        return False

    def _find_device(self):
        for js in sorted(glob.glob("/dev/input/js[0-9]*")):
            if os.path.exists(js):
                return ("js", js, self._sys_name(os.path.basename(js)) or "USB Gamepad")
        for ev in sorted(glob.glob("/dev/input/event[0-9]*")):
            base = os.path.basename(ev)
            name = self._sys_name(base)
            if self._is_gamepad_ev(base, name):
                return ("evdev", ev, name or "Gamepad")
        return (None, None, "")

    def _loop(self):
        while self.running:
            if self.paused:
                self.connected = False
                time.sleep(0.3)
                continue

            mode, path, name = self._find_device()
            if not path:
                self.connected = False
                self.device_name = ""
                time.sleep(1.5)
                continue

            fd = None
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                self.connected = True
                self.device_name = name
                if mode == "js":
                    self._read_js(fd)
                else:
                    self._read_evdev(fd)
            except Exception:
                self.connected = False
                time.sleep(1.0)
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except Exception:
                        pass

    def _read_js(self, fd):
        held = {}
        while self.running and not self.paused:
            try:
                data = os.read(fd, self.JS_SIZE)
                if not data or len(data) < self.JS_SIZE:
                    time.sleep(0.02)
                    continue
                _, val, ev_t, num = struct.unpack(self.JS_FMT, data)
                ev_t &= ~0x80

                if ev_t == 1 and val == 1:
                    self._handle_button(num, is_evdev=False)
                elif ev_t == 2 and not self.capture_mode:
                    if num in (0, 6):
                        held[num] = "left" if val < -16000 else ("right" if val > 16000 else None)
                        if held[num]:
                            self._emit(held[num], 0.16)
                    elif num in (1, 7):
                        held[num] = "up" if val < -16000 else ("down" if val > 16000 else None)
                        if held[num]:
                            self._emit(held[num], 0.16)
            except BlockingIOError:
                for d in held.values():
                    if d and not self.capture_mode:
                        self._emit(d, 0.20)
                time.sleep(0.03)
            except OSError:
                break

    def _abs_bounds(self, fd, axis):
        try:
            buf = fcntl.ioctl(fd, 0x80184540 + axis, b"\x00" * 24)
            _, mn, mx, _, _, _ = struct.unpack("iiiiii", buf)
            if mx > mn:
                return mn, mx
        except Exception:
            pass
        return -32767, 32767

    def _read_evdev(self, fd):
        bounds = {0: self._abs_bounds(fd, 0), 1: self._abs_bounds(fd, 1)}
        held = {}
        while self.running and not self.paused:
            try:
                data = os.read(fd, self.EV_SIZE)
                if not data or len(data) < self.EV_SIZE:
                    time.sleep(0.02)
                    continue
                _, _, ev_t, code, val = struct.unpack(self.EV_FMT, data)

                if ev_t == 1 and val == 1:
                    if code == 544 and not self.capture_mode:
                        self._emit("up", 0.16)
                    elif code == 545 and not self.capture_mode:
                        self._emit("down", 0.16)
                    elif code == 546 and not self.capture_mode:
                        self._emit("left", 0.16)
                    elif code == 547 and not self.capture_mode:
                        self._emit("right", 0.16)
                    else:
                        self._handle_button(code, is_evdev=True)
                elif ev_t == 3 and not self.capture_mode:
                    if code == 16:
                        held[16] = "left" if val < 0 else ("right" if val > 0 else None)
                        if held[16]:
                            self._emit(held[16], 0.16)
                    elif code == 17:
                        held[17] = "up" if val < 0 else ("down" if val > 0 else None)
                        if held[17]:
                            self._emit(held[17], 0.16)
                    elif code in (0, 1):
                        mn, mx = bounds[code]
                        norm = (val - (mn + mx) / 2.0) / max(1.0, (mx - mn) / 2.0)
                        if code == 0:
                            held[0] = "left" if norm < -0.55 else ("right" if norm > 0.55 else None)
                        else:
                            held[1] = "up" if norm < -0.55 else ("down" if norm > 0.55 else None)
                        if held[code]:
                            self._emit(held[code], 0.16)
            except BlockingIOError:
                for d in held.values():
                    if d and not self.capture_mode:
                        self._emit(d, 0.20)
                time.sleep(0.03)
            except OSError:
                break


# ==============================================================================
# 6. محرك الألعاب
# ==============================================================================
class EmulatorCore:
    def __init__(self):
        self.container = None
        self.on_exit_callback = None
        self.prev_service = None
        self._gamepad_ref = None
        self._screen_ref = None
        self._remote_watcher = None
        self.ensure_directories()

    def ensure_directories(self):
        try:
            os.makedirs(ROMS_BASE, exist_ok=True)
            os.makedirs(COVERS_BASE, exist_ok=True)
            for sys_key in SYSTEMS:
                os.makedirs(os.path.join(ROMS_BASE, sys_key), exist_ok=True)
        except Exception:
            pass

    def find_retroarch(self):
        found = shutil.which("retroarch")
        if found and os.path.exists(found):
            return found
        for p in ("/usr/bin/retroarch", "/usr/local/bin/retroarch", "/opt/retroarch/bin/retroarch"):
            if os.path.exists(p):
                return p
        return None

    def find_config(self):
        for cfg in RETRO_CONFIGS:
            if os.path.exists(cfg):
                return cfg
        return None

    def find_core(self, core_names):
        for name in core_names:
            for d in CORE_DIRS:
                full = os.path.join(d, name)
                if os.path.exists(full):
                    return full, True
        return os.path.join(CORE_DIRS[0], core_names[0]), False

    def scan_games(self, filter_system="all", tag="1920x1080"):
        games = []
        default_cover = os.path.join(ASSET_DIR, f"cover_v2_{tag}.png")

        for sys_key, info in SYSTEMS.items():
            if filter_system != "all" and filter_system != sys_key:
                continue
            sys_dir = os.path.join(ROMS_BASE, sys_key)
            if not os.path.isdir(sys_dir):
                continue

            core_path, core_ok = self.find_core(info["cores"])
            for fname in sorted(os.listdir(sys_dir)):
                if fname.lower() in BIOS_FILES:
                    continue
                if fname.lower().endswith(info["exts"]):
                    base = os.path.splitext(fname)[0]
                    rom_path = os.path.join(sys_dir, fname)
                    
                    loc_cov = os.path.join(COVERS_BASE, f"{base}.png")
                    hdd_cov = os.path.join(ROMS_BASE, "covers", f"{base}.png")
                    
                    cover = hdd_cov if os.path.exists(hdd_cov) else (loc_cov if os.path.exists(loc_cov) else default_cover)
                    
                    if not os.path.exists(cover) or cover == default_cover:
                        try:
                            if download_game_cover(base, sys_key, loc_cov):
                                cover = loc_cov
                        except Exception:
                            pass

                    try:
                        mb = os.path.getsize(rom_path) / (1024.0 * 1024.0)
                        size_str = f"{mb:.1f} MB" if mb >= 1.0 else f"{os.path.getsize(rom_path) // 1024} KB"
                    except Exception:
                        size_str = "--"

                    games.append({
                        "title": base.replace("_", " "),
                        "filename": fname,
                        "size": size_str,
                        "system": sys_key,
                        "system_name": info["name"],
                        "rom_path": rom_path,
                        "core_path": core_path,
                        "core_ok": core_ok,
                        "cover_path": cover,
                    })
        return games

    def delete_game(self, game):
        try:
            if os.path.exists(game["rom_path"]):
                os.remove(game["rom_path"])
            return True, "تم حذف اللعبة بنجاح"
        except Exception as e:
            return False, f"تعذر حذف الملف:\n{e}"

    def launch_game(self, session, screen_ref, game, settings, on_finish, gamepad=None):
        retro_bin = self.find_retroarch()
        if not retro_bin:
            return False, "محرك RetroArch غير مثبت على الجهاز."

        if not game["core_ok"] or not os.path.exists(game["core_path"]):
            return False, f"النواة غير مثبتة:\n{os.path.basename(game['core_path'])}"

        write_retro_override(settings)
        self.on_exit_callback = on_finish
        self._gamepad_ref = gamepad
        self._screen_ref = screen_ref

        self.prev_service = session.nav.getCurrentlyPlayingServiceReference()
        session.nav.stopService()

        if self._gamepad_ref:
            self._gamepad_ref.pause()
        eRCInput.getInstance().lock()
        self._screen_ref.hide()

        self._remote_watcher = RemoteBridgeWatcher(
            lambda: self.container.getPID() if self.container else 0,
            enable_pad=settings.get("remote_pad", True),
            layout=settings.get("remote_layout", "colors"),
        )
        self._remote_watcher.start()

        cfg = self.find_config()
        cfg_arg = f'--config "{cfg}" ' if cfg else ""
        cmd = f'exec "{retro_bin}" {cfg_arg}--appendconfig "{OVERRIDE_CFG}" -L "{game["core_path"]}" "{game["rom_path"]}"'

        self.container = eConsoleAppContainer()
        self.container.appClosed.append(self._on_app_closed)
        self.container.execute(cmd)
        return True, "OK"

    def _on_app_closed(self, retval):
        if self._remote_watcher:
            self._remote_watcher.stop()
            self._remote_watcher = None
        eRCInput.getInstance().unlock()
        if self._screen_ref:
            self._screen_ref.show()
        if self._gamepad_ref:
            self._gamepad_ref.resume()
        if self.on_exit_callback:
            self.on_exit_callback(retval)


# ==============================================================================
# 7. شاشة إدارة وحذف الألعاب (Game Manager)
# ==============================================================================
MANAGER_ROWS = 9


def build_manager_skin(dw, dh):
    scale = dw / 1920.0
    def s(v): return max(1, int(round(v * scale)))
    rows_xml = ""
    for i in range(MANAGER_ROWS):
        y = s(135) + i * s(88)
        rows_xml += f"""
        <widget name="mgr_bg_{i}" position="{s(180)},{y}" size="{s(1560)},{s(74)}" zPosition="1" alphatest="blend" />
        <widget name="mgr_title_{i}" position="{s(210)},{y+s(14)}" size="{s(980)},{s(46)}" font="Regular;{s(25)}" foregroundColor="#FFFFFF" backgroundColor="#141A29" transparent="1" noWrap="1" zPosition="3" />
        <widget name="mgr_meta_{i}" position="{s(1210)},{y+s(16)}" size="{s(500)},{s(42)}" font="Regular;{s(21)}" halign="right" foregroundColor="#3DDC84" backgroundColor="#141A29" transparent="1" zPosition="3" />
        """
    return f"""
    <screen name="DroidPlayGameManager" position="0,0" size="{dw},{dh}" flags="wfNoBorder" backgroundColor="#0A0D16">
        <widget name="header" position="{s(180)},{s(42)}" size="{s(1100)},{s(50)}" font="Regular;{s(34)}" foregroundColor="#3DDC84" backgroundColor="#0A0D16" transparent="1" zPosition="2" />
        <widget name="counter" position="{s(1300)},{s(48)}" size="{s(440)},{s(40)}" font="Regular;{s(24)}" halign="right" foregroundColor="#94A3B8" backgroundColor="#0A0D16" transparent="1" zPosition="2" />
        {rows_xml}
        <widget name="hint" position="{s(180)},{s(985)}" size="{s(1560)},{s(40)}" font="Regular;{s(22)}" foregroundColor="#94A3B8" backgroundColor="#0A0D16" transparent="1" zPosition="2" />
    </screen>
    """


class DroidPlayGameManager(Screen):
    def __init__(self, session, core, dw, dh, tag):
        self.core = core
        self.tag = tag
        self.skin = build_manager_skin(dw, dh)
        Screen.__init__(self, session)
        self.games = self.core.scan_games("all", self.tag)
        self.index = 0
        self.scroll = 0
        self.changed = False

        self["header"] = Label("إدارة وحذف الألعاب الشاملة")
        self["counter"] = Label("")
        self["hint"] = Label("OK / RED: حذف اللعبة المحددة   •   الأسهم: التنقل   •   EXIT: رجوع")

        for i in range(MANAGER_ROWS):
            self[f"mgr_bg_{i}"] = Pixmap()
            self[f"mgr_title_{i}"] = Label("")
            self[f"mgr_meta_{i}"] = Label("")

        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self.ask_delete,
            "red": self.ask_delete,
            "up": self.key_up,
            "down": self.key_down,
            "cancel": self.key_exit,
        }, -2)
        self.onLayoutFinish.append(self.startup)

    def startup(self):
        self.pix_n = LoadPixmap(os.path.join(ASSET_DIR, f"set_n_{self.tag}.png"))
        self.pix_f = LoadPixmap(os.path.join(ASSET_DIR, f"set_f_{self.tag}.png"))
        self.refresh()

    def refresh(self):
        total = len(self.games)
        self["counter"].setText(f"{self.index + 1 if total else 0} / {total} لعبة")
        for i in range(MANAGER_ROWS):
            idx = self.scroll + i
            if idx < total:
                g = self.games[idx]
                self[f"mgr_bg_{i}"].show()
                self[f"mgr_title_{i}"].show()
                self[f"mgr_meta_{i}"].show()
                pix = self.pix_f if idx == self.index else self.pix_n
                if pix:
                    self[f"mgr_bg_{i}"].instance.setPixmap(pix)
                self[f"mgr_title_{i}"].setText(g["title"])
                self[f"mgr_meta_{i}"].setText(f"{g['system_name']}  •  {g['size']}  [حذف]")
                self[f"mgr_meta_{i}"].instance.setForegroundColor(gRGB(0xF87171 if idx == self.index else 0x3DDC84))
            else:
                self[f"mgr_bg_{i}"].hide()
                self[f"mgr_title_{i}"].hide()
                self[f"mgr_meta_{i}"].hide()

    def key_up(self):
        if self.index > 0:
            self.index -= 1
            if self.index < self.scroll:
                self.scroll = self.index
            self.refresh()

    def key_down(self):
        if self.index + 1 < len(self.games):
            self.index += 1
            if self.index >= self.scroll + MANAGER_ROWS:
                self.scroll = self.index - MANAGER_ROWS + 1
            self.refresh()

    def ask_delete(self):
        if not self.games:
            return
        g = self.games[self.index]
        msg = f"هل تريد حذف هذه اللعبة نهائياً؟\n\n{g['title']}\n({g['system_name']} - {g['size']})"
        self.session.openWithCallback(self.confirm_delete, MessageBox, msg, MessageBox.TYPE_YESNO)

    def confirm_delete(self, answer):
        if not answer or not self.games:
            return
        g = self.games[self.index]
        ok, msg = self.core.delete_game(g)
        if ok:
            self.changed = True
            self.games.pop(self.index)
            if self.index >= len(self.games) and self.index > 0:
                self.index -= 1
            if self.scroll > 0 and self.scroll >= len(self.games):
                self.scroll = max(0, len(self.games) - MANAGER_ROWS)
            self.refresh()
        else:
            self.session.open(MessageBox, msg, MessageBox.TYPE_ERROR, timeout=5)

    def key_exit(self):
        self.close(self.changed)


# ==============================================================================
# 8. شاشة الإعدادات الشاملة (Settings)
# ==============================================================================
SETTINGS_ROWS = 9


def build_settings_skin(dw, dh):
    scale = dw / 1920.0
    def s(v): return max(1, int(round(v * scale)))
    rows_xml = ""
    for i in range(SETTINGS_ROWS):
        y = s(135) + i * s(88)
        rows_xml += f"""
        <widget name="set_bg_{i}" position="{s(180)},{y}" size="{s(1560)},{s(74)}" zPosition="1" alphatest="blend" />
        <widget name="set_name_{i}" position="{s(210)},{y+s(14)}" size="{s(900)},{s(46)}" font="Regular;{s(25)}" foregroundColor="#FFFFFF" backgroundColor="#141A29" transparent="1" noWrap="1" zPosition="3" />
        <widget name="set_val_{i}" position="{s(1120)},{y+s(16)}" size="{s(590)},{s(42)}" font="Regular;{s(23)}" halign="right" foregroundColor="#3DDC84" backgroundColor="#141A29" transparent="1" zPosition="3" />
        """
    return f"""
    <screen name="DroidPlaySettings" position="0,0" size="{dw},{dh}" flags="wfNoBorder" backgroundColor="#0A0D16">
        <widget name="header" position="{s(180)},{s(42)}" size="{s(950)},{s(50)}" font="Regular;{s(34)}" foregroundColor="#3DDC84" backgroundColor="#0A0D16" transparent="1" zPosition="2" />
        <widget name="gp_badge" position="{s(1140)},{s(48)}" size="{s(600)},{s(40)}" font="Regular;{s(22)}" halign="right" foregroundColor="#94A3B8" backgroundColor="#0A0D16" transparent="1" zPosition="2" />
        {rows_xml}
        <widget name="hint" position="{s(180)},{s(985)}" size="{s(1560)},{s(40)}" font="Regular;{s(22)}" foregroundColor="#94A3B8" backgroundColor="#0A0D16" transparent="1" zPosition="2" />
    </screen>
    """


class DroidPlaySettings(Screen):
    RES_OPTS = ["1280x720", "1920x1080", "640x480", "auto"]
    ASP_OPTS = ["auto", "4:3", "16:9"]
    DB_OPTS = ["0.0", "3.0", "-3.0", "-6.0", "-12.0"]

    def __init__(self, session, core, settings, current_game, gamepad, gp_queue, dw, dh, tag):
        self.core = core
        self.settings = dict(settings)
        self.current_game = current_game
        self.gamepad = gamepad
        self.gp_queue = gp_queue
        self.dw = dw
        self.dh = dh
        self.tag = tag

        self.skin = build_settings_skin(dw, dh)
        Screen.__init__(self, session)

        self.index = 0
        self.scroll = 0
        self.library_dirty = False
        self.waiting_capture_key = None

        self["header"] = Label("إعدادات الأداء والصوت • TAOUFIK GAMES E2")
        self["gp_badge"] = Label("")
        self["hint"] = Label("يمين / يسار: تغيير القيمة   •   OK: تنفيذ   •   EXIT: حفظ والعودة")

        for i in range(SETTINGS_ROWS):
            self[f"set_bg_{i}"] = Pixmap()
            self[f"set_name_{i}"] = Label("")
            self[f"set_val_{i}"] = Label("")

        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": self.key_ok,
            "cancel": self.key_exit,
            "up": self.key_up,
            "down": self.key_down,
            "left": self.key_left,
            "right": self.key_right,
        }, -2)

        self.gp_timer = eTimer()
        self.gp_timer.callback.append(self.poll_gamepad)
        self.onLayoutFinish.append(self.startup)

    def startup(self):
        self.pix_n = LoadPixmap(os.path.join(ASSET_DIR, f"set_n_{self.tag}.png"))
        self.pix_f = LoadPixmap(os.path.join(ASSET_DIR, f"set_f_{self.tag}.png"))
        self.gp_timer.start(50, False)
        self.refresh()

    def _get_master_vol(self):
        try:
            return eDVBVolumecontrol.getInstance().getVolume()
        except Exception:
            return 80

    def _step_master_vol(self, delta):
        try:
            vc = eDVBVolumecontrol.getInstance()
            v = max(0, min(100, vc.getVolume() + delta))
            vc.setVolume(v, v)
        except Exception:
            pass

    def _items(self):
        res_labels = {"1280x720": "1280x720 (موصى به للأداء السلس)", "1920x1080": "1920x1080 (Full HD ثقيل)", "640x480": "640x480 (سرعة قصوى)", "auto": "تلقائي (Auto)"}
        asp_labels = {"auto": "تلقائي (Core)", "4:3": "4:3 (كلاسيكي)", "16:9": "16:9 (شاشة كاملة)"}
        db_labels = {"0.0": "قياسي (100%)", "3.0": "مرتفع (+3dB)", "-3.0": "متوسط (-3dB)", "-6.0": "هادئ (-6dB)", "-12.0": "منخفض (-12dB)"}
        rlay_labels = {"colors": "ألوان الريموت (Red/Green/Yellow/Blue)", "numbers": "الأرقام (1 / 2 / 3 / 4 / 5 / 0)"}

        ok_lbl = f"زر #{self.settings['btn_ok']}" if self.settings.get("btn_ok") is not None else "افتراضي"
        ex_lbl = f"زر #{self.settings['btn_exit']}" if self.settings.get("btn_exit") is not None else "افتراضي"
        mn_lbl = f"زر #{self.settings['btn_menu']}" if self.settings.get("btn_menu") is not None else "افتراضي"

        items = [
            ("res", "دقة عرض اللعبة (Resolution)", f"◀  {res_labels.get(self.settings['resolution'])}  ▶"),
            ("aspect", "نسبة عرض الشاشة (Aspect Ratio)", f"◀  {asp_labels.get(self.settings['aspect'])}  ▶"),
            ("audio_sync", "مزامنة الصوت لمنع التقليق (Audio Sync)", f"◀  {'مفعّل (موصى به)' if self.settings.get('audio_sync', True) else 'معطّل'}  ▶"),
            ("hard_sync", "تسريع معالجة الرسوميات (Hard GPU Sync)", f"◀  {'مفعّل (أداء أسرع)' if self.settings.get('hard_sync', True) else 'معطّل'}  ▶"),
            ("smooth", "تنعيم الصورة (Bilinear Filter)", f"◀  {'مفعّل' if self.settings['smooth'] else 'معطّل (أسرع)'}  ▶"),
            ("fps", "إظهار عداد الإطارات (Show FPS)", f"◀  {'نعم' if self.settings['fps'] else 'لا'}  ▶"),
            ("mvol", "مستوى صوت الرسيفر العام", f"◀  {self._get_master_vol()}%  ▶"),
            ("db", "قوة صوت المحاكي (Audio Gain)", f"◀  {db_labels.get(self.settings['audio_db'], '0.0')}  ▶"),
            ("mute", "كتم صوت المحاكي (Audio Mute)", f"◀  {'مكتوم' if self.settings['audio_mute'] else 'يعمل'}  ▶"),
            ("rpad", "استخدام الريموت كيد تحكم", f"◀  {'مفعّل' if self.settings.get('remote_pad', True) else 'معطّل'}  ▶"),
            ("rlay", "توزيع أزرار الريموت للعب", f"◀  {rlay_labels.get(self.settings.get('remote_layout', 'colors'))}  ▶"),
            ("rmap", "عرض خريطة أزرار الريموت داخل اللعبة", "[OK للعرض]"),
            ("swap", "وضع أزرار اليد الفيزيائية (A / B)", f"◀  {'معكوس (B=OK)' if self.settings['swap_ab'] else 'قياسي (A=OK)'}  ▶"),
            ("map_ok", "تخصيص زر التشغيل في اليد (OK)", f"{'اضغط أي زر باليد...' if self.waiting_capture_key == 'btn_ok' else ok_lbl + '  [OK لتعيين]'}"),
            ("map_exit", "تخصيص زر الرجوع في اليد (Back)", f"{'اضغط أي زر باليد...' if self.waiting_capture_key == 'btn_exit' else ex_lbl + '  [OK لتعيين]'}"),
            ("map_menu", "تخصيص زر القائمة في اليد (Menu)", f"{'اضغط أي زر باليد...' if self.waiting_capture_key == 'btn_menu' else mn_lbl + '  [OK لتعيين]'}"),
            ("map_reset", "إعادة ضبط أزرار اليد للافتراضي", "[OK للتنفيذ]"),
        ]

        if self.current_game:
            items.append(("del_cur", f"حذف اللعبة الحالية: {self.current_game['title'][:26]}", "[OK للحذف]"))

        items.append(("mgr", "إدارة وحذف جميع الألعاب (Game Manager)", "[OK للفتح]"))
        items.append(("rescan", "تحديث قائمة الألعاب (Rescan Library)", "[OK للتحديث]"))
        return items

    def refresh(self):
        if self.gamepad.connected:
            self["gp_badge"].setText(f"●  {self.gamepad.device_name[:26]}")
            self["gp_badge"].instance.setForegroundColor(gRGB(0x3DDC84))
        else:
            self["gp_badge"].setText("○  No USB Pad  •  TAOUFIK GAMES E2")
            self["gp_badge"].instance.setForegroundColor(gRGB(0x94A3B8))

        items = self._items()
        total = len(items)
        for i in range(SETTINGS_ROWS):
            idx = self.scroll + i
            if idx < total:
                key, name, val = items[idx]
                self[f"set_bg_{i}"].show()
                self[f"set_name_{i}"].show()
                self[f"set_val_{i}"].show()
                pix = self.pix_f if idx == self.index else self.pix_n
                if pix:
                    self[f"set_bg_{i}"].instance.setPixmap(pix)
                self[f"set_name_{i}"].setText(name)
                self[f"set_val_{i}"].setText(val)
                if key == "del_cur":
                    self[f"set_val_{i}"].instance.setForegroundColor(gRGB(0xF87171))
                else:
                    self[f"set_val_{i}"].instance.setForegroundColor(gRGB(0x3DDC84 if idx == self.index else 0xCBD5E1))
            else:
                self[f"set_bg_{i}"].hide()
                self[f"set_name_{i}"].hide()
                self[f"set_val_{i}"].hide()

    def poll_gamepad(self):
        while not self.gp_queue.empty():
            try:
                act = self.gp_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(act, tuple) and act[0] == "captured_btn":
                if self.waiting_capture_key:
                    self.settings[self.waiting_capture_key] = act[1]
                    self.gamepad.apply_settings(self.settings)
                    self.waiting_capture_key = None
                    self.refresh()
                continue
            if self.waiting_capture_key:
                continue
            if act == "up":
                self.key_up()
            elif act == "down":
                self.key_down()
            elif act == "left":
                self.key_left()
            elif act == "right":
                self.key_right()
            elif act == "ok":
                self.key_ok()
            elif act == "exit":
                self.key_exit()

    def key_up(self):
        if self.waiting_capture_key:
            return
        if self.index > 0:
            self.index -= 1
            if self.index < self.scroll:
                self.scroll = self.index
            self.refresh()

    def key_down(self):
        if self.waiting_capture_key:
            return
        items = self._items()
        if self.index + 1 < len(items):
            self.index += 1
            if self.index >= self.scroll + SETTINGS_ROWS:
                self.scroll = self.index - SETTINGS_ROWS + 1
            self.refresh()

    def _cycle(self, key, direction):
        if key == "res":
            i = (self.RES_OPTS.index(self.settings["resolution"]) + direction) % len(self.RES_OPTS)
            self.settings["resolution"] = self.RES_OPTS[i]
        elif key == "aspect":
            i = (self.ASP_OPTS.index(self.settings["aspect"]) + direction) % len(self.ASP_OPTS)
            self.settings["aspect"] = self.ASP_OPTS[i]
        elif key == "audio_sync":
            self.settings["audio_sync"] = not self.settings.get("audio_sync", True)
        elif key == "hard_sync":
            self.settings["hard_sync"] = not self.settings.get("hard_sync", True)
        elif key == "smooth":
            self.settings["smooth"] = not self.settings["smooth"]
        elif key == "fps":
            self.settings["fps"] = not self.settings["fps"]
        elif key == "mvol":
            self._step_master_vol(5 * direction)
        elif key == "db":
            i = (self.DB_OPTS.index(self.settings["audio_db"]) + direction) % len(self.DB_OPTS)
            self.settings["audio_db"] = self.DB_OPTS[i]
        elif key == "mute":
            self.settings["audio_mute"] = not self.settings["audio_mute"]
        elif key == "rpad":
            self.settings["remote_pad"] = not self.settings.get("remote_pad", True)
        elif key == "rlay":
            cur = self.settings.get("remote_layout", "colors")
            self.settings["remote_layout"] = "numbers" if cur == "colors" else "colors"
        elif key == "swap":
            self.settings["swap_ab"] = not self.settings["swap_ab"]
            self.gamepad.apply_settings(self.settings)

    def key_left(self):
        if self.waiting_capture_key:
            return
        key = self._items()[self.index][0]
        self._cycle(key, -1)
        self.refresh()

    def key_right(self):
        if self.waiting_capture_key:
            return
        key = self._items()[self.index][0]
        self._cycle(key, 1)
        self.refresh()

    def key_ok(self):
        if self.waiting_capture_key:
            self.waiting_capture_key = None
            self.gamepad.capture_mode = False
            self.refresh()
            return

        key = self._items()[self.index][0]
        if key in ("res", "aspect", "audio_sync", "hard_sync", "smooth", "fps", "db", "mute", "rpad", "rlay", "swap"):
            self._cycle(key, 1)
            self.refresh()
        elif key == "rmap":
            self.session.open(MessageBox, "خريطة الأزرار: زر OK للبدء، الأسهم للحركة، EXIT للخروج.", MessageBox.TYPE_INFO, timeout=10)
        elif key in ("map_ok", "map_exit", "map_menu"):
            if not self.gamepad.connected:
                self.session.open(MessageBox, "وصّل ذراع التحكم USB أولاً!", MessageBox.TYPE_INFO, timeout=3)
                return
            self.waiting_capture_key = "btn_" + key.split("_")[1]
            self.gamepad.capture_mode = True
            self.refresh()
        elif key == "map_reset":
            self.settings["btn_ok"] = self.settings["btn_exit"] = self.settings["btn_menu"] = None
            self.gamepad.apply_settings(self.settings)
            self.refresh()
        elif key == "mgr":
            self.gp_timer.stop()
            self.session.openWithCallback(self._on_mgr_closed, DroidPlayGameManager, self.core, self.dw, self.dh, self.tag)
        elif key == "rescan":
            self.library_dirty = True
            self.key_exit()

    def _on_mgr_closed(self, changed):
        if changed:
            self.library_dirty = True
        self.gp_timer.start(50, False)
        self.refresh()

    def key_exit(self):
        self.gamepad.capture_mode = False
        self.gp_timer.stop()
        save_settings(self.settings)
        write_retro_override(self.settings)
        self.gamepad.apply_settings(self.settings)
        self.close((self.settings, self.library_dirty))


# ==============================================================================
# 9. واجهة أندرويد الرئيسية (Android TV Launcher)
# ==============================================================================
def build_android_skin(dw, dh, nav_count):
    scale = dw / 1920.0
    def s(v): return max(1, int(round(v * scale)))
    nav_xml = ""
    nav_y = s(105)
    for idx in range(nav_count):
        y = nav_y + idx * s(62)
        nav_xml += f"""
        <widget name="nav_pill_{idx}" position="{s(36)},{y}" size="{s(270)},{s(56)}" zPosition="2" alphatest="blend" />
        <widget name="nav_btn_{idx}" position="{s(50)},{y}" size="{s(246)},{s(56)}" font="Regular;{s(23)}" halign="left" valign="center" foregroundColor="#94A3B8" backgroundColor="#141926" transparent="1" zPosition="3" />
        """

    cards_xml = ""
    sx, sy = s(370), s(386)
    gx, gy = s(380), s(272)
    for i in range(PAGE_CARDS):
        row, col = i // COLS, i % COLS
        x = sx + col * gx
        y = sy + row * gy
        cards_xml += f"""
        <widget name="card_bg_{i}" position="{x},{y}" size="{s(350)},{s(248)}" zPosition="2" alphatest="blend" />
        <widget name="card_cover_{i}" position="{x+s(16)},{y+s(14)}" size="{s(318)},{s(152)}" zPosition="3" alphatest="blend" scale="1" />
        <widget name="card_title_{i}" position="{x+s(18)},{y+s(174)}" size="{s(314)},{s(32)}" font="Regular;{s(23)}" foregroundColor="#F8FAFC" backgroundColor="#161C2C" transparent="1" noWrap="1" zPosition="4" />
        <widget name="card_sys_{i}" position="{x+s(18)},{y+s(208)}" size="{s(314)},{s(24)}" font="Regular;{s(17)}" foregroundColor="#3DDC84" backgroundColor="#161C2C" transparent="1" zPosition="4" />
        """

    return f"""
    <screen name="DroidPlayLauncher" position="0,0" size="{dw},{dh}" flags="wfNoBorder" backgroundColor="#0A0D16">
        <widget name="brand_logo" position="{s(36)},{s(26)}" size="{s(290)},{s(42)}" font="Regular;{s(23)}" foregroundColor="#3DDC84" backgroundColor="#0A0D16" transparent="1" zPosition="2" />
        <widget name="gamepad_status" position="{s(370)},{s(30)}" size="{s(550)},{s(38)}" font="Regular;{s(21)}" halign="left" foregroundColor="#94A3B8" backgroundColor="#0A0D16" transparent="1" zPosition="2" />
        <widget name="status_info" position="{s(1020)},{s(30)}" size="{s(850)},{s(38)}" font="Regular;{s(21)}" halign="right" foregroundColor="#CBD5E1" backgroundColor="#0A0D16" transparent="1" zPosition="2" />

        <widget name="sidebar_bg" position="{s(24)},{s(84)}" size="{s(294)},{s(945)}" zPosition="1" alphatest="blend" />
        {nav_xml}

        <widget name="hero_bg" position="{s(370)},{s(96)}" size="{s(1500)},{s(215)}" zPosition="1" alphatest="blend" />
        <widget name="hero_title" position="{s(405)},{s(118)}" size="{s(1060)},{s(48)}" font="Regular;{s(36)}" foregroundColor="#FFFFFF" backgroundColor="#181F30" transparent="1" noWrap="1" zPosition="3" />
        <widget name="hero_meta" position="{s(405)},{s(176)}" size="{s(1060)},{s(36)}" font="Regular;{s(22)}" foregroundColor="#3DDC84" backgroundColor="#181F30" transparent="1" zPosition="3" />
        <widget name="hero_desc" position="{s(405)},{s(220)}" size="{s(1060)},{s(68)}" font="Regular;{s(20)}" foregroundColor="#94A3B8" backgroundColor="#181F30" transparent="1" zPosition="3" />
        <widget name="hero_cover" position="{s(1515)},{s(126)}" size="{s(318)},{s(152)}" zPosition="3" alphatest="blend" scale="1" />

        <widget name="section_title" position="{s(370)},{s(334)}" size="{s(1480)},{s(38)}" font="Regular;{s(25)}" foregroundColor="#E2E8F0" backgroundColor="#0A0D16" transparent="1" zPosition="2" />

        {cards_xml}

        <widget name="action_hint" position="{s(370)},{s(990)}" size="{s(1490)},{s(38)}" font="Regular;{s(22)}" foregroundColor="#E2E8F0" backgroundColor="#0A0D16" transparent="1" zPosition="2" />
    </screen>
    """


class DroidPlayLauncher(Screen):
    def __init__(self, session):
        d = getDesktop(0).size()
        self.dw, self.dh = d.width(), d.height()
        self.scale = self.dw / 1920.0
        self.tag = f"{self.dw}x{self.dh}"

        self.settings = load_settings()
        self.nav_categories = [
            ("all", "All Games"),
            ("neogeo", "Neo Geo"),
            ("arcade", "Arcade / MAME"),
            ("ps2", "PlayStation 2"),
            ("dreamcast", "Sega Dreamcast"),
        ]

        ensure_android_assets(self.scale)
        self.skin = build_android_skin(self.dw, self.dh, len(self.nav_categories))
        Screen.__init__(self, session)

        self.core = EmulatorCore()
        self.focus_zone = "grid"
        self.nav_index = 0
        self.grid_index = 0
        self.scroll_row = 0
        self.games = []
        self._cover_cache = OrderedDict()
        self._cached_ip = self._get_ip()

        self["sidebar_bg"] = Pixmap()
        self["hero_bg"] = Pixmap()
        self["hero_cover"] = Pixmap()
        self["brand_logo"] = Label("TAOUFIK GAMES E2")
        self["gamepad_status"] = Label("Gamepad: Scanning...")
        self["status_info"] = Label("")
        self["hero_title"] = Label("")
        self["hero_meta"] = Label("")
        self["hero_desc"] = Label("")
        self["section_title"] = Label("Library")
        self["action_hint"] = Label("OK: تشغيل  •  MENU: الإعدادات والأداء  •  INFO: معلومات  •  EXIT: خروج")

        for i in range(len(self.nav_categories)):
            self[f"nav_pill_{i}"] = Pixmap()
            self[f"nav_btn_{i}"] = Label(self.nav_categories[i][1])

        for i in range(PAGE_CARDS):
            self[f"card_bg_{i}"] = Pixmap()
            self[f"card_cover_{i}"] = Pixmap()
            self[f"card_title_{i}"] = Label("")
            self[f"card_sys_{i}"] = Label("")

        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "MenuActions", "InfobarEPGActions"], {
            "ok": self.key_ok,
            "cancel": self.key_exit,
            "up": self.key_up,
            "down": self.key_down,
            "left": self.key_left,
            "right": self.key_right,
            "menu": self.open_settings_menu,
            "showEventInfo": self.show_system_info,
        }, -2)

        self.gp_queue = queue.Queue()
        self.gamepad = GamepadReader(self.gp_queue, self.settings)

        self.status_timer = eTimer()
        self.status_timer.callback.append(self.update_status_bar)

        self.gp_timer = eTimer()
        self.gp_timer.callback.append(self.poll_gamepad_queue)

        self.onLayoutFinish.append(self.startup)

    def _load_cached_pixmap(self, path):
        if not path:
            return None
        if path in self._cover_cache:
            self._cover_cache.move_to_end(path)
            return self._cover_cache[path]
        pix = LoadPixmap(path)
        if pix:
            self._cover_cache[path] = pix
            if len(self._cover_cache) > 40:
                self._cover_cache.popitem(last=False)
        return pix

    def startup(self):
        self.pix_rail = LoadPixmap(os.path.join(ASSET_DIR, f"rail_{self.tag}.png"))
        self.pix_pill = LoadPixmap(os.path.join(ASSET_DIR, f"nav_pill_{self.tag}.png"))
        self.pix_hero = LoadPixmap(os.path.join(ASSET_DIR, f"hero_{self.tag}.png"))
        self.pix_norm = LoadPixmap(os.path.join(ASSET_DIR, f"card_n_{self.tag}.png"))
        self.pix_focus = LoadPixmap(os.path.join(ASSET_DIR, f"card_f_{self.tag}.png"))
        self.pix_def_cov = LoadPixmap(os.path.join(ASSET_DIR, f"cover_v2_{self.tag}.png"))

        if self.pix_rail:
            self["sidebar_bg"].instance.setPixmap(self.pix_rail)
        if self.pix_hero:
            self["hero_bg"].instance.setPixmap(self.pix_hero)

        self.gamepad.start()
        self.update_status_bar()
        self.status_timer.start(3000, False)
        self.gp_timer.start(45, False)
        self.load_category("all")

    def poll_gamepad_queue(self):
        while not self.gp_queue.empty():
            try:
                act = self.gp_queue.get_nowait()
            except queue.Empty:
                break
            if act == "up":
                self.key_up()
            elif act == "down":
                self.key_down()
            elif act == "left":
                self.key_left()
            elif act == "right":
                self.key_right()
            elif act == "ok":
                self.key_ok()
            elif act == "exit":
                self.key_exit()
            elif act == "menu":
                self.open_settings_menu()

    def update_status_bar(self):
        now = time.strftime("%H:%M")
        temp = self._get_cpu_temp()
        self["status_info"].setText(f"{self._cached_ip}   •   CPU {temp}   •   {now}")

        if self.gamepad.connected:
            self["gamepad_status"].setText(f"●  {self.gamepad.device_name[:22]}")
            self["gamepad_status"].instance.setForegroundColor(gRGB(0x3DDC84))
        elif self.settings.get("remote_pad", True):
            self["gamepad_status"].setText("●  TAOUFIK GAMES E2")
            self["gamepad_status"].instance.setForegroundColor(gRGB(0x4F8BFF))
        else:
            self["gamepad_status"].setText("○  No Gamepad")
            self["gamepad_status"].instance.setForegroundColor(gRGB(0x64748B))

    def _get_cpu_temp(self):
        for p in ("/proc/stb/fp/temp_sensor_avs", "/sys/class/thermal/thermal_zone0/temp"):
            if os.path.exists(p):
                try:
                    num = int(open(p).read().strip())
                    return f"{num // 1000 if num > 1000 else num}°C"
                except Exception:
                    pass
        return "--°C"

    def _get_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "Offline"

    def load_category(self, sys_key):
        self.games = self.core.scan_games(sys_key, self.tag)
        self.grid_index = 0
        self.scroll_row = 0
        if not self.games:
            self.focus_zone = "sidebar"
        self.refresh_ui()

    def open_settings_menu(self):
        self.gp_timer.stop()
        cur_game = self.games[self.grid_index] if (self.focus_zone == "grid" and self.games and self.grid_index < len(self.games)) else None
        self.session.openWithCallback(
            self.on_settings_closed,
            DroidPlaySettings,
            self.core,
            self.settings,
            cur_game,
            self.gamepad,
            self.gp_queue,
            self.dw,
            self.dh,
            self.tag,
        )

    def on_settings_closed(self, result):
        if result:
            new_settings, dirty = result
            self.settings = new_settings
            self.gamepad.apply_settings(self.settings)
            if dirty:
                self._cover_cache.clear()
                self.load_category(self.nav_categories[self.nav_index][0])
        self.gp_timer.start(45, False)
        self.refresh_ui()

    def refresh_ui(self):
        for i, (_, label) in enumerate(self.nav_categories):
            pill = self[f"nav_pill_{i}"]
            btn = self[f"nav_btn_{i}"]
            if i == self.nav_index:
                if self.pix_pill:
                    pill.instance.setPixmap(self.pix_pill)
                pill.show()
                if self.focus_zone == "sidebar":
                    btn.setText(f"▶  {label}")
                    btn.instance.setForegroundColor(gRGB(0x3DDC84))
                else:
                    btn.setText(f"●  {label}")
                    btn.instance.setForegroundColor(gRGB(0xFFFFFF))
            else:
                pill.hide()
                btn.setText(f"   {label}")
                btn.instance.setForegroundColor(gRGB(0x8A94A6))

        cat_name = self.nav_categories[self.nav_index][1]
        if self.games:
            self["section_title"].setText(f"{cat_name}   •   {self.grid_index + 1} / {len(self.games)}")
        else:
            self["section_title"].setText(f"{cat_name}   •   0 Games")

        start_idx = self.scroll_row * COLS
        for slot in range(PAGE_CARDS):
            idx = start_idx + slot
            if idx < len(self.games):
                g = self.games[idx]
                is_focused = (self.focus_zone == "grid" and idx == self.grid_index)
                self[f"card_bg_{slot}"].show()
                self[f"card_cover_{slot}"].show()
                self[f"card_title_{slot}"].show()
                self[f"card_sys_{slot}"].show()

                if is_focused and self.pix_focus:
                    self[f"card_bg_{slot}"].instance.setPixmap(self.pix_focus)
                elif self.pix_norm:
                    self[f"card_bg_{slot}"].instance.setPixmap(self.pix_norm)

                cov = self._load_cached_pixmap(g["cover_path"]) or self.pix_def_cov
                if cov:
                    self[f"card_cover_{slot}"].instance.setPixmap(cov)
                self[f"card_title_{slot}"].setText(g["title"])
                self[f"card_sys_{slot}"].setText(f"{g['system_name']}  •  {g['size']}")
            else:
                self[f"card_bg_{slot}"].hide()
                self[f"card_cover_{slot}"].hide()
                self[f"card_title_{slot}"].hide()
                self[f"card_sys_{slot}"].hide()

        if self.games and self.grid_index < len(self.games):
            g = self.games[self.grid_index]
            core_state = "Core Ready ✓" if g["core_ok"] else "Core Missing ✗"
            res_tag = self.settings.get("resolution", "1280x720").upper()
            self["hero_title"].setText(g["title"])
            self["hero_meta"].setText(f"{g['system_name']}   •   {g['size']}   •   {core_state}   •   {res_tag}")
            self["hero_desc"].setText(f"الملف: {g['filename']}\nالنواة: {os.path.basename(g['core_path'])}")
            cov = self._load_cached_pixmap(g["cover_path"]) or self.pix_def_cov
            if cov:
                self["hero_cover"].instance.setPixmap(cov)
            self["hero_cover"].show()
        else:
            self["hero_title"].setText("لا توجد ألعاب في هذا القسم")
            self["hero_meta"].setText(f"{cat_name}   •   RetroArch Ready")
            self["hero_desc"].setText(f"ضع الملفات داخل: {ROMS_BASE}/{self.nav_categories[self.nav_index][0]}/")
            if self.pix_def_cov:
                self["hero_cover"].instance.setPixmap(self.pix_def_cov)

    def _sync_scroll(self):
        cur_row = self.grid_index // COLS
        if cur_row < self.scroll_row:
            self.scroll_row = cur_row
        elif cur_row >= self.scroll_row + ROWS:
            self.scroll_row = cur_row - ROWS + 1

    def key_up(self):
        if self.focus_zone == "sidebar":
            if self.nav_index > 0:
                self.nav_index -= 1
                self.load_category(self.nav_categories[self.nav_index][0])
        elif self.grid_index - COLS >= 0:
            self.grid_index -= COLS
            self._sync_scroll()
            self.refresh_ui()

    def key_down(self):
        if self.focus_zone == "sidebar":
            if self.nav_index < len(self.nav_categories) - 1:
                self.nav_index += 1
                self.load_category(self.nav_categories[self.nav_index][0])
        else:
            if self.grid_index + COLS < len(self.games):
                self.grid_index += COLS
                self._sync_scroll()
                self.refresh_ui()
            elif (self.grid_index // COLS) < ((len(self.games) - 1) // COLS):
                self.grid_index = len(self.games) - 1
                self._sync_scroll()
                self.refresh_ui()

    def key_left(self):
        if self.focus_zone == "grid":
            if self.grid_index % COLS == 0:
                self.focus_zone = "sidebar"
            else:
                self.grid_index -= 1
            self.refresh_ui()

    def key_right(self):
        if self.focus_zone == "sidebar":
            if self.games:
                self.focus_zone = "grid"
                self.refresh_ui()
        elif self.grid_index + 1 < len(self.games):
            self.grid_index += 1
            self._sync_scroll()
            self.refresh_ui()

    def key_ok(self):
        if self.focus_zone == "sidebar":
            if self.games:
                self.focus_zone = "grid"
                self.refresh_ui()
            return
        if self.games and self.grid_index < len(self.games):
            game = self.games[self.grid_index]
            ok, msg = self.core.launch_game(self.session, self, game, self.settings, self.on_game_finished, self.gamepad)
            if not ok:
                self.session.open(MessageBox, msg, MessageBox.TYPE_INFO, timeout=6)

    def show_system_info(self):
        ra = self.core.find_retroarch() or "غير مثبت"
        cfg = self.core.find_config() or "افتراضي"
        lines = [f"RetroArch: {ra}", f"Config: {cfg}", f"uinput: {check_uinput_ready()}", ""]
        for _, info in SYSTEMS.items():
            cpath, ok = self.core.find_core(info["cores"])
            lines.append(f"{'✓' if ok else '✗'} {info['name']}: {os.path.basename(cpath)}")
        self.session.open(MessageBox, "\n".join(lines), MessageBox.TYPE_INFO, timeout=10)

    def on_game_finished(self, retval):
        self.refresh_ui()

    def key_exit(self):
        self.status_timer.stop()
        self.gp_timer.stop()
        self.gamepad.stop()
        if self.core._remote_watcher:
            self.core._remote_watcher.stop()
        self._cover_cache.clear()
        if self.core.prev_service:
            self.session.nav.playService(self.core.prev_service)
        self.close()


def main(session, **kwargs):
    session.open(DroidPlayLauncher)


def Plugins(**kwargs):
    return [
        PluginDescriptor(
            name="TAOUFIK GAMES E2",
            description="واجهة ألعاب TAOUFIK GAMES E2 الشاملة والاحترافية",
            where=[PluginDescriptor.WHERE_PLUGINMENU, PluginDescriptor.WHERE_EXTENSIONSMENU],
            fnc=main,
        )
    ]
