#!/usr/bin/env python3
"""laptop-fixes: спеціальні клавіші ASUS ExpertBook B1400CEAE під Linux Mint (Cinnamon, X11).

Кожен фікс вмикається/вимикається окремо:  laptop-fixes list|status|enable|disable|gui
Частини, що потребують root, виконуються через sudo (CLI) або pkexec (GUI) лише після підтвердження.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
USER_BIN = os.path.join(HOME, ".local/bin/laptop-fixes")
STATE_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config")),
                          "laptop-fixes/state.json")

ROOT_LIB = "/usr/local/lib/laptop-fixes"
ROOT_SCRIPT = f"{ROOT_LIB}/laptop_fixes.py"
VAR_DIR = "/var/lib/laptop-fixes"
RUN_DIR = "/run/laptop-fixes"

FN_UNIT = "laptop-fixes-fnlock.service"
FN_MODPROBE = "/etc/modprobe.d/laptop-fixes-fnlock.conf"
FN_STATE = f"{VAR_DIR}/fnlock"
FN_RUNSTATE = f"{RUN_DIR}/fnlock"
FN_PARAM = "/sys/module/asus_wmi/parameters/fnlock_default"
FN_EVENT_CODE = 0x4E                      # NOTIFY_FNLOCK_TOGGLE в asus-wmi
TRACE = "/sys/kernel/tracing"
KPROBE = "laptop_fixes/fnlock_key"

MICLED_UNIT = "laptop-fixes-micled.service"
CAM_RULE = "/etc/udev/rules.d/70-laptop-fixes-camera.rules"

MEDIA = "org.cinnamon.desktop.keybindings.media-keys"
KB = "org.cinnamon.desktop.keybindings"
CUSTOM_SCHEMA = "org.cinnamon.desktop.keybindings.custom-keybinding"
CUSTOM_PATH = "/org/cinnamon/desktop/keybindings/custom-keybindings/{}/"

NC_SOURCE = "lf_nc_source"
NC_SINK = "lf_nc_sink"


# ---------------------------------------------------------------- helpers

def run(*cmd, check=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()}")
    return r.stdout.strip()


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def sysfs_write(path, text):
    with open(path, "w") as f:
        f.write(text)


def rm(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    write(STATE_FILE, json.dumps(st, indent=2, ensure_ascii=False) + "\n")


def gget(schema, key):
    out = run("gsettings", "get", schema, key)
    if out.startswith("@as "):
        out = out[4:]
    return eval(out, {"__builtins__": {}}, {"true": True, "false": False})


def gset(schema, key, value):
    run("gsettings", "set", schema, key, repr(value), check=True)


def osd(icon, label):
    run("gdbus", "call", "--session", "--dest", "org.Cinnamon", "--object-path", "/org/Cinnamon",
        "--method", "org.Cinnamon.ShowOSD", f"{{'icon': <'{icon}'>, 'label': <'{label}'>}}")


def unit_active(unit):
    return run("systemctl", "is-active", unit) == "active"


# ------------------------------------------------- Cinnamon keybinding parts

def add_custom(cid, name, binding, command):
    path = CUSTOM_PATH.format(cid)
    gset(f"{CUSTOM_SCHEMA}:{path}", "name", name)
    gset(f"{CUSTOM_SCHEMA}:{path}", "command", command)
    gset(f"{CUSTOM_SCHEMA}:{path}", "binding", binding if isinstance(binding, list) else [binding])
    lst = gget(KB, "custom-list")
    if cid in lst:
        # Cinnamon перечитує прив'язки лише при зміні custom-list, тому перезаписуємо запис у списку
        gset(KB, "custom-list", [x for x in lst if x != cid])
        time.sleep(0.5)
        gset(KB, "custom-list", lst)
    else:
        gset(KB, "custom-list", lst + [cid])


def del_custom(cid):
    lst = gget(KB, "custom-list")
    if cid in lst:
        gset(KB, "custom-list", [x for x in lst if x != cid])
    run("dconf", "reset", "-f", CUSTOM_PATH.format(cid))


def has_custom(cid, binding=None):
    if cid not in gget(KB, "custom-list"):
        return False
    return binding is None or gget(f"{CUSTOM_SCHEMA}:{CUSTOM_PATH.format(cid)}", "binding") == binding


def list_add(schema, key, item):
    cur = gget(schema, key)
    if item not in cur:
        gset(schema, key, cur + [item])


def list_remove(schema, key, item):
    cur = gget(schema, key)
    if item in cur:
        gset(schema, key, [x for x in cur if x != item])


def clear_saving(schema, key, st_key):
    """Очищає прив'язку, запам'ятовуючи оригінал для відкату."""
    st = load_state()
    cur = gget(schema, key)
    if cur:
        st.setdefault("saved", {})[st_key] = cur
        save_state(st)
        gset(schema, key, [])


def restore_saved(schema, key, st_key):
    st = load_state()
    saved = st.get("saved", {}).pop(st_key, None)
    if saved is not None and not gget(schema, key):
        gset(schema, key, saved)
    save_state(st)


# --------------------------------------------------------------- the fixes

class Fix:
    name = title = desc = ""
    root_desc = ""            # що саме зміниться з правами root (порожньо — root не потрібен)

    def user_enable(self): pass
    def user_disable(self): pass
    def user_enabled(self): return True
    def root_enable(self, args): pass
    def root_disable(self): pass
    def root_enabled(self): return True
    def root_args(self): return []
    def detail(self): return ""

    def enabled(self):
        return self.user_enabled() and self.root_enabled()

    def partial(self):
        has_user = type(self).user_enable is not Fix.user_enable
        return bool(self.root_desc) and has_user and self.user_enabled() != self.root_enabled()


class FnLock(Fix):
    name = "fnlock"
    title = "Fn-lock (Fn+Esc) зберігається після перезавантаження"
    desc = ("Драйвер asus-wmi під час кожного завантаження ставить Fn-lock у fnlock_default=Y. "
            "Служба стежить за Fn+Esc через kprobe і записує стан в /etc/modprobe.d, тому після "
            "перезавантаження драйвер відновлює саме ваш стан.")
    root_desc = (f"systemd-служба {FN_UNIT}, файл {FN_MODPROBE}, каталог {VAR_DIR}, "
                 f"копія програми в {ROOT_LIB}")

    def root_enabled(self):
        return os.path.exists(f"/etc/systemd/system/{FN_UNIT}")

    def root_enable(self, args):
        state = read(FN_RUNSTATE) or read(FN_PARAM, "Y")
        fn_save(state)
        write(f"/etc/systemd/system/{FN_UNIT}", f"""[Unit]
Description=laptop-fixes: remember ASUS Fn-lock (Fn+Esc) state across reboots
After=systemd-modules-load.service systemd-udevd.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 -I {ROOT_SCRIPT} fnlockd
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
""")
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", FN_UNIT, check=True)

    def root_disable(self):
        run("systemctl", "disable", "--now", FN_UNIT)
        rm(f"/etc/systemd/system/{FN_UNIT}")
        run("systemctl", "daemon-reload")
        for p in (FN_MODPROBE, FN_STATE, FN_RUNSTATE):
            rm(p)

    def detail(self):
        cur = read(FN_RUNSTATE)
        boot = read(FN_STATE)
        parts = []
        if cur:
            parts.append(f"поточний стан: {fn_human(cur)}")
        if boot:
            parts.append(f"після завантаження: {fn_human(boot)}")
        if self.root_enabled():
            parts.append("служба " + ("працює" if unit_active(FN_UNIT) else "НЕ працює"))
        return "; ".join(parts)


def fn_human(v):
    return {"Y": "увімкнено (Y)", "N": "вимкнено (N)"}.get(v, v)


def fn_save(state, runtime=True):
    write(FN_MODPROBE, "# laptop-fixes: стан Fn-lock (Fn+Esc), оновлюється автоматично\n"
                       f"options asus_wmi fnlock_default={state}\n")
    write(FN_STATE, state + "\n")
    if runtime:
        write(FN_RUNSTATE, state + "\n")


class MicMute(Fix):
    name = "micmute"
    title = "Клавіша мікрофона вимикає всі мікрофони, світлодіод показує стан"
    desc = ("Cinnamon вимикає лише типове джерело (зараз це мікрофон гарнітури), а вбудований "
            "DMIC лишається увімкненим, тому світлодіод не горить. Фікс перехоплює XF86AudioMicMute: "
            "вимикає або вмикає всі мікрофони разом і показує OSD. Світлодіод також прив'язується до "
            "входу гарнітури, тож горить лише тоді, коли вимкнені всі мікрофони.")
    root_desc = f"systemd-служба {MICLED_UNIT} (прив'язує ALSA «Capture Switch» до світлодіода micmute)"
    cid = "laptop-fixes-micmute"

    def user_enabled(self):
        return has_custom(self.cid)

    def user_enable(self):
        clear_saving(MEDIA, "mic-mute", "mic-mute")
        add_custom(self.cid, "laptop-fixes: мікрофон", "XF86AudioMicMute", f"{USER_BIN} action micmute")

    def user_disable(self):
        del_custom(self.cid)
        restore_saved(MEDIA, "mic-mute", "mic-mute")

    def root_enabled(self):
        return os.path.exists(f"/etc/systemd/system/{MICLED_UNIT}")

    def root_enable(self, args):
        write(f"/etc/systemd/system/{MICLED_UNIT}", f"""[Unit]
Description=laptop-fixes: mic-mute LED follows all internal capture switches
After=sound.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 -I {ROOT_SCRIPT} micled attach
ExecStop=/usr/bin/python3 -I {ROOT_SCRIPT} micled detach

[Install]
WantedBy=sound.target multi-user.target
""")
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", MICLED_UNIT, check=True)

    def root_disable(self):
        run("systemctl", "disable", "--now", MICLED_UNIT)
        rm(f"/etc/systemd/system/{MICLED_UNIT}")
        run("systemctl", "daemon-reload")

    def detail(self):
        led = read("/sys/class/leds/platform::micmute/brightness", "?")
        muted = mic_all_muted()
        return f"мікрофони: {'вимкнені' if muted else 'увімкнені'}; світлодіод: {'горить' if led == '1' else 'не горить'}"


class LockKey(Fix):
    name = "lock-key"
    title = "F9 (замок, Super+L) блокує екран"
    desc = ("Клавіша надсилає Super+L, а в Cinnamon Super+L відкриває Looking Glass "
            "(налагоджувач). Фікс знімає Super+L з Looking Glass і прив'язує його до блокування екрана.")

    def user_enabled(self):
        return "<Super>l" in gget(MEDIA, "screensaver")

    def user_enable(self):
        clear_saving(KB, "looking-glass-keybinding", "looking-glass-keybinding")
        list_add(MEDIA, "screensaver", "<Super>l")

    def user_disable(self):
        list_remove(MEDIA, "screensaver", "<Super>l")
        restore_saved(KB, "looking-glass-keybinding", "looking-glass-keybinding")


class ScreenshotKey(Fix):
    name = "screenshot-key"
    title = "F11 (Super+Shift+S) робить знімок області в буфер обміну"
    desc = "Як у Windows: виділяєте область, і знімок потрапляє в буфер обміну."

    def user_enabled(self):
        return "<Super><Shift>s" in gget(MEDIA, "area-screenshot-clip")

    def user_enable(self):
        list_add(MEDIA, "area-screenshot-clip", "<Super><Shift>s")

    def user_disable(self):
        list_remove(MEDIA, "area-screenshot-clip", "<Super><Shift>s")


class CameraKey(Fix):
    name = "camera"
    title = "F10 (камера) вимикає/вмикає вебкамеру з OSD"
    desc = ("XF86WebCam ніде не був прив'язаний. Фікс вимикає камеру на рівні USB (authorized=0): "
            "програми її не бачать, доки ви знову не натиснете клавішу. Після перезавантаження камера знову увімкнена.")
    cid = "laptop-fixes-camera"

    def __init__(self):
        self.root_desc = f"udev-правило {CAM_RULE} (дозволяє групі plugdev вмикати/вимикати камеру)"

    def user_enabled(self):
        return has_custom(self.cid)

    def user_enable(self):
        add_custom(self.cid, "laptop-fixes: камера", "XF86WebCam", f"{USER_BIN} action camera")

    def user_disable(self):
        cam_set(True)
        del_custom(self.cid)

    def root_enabled(self):
        return os.path.exists(CAM_RULE)

    def root_args(self):
        cid = cam_find_id()
        if not cid:
            raise RuntimeError("не знайдено USB-вебкамеру")
        return [cid]

    def root_enable(self, args):
        vid, pid = args[0].split(":")
        write(CAM_RULE, "# laptop-fixes: дозволити групі plugdev вимикати вебкамеру (USB authorized)\n"
              f'ACTION=="add|change", SUBSYSTEM=="usb", ENV{{DEVTYPE}}=="usb_device", '
              f'ATTR{{idVendor}}=="{vid}", ATTR{{idProduct}}=="{pid}", '
              'RUN+="/bin/chgrp plugdev /sys%p/authorized", RUN+="/bin/chmod 0664 /sys%p/authorized"\n')
        run("udevadm", "control", "--reload")
        dev = cam_dev(args[0])
        if dev:
            run("udevadm", "trigger", "--action=change", dev)
            run("udevadm", "settle")

    def root_disable(self):
        cid = None
        rule = read(CAM_RULE, "")
        if 'idVendor}=="' in rule:
            vid = rule.split('idVendor}=="')[1].split('"')[0]
            pid = rule.split('idProduct}=="')[1].split('"')[0]
            cid = f"{vid}:{pid}"
        rm(CAM_RULE)
        run("udevadm", "control", "--reload")
        dev = cam_dev(cid) if cid else None
        if dev:
            sysfs_write(f"{dev}/authorized", "1")
            shutil.chown(f"{dev}/authorized", "root", "root")
            os.chmod(f"{dev}/authorized", 0o644)

    def detail(self):
        dev = cam_dev(cam_find_id())
        if not dev:
            return "камеру не знайдено"
        return "камера " + ("увімкнена" if read(f"{dev}/authorized") == "1" else "ВИМКНЕНА")


class NoiseKey(Fix):
    name = "noise-cancel"
    title = "Клавіша шумодаву (біля мікрофона): шумозаглушення мікрофона й динаміків"
    desc = ("Клавіша надсилає XF86Tools, а з Fn XF86Launch5. Як AI Noise Cancelation в ASUS, працює в обидва боки: "
            "очищає мікрофон (те, що чують співрозмовники) і звук, що відтворюється (відео, дзвінки), "
            "через PipeWire WebRTC. Повторне натискання повертає звичайні пристрої. Стан не зберігається між сеансами.")
    cid = "laptop-fixes-noise"
    keys = ["XF86Tools", "XF86Launch5"]          # без Fn / з Fn

    def user_enabled(self):
        return has_custom(self.cid, self.keys)

    def user_enable(self):
        add_custom(self.cid, "laptop-fixes: шумозаглушення", self.keys, f"{USER_BIN} action noise")

    def user_disable(self):
        if nc_module():
            nc_toggle(quiet=True)
        del_custom(self.cid)

    def detail(self):
        return ("мікрофон: " + ("так" if nc_in_mods() else "ні") + "; динаміки: " + ("так" if nc_out_mods() else "ні"))


BAT = "/sys/class/power_supply/BAT0"
BAT_UNIT = "laptop-fixes-battery.service"
BAT_SLEEP_HOOK = "/usr/lib/systemd/system-sleep/laptop-fixes-battery"
BAT_LIMIT = f"{VAR_DIR}/battery-limit"
BAT_DEFAULT = 80


class BatteryLimit(Fix):
    name = "battery-limit"
    title = "Обмеження заряду батареї (за замовчуванням 80%)"
    desc = ("Як «Battery Health Charging» у MyASUS: заряд зупиняється на заданому рівні. "
            "asus-wmi дає charge_control_end_threshold, але BIOS після перезавантаження скидає його "
            "на 100%. Служба знову виставляє ліміт під час завантаження і після сну.")
    root_desc = f"systemd-служба {BAT_UNIT}, хук {BAT_SLEEP_HOOK}, файл {BAT_LIMIT}"

    def root_enabled(self):
        return os.path.exists(f"/etc/systemd/system/{BAT_UNIT}")

    def root_args(self):
        return [str(load_state().get("battery_limit", BAT_DEFAULT))]

    def root_enable(self, args):
        limit = int(args[0]) if args else BAT_DEFAULT
        if not 20 <= limit <= 100:
            raise RuntimeError("ліміт має бути від 20 до 100")
        write(BAT_LIMIT, f"{limit}\n")
        write(f"/etc/systemd/system/{BAT_UNIT}", f"""[Unit]
Description=laptop-fixes: battery charge limit
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 -I {ROOT_SCRIPT} battery-apply

[Install]
WantedBy=multi-user.target
""")
        write(BAT_SLEEP_HOOK, f"""#!/bin/sh
# laptop-fixes: відновити ліміт заряду після сну
[ "$1" = post ] && /usr/bin/python3 -I {ROOT_SCRIPT} battery-apply
exit 0
""", mode=0o755)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", BAT_UNIT, check=True)
        run("systemctl", "restart", BAT_UNIT, check=True)

    def root_disable(self):
        run("systemctl", "disable", "--now", BAT_UNIT)
        rm(f"/etc/systemd/system/{BAT_UNIT}")
        rm(BAT_SLEEP_HOOK)
        rm(BAT_LIMIT)
        run("systemctl", "daemon-reload")
        try:
            sysfs_write(f"{BAT}/charge_control_end_threshold", "100")
        except OSError:
            pass

    def detail(self):
        thr = read(f"{BAT}/charge_control_end_threshold", "?")
        return f"ліміт зараз: {thr}%; заряд {read(f'{BAT}/capacity', '?')}% ({read(f'{BAT}/status', '?')})"


def battery_apply():
    limit = read(BAT_LIMIT, str(BAT_DEFAULT))
    for _ in range(30):
        try:
            sysfs_write(f"{BAT}/charge_control_end_threshold", limit)
            print(f"battery: ліміт {limit}%")
            return
        except OSError:
            time.sleep(1)
    sys.exit("battery: не вдалося записати charge_control_end_threshold")


def battery_set(limit, gui=False):
    st = load_state()
    st["battery_limit"] = int(limit)
    save_state(st)
    root_call("enable", "battery-limit", gui)


DEEP_UNIT = "laptop-fixes-deepsleep.service"


class DeepSleep(Fix):
    name = "deep-sleep"
    title = "Сон у режимі S3 (deep): без вентилятора і розряду"
    desc = ("Linux за замовчуванням вибирає s2idle: процесор «дрімає», але вентилятор крутиться "
            "і батарея сідає. Цей ноутбук підтримує справжній S3, тож служба під час завантаження "
            "перемикає /sys/power/mem_sleep на deep.")
    root_desc = f"systemd-служба {DEEP_UNIT} (mem_sleep=deep)"

    def root_enabled(self):
        return os.path.exists(f"/etc/systemd/system/{DEEP_UNIT}")

    def root_enable(self, args):
        write(f"/etc/systemd/system/{DEEP_UNIT}", """[Unit]
Description=laptop-fixes: use S3 (deep) instead of s2idle for suspend

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'echo deep > /sys/power/mem_sleep'
ExecStop=/bin/sh -c 'echo s2idle > /sys/power/mem_sleep'

[Install]
WantedBy=multi-user.target
""")
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", DEEP_UNIT, check=True)

    def root_disable(self):
        run("systemctl", "disable", "--now", DEEP_UNIT)
        rm(f"/etc/systemd/system/{DEEP_UNIT}")
        run("systemctl", "daemon-reload")

    def detail(self):
        return "режим сну: " + read("/sys/power/mem_sleep", "?")


SWAPFILE = "/swapfile"
FSTAB_MARK = "# laptop-fixes: swapfile"
SWAP_STATE = f"{VAR_DIR}/swap.json"
HIB_GRUB = "/etc/default/grub.d/99-laptop-fixes-hibernate.cfg"
HIB_INITRAMFS = "/etc/initramfs-tools/conf.d/laptop-fixes-resume"
HIB_POLKIT = "/etc/polkit-1/rules.d/40-laptop-fixes-hibernate.rules"
HIB_STATE = f"{VAR_DIR}/hibernate.json"
GIB = 2**30


def mem_gib():
    for line in open("/proc/meminfo"):
        if line.startswith("MemTotal:"):
            return -(-int(line.split()[1]) * 1024 // GIB)       # округлення вгору
    return 8


def load_json(path):
    try:
        return json.loads(read(path, "{}"))
    except ValueError:
        return {}


def swap_active():
    return any(l.split()[:1] == [SWAPFILE] for l in read("/proc/swaps", "").splitlines()[1:])


def swap_size_gib():
    try:
        return round(os.path.getsize(SWAPFILE) / GIB)
    except OSError:
        return 0


def fstab_has_swap():
    return any(l.split()[:1] == [SWAPFILE] for l in read("/etc/fstab", "").splitlines()
               if not l.lstrip().startswith("#"))


def fstab_remove_swap():
    out, skip = [], False
    for l in read("/etc/fstab", "").splitlines():
        if l.startswith("# laptop-fixes:"):
            skip = True
            continue
        if skip and l.split()[:1] == [SWAPFILE]:
            skip = False
            continue
        skip = False
        out.append(l)
    write("/etc/fstab", "\n".join(out) + "\n")


def swap_create(gib):
    need = gib * GIB + GIB
    if shutil.disk_usage("/").free + (os.path.getsize(SWAPFILE) if os.path.exists(SWAPFILE) else 0) < need:
        raise RuntimeError(f"на диску немає {gib + 1} ГіБ вільного місця")
    if swap_active():
        print(f"swapoff {SWAPFILE}…", flush=True)
        run("swapoff", SWAPFILE, check=True)
    rm(SWAPFILE)
    print(f"Створюю {SWAPFILE} на {gib} ГіБ…", flush=True)
    run("fallocate", "-l", f"{gib}G", SWAPFILE, check=True)
    os.chmod(SWAPFILE, 0o600)
    run("mkswap", SWAPFILE, check=True)
    run("swapon", SWAPFILE, check=True)


def migrate_legacy():
    """Перші версії тримали swap-файл у hibernate.json — переносимо в swap.json."""
    hib = load_json(HIB_STATE)
    if "created_swapfile" in hib or "fstab" in hib:
        write(SWAP_STATE, json.dumps({"created": hib.pop("created_swapfile", False),
                                      "fstab": hib.pop("fstab", False)}) + "\n")
        hib["enabled_swap"] = True
        write(HIB_STATE, json.dumps(hib) + "\n")


class SwapFile(Fix):
    name = "swapfile"
    title = "Swap-файл (розмір можна змінювати)"
    desc = (f"Файл підкачки {SWAPFILE} на системному диску. Потрібен для гібернації: щоб вона "
            "спрацьовувала надійно, розмір має бути не меншим за обсяг RAM. Під час зміни розміру "
            "swap ненадовго вимикається, а resume_offset для гібернації оновлюється автоматично.")
    root_desc = f"{SWAPFILE} + рядок у /etc/fstab, {SWAP_STATE}"

    def root_enabled(self):
        return os.path.exists(SWAP_STATE) or "created_swapfile" in load_json(HIB_STATE)

    def root_args(self):
        return [str(load_state().get("swap_gib") or swap_size_gib() or mem_gib())]

    def root_enable(self, args):
        gib = int(args[0]) if args else mem_gib()
        if not 1 <= gib <= 128:
            raise RuntimeError("розмір swap має бути від 1 до 128 ГіБ")
        st = load_json(SWAP_STATE)
        if not st:
            if os.path.exists(SWAPFILE) and not swap_active():
                raise RuntimeError(f"{SWAPFILE} вже існує, але не підключений. Перевірте його вручну")
            st = {"created": not os.path.exists(SWAPFILE), "fstab": False}
            if not st["created"]:
                st["original_gib"] = swap_size_gib()
        if swap_size_gib() != gib or not swap_active():
            swap_create(gib)
        if not fstab_has_swap():
            with open("/etc/fstab", "a") as f:
                f.write(f"{FSTAB_MARK}\n{SWAPFILE} none swap sw 0 0\n")
            st["fstab"] = True
        write(SWAP_STATE, json.dumps(st) + "\n")
        if Hibernate().root_enabled():
            hibernate_refresh()

    def root_disable(self):
        if Hibernate().root_enabled():
            raise RuntimeError("гібернації потрібен swap: спершу вимкніть фікс hibernate")
        st = load_json(SWAP_STATE)
        if st.get("fstab"):
            fstab_remove_swap()
        if st.get("created"):
            if swap_active():
                run("swapoff", SWAPFILE, check=True)
            rm(SWAPFILE)
        elif st.get("original_gib") and st["original_gib"] != swap_size_gib():
            swap_create(st["original_gib"])
        rm(SWAP_STATE)

    def detail(self):
        if not os.path.exists(SWAPFILE):
            return "swap-файлу немає"
        used = 0
        for l in read("/proc/swaps", "").splitlines()[1:]:
            p = l.split()
            if p[0] == SWAPFILE:
                used = int(p[3]) // 1024
        return (f"розмір {swap_size_gib()} ГіБ (RAM {mem_gib()} ГіБ); "
                + (f"увімкнений, зайнято {used} МіБ" if swap_active() else "НЕ підключений"))


def swap_offset():
    frag = run("filefrag", "-v", SWAPFILE, check=True)
    return next(l.split()[3].rstrip(".") for l in frag.splitlines() if l.strip().startswith("0:"))


def hibernate_refresh():
    """Оновити resume_offset після (пере)створення swap-файлу."""
    uuid = run("findmnt", "-no", "UUID", "-T", SWAPFILE, check=True)
    offset = swap_offset()
    st = load_json(HIB_STATE)
    old = (st.get("uuid"), st.get("offset"))
    st.update(uuid=uuid, offset=offset)
    write(HIB_STATE, json.dumps(st) + "\n")
    write(HIB_GRUB, "# laptop-fixes: відновлення з гібернації\n"
          f'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT resume=UUID={uuid} resume_offset={offset}"\n')
    write(HIB_INITRAMFS, f"# laptop-fixes\nRESUME=UUID={uuid}\n")
    if old != (uuid, offset) or not os.path.exists("/boot/grub/grub.cfg") or \
            f"resume_offset={offset}" not in read("/boot/grub/grub.cfg", ""):
        print("update-grub…", flush=True)
        run("update-grub", check=True)
    if old[0] != uuid:
        print("update-initramfs (до хвилини)…", flush=True)
        run("update-initramfs", "-u", check=True)
    # щоб гібернація працювала вже в цьому сеансі, без перезавантаження
    try:
        sysfs_write("/sys/power/resume_offset", offset)
        sysfs_write("/sys/power/resume", run("findmnt", "-no", "MAJ:MIN", "-T", SWAPFILE))
    except OSError as e:
        print(f"(resume у поточному сеансі не встановлено: {e}; запрацює після перезавантаження)")


def swap_set(gib, gui=False):
    st = load_state()
    st["swap_gib"] = int(gib)
    save_state(st)
    root_call("enable", "swapfile", gui)


class Hibernate(Fix):
    name = "hibernate"
    title = "Гібернація (з'являється в меню вимкнення і налаштуваннях живлення)"
    desc = ("Прописує для ядра resume= і resume_offset= (GRUB) та initramfs і знімає заборону "
            "гібернації, яку Ubuntu ставить через polkit. Якщо swap-файлу ще немає, створює його розміром з RAM. "
            "Що робити при закритті кришки, налаштовується далі штатно в «Керуванні живленням».")
    root_desc = (f"{HIB_GRUB} + update-grub, {HIB_INITRAMFS} + update-initramfs, "
                 f"polkit-правило {HIB_POLKIT}; swap-файл, якщо його немає")

    def root_enabled(self):
        return os.path.exists(HIB_STATE)

    def root_enable(self, args):
        st = load_json(HIB_STATE)
        if not SwapFile().root_enabled():
            SwapFile().root_enable([str(mem_gib())])
            st["enabled_swap"] = True
        write(HIB_STATE, json.dumps(st) + "\n")
        write(HIB_POLKIT, """// laptop-fixes: дозволити гібернацію (Ubuntu забороняє її в com.ubuntu.desktop.rules)
polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.login1.hibernate" ||
         action.id == "org.freedesktop.login1.handle-hibernate-key" ||
         action.id == "org.freedesktop.upower.hibernate") &&
        subject.active && subject.local) {
        return polkit.Result.YES;
    }
    if (action.id == "org.freedesktop.login1.hibernate-multiple-sessions" &&
        subject.active && subject.local) {
        return polkit.Result.AUTH_ADMIN_KEEP;
    }
});
""")
        hibernate_refresh()

    def root_disable(self):
        st = load_json(HIB_STATE)
        for p in (HIB_POLKIT, HIB_GRUB, HIB_INITRAMFS):
            rm(p)
        rm(HIB_STATE)
        run("update-grub")
        run("update-initramfs", "-u")
        if st.get("enabled_swap") and SwapFile().root_enabled():
            SwapFile().root_disable()

    def detail(self):
        can = run("busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1",
                  "org.freedesktop.login1.Manager", "CanHibernate").replace('s "', "").rstrip('"')
        cmd = "resume=" in read("/proc/cmdline", "")
        d = (f"CanHibernate: {can}; resume= у поточному ядрі: "
             f"{'так' if cmd else 'ні (буде після перезавантаження)'}")
        if self.root_enabled() and swap_size_gib() < mem_gib():
            d += f"; увага: swap {swap_size_gib()} ГіБ менший за RAM {mem_gib()} ГіБ"
        return d


class MyAsusKey(Fix):
    name = "myasus-key"
    title = "F12 (MyASUS) відкриває/закриває вікно laptop-fixes"
    desc = "MyASUS під Linux немає, тож клавіша XF86Launch1 відкриває вікно цих фіксів, а повторне натискання його закриває."
    cid = "laptop-fixes-myasus"

    def user_enabled(self):
        return has_custom(self.cid)

    def user_enable(self):
        add_custom(self.cid, "laptop-fixes: MyASUS", "XF86Launch1", f"{USER_BIN} gui")

    def user_disable(self):
        del_custom(self.cid)


GROUPS = [
    ("Клавіатура", [FnLock(), LockKey(), ScreenshotKey(), MyAsusKey()]),
    ("Мікрофон і камера", [MicMute(), NoiseKey(), CameraKey()]),
    ("Живлення і сон", [BatteryLimit(), DeepSleep(), SwapFile(), Hibernate()]),
]
FIXES = [f for _, fixes in GROUPS for f in fixes]
BY_NAME = {f.name: f for f in FIXES}

# Фікси з числовим параметром: підпис, мін, макс, крок, поточне значення, застосувати, питання
SETTINGS = {
    "battery-limit": ("Ліміт заряду, %", 20, 100, 5, lambda: load_state().get("battery_limit", BAT_DEFAULT),
                      battery_set, "Встановити ліміт заряду {}%?"),
    "swapfile": ("Розмір, ГіБ", 1, 128, 1, lambda: load_state().get("swap_gib") or swap_size_gib() or mem_gib(),
                 swap_set, "Змінити розмір swap-файлу на {} ГіБ?"),
}


# --------------------------------------------------------------- actions

def sources():
    out = run("pactl", "list", "sources", "short")
    names = [l.split("\t")[1] for l in out.splitlines() if "\t" in l]
    return [n for n in names if n.startswith(("alsa_input.", "bluez_input.")) and not n.endswith(".monitor")]


def mic_all_muted():
    srcs = sources()
    return bool(srcs) and all(run("pactl", "get-source-mute", s).endswith(("yes", "так")) for s in srcs)


def mic_toggle():
    mute = not mic_all_muted()
    for s in sources():
        run("pactl", "set-source-mute", s, "1" if mute else "0")
    if mute:
        osd("microphone-sensitivity-muted-symbolic", "Мікрофон вимкнено")
    else:
        osd("microphone-sensitivity-high-symbolic", "Мікрофон увімкнено")


def cam_find_id():
    st = load_state()
    if st.get("camera_id"):
        return st["camera_id"]
    base = "/sys/bus/usb/devices"
    for d in sorted(os.listdir(base)):
        if ":" in d and read(f"{base}/{d}/bInterfaceClass") == "0e":
            dev = f"{base}/{d.split(':')[0]}"
            cid = f"{read(dev + '/idVendor')}:{read(dev + '/idProduct')}"
            if os.geteuid() != 0:
                st["camera_id"] = cid
                save_state(st)
            return cid
    return None


def cam_dev(cid):
    if not cid:
        return None
    base = "/sys/bus/usb/devices"
    for d in os.listdir(base):
        p = f"{base}/{d}"
        if ":" not in d and f"{read(p + '/idVendor')}:{read(p + '/idProduct')}" == cid:
            return os.path.realpath(p)
    return None


def cam_set(on):
    dev = cam_dev(cam_find_id())
    if not dev:
        return False
    try:
        sysfs_write(f"{dev}/authorized", "1" if on else "0")
        return True
    except OSError:
        return False


def cam_toggle():
    dev = cam_dev(cam_find_id())
    if not dev:
        osd("laptop-fixes-camera-off-symbolic", "Камеру не знайдено")
        return
    on = read(f"{dev}/authorized") != "1"
    if not cam_set(on):
        osd("laptop-fixes-camera-off-symbolic", "Немає прав (увімкніть фікс camera)")
    elif on:
        osd("laptop-fixes-camera-on-symbolic", "Камеру увімкнено")
    else:
        osd("laptop-fixes-camera-off-symbolic", "Камеру вимкнено")


NCO_SINK = "lf_nco_sink"          # куди грають програми
NCO_CLEAN = "lf_nco_clean"        # очищений звук, що йде на справжні динаміки
NCO_REF = "lf_nco_ref"


def nc_runtime(name):
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), f"laptop-fixes-{name}")


def nc_mods(*markers):
    """Номери модулів PipeWire-pulse, в аргументах яких є будь-який із маркерів."""
    out = []
    for line in run("pactl", "list", "modules", "short").splitlines():
        if any(m in line for m in markers):
            out.append(line.split("\t")[0])
    return out


def nc_in_mods():
    return nc_mods(f"source_name={NC_SOURCE}")


def nc_out_mods():
    return nc_mods(f"sink_name={NCO_SINK}", f"source_name={NCO_CLEAN}", f"source={NCO_CLEAN}")


def nc_module():
    return bool(nc_in_mods() or nc_out_mods())


def nc_in_on():
    master = run("pactl", "get-default-source")
    if master not in sources():
        srcs = sources()
        master = srcs[0] if srcs else ""
    write(nc_runtime("nc-prev"), master)
    run("pactl", "load-module", "module-echo-cancel", "aec_method=webrtc",
        f"source_master={master}", f"source_name={NC_SOURCE}", f"sink_name={NC_SINK}",
        'source_properties=device.description="Шумозаглушення (мікрофон)"',
        'sink_properties=device.description="Шумозаглушення (службовий)"', check=True)
    time.sleep(0.3)
    run("pactl", "set-default-source", NC_SOURCE)


def nc_in_off():
    for m in reversed(nc_in_mods()):
        run("pactl", "unload-module", m)
    prev = read(nc_runtime("nc-prev"))
    if prev and prev in sources():
        run("pactl", "set-default-source", prev)
    rm(nc_runtime("nc-prev"))


def sinks():
    return [l.split("\t")[1] for l in run("pactl", "list", "sinks", "short").splitlines() if "\t" in l]


def nc_out_on():
    """Програми → нульовий sink → WebRTC-шумодав → loopback → справжні динаміки/навушники."""
    real = run("pactl", "get-default-sink")
    if real.startswith("lf_"):
        return
    write(nc_runtime("nco-prev"), real)
    run("pactl", "load-module", "module-null-sink", f"sink_name={NCO_SINK}",
        'sink_properties=device.description="Шумозаглушення (динаміки)"', check=True)
    run("pactl", "load-module", "module-echo-cancel", "aec_method=webrtc",
        f"source_master={NCO_SINK}.monitor", f"sink_master={real}",
        f"source_name={NCO_CLEAN}", f"sink_name={NCO_REF}",
        'source_properties=device.description="Шумозаглушення (очищений звук)"',
        'sink_properties=device.description="Шумозаглушення (службовий)"', check=True)
    run("pactl", "load-module", "module-loopback", f"source={NCO_CLEAN}", f"sink={real}",
        "latency_msec=30", check=True)
    time.sleep(0.3)
    run("pactl", "set-default-sink", NCO_SINK)
    # перенести те, що вже грає (потоки програм, не службові потоки модулів)
    for l in run("pactl", "list", "sink-inputs", "short").splitlines():
        p = l.split("\t")
        if len(p) > 2 and p[2] != "-":
            run("pactl", "move-sink-input", p[0], NCO_SINK)


def nc_out_off():
    prev = read(nc_runtime("nco-prev"))
    if prev and prev in sinks():
        run("pactl", "set-default-sink", prev)
        for l in run("pactl", "list", "sink-inputs", "short").splitlines():
            p = l.split("\t")
            if len(p) > 2 and p[2] != "-":
                run("pactl", "move-sink-input", p[0], prev)
    for m in reversed(nc_out_mods()):
        run("pactl", "unload-module", m)
    rm(nc_runtime("nco-prev"))


def nc_toggle(quiet=False):
    """Як AI Noise Cancelation в ASUS: в обидва боки — мікрофон і звук, що відтворюється."""
    if nc_module():
        nc_out_off()
        nc_in_off()
        if not quiet:
            osd("audio-input-microphone-symbolic", "Шумозаглушення вимкнено")
        return
    try:
        nc_in_on()
        nc_out_on()
    except Exception:
        nc_out_off()
        nc_in_off()
        if not quiet:
            osd("audio-input-microphone-symbolic", "Шумозаглушення: помилка")
        raise
    if not quiet:
        osd("audio-input-microphone-symbolic", "Шумозаглушення увімкнено (мікрофон і динаміки)")


# --------------------------------------------------------- root-side code

def micled(action):
    """Прив'язати/відв'язати ALSA «Capture Switch» (вхід гарнітури) до тригера audio-micmute."""
    for _ in range(30):
        cards = [c for c in os.listdir("/sys/class/sound/ctl-led/mic")
                 if c.startswith("card")] if os.path.isdir("/sys/class/sound/ctl-led/mic") else []
        if cards:
            break
        time.sleep(1)
    for c in cards:
        n = c[4:]
        numid = None
        for line in run("amixer", "-c", n, "controls").splitlines():
            if line.endswith("name='Capture Switch'"):
                numid = line.split(",")[0].split("=")[1]
        if not numid:
            continue
        d = f"/sys/class/sound/ctl-led/mic/{c}"
        attached = numid in read(f"{d}/list", "").split()
        if action == "attach" and not attached:
            sysfs_write(f"{d}/attach", f"numid={numid}")
        elif action == "detach" and attached:
            sysfs_write(f"{d}/detach", f"numid={numid}")
        print(f"{c}: {action} numid={numid}; list={read(f'{d}/list')}")


def kprobe_events(line):
    # Без lseek: open(..., "a") у Python на tracefs дає EINVAL
    fd = os.open(f"{TRACE}/kprobe_events", os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)


def fnlockd():
    """Root-демон: kprobe на asus_wmi_handle_event_code(code=0x4E) => Fn+Esc перемкнув Fn-lock."""
    for _ in range(60):
        if os.path.exists(FN_PARAM):
            break
        time.sleep(1)
    state = read(FN_RUNSTATE) or read(FN_PARAM, "Y")
    fn_save(state)
    print(f"fnlock: початковий стан {state}", flush=True)

    inst = f"{TRACE}/instances/laptop_fixes"
    ev = f"{inst}/events/{KPROBE}"

    def cleanup():
        if os.path.exists(ev):
            try:
                open(f"{ev}/enable", "w").write("0")
            except OSError:
                pass
        try:
            os.rmdir(inst)
        except OSError:
            pass
        try:
            kprobe_events(f"-:{KPROBE}")
        except OSError:
            pass

    def on_term(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, on_term)
    cleanup()
    try:
        kprobe_events(f"p:{KPROBE} asus_wmi_handle_event_code code=%di:s32")
        os.makedirs(inst, exist_ok=True)
        open(f"{ev}/filter", "w").write(f"code == {FN_EVENT_CODE}")
        open(f"{ev}/enable", "w").write("1")
        with open(f"{inst}/trace_pipe") as pipe:
            for line in pipe:
                if "fnlock_key" not in line:
                    continue
                state = "N" if state == "Y" else "Y"
                fn_save(state)
                print(f"fnlock: Fn+Esc, новий стан {state}", flush=True)
    finally:
        cleanup()


def root_apply(action, fix, args):
    if os.geteuid() != 0:
        sys.exit("root-apply треба запускати з правами root")
    migrate_legacy()
    f = BY_NAME[fix]
    me = os.path.realpath(__file__)
    if action == "enable":
        if me != ROOT_SCRIPT and read(me) != read(ROOT_SCRIPT):
            os.makedirs(ROOT_LIB, exist_ok=True)
            shutil.copy(me, ROOT_SCRIPT)
            os.chmod(ROOT_SCRIPT, 0o755)
            if fix != "fnlock" and BY_NAME["fnlock"].root_enabled():
                run("systemctl", "restart", FN_UNIT)     # служба працює з оновленої копії
        f.root_enable(args)
    else:
        f.root_disable()
        if not any(x.root_desc and x.root_enabled() for x in FIXES) and os.path.isdir(ROOT_LIB):
            shutil.rmtree(ROOT_LIB)
            shutil.rmtree(VAR_DIR, ignore_errors=True)
            shutil.rmtree(RUN_DIR, ignore_errors=True)


# --------------------------------------------------------- user-side CLI

def root_call(action, fix, gui=False):
    f = BY_NAME[fix]
    args = f.root_args() if action == "enable" else []
    me = os.path.realpath(__file__)
    cmd = ["python3", "-I", me, "root-apply", action, fix] + args
    if gui:
        r = subprocess.run(["pkexec"] + cmd)
    else:
        r = subprocess.run(["sudo"] + cmd)
    if r.returncode:
        raise RuntimeError(f"root-частина «{fix}» не виконана (код {r.returncode})")


def yes(prompt="Продовжити?"):
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes", "т", "так")


def confirm_cli(fixes, action):
    need = [f for f in fixes if f.root_desc]
    if not need:
        return True
    print(f"Для {'увімкнення' if action == 'enable' else 'вимкнення'} потрібні права root (sudo):")
    for f in need:
        print(f"  • {f.name}: {f.root_desc}")
    return yes()


def apply(fixes, action, gui=False, confirmed=False):
    if not confirmed and not confirm_cli(fixes, action):
        print("Скасовано. Root-зміни не виконано.")
        fixes = [f for f in fixes if not f.root_desc]
    # вимикаємо у зворотному порядку: hibernate раніше за swapfile
    for f in (fixes if action == "enable" else list(reversed(fixes))):
        try:
            if action == "enable":
                if f.root_desc and not f.root_enabled():
                    root_call("enable", f.name, gui)
                if not f.user_enabled():
                    f.user_enable()
            else:
                f.user_disable()
                if f.root_desc and f.root_enabled():
                    root_call("disable", f.name, gui)
            print(f"{f.name}: {'увімкнено' if action == 'enable' else 'вимкнено'}")
        except Exception as e:
            print(f"{f.name}: ПОМИЛКА: {e}", file=sys.stderr)


def status(verbose=True):
    for title, fixes in GROUPS:
        print(f"── {title}")
        for f in fixes:
            mark = "[x]" if f.enabled() else ("[~]" if f.partial() else "[ ]")
            print(f"{mark} {f.name:15} {f.title}")
            if verbose:
                if f.partial():
                    print(f"      частково: користувацька частина {'є' if f.user_enabled() else 'немає'}, "
                          f"root-частина {'є' if f.root_enabled() else 'немає'}")
                d = f.detail()
                if d:
                    print(f"      {d}")
        print()


def pick(names):
    if names == ["all"]:
        return FIXES
    bad = [n for n in names if n not in BY_NAME]
    if bad or not names:
        sys.exit(f"Невідомі фікси: {' '.join(bad) or '(не вказано)'}. Є: {', '.join(BY_NAME)}, all")
    return [BY_NAME[n] for n in names]


def gui():
    import threading
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib

    app = Gtk.Application(application_id="io.github.dmytro.LaptopFixes")

    def build():
        win = Gtk.ApplicationWindow(application=app, title="Laptop fixes — ASUS ExpertBook")
        win.set_default_size(680, 560)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_border_width(10)
        win.add(outer)
        notebook = Gtk.Notebook()
        outer.pack_start(notebook, True, True, 0)
        rows = {}

        def refresh():
            for f in FIXES:
                sw, det, hid, spin = rows[f.name]
                sw.handler_block(hid)
                sw.set_active(f.enabled())
                sw.set_state(f.enabled())
                sw.handler_unblock(hid)
                sw.set_sensitive(True)
                d = f.detail()
                if f.partial():
                    d = ("частково увімкнено; " + d).strip("; ")
                det.set_text(d)
                if spin:
                    spin.set_value(SETTINGS[f.name][4]())
            return False

        def confirm(text, secondary):
            dlg = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.QUESTION,
                                    buttons=Gtk.ButtonsType.OK_CANCEL, text=text)
            dlg.format_secondary_text(secondary)
            ok = dlg.run() == Gtk.ResponseType.OK
            dlg.destroy()
            return ok

        def background(job):
            def wrapped():
                try:
                    job()
                except Exception as e:
                    print(e, file=sys.stderr)
                finally:
                    GLib.idle_add(refresh)
            threading.Thread(target=wrapped, daemon=True).start()

        def on_switch(sw, state, f):
            action = "enable" if state else "disable"
            if f.root_desc and not confirm(f"{'Увімкнути' if state else 'Вимкнути'} «{f.name}»?",
                                           f"Потрібні права root. Зміниться:\n{f.root_desc}"):
                refresh()
                return True
            sw.set_sensitive(False)
            background(lambda: apply([f], action, gui=True, confirmed=True))
            return True

        def on_setting(_btn, f, spin):
            value = int(spin.get_value())
            label, _lo, _hi, _step, _get, setter, question = SETTINGS[f.name]
            if confirm(question.format(value), f"Потрібні права root. Зміниться:\n{f.root_desc}"):
                background(lambda: setter(value, gui=True))

        for group, fixes in GROUPS:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            box.set_border_width(12)
            for f in fixes:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                t = Gtk.Label(xalign=0)
                t.set_markup(f"<b>{GLib.markup_escape_text(f.title)}</b>" + (" 🔒" if f.root_desc else ""))
                t.set_line_wrap(True)
                dsc = Gtk.Label(label=f.desc, xalign=0)
                dsc.set_line_wrap(True)
                dsc.set_max_width_chars(70)
                dsc.get_style_context().add_class("dim-label")
                det = Gtk.Label(xalign=0)
                det.set_line_wrap(True)
                for w in (t, dsc, det):
                    text.pack_start(w, False, False, 0)
                spin = None
                if f.name in SETTINGS:
                    label, lo, hi, step = SETTINGS[f.name][:4]
                    line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    spin = Gtk.SpinButton.new_with_range(lo, hi, step)
                    btn = Gtk.Button(label="Застосувати")
                    btn.connect("clicked", on_setting, f, spin)
                    line.pack_start(Gtk.Label(label=f"{label}:"), False, False, 0)
                    line.pack_start(spin, False, False, 0)
                    line.pack_start(btn, False, False, 0)
                    text.pack_start(line, False, False, 4)
                sw = Gtk.Switch(valign=Gtk.Align.CENTER)
                hid = sw.connect("state-set", on_switch, f)
                row.pack_start(text, True, True, 0)
                row.pack_end(sw, False, False, 0)
                box.pack_start(row, False, False, 0)
                box.pack_start(Gtk.Separator(), False, False, 0)
                rows[f.name] = (sw, det, hid, spin)
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            scroll.add(box)
            notebook.append_page(scroll, Gtk.Label(label=group))

        note = Gtk.Label(xalign=0)
        note.set_markup("<small>🔒: потрібні права root (з'явиться запит пароля). "
                        "CLI: <tt>laptop-fixes status</tt></small>")
        outer.pack_start(note, False, False, 0)
        refresh()
        win.show_all()

    def activate(app):
        if app.get_windows():      # повторне натискання F12 закриває вікно
            app.quit()
        else:
            build()

    app.connect("activate", activate)
    app.run([])


USAGE = """Використання: laptop-fixes КОМАНДА
  list                 список фіксів (коротко)
  status               стан кожного фіксу та заліза
  enable  FIX...|all   увімкнути фікс(и)
  disable FIX...|all   вимкнути фікс(и) і повністю відкотити зміни
  gui                  вікно з перемикачами
  fnlock-boot Y|N      вручну задати стан Fn-lock після завантаження
  battery-limit N      ліміт заряду батареї, % (20–100)
  swap-size N          розмір swap-файлу, ГіБ (1–128)
"""


def main(argv):
    if not argv:
        print(USAGE)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "list":
        status(verbose=False)
    elif cmd == "status":
        status()
    elif cmd in ("enable", "disable"):
        apply(pick(rest), cmd)
    elif cmd == "gui":
        gui()
    elif cmd == "action":
        {"micmute": mic_toggle, "camera": cam_toggle, "noise": nc_toggle}[rest[0]]()
    elif cmd == "fnlock-boot":
        v = rest[0].upper() if rest else ""
        if v not in ("Y", "N"):
            sys.exit("fnlock-boot Y|N")
        subprocess.run(["sudo", "python3", "-I", os.path.realpath(__file__), "fnlock-set", v])
    elif cmd == "fnlock-set" and os.geteuid() == 0:
        fn_save(rest[0], runtime=False)
        print(f"Після наступного завантаження Fn-lock: {fn_human(rest[0])}")
    elif cmd == "root-apply":
        root_apply(rest[0], rest[1], rest[2:])
    elif cmd == "fnlockd":
        fnlockd()
    elif cmd == "battery-limit":
        if not rest or not rest[0].isdigit() or not 20 <= int(rest[0]) <= 100:
            sys.exit("battery-limit N  (20–100)")
        print(f"Потрібні права root: {BatteryLimit.root_desc}")
        if yes():
            battery_set(rest[0])
            print(f"Ліміт заряду: {rest[0]}%")
    elif cmd == "swap-size":
        if not rest or not rest[0].isdigit() or not 1 <= int(rest[0]) <= 128:
            sys.exit("swap-size N  (1–128 ГіБ)")
        print(f"Потрібні права root: {SwapFile.root_desc}")
        if int(rest[0]) < mem_gib():
            print(f"Увага: swap менший за RAM ({mem_gib()} ГіБ), тож гібернація може не вдатися.")
        if yes():
            swap_set(rest[0])
            print(f"Swap-файл: {rest[0]} ГіБ")
    elif cmd == "battery-apply":
        battery_apply()
    elif cmd == "micled":
        micled(rest[0])
    else:
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
