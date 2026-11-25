"""Legal text and credits shown on the splash screen and in Help > About."""
from .. import CONTACT_EMAIL

TERMS_HTML = f"""
<h3>VU3INI PacketLab &mdash; Terms of Use, Licence and Disclaimer</h3>
<p><i>Version 2026-11-25. Please read carefully. This is a legal agreement between you and the author,
Sasanka Boruah (VU3INI).</i></p>

<p><b>1. Acceptance.</b> By clicking <b>&ldquo;I Agree&rdquo;</b>, <b>&ldquo;OK&rdquo;</b> or any similar button, or by
installing, copying, opening or otherwise using VU3INI PacketLab (the &ldquo;Software&rdquo;), you confirm that you have
read, understood and agree to be legally bound by these Terms. If you do not agree, click <b>Decline</b>, do not use the
Software and uninstall it. If you accept on behalf of another person you confirm that you have authority to do so.</p>

<p><b>2. Personal use licence.</b> The author grants you a personal, non-exclusive, non-transferable, revocable,
royalty-free licence to install and use the Software for your own <b>personal, non-commercial</b> learning, study and
experimentation. You must not sell, rent, lease, sub-license, publish for a fee, offer as a hosted or paid service, or
otherwise use the Software for commercial gain without the author&rsquo;s prior written permission. You must not remove
or alter the author&rsquo;s name, logo, copyright notices or these Terms.</p>

<p><b>3. Nature of the Software.</b> The Software is an educational design and simulation tool. Its simulations,
measurements, test verdicts, timings and exported configurations are <b>models and approximations</b> and may be
incomplete or wrong. They are not a certification, professional engineering advice, or a guarantee of how any real
network or device will behave. Do not rely on the Software for production, safety-critical, life-critical, financial,
legal or regulatory decisions. Always verify designs against vendor documentation and on real equipment.</p>

<p><b>4. Your responsibility.</b> You alone are responsible for how you use the Software and its outputs, including any
configuration or file you export and apply to real systems. You must comply with all laws and regulations that apply
to you, including, where applicable in India, the Information Technology Act, 2000, the Digital Personal Data
Protection Act, 2023 and the Telecommunications Act, 2023, and the equivalent laws of your country. You must only
emulate, scan, connect to or test networks and devices that you own or are expressly authorised to test. Unauthorised
access to computer systems is a criminal offence.</p>

<p><b>5. Third-party software, names and images.</b> The Software includes or works with open-source components that
remain under their own licences (see &ldquo;With thanks to&rdquo;). Optional components you choose to download (for
example the GPU add-on or network operating-system container images) are provided by third parties under their own
terms, which you must accept and follow. Product and company names such as Cisco, Packet Tracer, Juniper, Junos,
Arista, Nokia, Wireshark, NVIDIA, CUDA, Microsoft and Windows are trademarks of their respective owners. The Software is
independent and is <b>not affiliated with, sponsored or endorsed by</b> any of them.</p>

<p><b>6. Privacy and Internet use.</b> The Software works offline. It connects to the Internet only when you start an
action that needs it, such as checking for updates, downloading the optional GPU add-on, or deploying a lab whose
container images must be downloaded. The author does not collect, receive or store your personal data, projects or
usage information; your files stay on your computer.</p>

<p><b>7. No warranty.</b> THE SOFTWARE IS PROVIDED &ldquo;AS IS&rdquo; AND &ldquo;AS AVAILABLE&rdquo;, WITHOUT ANY
WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE,
ACCURACY, RELIABILITY, AVAILABILITY OR NON-INFRINGEMENT, TO THE FULLEST EXTENT PERMITTED BY LAW.</p>

<p><b>8. Limitation of liability.</b> TO THE FULLEST EXTENT PERMITTED BY LAW, IN NO EVENT SHALL THE AUTHOR OR ANY
CONTRIBUTOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, PUNITIVE OR CONSEQUENTIAL LOSS OR
DAMAGE, INCLUDING LOSS OF DATA, PROFITS, REVENUE, BUSINESS OR GOODWILL, NETWORK DOWNTIME, SERVICE INTERRUPTION OR DAMAGE
TO HARDWARE, ARISING OUT OF OR IN CONNECTION WITH THE SOFTWARE OR ITS USE, HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE. Where liability cannot lawfully be excluded, it is limited
to the amount you paid for the Software, which is nil.</p>

<p><b>9. Indemnity.</b> You agree to indemnify, defend and hold harmless the author from and against any claim,
demand, loss, liability, damage, penalty, cost or expense (including reasonable legal fees) arising from your use or
misuse of the Software, your breach of these Terms, or your violation of any law or the rights of any third party.</p>

<p><b>10. Termination.</b> This licence ends automatically, without notice, if you breach these Terms. On termination
you must stop using the Software and delete all copies. Sections 3 to 12 survive termination.</p>

<p><b>11. Changes.</b> A future version of the Software may come with updated Terms. Continuing to use that version
after it shows you the updated Terms means you accept them.</p>

<p><b>12. Governing law and jurisdiction.</b> These Terms are governed by the laws of India. The courts at Guwahati,
Assam shall have exclusive jurisdiction over any dispute arising out of or relating to the Software or these Terms.
If any provision is held invalid or unenforceable, the remaining provisions remain in full force. These Terms are the
entire agreement between you and the author about the Software.</p>

<p><b>13. Contact.</b> Questions about these Terms: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p>

<p><b>By clicking &ldquo;I Agree&rdquo; / &ldquo;OK&rdquo;, you confirm that you accept and will abide by these Terms of
Use, that you use the Software for personal use only, and that you are solely responsible for its use.</b></p>
"""

# (name, creators, role, licence)
CREDITS = [
    ("Bundled software (runs inside PacketLab)", None, None, None),
    ("Python", "Guido van Rossum and the Python Software Foundation", "programming language and runtime", "PSF License"),
    ("Qt 6", "The Qt Company and the Qt Project contributors", "user-interface framework", "LGPL v3"),
    ("PySide6 / Shiboken6", "The Qt for Python team at The Qt Company", "Python bindings for Qt", "LGPL v3"),
    ("NumPy", "Travis E. Oliphant and the NumPy developers", "numerical computing on the CPU", "BSD 3-Clause"),
    ("PyYAML", "Kirill Simonov, Ingy döt Net and contributors", "reading and writing topology files", "MIT"),
    ("Pillow", "Jeffrey A. Clark (Alex) and contributors, after PIL by Fredrik Lundh", "image handling", "MIT-CMU"),
    ("PyInstaller", "Hartmut Goebel, Giovanni Bajo, David Vierra, David Cortesi and the PyInstaller team",
     "packaging into a Windows program", "GPL v2 with bootloader exception"),
    ("Optional GPU add-on (downloaded only if you choose)", None, None, None),
    ("CuPy", "Preferred Networks, Inc. and the CuPy developers", "GPU computing on NVIDIA cards", "MIT"),
    ("NVIDIA CUDA runtime", "NVIDIA Corporation", "GPU runtime libraries", "NVIDIA CUDA EULA"),
    ("Optional emulation tools (run on your Linux / WSL2 host)", None, None, None),
    ("Containerlab", "Roman Dodin, Karim Radhouani, Wim Henderickx and the srl-labs community",
     "deploys exported labs; its GUI ideas shaped the canvas", "BSD 3-Clause"),
    ("FRRouting", "The FRRouting Project community", "real routing software for emulation", "GPL v2+"),
    ("Kathará", "The Kathará team, Roma Tre University", "alternative emulation target", "GPL v3"),
    ("Projects whose ideas inspired PacketLab (studied, no code copied)", None, None, None),
    ("Cisco Packet Tracer", "Cisco Systems, Inc.", "the realtime / simulation-mode learning experience", ""),
    ("GNS3", "Jeremy Grossmann and the GNS3 team", "graphical network emulation", ""),
    ("CORE", "Boeing Research & Technology and the U.S. Naval Research Laboratory", "hybrid emulation ideas", ""),
    ("Mininet", "Bob Lantz, Brandon Heller and the Mininet team", "lightweight network emulation", ""),
    ("netlab", "Ivan Pepelnjak (ipSpace.net) and contributors", "describe-once, generate-many topology model", ""),
    ("Batfish", "Intentionet and the Batfish community", "static configuration analysis", ""),
    ("Filius", "Stefan Freischlad and contributors", "beginner-friendly network teaching", ""),
    ("netemu", "Gadzhik", "multi-vendor CLI and auto-checked labs", ""),
    ("netlabcraft", "ayozetr", "layered discrete-event simulator design", ""),
    ("OpenNetSim", "ganeshparajuli11", "compact browser simulator engine", ""),
    ("NetSim", "joxorsayan", "declarative labs with assertions", ""),
    ("yanas net", "yagma-tunita", "OSPF and 802.1Q modelling detail", ""),
    ("Cisco Packet Tracer Web (NetCanvas)", "Akgmiz", "OSI / Wireshark-style PDU inspector", ""),
    ("PackeTTrino", "Amín Pérez", "packet animation and state tables", ""),
    ("NetworkCongestionSimulator", "Nsathwikvarma", "WAN bottleneck modelling", ""),
    ("TopoViewer", "FloSch62 and the srl-labs community", "Cytoscape-style topology viewing", ""),
    ("Antimony", "Tom Stromer and Kian Gribi (OST)", "Containerlab topology dashboard", ""),
    ("Wireshark / pcapng", "Gerald Combs and the Wireshark community", "capture file format and analysis", ""),
    ("Standards", None, None, None),
    ("IEEE 802, IETF RFCs, ITU-T, MEF Forum", "Their working groups and editors",
     "the standards PacketLab models (802.1Q, 802.1w, RFC 2328, RFC 4271, RFC 5798, G.8032, Y.1564, RFC 2544 ...)", ""),
]


def credits_html() -> str:
    out = ["<p>VU3INI PacketLab was written by <b>Sasanka Boruah (VU3INI)</b>. Heartfelt thanks to the creators of "
           "every library, tool, project and standard below.</p>"]
    for name, who, role, lic in CREDITS:
        if who is None:
            out.append(f"<h4 style='margin-bottom:2px'>{name}</h4>")
        else:
            extra = f" <span style='opacity:.7'>({lic})</span>" if lic else ""
            out.append(f"<p style='margin:2px 0'><b>{name}</b> &mdash; {who}<br>"
                       f"<span style='opacity:.8'>{role}</span>{extra}</p>")
    return "\n".join(out)
