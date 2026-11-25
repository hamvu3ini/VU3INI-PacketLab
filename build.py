"""One-click Windows build for VU3INI PacketLab.

Run BUILD.bat (it finds Python 3.11-3.14 and calls this script).  Steps:
  1. pick the version (auto-increments if that version was already released)
  2. create an isolated build environment from the bundled wheels (offline)
  3. run the functional test suite
  4. freeze the app with PyInstaller (windowed, icon, version resource)
  5. optionally download the NVIDIA GPU add-on wheels (needs Internet once; skipped if offline)
  6. build the uninstaller and the setup wizard
  7. write release/VU3INI_PacketLab_Installer_v<version>.zip

Options: --bump (force a new patch version)  --version X.Y.Z  --no-tests  --no-gpu  --online (allow pip downloads)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import venv
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
PKG = os.path.join(APP, "packetlab")
INST = os.path.join(ROOT, "installer")
BUILD = os.path.join(ROOT, "build")
RELEASE = os.path.join(ROOT, "release")
WHEELS = os.path.join(ROOT, "wheels")
NAME = "VU3INI PacketLab"
REQS = ["PySide6-Essentials", "shiboken6", "numpy", "PyYAML", "pyinstaller", "pefile", "pywin32-ctypes"]
GPU_PKGS = ["cupy-cuda12x", "cuda-pathfinder", "nvidia-cuda-runtime-cu12", "nvidia-cuda-nvrtc-cu12",
            "nvidia-curand-cu12"]


def step(msg: str) -> None:
    print("\n" + "=" * 78 + f"\n  {msg}\n" + "=" * 78, flush=True)


def run(cmd, **kw) -> None:
    print("  $ " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd), flush=True)
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        raise SystemExit(f"\nBUILD FAILED at: {' '.join(map(str, cmd[:3]))} (exit code {r.returncode})")


def read_version() -> str:
    with open(os.path.join(ROOT, "version.txt"), encoding="utf-8") as f:
        return f.read().strip()


def write_version(v: str) -> None:
    for p in (os.path.join(ROOT, "version.txt"), os.path.join(PKG, "VERSION")):
        with open(p, "w", encoding="utf-8") as f:
            f.write(v + "\n")


def bump(v: str) -> str:
    a, b, c = (int(x) for x in v.split("."))
    return f"{a}.{b}.{c + 1}"


def release_zip(v: str) -> str:
    return os.path.join(RELEASE, f"VU3INI_PacketLab_Installer_v{v}.zip")


def choose_version(args) -> str:
    v = args.version or read_version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        raise SystemExit(f"bad version '{v}' (use X.Y.Z)")
    if args.bump:
        v = bump(v)
    while os.path.exists(release_zip(v)):
        print(f"  version {v} was already released -> using {bump(v)}")
        v = bump(v)
    write_version(v)
    return v


def make_env(args) -> str:
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    envdir = os.path.join(BUILD, f"venv-{tag}")
    py = os.path.join(envdir, "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")
    if not os.path.exists(py):
        venv.EnvBuilder(with_pip=True, clear=True).create(envdir)
    finds = []
    for d in (os.path.join(WHEELS, "common"), os.path.join(WHEELS, tag)):
        if os.path.isdir(d):
            finds += ["--find-links", d]
    have_local = os.path.isdir(os.path.join(WHEELS, tag))
    base = [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location"]
    if have_local:
        r = subprocess.run(base + ["--no-index"] + finds + REQS)
        if r.returncode == 0:
            return py
        print("  offline install from bundled wheels failed")
    else:
        print(f"  no bundled wheels for Python {sys.version_info.major}.{sys.version_info.minor} ({tag})")
    if not args.online:
        print("  trying the Internet once (use Python 3.11-3.14 to stay fully offline)")
    run(base + finds + REQS)
    return py


def run_tests(py: str) -> None:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=os.pathsep.join([APP, INST]),
               APPDATA=os.path.join(BUILD, "test-appdata"), LOCALAPPDATA=os.path.join(BUILD, "test-localappdata"))
    run([py, "-m", "unittest", "discover", "-s", os.path.join(ROOT, "tests"), "-v"], env=env, cwd=ROOT)


def version_file(v: str, desc: str, original: str) -> str:
    a, b, c = (int(x) for x in v.split("."))
    path = os.path.join(BUILD, f"version_{re.sub(r'[^A-Za-z]', '', original)}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({a},{b},{c},0), prodvers=({a},{b},{c},0), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Sasanka Boruah (VU3INI)'),
      StringStruct('FileDescription', '{desc}'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'PacketLab'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Sasanka Boruah, VU3INI. Personal use only.'),
      StringStruct('OriginalFilename', '{original}'),
      StringStruct('ProductName', '{NAME}'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
""")
    return path


def data_arg(src: str, dest: str) -> list:
    return ["--add-data", f"{src}{os.pathsep}{dest}"]


EXCLUDES = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
            "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf",
            "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtSql",
            "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtOpenGL", "PySide6.QtPositioning"]


def freeze_app(py: str, v: str) -> str:
    dist = os.path.join(BUILD, "dist")
    cmd = [py, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
           "--icon", os.path.join(PKG, "assets", "app.ico"),
           "--version-file", version_file(v, NAME, f"{NAME}.exe"),
           "--distpath", dist, "--workpath", os.path.join(BUILD, "work"), "--specpath", os.path.join(BUILD, "spec"),
           "--paths", APP, "--hidden-import", "yaml", "--hidden-import", "numpy"]
    cmd += data_arg(os.path.join(PKG, "assets"), "packetlab/assets")
    cmd += data_arg(os.path.join(PKG, "help"), "packetlab/help")
    cmd += data_arg(os.path.join(PKG, "samples"), "packetlab/samples")
    cmd += data_arg(os.path.join(PKG, "VERSION"), "packetlab")
    for m in EXCLUDES:
        cmd += ["--exclude-module", m]
    cmd.append(os.path.join(APP, "run_packetlab.py"))
    run(cmd, cwd=ROOT)
    out = os.path.join(dist, NAME)
    if not os.path.exists(os.path.join(out, f"{NAME}.exe" if os.name == "nt" else NAME)):
        raise SystemExit("PyInstaller did not produce the application")
    return out


def zip_dir(src: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _d, files in os.walk(src):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, src))
    print(f"  {os.path.basename(dest)}: {os.path.getsize(dest) / 2 ** 20:.1f} MB")


def gpu_payload(dest_zip: str) -> bool:
    """Fetch CuPy + CUDA runtime wheels for this Python (cached) and pack them as gpu.zip."""
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    cache = os.path.join(BUILD, "gpu-wheels", tag)
    local = os.path.join(WHEELS, "gpu", tag)
    os.makedirs(cache, exist_ok=True)
    files = []
    for pkg in GPU_PKGS:
        found = None
        for d in (local, cache):
            if os.path.isdir(d):
                norm = pkg.replace("-", "_").lower()
                for f in os.listdir(d):
                    if f.lower().startswith(norm + "-") and f.endswith(".whl"):
                        found = os.path.join(d, f)
        if found is None:
            try:
                with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=60) as r:
                    data = json.load(r)
                pick = None
                for u in data.get("urls", []):
                    fn = u["filename"]
                    if fn.endswith(".whl") and ("win_amd64" in fn and (tag in fn or "py3-none" in fn)):
                        pick = u
                        break
                    if fn.endswith("py3-none-any.whl"):
                        pick = pick or u
                if pick is None:
                    print(f"  no Windows wheel for {pkg} ({tag}); GPU add-on skipped")
                    return False
                found = os.path.join(cache, pick["filename"])
                print(f"  downloading {pick['filename']} ({int(pick.get('size', 0)) / 2 ** 20:.0f} MB)")
                with urllib.request.urlopen(pick["url"], timeout=300) as r, open(found + ".part", "wb") as out:
                    shutil.copyfileobj(r, out, 1 << 20)
                os.replace(found + ".part", found)
            except OSError as e:
                print(f"  could not download {pkg}: {e}\n  -> the installer will not include the GPU add-on "
                      f"(it can still be downloaded later from Settings > Compute)")
                return False
        files.append(found)
    root = os.path.join(BUILD, "gpu-root")
    if os.path.isdir(root):
        shutil.rmtree(root)
    for w in files:
        with zipfile.ZipFile(w) as z:
            z.extractall(root)
    zip_dir(root, dest_zip)
    return True


def freeze_installer(py: str, v: str, payload_dir: str) -> tuple:
    dist = os.path.join(BUILD, "dist-installer")
    common = ["--noconfirm", "--clean", "--windowed", "--onefile", "--icon", os.path.join(PKG, "assets", "app.ico"),
              "--workpath", os.path.join(BUILD, "work-inst"), "--specpath", os.path.join(BUILD, "spec"),
              "--distpath", dist, "--paths", INST]
    for m in EXCLUDES + ["numpy", "yaml"]:
        common += ["--exclude-module", m]
    res = []
    for f in ("logo_round.png", "app.ico"):
        res += data_arg(os.path.join(PKG, "assets", f), ".")
    res += data_arg(os.path.join(PKG, "VERSION"), ".")
    res += data_arg(os.path.join(payload_dir, "terms.html"), ".")
    un_name = "Uninstall VU3INI PacketLab"
    run([py, "-m", "PyInstaller"] + common + res + ["--name", un_name,
                                                    "--version-file", version_file(v, f"{NAME} uninstaller",
                                                                                   f"{un_name}.exe"),
                                                    os.path.join(INST, "setup_wizard.py")], cwd=ROOT)
    un_exe = os.path.join(dist, un_name + (".exe" if os.name == "nt" else ""))
    setup_name = f"VU3INI_PacketLab_Setup_v{v}"
    run([py, "-m", "PyInstaller"] + common + res +
        ["--name", setup_name, "--version-file", version_file(v, f"{NAME} setup", f"{setup_name}.exe"),
         os.path.join(INST, "setup_wizard.py")], cwd=ROOT)
    return os.path.join(dist, setup_name + (".exe" if os.name == "nt" else "")), un_exe


README_INSTALL = """VU3INI PacketLab {v} - Installer
===================================

1. Extract this whole zip file (right-click > Extract All...).
2. Open the extracted folder and double-click  VU3INI_PacketLab_Setup_v{v}.exe
3. Follow the wizard. No administrator rights are needed; everything is installed offline.
   Keep the 'data' folder next to the Setup program.

If Windows SmartScreen says "Windows protected your PC", click "More info" > "Run anyway".
(The program is not code-signed with a paid certificate.)

Remove it any time from Windows Settings > Apps > Installed apps, or the
"Uninstall VU3INI PacketLab" Start-menu shortcut.

GPU add-on included: {gpu}

By Sasanka Boruah - VU3INI   ·   hamvu3ini@gmail.com
Personal use only. By installing you accept the Terms of Use (Terms_of_Use.html).
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bump", action="store_true")
    ap.add_argument("--version")
    ap.add_argument("--no-tests", action="store_true")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--online", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    print(f"VU3INI PacketLab build - Python {sys.version.split()[0]} on {sys.platform}")
    if not (3, 11) <= sys.version_info[:2] <= (3, 14):
        raise SystemExit("Please use Python 3.11, 3.12, 3.13 or 3.14 (64-bit).")
    os.makedirs(BUILD, exist_ok=True)
    step("1/7 Version")
    v = choose_version(args)
    print(f"  building version {v}")
    step("2/7 Build environment (bundled wheels, offline)")
    py = make_env(args)
    step("3/7 Functional tests")
    if args.no_tests:
        print("  skipped (--no-tests)")
    else:
        run_tests(py)
    step("4/7 Freezing the application with PyInstaller")
    app_dir = freeze_app(py, v)
    payload = os.path.join(BUILD, "payload")
    if os.path.isdir(payload):
        shutil.rmtree(payload)
    os.makedirs(payload)
    zip_dir(app_dir, os.path.join(payload, "app.zip"))
    sys.path.insert(0, APP)
    from packetlab.gui.texts import TERMS_HTML  # noqa: E402
    with open(os.path.join(payload, "terms.html"), "w", encoding="utf-8") as f:
        f.write(TERMS_HTML)
    step("5/7 NVIDIA GPU add-on (optional)")
    gpu = False
    if args.no_gpu:
        print("  skipped (--no-gpu)")
    else:
        gpu = gpu_payload(os.path.join(payload, "gpu.zip"))
    step("6/7 Setup wizard and uninstaller")
    setup_exe, _un = freeze_installer(py, v, payload)
    step("7/7 Release package")
    rel = os.path.join(RELEASE, f"VU3INI_PacketLab_Installer_v{v}")
    if os.path.isdir(rel):
        shutil.rmtree(rel)
    os.makedirs(os.path.join(rel, "data"))
    shutil.copy2(setup_exe, rel)
    shutil.copy2(os.path.join(payload, "app.zip"), os.path.join(rel, "data"))
    shutil.copy2(_un, os.path.join(rel, "data"))
    if gpu:
        shutil.copy2(os.path.join(payload, "gpu.zip"), os.path.join(rel, "data"))
    shutil.copy2(os.path.join(payload, "terms.html"), os.path.join(rel, "Terms_of_Use.html"))
    with open(os.path.join(rel, "README_INSTALL.txt"), "w", encoding="utf-8") as f:
        f.write(README_INSTALL.format(v=v, gpu="yes" if gpu else "no (download later from Settings > Compute)"))
    zip_dir(rel, release_zip(v))
    print(f"\nDONE in {(time.time() - t0) / 60:.1f} min\n\n  Installer zip:  {release_zip(v)}\n"
          f"  Unzipped copy:  {rel}\n")
    if os.name == "nt":
        subprocess.run(["explorer", RELEASE])


if __name__ == "__main__":
    main()
