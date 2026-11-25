"""Install / uninstall logic for VU3INI PacketLab (no GUI code here, so it can be tested).

Per-user installation (no administrator rights needed):
  program files   %LOCALAPPDATA%\\Programs\\VU3INI PacketLab   (changeable)
  GPU add-on      %LOCALAPPDATA%\\VU3INI PacketLab\\addons
  Start menu      %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\VU3INI PacketLab
  Apps & features HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\VU3INI_PacketLab
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from typing import Callable, List, Optional

APP_NAME = "VU3INI PacketLab"
APP_ID = "VU3INI_PacketLab"
EXE_NAME = "VU3INI PacketLab.exe"
UNINSTALL_EXE = "Uninstall VU3INI PacketLab.exe"
PUBLISHER = "Sasanka Boruah (VU3INI)"
CONTACT = "hamvu3ini@gmail.com"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\VU3INI_PacketLab"
PROGID = "VU3INI.PacketLab.Topology"

Progress = Callable[[int, str], None]


def resource(name: str) -> str:
    """Find a bundled file: inside the exe, in a 'data' folder beside the exe, or beside the script."""
    cands = []
    if getattr(sys, "_MEIPASS", None):
        cands.append(os.path.join(sys._MEIPASS, name))
    if getattr(sys, "frozen", False):
        here = os.path.dirname(os.path.abspath(sys.executable))
        cands += [os.path.join(here, "data", name), os.path.join(here, name)]
    here = os.path.dirname(os.path.abspath(__file__))
    cands += [os.path.join(here, "data", name), os.path.join(here, name)]
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[0]


def default_install_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "Programs", APP_NAME)


def addons_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, APP_NAME, "addons")


def user_data_dirs() -> List[str]:
    out = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        b = os.environ.get(env)
        if b:
            out.append(os.path.join(b, APP_NAME))
    return out


def start_menu_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs", APP_NAME)


def desktop_dir() -> str:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, 0x0000, None, 0, buf)  # CSIDL_DESKTOP (OneDrive aware)
            if buf.value:
                return buf.value
        except Exception:  # noqa: BLE001
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def free_space_mb(path: str) -> int:
    p = path
    while p and not os.path.exists(p):
        p = os.path.dirname(p)
    try:
        return shutil.disk_usage(p or ".").free // 2 ** 20
    except OSError:
        return -1


def zip_size_mb(path: str) -> int:
    try:
        with zipfile.ZipFile(path) as z:
            return sum(i.file_size for i in z.infolist()) // 2 ** 20
    except (OSError, zipfile.BadZipFile):
        return 0


def is_running(exe_path: str) -> bool:
    if os.name != "nt" or not os.path.exists(exe_path):
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {os.path.basename(exe_path)}", "/NH"],
                             capture_output=True, text=True, creationflags=0x08000000, timeout=10).stdout
        return os.path.basename(exe_path).lower() in out.lower()
    except Exception:  # noqa: BLE001
        return False


def existing_install() -> Optional[dict]:
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            loc = winreg.QueryValueEx(k, "InstallLocation")[0]
            ver = winreg.QueryValueEx(k, "DisplayVersion")[0]
            return {"path": loc, "version": ver}
    except OSError:
        return None


# ------------------------------------------------------------------------- install
def extract(zip_path: str, target: str, progress: Progress, lo: int = 0, hi: int = 100) -> int:
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        items = z.infolist()
        total = sum(i.file_size for i in items) or 1
        done = 0
        root = os.path.realpath(target)
        for k, info in enumerate(items):
            dest = os.path.realpath(os.path.join(target, info.filename))
            if not dest.startswith(root):
                raise RuntimeError(f"unsafe path in archive: {info.filename}")
            if info.is_dir():
                os.makedirs(dest, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            done += info.file_size
            if k % 20 == 0 or done == total:
                progress(lo + int((hi - lo) * done / total), f"Copying {info.filename}")
        return len(items)


def remove_old_program(target: str, progress: Progress) -> None:
    """Remove program files of a previous version (user data lives elsewhere and is kept)."""
    if not os.path.isdir(target):
        return
    marker = os.path.join(target, EXE_NAME)
    if not os.path.exists(marker) and os.listdir(target):
        raise RuntimeError(f"{target} is not empty and does not contain {APP_NAME}. Choose an empty folder.")
    progress(2, "Removing the previous version…")
    for name in os.listdir(target):
        p = os.path.join(target, name)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=False)
        else:
            os.remove(p)


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def make_shortcut(lnk: str, target: str, workdir: str, icon: str, desc: str, args: str = "") -> bool:
    if os.name != "nt":
        # non-Windows (tests): write a small text file so the step is visible
        os.makedirs(os.path.dirname(lnk), exist_ok=True)
        with open(lnk + ".txt", "w", encoding="utf-8") as f:
            f.write(f"{target} {args}\n")
        return True
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(lnk) + ");"
          "$s.TargetPath=" + _ps_quote(target) + ";$s.WorkingDirectory=" + _ps_quote(workdir) + ";"
          "$s.IconLocation=" + _ps_quote(icon + ",0") + ";$s.Description=" + _ps_quote(desc) + ";"
          "$s.Arguments=" + _ps_quote(args) + ";$s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, creationflags=0x08000000, timeout=60)
    return r.returncode == 0 and os.path.exists(lnk)


def register(target: str, version: str, size_kb: int, associate: bool) -> None:
    if os.name != "nt":
        with open(os.path.join(target, "registry.txt"), "w", encoding="utf-8") as f:
            f.write(f"{UNINSTALL_KEY}\nversion={version}\nassociate={associate}\n")
        return
    import winreg
    exe = os.path.join(target, EXE_NAME)
    un = os.path.join(target, UNINSTALL_EXE)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        vals = {"DisplayName": APP_NAME, "DisplayVersion": version, "Publisher": PUBLISHER,
                "DisplayIcon": exe + ",0", "InstallLocation": target, "UninstallString": f'"{un}"',
                "QuietUninstallString": f'"{un}" --quiet', "HelpLink": f"mailto:{CONTACT}",
                "Comments": "Design, simulate and test LAN, MAN and WAN networks"}
        for name, v in vals.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, v)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, int(size_kb))
    if associate:
        base = r"Software\Classes"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\.nstopo") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, PROGID)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\" + PROGID) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "PacketLab topology")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\" + PROGID + r"\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, exe + ",0")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\" + PROGID + r"\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{exe}" "%1"')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              base + r"\Applications\VU3INI PacketLab.exe\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{exe}" "%1"')
        # appear in "Open with" for .yaml/.yml without taking over the default program
        for ext in (".yaml", ".yml"):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\" + ext + r"\OpenWithProgids") as k:
                winreg.SetValueEx(k, PROGID, 0, winreg.REG_NONE, b"")
        try:
            import ctypes
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        except Exception:  # noqa: BLE001
            pass


def dir_size_kb(path: str) -> int:
    tot = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                tot += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return tot // 1024


def install(target: str, version: str, app_zip: str, uninstaller: Optional[str], gpu_zip: Optional[str],
            start_menu: bool, desktop: bool, associate: bool, progress: Progress, log: Callable[[str], None]) -> str:
    target = os.path.abspath(target)
    need = zip_size_mb(app_zip) + (zip_size_mb(gpu_zip) if gpu_zip else 0) + 50
    free = free_space_mb(target)
    if 0 <= free < need:
        raise RuntimeError(f"Not enough disk space: {need} MB needed, {free} MB free on the target drive.")
    exe = os.path.join(target, EXE_NAME)
    if is_running(exe):
        raise RuntimeError(f"{APP_NAME} is running. Close it and try again.")
    log(f"Installing {APP_NAME} {version} to {target}")
    remove_old_program(target, progress)
    n = extract(app_zip, target, progress, 5, 80 if gpu_zip else 90)
    log(f"Copied {n} files")
    if uninstaller and os.path.exists(uninstaller):
        shutil.copy2(uninstaller, os.path.join(target, UNINSTALL_EXE))
        log("Uninstaller added")
    with open(os.path.join(target, "INSTALL_INFO.txt"), "w", encoding="utf-8") as f:
        f.write(f"{APP_NAME} {version}\nInstalled by the VU3INI PacketLab setup wizard.\nPublisher: {PUBLISHER}\n"
                f"Contact: {CONTACT}\n")
    if gpu_zip:
        progress(81, "Installing the NVIDIA GPU add-on…")
        ad = addons_dir()
        if os.path.isdir(ad):
            shutil.rmtree(ad, ignore_errors=True)
        extract(gpu_zip, ad, progress, 81, 92)
        log(f"GPU add-on installed to {ad}")
    progress(93, "Creating shortcuts…")
    icon = exe
    if start_menu:
        sm = start_menu_dir()
        ok1 = make_shortcut(os.path.join(sm, f"{APP_NAME}.lnk"), exe, target, icon,
                            "Design, simulate and test networks")
        ok2 = make_shortcut(os.path.join(sm, f"Uninstall {APP_NAME}.lnk"), os.path.join(target, UNINSTALL_EXE), target,
                            icon, f"Remove {APP_NAME}")
        log("Start menu shortcuts created" if ok1 and ok2 else "Warning: Start menu shortcut could not be created")
    if desktop:
        ok = make_shortcut(os.path.join(desktop_dir(), f"{APP_NAME}.lnk"), exe, target, icon,
                           "Design, simulate and test networks")
        log("Desktop shortcut created" if ok else "Warning: desktop shortcut could not be created")
    progress(97, "Registering with Windows (Apps & features)…")
    register(target, version, dir_size_kb(target), associate)
    log("Registered in Apps & features")
    progress(100, "Installation complete")
    return exe


# ------------------------------------------------------------------------- uninstall
def uninstall(target: str, remove_user_data: bool, progress: Progress, log: Callable[[str], None]) -> None:
    target = os.path.abspath(target)
    if is_running(os.path.join(target, EXE_NAME)):
        raise RuntimeError(f"{APP_NAME} is running. Close it and try again.")
    if not os.path.exists(os.path.join(target, EXE_NAME)) and not os.path.exists(os.path.join(target,
                                                                                              "INSTALL_INFO.txt")):
        raise RuntimeError(f"{target} does not look like a {APP_NAME} installation; nothing was removed.")
    progress(5, "Removing shortcuts…")
    for p in (os.path.join(desktop_dir(), f"{APP_NAME}.lnk"), os.path.join(desktop_dir(), f"{APP_NAME}.lnk.txt")):
        if os.path.exists(p):
            os.remove(p)
    sm = start_menu_dir()
    if os.path.isdir(sm):
        shutil.rmtree(sm, ignore_errors=True)
    files = []
    for root, dirs, fs in os.walk(target):
        for f in fs:
            files.append(os.path.join(root, f))
    total = len(files) or 1
    for k, f in enumerate(files):
        try:
            os.remove(f)
        except OSError as e:
            log(f"could not remove {f}: {e}")
        if k % 25 == 0:
            progress(10 + int(75 * k / total), f"Removing {os.path.relpath(f, target)}")
    shutil.rmtree(target, ignore_errors=True)
    log(f"Removed {len(files)} files from {target}")
    progress(88, "Removing registry entries…")
    if os.name == "nt":
        import winreg
        for key in (UNINSTALL_KEY, r"Software\Classes\.nstopo",
                    r"Software\Classes\Applications\VU3INI PacketLab.exe\shell\open\command",
                    r"Software\Classes\Applications\VU3INI PacketLab.exe\shell\open",
                    r"Software\Classes\Applications\VU3INI PacketLab.exe\shell",
                    r"Software\Classes\Applications\VU3INI PacketLab.exe",
                    r"Software\Classes\%s\shell\open\command" % PROGID, r"Software\Classes\%s\shell\open" % PROGID,
                    r"Software\Classes\%s\shell" % PROGID, r"Software\Classes\%s\DefaultIcon" % PROGID,
                    r"Software\Classes\%s" % PROGID):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass
        for ext in (".yaml", ".yml"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\%s\OpenWithProgids" % ext, 0,
                                    winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, PROGID)
            except OSError:
                pass
    if remove_user_data:
        progress(94, "Removing settings, autosaves and add-ons…")
        for d in user_data_dirs():
            shutil.rmtree(d, ignore_errors=True)
        log("Settings, autosaves and the GPU add-on removed (your saved topology files are kept)")
    progress(100, "Uninstall complete")


def schedule_self_delete(path: str) -> None:
    """Delete a temporary uninstaller copy a few seconds after it exits (Windows only)."""
    if os.name != "nt":
        return
    cmd = f'ping 127.0.0.1 -n 4 > nul & del /f /q "{path}"'
    subprocess.Popen(["cmd", "/c", cmd], creationflags=0x08000000 | 0x00000008, close_fds=True)
