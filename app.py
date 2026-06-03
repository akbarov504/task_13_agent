import subprocess
import socket
import time
import os
import sys
import shutil
import re
import threading
import signal
import platform
from datetime import datetime

class Colors:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"
    BLUE   = "\033[94m"
    WHITE  = "\033[97m"

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
            result = subprocess.run(
                ["ifconfig"],
                capture_output=True, text=True, timeout=5
            )
            if "can" in result.stdout.lower():
                return True, "CAN interfeys mavjud (ifconfig)"
            return False, "CAN interfeys topilmadi"
        except Exception as e:
            return False, f"Tekshirib bo'lmadi: {e}"
    except Exception as e:
        return False, f"Xato: {e}"

def check_internet():
    hosts = [
        ("8.8.8.8", 53),       # Google DNS
        ("1.1.1.1", 53),       # Cloudflare DNS
        ("208.67.222.222", 53) # OpenDNS
    ]
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
        result = subprocess.run(
            ["lsusb"],
            capture_output=True, text=True, timeout=5
        )
        gps_keywords = ["GPS", "u-blox", "SiRF", "MTK", "Globalsat", "Garmin"]
        for keyword in gps_keywords:
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
                r = subprocess.run(
                    [cmd, "--version"],
                    capture_output=True, text=True, timeout=3
                )
                ver = r.stdout.strip() or r.stderr.strip()
                versions[cmd] = ver
            except Exception:
                pass

    for cmd in ["python3", "python"]:
        path = shutil.which(cmd)
        if path:
            try:
                r = subprocess.run(
                    [cmd, "--version"],
                    capture_output=True, text=True, timeout=3
                )
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

    def status_icon(ok):
        if ok:
            return f"{Colors.GREEN}✔ ISHLAYAPTI{Colors.RESET}"
        return f"{Colors.RED}✘ ISHLAMAYAPTI{Colors.RESET}"

    sep = Colors.CYAN + "─" * 55 + Colors.RESET

    print(f"\n{sep}")
    print(f"  {Colors.BOLD}{Colors.WHITE}SYSTEM AGENT  [{now}]{Colors.RESET}")
    print(sep)
    print(f"  {Colors.YELLOW}CAN Bus :{Colors.RESET} {status_icon(can_ok)}  — {can_msg}")
    print(f"  {Colors.YELLOW}Internet:{Colors.RESET} {status_icon(net_ok)}  — {net_msg}")
    print(f"  {Colors.YELLOW}GPS     :{Colors.RESET} {status_icon(gps_ok)}  — {gps_msg}")
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
    r = subprocess.run(
        ["apt-cache", "show", pkg_name],
        capture_output=True, text=True, timeout=5
    )
    return r.returncode == 0

def cmd_install_python(version: str):
    parts = version.split(".")
    if len(parts) < 2:
        print(f"{Colors.RED}Noto'g'ri format. Misol: 3.11{Colors.RESET}")
        return

    apt_version = ".".join(parts[:2])
    if len(parts) > 2:
        print(f"  {Colors.YELLOW}⚠ apt faqat major.minor qabul qiladi: '{version}' → '{apt_version}' ishlatiladi{Colors.RESET}")
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
        print(f"\n{Colors.RED}✘ python{apt_version} tizimda topilmadi!{Colors.RESET}")
        print(f"  Avval o'rnating: {Colors.CYAN}python install {apt_version}{Colors.RESET}\n")
        return

    print(f"\n{Colors.YELLOW}Agent python{apt_version} da qayta ishga tushirilmoqda...{Colors.RESET}")
    print(f"  {Colors.CYAN}Yangi interpreter: {py_path}{Colors.RESET}\n")

    script = os.path.abspath(sys.argv[0])

    _stop_event.set()
    time.sleep(0.5)

    os.execv(py_path, [py_path, script] + sys.argv[1:])

def cmd_reboot():
    print(f"\n{Colors.YELLOW}⚠  Tizim 3 soniyadan keyin reboot bo'ladi...{Colors.RESET}")
    for i in range(3, 0, -1):
        print(f"  {Colors.RED}{i}...{Colors.RESET}")
        time.sleep(1)
    print(f"  {Colors.CYAN}$ sudo reboot{Colors.RESET}")
    subprocess.run(["sudo", "reboot"])

def cmd_shutdown():
    print(f"\n{Colors.YELLOW}⚠  Tizim 3 soniyadan keyin shutdown bo'ladi...{Colors.RESET}")
    for i in range(3, 0, -1):
        print(f"  {Colors.RED}{i}...{Colors.RESET}")
        time.sleep(1)
    print(f"  {Colors.CYAN}$ sudo shutdown -h now{Colors.RESET}")
    subprocess.run(["sudo", "shutdown", "-h", "now"])

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
{Colors.BOLD}Mavjud buyruqlar:{Colors.RESET}
  {Colors.CYAN}status{Colors.RESET}                   — hozir tekshir va ko'rsat
  {Colors.CYAN}python version{Colors.RESET}           — Python versiyalarini ko'rsat
  {Colors.CYAN}python install <version>{Colors.RESET}  — Python o'rnat   (misol: python install 3.11)
  {Colors.CYAN}python remove  <version>{Colors.RESET}  — Python o'chir   (misol: python remove 3.10)
  {Colors.CYAN}python change  <version>{Colors.RESET}  — Python almashtir (misol: python change 3.12)
  {Colors.CYAN}interval <soniya>{Colors.RESET}         — tekshirish intervalini o'zgartir
  {Colors.CYAN}reboot{Colors.RESET}                   — tizimni qayta yuklash
  {Colors.CYAN}shutdown{Colors.RESET}                 — tizimni o'chirish
  {Colors.CYAN}help{Colors.RESET}                     — shu yordam
  {Colors.CYAN}exit{Colors.RESET}                     — agentni to'xtat
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
                new_interval = int(parts[1])
                if new_interval < 5:
                    print(f"  {Colors.YELLOW}Minimal interval 5 soniya.{Colors.RESET}")
                else:
                    global CHECK_INTERVAL
                    CHECK_INTERVAL = new_interval
                    print(f"  {Colors.GREEN}Interval {new_interval} soniyaga o'zgartirildi.{Colors.RESET}")
            except ValueError:
                print(f"  {Colors.RED}Raqam kiriting!{Colors.RESET}")
        else:
            print(f"  {Colors.CYAN}Hozirgi interval: {CHECK_INTERVAL} soniya{Colors.RESET}")

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
    global CHECK_INTERVAL

    def signal_handler(sig, frame):
        print(f"\n\n{Colors.YELLOW}Ctrl+C bosildi. Agent to'xtatilmoqda...{Colors.RESET}\n")
        _stop_event.set()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    print(f"""
{Colors.BOLD}{Colors.CYAN}╔══════════════════════════════════════════╗
║         SYSTEM MONITOR AGENT             ║
║   CAN Bus | Internet | GPS | Python      ║
╚══════════════════════════════════════════╝{Colors.RESET}
  {Colors.WHITE}Har {CHECK_INTERVAL} soniyada avtomatik tekshiradi.{Colors.RESET}
  {Colors.WHITE}'help' buyrug'i uchun yordam.{Colors.RESET}
""")

    monitor_thread = threading.Thread(target=monitoring_loop, daemon=True)
    monitor_thread.start()

    while True:
        try:
            cmd_line = input(f"\n{Colors.BOLD}{Colors.BLUE}agent>{Colors.RESET} ").strip()
            if not handle_command(cmd_line):
                _stop_event.set()
                break
        except EOFError:
            print(f"\n{Colors.YELLOW}Stdin yo'q. Faqat monitoring rejimi.{Colors.RESET}")
            try:
                monitor_thread.join()
            except KeyboardInterrupt:
                pass
            break
        except KeyboardInterrupt:
            _stop_event.set()
            print(f"\n{Colors.YELLOW}Agent to'xtatildi.{Colors.RESET}\n")
            break

if __name__ == "__main__":
    main()
