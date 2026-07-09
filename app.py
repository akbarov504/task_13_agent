import os
import re
import sys
import time
import json
import socket
import shutil
import signal
import platform
import threading
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime

import websocket

class Colors:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"
    BLUE   = "\033[94m"
    WHITE  = "\033[97m"

TOKEN_URL      = "http://127.0.0.1:8787/token"
API_BASE_URL   = "https://dev-gw.tracksafe365.com"
AI_RELEASE_URL = API_BASE_URL + "/services/glsmanagement/api/ai-release/download-url/{model}/{version}"
SW_RELEASE_URL = API_BASE_URL + "/services/glsmanagement/api/software-release/download-url/{model}/{version}"

WS_URL   = "wss://dev-gw.tracksafe365.com/services/glsstream/stream"
WS_HOST  = "dev-gw.tracksafe365.com"
WS_TOPIC = "/topic/agent/command"

TYPE_MAP = {"ai": "ai", "software": "sw"}

BASE_DIR     = Path(os.path.dirname(os.path.abspath(sys.argv[0])))
AI_DIR       = BASE_DIR / "ai"
SW_DIR       = BASE_DIR / "software"
AI_DIR.mkdir(exist_ok=True)
SW_DIR.mkdir(exist_ok=True)

STATE_FILE = BASE_DIR / "agent_state.json"

_running_processes: dict = {}
_cached_token: str | None = None
_token_lock = threading.Lock()
_state_lock = threading.Lock()
_ws_action_lock = threading.Lock()

def get_token() -> str | None:
    global _cached_token
    with _token_lock:
        try:
            req = urllib.request.Request(TOKEN_URL)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())

            token = data.get("token")
            if not token:
                token = data.get("access_token")

            if not token and isinstance(data.get("result"), dict):
                token = data["result"].get("token")

            if token:
                _cached_token = str(token)
                return _cached_token

            print(f"  {Colors.RED}Token topilmadi. Response: {data}{Colors.RESET}")
            return None
        
        except Exception as e:
            print(f"  {Colors.RED}Token olishda xato: {e}{Colors.RESET}")
            return None

def get_download_url(kind: str, model: str, version: str) -> str | None:
    token = get_token()
    if not token:
        return None

    if kind == "ai":
        url = AI_RELEASE_URL.format(model=model, version=version)
    else:
        url = SW_RELEASE_URL.format(model=model, version=version)

    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return data.get("url")
    
    except urllib.error.HTTPError as e:
        print(f"  {Colors.RED}API xato {e.code}: {e.reason}{Colors.RESET}")
        return None
    
    except Exception as e:
        print(f"  {Colors.RED}Download URL olishda xato: {e}{Colors.RESET}")
        return None

def download_file(url: str, dest: Path) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"  {Colors.CYAN}Yuklanmoqda...{Colors.RESET}", end="", flush=True)

        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 8192
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        print(f"\r  {Colors.CYAN}Yuklanmoqda... {pct}%{Colors.RESET}  ", end="", flush=True)

        print(f"\r  {Colors.GREEN}✔ Yuklandi: {dest}{Colors.RESET}          ")
        dest.chmod(0o755)
        return True
    
    except Exception as e:
        print(f"\r  {Colors.RED}✘ Yuklashda xato: {e}{Colors.RESET}")
        return False

def meta_path(kind: str) -> Path:
    base = AI_DIR if kind == "ai" else SW_DIR
    return base / "installed.json"

def load_meta(kind: str) -> dict:
    p = meta_path(kind)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}

def save_meta(kind: str, meta: dict):
    meta_path(kind).write_text(json.dumps(meta, indent=2))

def get_binary_path(kind: str, model: str, version: str) -> Path:
    base = AI_DIR if kind == "ai" else SW_DIR
    return base / model / version / "app.bin"

def cmd_install(kind: str, model: str, version: str):
    label = "AI" if kind == "ai" else "Software"
    print(f"\n{Colors.YELLOW}{label} '{model} v{version}' o'rnatilmoqda...{Colors.RESET}")

    dl_url = get_download_url(kind, model, version)
    if not dl_url:
        print(f"  {Colors.RED}✘ Download URL olinmadi.{Colors.RESET}\n")
        return

    dest = get_binary_path(kind, model, version)

    if dest.parent.exists():
        shutil.rmtree(dest.parent)

    if not download_file(dl_url, dest):
        return

    BINARY_MAGIC = [
        b"\x7fELF",
        b"\xcf\xfa\xed\xfe",
        b"\xce\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"MZ",
    ]
    try:
        with open(dest, "rb") as f:
            magic = f.read(4)
        is_binary = any(magic.startswith(m) for m in BINARY_MAGIC)

        if not is_binary:
            print(f"  {Colors.RED}✘ Yuklangan fayl binary emas!{Colors.RESET}")
            print(f"  {Colors.YELLOW}Fayl boshi: {magic}{Colors.RESET}")
            dest.unlink(missing_ok=True)
            return

        if magic.startswith(b"\x7fELF"):
            btype = "Linux ELF"
        elif magic[0:2] == b"MZ":
            btype = "Windows PE"
        else:
            btype = "macOS Mach-O"
        print(f"  {Colors.CYAN}Binary turi: {btype}{Colors.RESET}")

    except Exception as e:
        print(f"  {Colors.YELLOW}⚠ Binary tekshirishda xato: {e}{Colors.RESET}")

    meta = load_meta(kind)
    if model not in meta:
        meta[model] = {}
    meta[model][version] = {
        "installed_at": datetime.now().isoformat(),
        "path": str(dest)
    }

    save_meta(kind, meta)
    print(f"  {Colors.GREEN}✔ {label} '{model} v{version}' tayyor!{Colors.RESET}\n")

def cmd_list(kind: str):
    label = "AI" if kind == "ai" else "Software"
    meta = load_meta(kind)

    print(f"\n{Colors.BOLD}O'rnatilgan {label} modellari:{Colors.RESET}")
    if not meta:
        print(f"  {Colors.YELLOW}Hech narsa o'rnatilmagan{Colors.RESET}")
    else:
        for model, versions in meta.items():
            print(f"\n  {Colors.CYAN}{model}{Colors.RESET}")
            for ver, info in versions.items():
                key = f"{kind}/{model}/{ver}"
                running = key in _running_processes and _running_processes[key].poll() is None
                status = f"{Colors.GREEN}● ISHLAYAPTI{Colors.RESET}" if running else f"{Colors.RED}○ to'xtagan{Colors.RESET}"
                print(f"    v{ver}  {status}  [{info['installed_at'][:10]}]")
    print()

def cmd_run(kind: str, model: str, version: str):
    label = "AI" if kind == "ai" else "Software"
    key = f"{kind}/{model}/{version}"

    if key in _running_processes and _running_processes[key].poll() is None:
        print(f"\n  {Colors.YELLOW}⚠ '{model} v{version}' allaqachon ishlayapti (PID: {_running_processes[key].pid}){Colors.RESET}\n")
        return

    binary = get_binary_path(kind, model, version)
    if not binary.exists():
        print(f"\n  {Colors.RED}✘ Binary topilmadi: {binary}{Colors.RESET}")
        print(f"  Avval o'rnating: {Colors.CYAN}{kind} install {model} {version}{Colors.RESET}\n")
        return

    log_dir = binary.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{model}_{version}.log"

    print(f"\n{Colors.YELLOW}{label} '{model} v{version}' ishga tushirilmoqda...{Colors.RESET}")
    print(f"  Log fayl: {log_file}")

    try:
        log_fd = open(log_file, "a")
        log_fd.write(f"\n{'='*50}\nStarted: {datetime.now().isoformat()}\n{'='*50}\n")
        log_fd.flush()

        proc = subprocess.Popen(
            [str(binary)],
            stdout=log_fd,
            stderr=log_fd,
            cwd=str(binary.parent)
        )
        _running_processes[key] = proc
        print(f"  {Colors.GREEN}✔ Ishga tushdi! PID: {proc.pid}{Colors.RESET}\n")

    except Exception as e:
        print(f"  {Colors.RED}✘ Ishga tushirishda xato: {e}{Colors.RESET}\n")

def cmd_stop(kind: str, model: str, version: str):
    key = f"{kind}/{model}/{version}"

    if key not in _running_processes or _running_processes[key].poll() is not None:
        print(f"\n  {Colors.YELLOW}'{model} v{version}' ishlamayapti.{Colors.RESET}\n")
        return

    proc = _running_processes[key]
    print(f"\n{Colors.YELLOW}'{model} v{version}' to'xtatilmoqda (PID: {proc.pid})...{Colors.RESET}")
    proc.terminate()

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        print(f"  {Colors.RED}Force kill qilindi.{Colors.RESET}")

    del _running_processes[key]
    print(f"  {Colors.GREEN}✔ To'xtatildi.{Colors.RESET}\n")

def cmd_remove(kind: str, model: str, version: str):
    label = "AI" if kind == "ai" else "Software"
    key = f"{kind}/{model}/{version}"

    if key in _running_processes and _running_processes[key].poll() is None:
        cmd_stop(kind, model, version)

    binary = get_binary_path(kind, model, version)
    if binary.parent.exists():
        shutil.rmtree(binary.parent)
        print(f"  {Colors.GREEN}✔ {label} '{model} v{version}' o'chirildi.{Colors.RESET}")
    else:
        print(f"  {Colors.YELLOW}Fayl topilmadi: {binary.parent}{Colors.RESET}")

    meta = load_meta(kind)
    if model in meta and version in meta[model]:
        del meta[model][version]
        if not meta[model]:
            del meta[model]
        save_meta(kind, meta)
    print()

def cmd_logs(kind: str, model: str, version: str, lines: int = 50):
    binary = get_binary_path(kind, model, version)
    log_file = binary.parent / "logs" / f"{model}_{version}.log"

    if not log_file.exists():
        print(f"\n  {Colors.YELLOW}Log fayl topilmadi: {log_file}{Colors.RESET}\n")
        return

    print(f"\n{Colors.BOLD}Log: {log_file} (oxirgi {lines} qator){Colors.RESET}")
    print(Colors.CYAN + "─" * 55 + Colors.RESET)

    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), str(log_file)],
            capture_output=True, text=True
        )
        print(result.stdout)
    except Exception as e:
        print(f"  {Colors.RED}Log o'qishda xato: {e}{Colors.RESET}")

    print(Colors.CYAN + "─" * 55 + Colors.RESET + "\n")

def check_can_bus():
    try:
        result = subprocess.run(
            ["ip", "link", "show", "type", "can"],
            capture_output=True, text=True, timeout=5
        )

        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            interfaces = []

            for line in lines:
                if ":" in line and "can" in line.lower():
                    iface = line.split(":")[1].strip().split("@")[0].strip()
                    state = "UP" if "UP" in line else "DOWN"
                    interfaces.append(f"{iface}({state})")

            if interfaces:
                return True, f"Topildi: {', '.join(interfaces)}"
            
            return False, "CAN interfeys topilmadi"
        else:
            net_path = "/sys/class/net"
            if os.path.exists(net_path):
                ifaces = os.listdir(net_path)
                can_ifaces = [i for i in ifaces if i.startswith("can")]

                if can_ifaces:
                    return True, f"Topildi: {', '.join(can_ifaces)}"
                
            return False, "CAN interfeys topilmadi"
        
    except FileNotFoundError:
        try:
            result = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5)

            if "can" in result.stdout.lower():
                return True, "CAN interfeys mavjud (ifconfig)"
            
            return False, "CAN interfeys topilmadi"
        
        except Exception as e:
            return False, f"Tekshirib bo'lmadi: {e}"
        
    except Exception as e:
        return False, f"Xato: {e}"

def check_internet():
    hosts = [("8.8.8.8", 53), ("1.1.1.1", 53), ("208.67.222.222", 53)]
    for host, port in hosts:
        try:
            sock = socket.create_connection((host, port), timeout=3)
            sock.close()
            ping_result = subprocess.run(
                ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
                capture_output=True, text=True, timeout=5
            )
            if ping_result.returncode == 0:
                match = re.search(r"time=([\d.]+)", ping_result.stdout)
                if match:
                    return True, f"Ulangan | Ping: {match.group(1)} ms"
            return True, "Ulangan (TCP OK)"
        
        except (socket.timeout, socket.error, OSError):
            continue

        except Exception:
            continue

    return False, "Internet yo'q"

def check_gps():
    status_list = []
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "gpsd"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip() == "active":
            status_list.append("gpsd:active")
        else:
            status_list.append(f"gpsd:{result.stdout.strip()}")

    except Exception:
        pass

    try:
        import socket as sock_module
        s = sock_module.socket(sock_module.AF_INET, sock_module.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("127.0.0.1", 2947))
        s.send(b'?WATCH={"enable":true,"json":true}')
        data = s.recv(4096).decode("utf-8", errors="ignore")
        s.close()
        if "TPV" in data or "SKY" in data or "class" in data:
            return True, "GPS ma'lumot kelmoqda (gpsd)"
        status_list.append("gpsd:bog'landi lekin ma'lumot yo'q")

    except ConnectionRefusedError:
        status_list.append("gpsd:ishlamayapti")

    except Exception:
        pass

    gps_ports = []
    for dev in ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0",
                "/dev/ttyACM1", "/dev/ttyS0", "/dev/serial0"]:
        if os.path.exists(dev):
            gps_ports.append(dev)

    if gps_ports:
        return True, f"GPS port topildi: {', '.join(gps_ports)}"

    try:
        result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
        for keyword in ["GPS", "u-blox", "SiRF", "MTK", "Globalsat", "Garmin"]:
            if keyword.lower() in result.stdout.lower():
                return True, f"GPS USB qurilma topildi: {keyword}"
            
    except Exception:
        pass

    if status_list:
        return False, " | ".join(status_list)
    
    return False, "GPS qurilma topilmadi"

def get_python_version():
    return platform.python_version()

def get_available_python_versions():
    versions = {}
    for i in range(6, 15):
        cmd = f"python3.{i}"
        path = shutil.which(cmd)
        if path:
            try:
                r = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=3)
                ver = r.stdout.strip() or r.stderr.strip()
                versions[cmd] = ver
            except Exception:
                pass

    for cmd in ["python3", "python"]:
        path = shutil.which(cmd)
        if path:
            try:
                r = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=3)
                ver = r.stdout.strip() or r.stderr.strip()
                if ver:
                    versions[cmd] = ver
            except Exception:
                pass

    return versions

def print_status():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    can_ok, can_msg = check_can_bus()
    net_ok, net_msg = check_internet()
    gps_ok, gps_msg = check_gps()
    py_ver          = get_python_version()
    ws_ok           = ws_client._connected.is_set() if ws_client else False

    def status_icon(ok):
        return f"{Colors.GREEN}✔ ISHLAYAPTI{Colors.RESET}" if ok else f"{Colors.RED}✘ ISHLAMAYAPTI{Colors.RESET}"

    sep = Colors.CYAN + "─" * 55 + Colors.RESET
    print(f"\n{sep}")
    print(f"  {Colors.BOLD}{Colors.WHITE}SYSTEM AGENT  [{now}]{Colors.RESET}")
    print(sep)
    print(f"  {Colors.YELLOW}CAN Bus :{Colors.RESET} {status_icon(can_ok)}  — {can_msg}")
    print(f"  {Colors.YELLOW}Internet:{Colors.RESET} {status_icon(net_ok)}  — {net_msg}")
    print(f"  {Colors.YELLOW}GPS     :{Colors.RESET} {status_icon(gps_ok)}  — {gps_msg}")
    print(f"  {Colors.YELLOW}WS/STOMP:{Colors.RESET} {status_icon(ws_ok)}  — {WS_TOPIC}")
    print(f"  {Colors.YELLOW}Python  :{Colors.RESET} {Colors.BLUE}v{py_ver}{Colors.RESET}")
    print(sep)

def cmd_python_version():
    ver = get_python_version()
    avail = get_available_python_versions()
    print(f"\n{Colors.BOLD}Hozirgi Python:{Colors.RESET} {Colors.GREEN}v{ver}{Colors.RESET}")

    if avail:
        print(f"{Colors.BOLD}Tizimda mavjud:{Colors.RESET}")
        for cmd, info in avail.items():
            print(f"  {Colors.CYAN}{cmd}{Colors.RESET} → {info}")
    else:
        print(f"  {Colors.YELLOW}Boshqa versiyalar topilmadi{Colors.RESET}")

def _pkg_exists(pkg_name):
    r = subprocess.run(["apt-cache", "show", pkg_name], capture_output=True, text=True, timeout=5)
    return r.returncode == 0

def cmd_install_python(version: str):
    parts = version.split(".")
    if len(parts) < 2:
        print(f"{Colors.RED}Noto'g'ri format. Misol: 3.11{Colors.RESET}")
        return
    
    apt_version = ".".join(parts[:2])
    if len(parts) > 2:
        print(f"  {Colors.YELLOW}⚠ '{version}' → '{apt_version}' ishlatiladi{Colors.RESET}")

    version = apt_version
    try:
        minor = int(parts[1])
    except ValueError:
        minor = 0

    print(f"\n{Colors.YELLOW}Python {version} o'rnatilmoqda...{Colors.RESET}")
    for cmd in [
        ["sudo", "apt-get", "update", "-y"],
        ["sudo", "add-apt-repository", "-y", "ppa:deadsnakes/ppa"],
    ]:
        print(f"  {Colors.CYAN}$ {' '.join(cmd)}{Colors.RESET}")
        result = subprocess.run(cmd, text=True)

        if result.returncode != 0:
            print(f"  {Colors.RED}Xato yuz berdi! To'xtatildi.{Colors.RESET}")
            return
        
        print(f"  {Colors.GREEN}✔ OK{Colors.RESET}")

    pkgs = [f"python{version}", f"python{version}-venv"]
    if minor <= 11 and _pkg_exists(f"python{version}-distutils"):
        pkgs.append(f"python{version}-distutils")

    install_cmd = ["sudo", "apt-get", "install", "-y"] + pkgs
    print(f"  {Colors.CYAN}$ {' '.join(install_cmd)}{Colors.RESET}")
    result = subprocess.run(install_cmd, text=True)

    if result.returncode != 0:
        print(f"  {Colors.RED}Xato yuz berdi!{Colors.RESET}")
        return
    
    print(f"  {Colors.GREEN}Python {version} muvaffaqiyatli o'rnatildi!{Colors.RESET}")
    path = shutil.which(f"python{version}")
    if path:
        print(f"  Joylashuvi: {path}")

def cmd_remove_python(version: str):
    print(f"\n{Colors.RED}Python {version} o'chirilmoqda...{Colors.RESET}")
    cmd = ["sudo", "apt-get", "remove", "--purge", "-y", f"python{version}"]
    print(f"  {Colors.CYAN}$ {' '.join(cmd)}{Colors.RESET}")
    result = subprocess.run(cmd, text=True)

    if result.returncode == 0:
        print(f"  {Colors.GREEN}Python {version} o'chirildi!{Colors.RESET}")
    else:
        print(f"  {Colors.RED}O'chirishda xato!{Colors.RESET}")

def cmd_change_python(version: str):
    parts = version.split(".")
    apt_version = ".".join(parts[:2]) if len(parts) >= 2 else version
    py_path = shutil.which(f"python{apt_version}")

    if not py_path:
        print(f"\n{Colors.RED}✘ python{apt_version} topilmadi! Avval o'rnating.{Colors.RESET}\n")
        return
    
    print(f"\n{Colors.YELLOW}Agent python{apt_version} da qayta ishga tushirilmoqda...{Colors.RESET}")
    print(f"  {Colors.CYAN}Yangi interpreter: {py_path}{Colors.RESET}\n")
    script = os.path.abspath(sys.argv[0])
    _stop_event.set()
    if ws_client:
        ws_client.stop()
    time.sleep(0.5)
    os.execv(py_path, [py_path, script] + sys.argv[1:])

def cmd_reboot():
    print(f"\n{Colors.YELLOW}⚠  Tizim 3 soniyadan keyin reboot bo'ladi...{Colors.RESET}")
    for i in range(3, 0, -1):
        print(f"  {Colors.RED}{i}...{Colors.RESET}")
        time.sleep(1)
    subprocess.run(["sudo", "reboot"])

def cmd_shutdown():
    print(f"\n{Colors.YELLOW}⚠  Tizim 3 soniyadan keyin shutdown bo'ladi...{Colors.RESET}")
    for i in range(3, 0, -1):
        print(f"  {Colors.RED}{i}...{Colors.RESET}")
        time.sleep(1)
    subprocess.run(["sudo", "shutdown", "-h", "now"])

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}

def save_state(state: dict):
    with _state_lock:
        STATE_FILE.write_text(json.dumps(state, indent=2))

def get_model_state(state: dict, kind: str, model: str) -> dict:
    return state.get(kind, {}).get(model, {})

def set_model_state(kind: str, model: str, current_version: str = None, prev_version: str = None):
    state = load_state()
    state.setdefault(kind, {}).setdefault(model, {})
    entry = state[kind][model]
    if current_version is not None:
        entry["current_version"] = current_version
    if prev_version is not None:
        entry["prev_version"] = prev_version
    entry["updated_at"] = datetime.now().isoformat()
    save_state(state)
    print(f"  {Colors.CYAN}[state] {kind}/{model} → current={entry.get('current_version')} prev={entry.get('prev_version')}{Colors.RESET}")

def ws_handle_install(kind: str, model: str, version: str):
    with _ws_action_lock:
        print(f"\n{Colors.BOLD}{Colors.CYAN}[WS] INSTALL → {kind}/{model} v{version}{Colors.RESET}")
        cmd_install(kind, model, version)
        cmd_run(kind, model, version)
        set_model_state(kind, model, current_version=version)

def ws_handle_update(kind: str, model: str, version: str):
    with _ws_action_lock:
        print(f"\n{Colors.BOLD}{Colors.CYAN}[WS] UPDATE → {kind}/{model} → v{version}{Colors.RESET}")
        state = load_state()
        entry = get_model_state(state, kind, model)
        old_version = entry.get("current_version")

        if old_version:
            cmd_stop(kind, model, old_version)
            cmd_remove(kind, model, old_version)
        else:
            print(f"  {Colors.YELLOW}⚠ Joriy versiya topilmadi, faqat install qilinadi{Colors.RESET}")

        cmd_install(kind, model, version)
        cmd_run(kind, model, version)
        set_model_state(kind, model, current_version=version, prev_version=old_version)

def ws_handle_revert(kind: str, model: str, version: str):
    with _ws_action_lock:
        print(f"\n{Colors.BOLD}{Colors.CYAN}[WS] REVERT → {kind}/{model}, joriy: v{version}{Colors.RESET}")
        state = load_state()
        entry = get_model_state(state, kind, model)
        prev_version = entry.get("prev_version")

        if not prev_version:
            print(f"  {Colors.RED}✘ prev_version topilmadi ({kind}/{model}), revert qilib bo'lmaydi{Colors.RESET}")
            return

        cmd_stop(kind, model, version)
        cmd_remove(kind, model, version)
        cmd_install(kind, model, prev_version)
        cmd_run(kind, model, prev_version)
        # current/prev joylarini svop qilamiz
        set_model_state(kind, model, current_version=prev_version, prev_version=version)

def ws_dispatch(payload: dict):
    try:
        data = payload.get("data") or {}
        command = (data.get("command") or "").strip().lower()
        type_   = (data.get("type") or "").strip().lower()
        service = data.get("service") or {}
        model   = service.get("model")
        version = service.get("version")

        if not (command and type_ and model and version):
            print(f"  {Colors.YELLOW}⚠ WS xabar to'liq emas: {payload}{Colors.RESET}")
            return

        kind = TYPE_MAP.get(type_)
        if not kind:
            print(f"  {Colors.RED}✘ Noma'lum type: '{type_}' (ai yoki software bo'lishi kerak){Colors.RESET}")
            return

        if command == "install":
            ws_handle_install(kind, model, version)
        elif command == "update":
            ws_handle_update(kind, model, version)
        elif command == "revert":
            ws_handle_revert(kind, model, version)
        else:
            print(f"  {Colors.YELLOW}⚠ Noma'lum command: '{command}' (install/update/revert bo'lishi kerak){Colors.RESET}")

    except Exception as e:
        print(f"  {Colors.RED}✘ WS command bajarishda xato: {e}{Colors.RESET}")

class StompWsClient:
    def __init__(self, url: str, host: str, topic: str):
        self.url = url
        self.host = host
        self.topic = topic
        self.ws = None
        self._connected = threading.Event()
        self._stop = threading.Event()
        self._buffer = ""

    @staticmethod
    def _build_frame(command: str, headers: dict, body: str = "") -> str:
        lines = [command]
        for k, v in headers.items():
            lines.append(f"{k}:{v}")
        return "\n".join(lines) + "\n\n" + body + "\x00"

    def _on_open(self, ws):
        headers = {
            "accept-version": "1.1,1.2",
            "host": self.host,
            "heart-beat": "10000,10000",
        }
        token = get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        ws.send(self._build_frame("CONNECT", headers))

    def _on_message(self, ws, message):
        self._buffer += message
        while "\x00" in self._buffer:
            raw, self._buffer = self._buffer.split("\x00", 1)
            self._process_frame(raw.lstrip("\n"))

    def _process_frame(self, raw: str):
        if not raw.strip():
            return

        if "\n\n" in raw:
            head, body = raw.split("\n\n", 1)
        else:
            head, body = raw, ""

        head_lines = head.split("\n")
        frame_cmd = head_lines[0].strip()

        if frame_cmd == "CONNECTED":
            self._connected.set()
            print(f"  {Colors.GREEN}✔ WS/STOMP ulandi: {self.url}{Colors.RESET}")
            sub_headers = {
                "id": "sub-agent-cmd",
                "destination": self.topic,
                "ack": "auto",
            }
            self.ws.send(self._build_frame("SUBSCRIBE", sub_headers))
            print(f"  {Colors.CYAN}Subscribed: {self.topic}{Colors.RESET}")

        elif frame_cmd == "MESSAGE":
            body = body.strip()
            if body:
                try:
                    payload = json.loads(body)
                except Exception as e:
                    print(f"  {Colors.RED}✘ MESSAGE JSON parse xato: {e} | body={body}{Colors.RESET}")
                    return
                threading.Thread(target=ws_dispatch, args=(payload,), daemon=True).start()

        elif frame_cmd == "ERROR":
            print(f"  {Colors.RED}✘ STOMP ERROR: {body}{Colors.RESET}")

    def _on_error(self, ws, error):
        print(f"  {Colors.RED}✘ WS xato: {error}{Colors.RESET}")

    def _on_close(self, ws, close_status_code, close_msg):
        self._connected.clear()
        print(f"  {Colors.YELLOW}⚠ WS uzildi (code={close_status_code}, msg={close_msg}){Colors.RESET}")

    def run_forever_with_reconnect(self):
        backoff = 2
        while not self._stop.is_set():
            try:
                self._buffer = ""
                self.ws = websocket.WebSocketApp(
                    self.url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self.ws.run_forever(ping_interval=25, ping_timeout=10)
            except Exception as e:
                print(f"  {Colors.RED}✘ WS ulanishda xato: {e}{Colors.RESET}")

            if self._stop.is_set():
                break

            print(f"  {Colors.YELLOW}WS qayta ulanish {backoff}s dan keyin...{Colors.RESET}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def stop(self):
        self._stop.set()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

ws_client: StompWsClient | None = None

def handle_command(cmd_line: str) -> bool:
    parts = cmd_line.strip().split()
    if not parts:
        return True

    cmd = parts[0].lower()

    if cmd in ("exit", "quit", "q"):
        print(f"\n{Colors.YELLOW}Agent to'xtatildi. Xayr!{Colors.RESET}\n")
        return False

    elif cmd in ("help", "h", "?"):
        print(f"""
{Colors.BOLD}Buyruqlar:{Colors.RESET}

  {Colors.BOLD}{Colors.WHITE}[ SYSTEM ]{Colors.RESET}
  {Colors.CYAN}status{Colors.RESET}                        — CAN/Internet/GPS/WS/Python tekshir
  {Colors.CYAN}reboot{Colors.RESET}                        — tizimni qayta yuklash
  {Colors.CYAN}shutdown{Colors.RESET}                      — tizimni o'chirish

  {Colors.BOLD}{Colors.WHITE}[ PYTHON ]{Colors.RESET}
  {Colors.CYAN}python version{Colors.RESET}                — versiyalarni ko'rsat
  {Colors.CYAN}python install <ver>{Colors.RESET}           — o'rnat   (python install 3.11)
  {Colors.CYAN}python remove  <ver>{Colors.RESET}           — o'chir   (python remove 3.10)
  {Colors.CYAN}python change  <ver>{Colors.RESET}           — almashtir (python change 3.12)
  {Colors.CYAN}interval <soniya>{Colors.RESET}              — monitoring intervalini o'zgartir

  {Colors.BOLD}{Colors.WHITE}[ AI MODELS (qo'lda test uchun) ]{Colors.RESET}
  {Colors.CYAN}ai install <model> <version>{Colors.RESET}  — AI model yuklab o'rnat
  {Colors.CYAN}ai list{Colors.RESET}                       — o'rnatilgan AI modellar
  {Colors.CYAN}ai run     <model> <version>{Colors.RESET}  — AI model ishga tushir
  {Colors.CYAN}ai stop    <model> <version>{Colors.RESET}  — AI model to'xtat
  {Colors.CYAN}ai remove  <model> <version>{Colors.RESET}  — AI model o'chir
  {Colors.CYAN}ai logs    <model> <version>{Colors.RESET}  — AI model loglari

  {Colors.BOLD}{Colors.WHITE}[ SOFTWARE (qo'lda test uchun) ]{Colors.RESET}
  {Colors.CYAN}sw install <model> <version>{Colors.RESET}  — Software yuklab o'rnat
  {Colors.CYAN}sw list{Colors.RESET}                       — o'rnatilgan softwarelar
  {Colors.CYAN}sw run     <model> <version>{Colors.RESET}  — Software ishga tushir
  {Colors.CYAN}sw stop    <model> <version>{Colors.RESET}  — Software to'xtat
  {Colors.CYAN}sw remove  <model> <version>{Colors.RESET}  — Software o'chir
  {Colors.CYAN}sw logs    <model> <version>{Colors.RESET}  — Software loglari

  {Colors.BOLD}{Colors.WHITE}[ NOTE ]{Colors.RESET}
  Endi install/update/revert asosan WEBSOCKET orqali ({WS_TOPIC})
  avtomatik keladi. Yuqoridagi ai/sw buyruqlari qo'lda test qilish uchun qoldirildi.

  {Colors.CYAN}exit{Colors.RESET}                          — chiqish
""")

    elif cmd == "status":
        print_status()

    elif cmd == "python":
        if len(parts) < 2:
            cmd_python_version()
        elif parts[1] == "version":
            cmd_python_version()
        elif parts[1] == "install" and len(parts) >= 3:
            cmd_install_python(parts[2])
        elif parts[1] == "remove" and len(parts) >= 3:
            cmd_remove_python(parts[2])
        elif parts[1] == "change" and len(parts) >= 3:
            cmd_change_python(parts[2])
        else:
            print(f"  {Colors.RED}Noto'g'ri buyruq. 'help' yozing.{Colors.RESET}")

    elif cmd == "interval":
        if len(parts) >= 2:
            try:
                val = int(parts[1])
                if val < 5:
                    print(f"  {Colors.YELLOW}Minimal interval 5 soniya.{Colors.RESET}")
                else:
                    global CHECK_INTERVAL
                    CHECK_INTERVAL = val
                    print(f"  {Colors.GREEN}Interval {val} soniyaga o'zgartirildi.{Colors.RESET}")

            except ValueError:
                print(f"  {Colors.RED}Raqam kiriting!{Colors.RESET}")
        else:
            print(f"  Hozirgi interval: {CHECK_INTERVAL} soniya")

    elif cmd == "ai":
        if len(parts) < 2:
            print(f"  {Colors.RED}Buyruq kerak. 'help' yozing.{Colors.RESET}")
        elif parts[1] == "list":
            cmd_list("ai")
        elif parts[1] == "install" and len(parts) >= 4:
            cmd_install("ai", parts[2], parts[3])
        elif parts[1] == "run" and len(parts) >= 4:
            cmd_run("ai", parts[2], parts[3])
        elif parts[1] == "stop" and len(parts) >= 4:
            cmd_stop("ai", parts[2], parts[3])
        elif parts[1] == "remove" and len(parts) >= 4:
            cmd_remove("ai", parts[2], parts[3])
        elif parts[1] == "logs" and len(parts) >= 4:
            lines = int(parts[4]) if len(parts) >= 5 else 50
            cmd_logs("ai", parts[2], parts[3], lines)
        else:
            print(f"  {Colors.RED}Noto'g'ri buyruq. Misol: ai install safety 1.0.0{Colors.RESET}")

    elif cmd == "sw":
        if len(parts) < 2:
            print(f"  {Colors.RED}Buyruq kerak. 'help' yozing.{Colors.RESET}")
        elif parts[1] == "list":
            cmd_list("sw")
        elif parts[1] == "install" and len(parts) >= 4:
            cmd_install("sw", parts[2], parts[3])
        elif parts[1] == "run" and len(parts) >= 4:
            cmd_run("sw", parts[2], parts[3])
        elif parts[1] == "stop" and len(parts) >= 4:
            cmd_stop("sw", parts[2], parts[3])
        elif parts[1] == "remove" and len(parts) >= 4:
            cmd_remove("sw", parts[2], parts[3])
        elif parts[1] == "logs" and len(parts) >= 4:
            lines = int(parts[4]) if len(parts) >= 5 else 50
            cmd_logs("sw", parts[2], parts[3], lines)
        else:
            print(f"  {Colors.RED}Noto'g'ri buyruq. Misol: sw install safety 1.0.0{Colors.RESET}")

    elif cmd == "reboot":
        cmd_reboot()
    elif cmd == "shutdown":
        cmd_shutdown()

    else:
        print(f"  {Colors.RED}Noma'lum buyruq: '{cmd}'. 'help' yozing.{Colors.RESET}")

    return True

CHECK_INTERVAL = 60
_stop_event = threading.Event()

def monitoring_loop():
    while not _stop_event.is_set():
        print_status()
        for _ in range(CHECK_INTERVAL):
            if _stop_event.is_set():
                break
            time.sleep(1)

def main():
    global CHECK_INTERVAL, ws_client

    def signal_handler(sig, frame):
        print(f"\n\n{Colors.YELLOW}Ctrl+C bosildi. Agent to'xtatilmoqda...{Colors.RESET}\n")
        _stop_event.set()
        if ws_client:
            ws_client.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    print(f"""
{Colors.BOLD}{Colors.CYAN}╔══════════════════════════════════════════╗
║         SYSTEM MONITOR AGENT             ║
║  CAN | Internet | GPS | WS | Python | AI ║
╚══════════════════════════════════════════╝{Colors.RESET}
  {Colors.WHITE}Har {CHECK_INTERVAL} soniyada avtomatik tekshiradi.{Colors.RESET}
  {Colors.WHITE}install/update/revert endi {WS_TOPIC} orqali keladi.{Colors.RESET}
  {Colors.WHITE}'help' buyrug'i uchun yordam.{Colors.RESET}
""")

    ws_client = StompWsClient(WS_URL, WS_HOST, WS_TOPIC)
    ws_thread = threading.Thread(target=ws_client.run_forever_with_reconnect, daemon=True)
    ws_thread.start()

    monitor_thread = threading.Thread(target=monitoring_loop, daemon=True)
    monitor_thread.start()

    while True:
        try:
            cmd_line = input(f"\n{Colors.BOLD}{Colors.BLUE}agent>{Colors.RESET} ").strip()
            if not handle_command(cmd_line):
                _stop_event.set()
                ws_client.stop()
                break

        except EOFError:
            print(f"\n{Colors.YELLOW}Stdin yo'q. Faqat monitoring/websocket rejimi.{Colors.RESET}")
            try:
                monitor_thread.join()
            except KeyboardInterrupt:
                pass
            break
        
        except KeyboardInterrupt:
            _stop_event.set()
            ws_client.stop()
            print(f"\n{Colors.YELLOW}Agent to'xtatildi.{Colors.RESET}\n")
            break

if __name__ == "__main__":
    main()
