"""Emulation back ends: run the exported lab on real network operating systems.

Containerlab and Kathara need Linux with Docker.  On Windows 11 PacketLab drives
them through WSL2 (``wsl.exe``) or over SSH to a Linux machine (for example a lab
server).  This module only builds and runs commands; it never needs admin rights
on Windows itself.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
from typing import Callable, List, Optional


def win_to_wsl(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        return f"/mnt/{p[0].lower()}{p[2:]}"
    return p


class Emulator:
    def __init__(self, settings: dict, log: Callable[[str], None]):
        self.s = settings
        self.log = log
        self.proc: Optional[subprocess.Popen] = None

    @property
    def backend(self) -> str:
        return self.s.get("emu_backend", "none")

    def available(self) -> str:
        b = self.backend
        if b == "wsl":
            return "ok" if shutil.which("wsl") or shutil.which("wsl.exe") else "wsl.exe not found (install WSL2)"
        if b == "ssh":
            if not self.s.get("emu_ssh_target"):
                return "set the SSH target (user@host) in Settings > Emulation"
            return "ok" if shutil.which("ssh") or shutil.which("ssh.exe") else "OpenSSH client not found"
        if b == "local":
            return "ok" if shutil.which(self.s.get("emu_clab_cmd", "containerlab")) else "containerlab not on PATH"
        return "no emulation back end selected (Settings > Emulation)"

    def command(self, action: str, clab_file: str) -> List[str]:
        clab = self.s.get("emu_clab_cmd", "containerlab")
        sudo = "sudo " if self.s.get("emu_sudo", True) else ""
        b = self.backend
        if b == "wsl":
            d = win_to_wsl(os.path.dirname(clab_file))
            f = os.path.basename(clab_file)
            inner = f"cd {shlex.quote(d)} && {sudo}{clab} {action} -t {shlex.quote(f)}"
            if action == "deploy":
                inner += " && sh ./apply-impairments.sh"
            cmd = ["wsl"]
            if self.s.get("emu_wsl_distro"):
                cmd += ["-d", self.s["emu_wsl_distro"]]
            return cmd + ["--", "bash", "-lc", inner]
        if b == "ssh":
            remote_dir = self.s.get("emu_remote_dir", "~/packetlab")
            f = os.path.basename(clab_file)
            inner = f"cd {remote_dir} && {sudo}{clab} {action} -t {shlex.quote(f)}"
            if action == "deploy":
                inner += " && sh ./apply-impairments.sh"
            return ["ssh", self.s["emu_ssh_target"], inner]
        if b == "local":
            return [clab, action, "-t", clab_file]
        raise RuntimeError("no emulation back end selected")

    def upload_command(self, folder: str) -> Optional[List[str]]:
        if self.backend != "ssh":
            return None
        remote_dir = self.s.get("emu_remote_dir", "~/packetlab")
        return ["scp", "-r", os.path.join(folder, "."), f"{self.s['emu_ssh_target']}:{remote_dir}"]

    def run(self, cmd: List[str], done: Optional[Callable[[int], None]] = None) -> None:
        """Run a command in a background thread, streaming output to the log."""
        def worker():
            self.log("$ " + " ".join(cmd))
            try:
                flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                             errors="replace", creationflags=flags)
                for line in self.proc.stdout:  # type: ignore[union-attr]
                    self.log(line.rstrip())
                rc = self.proc.wait()
            except OSError as e:
                self.log(f"could not start: {e}")
                rc = -1
            self.log(f"[exit code {rc}]")
            if done:
                done(rc)
        threading.Thread(target=worker, daemon=True).start()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
