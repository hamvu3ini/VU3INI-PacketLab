# VU3INI PacketLab — Build Kit

**Design, simulate and test LAN, MAN and WAN networks to current industry standards — fully offline on Windows 10/11.**
By Sasanka Boruah – VU3INI · hamvu3ini@gmail.com

---

## 1. Build the Windows installer (one double-click)

1. Extract this whole zip to a folder, for example `C:\VU3INI\PacketLab-kit`.
2. Double-click **`BUILD.bat`**.
3. About 3–6 minutes later, the **`release`** folder opens and contains:

```
release\VU3INI_PacketLab_Installer_v1.0.0.zip     <- carry / share this
release\VU3INI_PacketLab_Installer_v1.0.0\
    VU3INI_PacketLab_Setup_v1.0.0.exe              <- the setup wizard
    data\app.zip                                   <- the application (offline)
    data\gpu.zip                                   <- NVIDIA GPU add-on (if it could be fetched)
    data\Uninstall VU3INI PacketLab.exe
    README_INSTALL.txt, Terms_of_Use.html
```

**Requirement on the build PC:** 64-bit Python 3.11, 3.12, 3.13 or 3.14 installed, with "Add python.exe to PATH" ticked or
the `py` launcher present. All Python libraries are already in `wheels\`, so the build works **offline**.

**Version numbers:** the version is read from `version.txt`. If that version was already built, the next patch number is
used automatically (1.0.0 → 1.0.1 …), so a release is never overwritten. Options:

| Command | Effect |
|---|---|
| `BUILD.bat` | normal build (tests + GPU add-on if the Internet is available) |
| `BUILD.bat --bump` | force a new patch version |
| `BUILD.bat --version 1.2.0` | set the version |
| `BUILD.bat --no-gpu` | leave the GPU add-on out (smaller installer, no Internet needed at all) |
| `BUILD.bat --no-tests` | skip the functional test suite (not recommended) |

## 2. Install on any Windows 10/11 PC (offline)

Extract `VU3INI_PacketLab_Installer_v1.0.0.zip`, run `VU3INI_PacketLab_Setup_v1.0.0.exe`, and follow the wizard:
Welcome → Terms of Use (must accept) → Install location → Options (Start-menu and desktop shortcuts, "Open with"
for .yaml topologies, NVIDIA GPU add-on) → Ready → progress bar with live status → Finish (launch).

- No administrator rights are needed (per-user install in `%LOCALAPPDATA%\Programs\VU3INI PacketLab`).
- It appears in **Settings › Apps › Installed apps** with a working **Uninstall**. There is also an Uninstall shortcut
  in the Start menu.
- Upgrades: run a newer Setup; it replaces the program and keeps your settings and networks.
- SmartScreen may say "Windows protected your PC" because the exe is not code-signed. Click **More info › Run anyway**.

## 3. What is inside the app

- **Canvas** with routers, L3 switches, switches, firewalls, PCs, servers, Wi-Fi APs and an Internet cloud. Site zones
  (LAN blue, MAN green, WAN orange) and spanning-tree blocked links drawn dashed.
- **Topology file** (`*.nstopo.yaml`, the NSTopo format from your spec), kept in sync with the canvas, the Properties
  panel and the consoles. Undo/redo, validation with exact error locations.
- **Simulation engine** covering 802.1Q VLANs and trunks, per-VLAN RSTP, LACP, G.8032 rings, ARP, IPv4 longest-prefix
  forwarding, OSPF, IS-IS, BGP (local-pref, AS-path, iBGP/eBGP), MPLS LDP label switching, VRRPv3, DHCP with relay, DNS,
  NAT overload, stateful ACLs and IPsec/GRE tunnels.
- **Realtime and Simulation modes:** step-by-step events, a PDU inspector (layers and hex), packet animation, and
  **pcapng** captures for Wireshark.
- **IOS-style CLI** on every device (abbreviations, history, Tab completion), plus a Windows-style prompt on hosts.
- **Test runner:** static, functional and performance checks (RFC 2544, ITU-T Y.1564, TWAMP), failure scenarios with
  failover times from protocol timers, and JSON + JUnit export.
- **CPU / GPU / hybrid compute** (NumPy and CuPy) for path matrices and Monte-Carlo statistics, with automatic CPU fallback
  on any GPU error or out-of-memory.
- **Emulation:** export to Containerlab (FRRouting configs plus tc-netem impairments) or Kathará, and deploy via WSL2,
  SSH or local Containerlab.
- Dark/light themes, a splash screen with Terms of Use and credits (closes only on OK), searchable Help, Help › About
  with the contact e-mail, and a Settings window with 80 options in 10 groups. Logo at top left; footer
  "By Sasanka Boruah -VU3INI" at bottom left.

## 4. Folder layout

```
BUILD.bat              one-click build          RUN_FROM_SOURCE.bat   run without building
build.py               build pipeline           version.txt           current version
app\                   application source (packetlab package, samples, help, assets)
installer\             setup wizard + uninstaller (installer_core.py is GUI-free and tested)
tests\                 46 functional tests (engine, CLI, runner, exports, pcapng, GUI, installer)
wheels\                offline Windows wheels for Python 3.11-3.14
```

## 5. Notes

- Containerlab, Kathará, GNS3, CORE and similar emulators run only on Linux/Docker, so they are not bundled. PacketLab
  has its own simulator and exports labs to them (see Help › Emulation).
- The GPU add-on (CuPy + CUDA 12.9 runtime, about 240 MB) is downloaded once at build time and put into the installer,
  so installation stays offline. Without it, everything runs on the CPU.
- The Terms of Use were drafted carefully but are not legal advice. Have a lawyer review them before wide distribution.
