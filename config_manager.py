from machine import unique_id
import socket
import ssl
import ujson as json
import os
import time
import random

_CONFIG_PATH = "config.json"

config = {
    'loaded': False,
    # Network
    'dhcp': True,
    'ip': None,
    'subnet': None,
    'gateway': None,
    'dns': None,
    # Base
    'base_mode': 'time',
    'base_duration': 60,
    'base_pdop': 1,
    'base_lat': 0,
    'base_lon': 0,
    'base_alt': 0,
    'signal_group': 2,
    'sbas_enabled': True,
    'rtcm_interval': 1,
    # NTRIP
    'ntrip_server': 'crtk.net',
    'ntrip_port': 2101,
    'ntrip_mountpoint': None,
    'ntrip_user': None,
    'ntrip_password': None
}

def load_env(path='.env'):
    """Load key=value pairs from a .env file into global config."""
    try:
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                config[key.strip().lower()] = value.strip()
        print("✓ .env loaded")
    except OSError:
        print("No .env file found")

def get_hardware_id():
    uid = unique_id()
    return ''.join('{:02x}'.format(b) for b in uid)

# ── Local config ──────────────────────────────────────────────────────────────

def _config_exists():
    try:
        os.stat(_CONFIG_PATH)
        return True
    except OSError:
        return False

def load_local():
    """Read config from local file and merge into global config. Returns True on success."""
    global config
    if not _config_exists():
        print("No local config file found, using defaults")
        return False
    try:
        with open(_CONFIG_PATH, "r") as f:
            data = json.load(f)
        config.update(data)
        config['loaded'] = True
        print("✓ Local config loaded")
        return True
    except Exception as e:
        print(f"ERROR loading local config: {e}")
        return False

def _save_local():
    """Persist current global config to file."""
    try:
        with open(_CONFIG_PATH, "w") as f:
            filtered_config = {k: v for k, v in config.items() if k != "loaded" and k != "config_url" and k != "telemetry_url"}
            json.dump(filtered_config, f)
        print("✓ Config saved locally")
    except Exception as e:
        print(f"ERROR saving config: {e}")

# ── Remote config ─────────────────────────────────────────────────────────────

def download_config(server_url, timeout=10):
    """
    Download configuration from server and update global config.
    Supports HTTP only (HTTPS requires ssl module).

    Args:
        server_url: Full URL (e.g., 'http://192.168.1.100' or 'https://example.com')
        timeout:    Unused — kept for API compatibility (Wiznet driver does not support settimeout)

    Returns:
        bool: True if config loaded successfully, False otherwise
    """
    global config

    hw_id = get_hardware_id()

    print(f"\n=== Downloading Configuration ===")
    print(f"Hardware ID: {hw_id}")

    try:
        # Parse URL
        if server_url.startswith('https://'):
            use_ssl = True
            host = server_url[8:]
            port = 443
        elif server_url.startswith('http://'):
            use_ssl = False
            host = server_url[7:]
            port = 80
        else:
            print("ERROR: URL must start with http:// or https://")
            return False

        # Extract path prefix if present
        if '/' in host:
            host, path_prefix = host.split('/', 1)
            path_prefix = '/' + path_prefix
        else:
            path_prefix = ''

        # Handle port in host
        if ':' in host:
            host, port_str = host.split(':', 1)
            port = int(port_str)

        path = f"{path_prefix}?b={hw_id}"

        print(f"Host: {host}")
        print(f"Port: {port}")
        print(f"Path: {path}")
        print(f"SSL:  {use_ssl}")

        # DNS lookup
        try:
            ai = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except Exception as e:
            print(f"ERROR: DNS lookup failed: {e}")
            return False
        ai = ai[0]

        # Create socket — no settimeout, Wiznet driver does not support it
        s = socket.socket(ai[0], ai[1], ai[2])

        # Connect
        addr = ai[-1]
        print(f"Connecting to: {addr}")
        s.connect(addr)

        # Upgrade to TLS if HTTPS
        if use_ssl:
            print("Upgrading to TLS...")
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            s = ctx.wrap_socket(s)

        # Send HTTP request
        request = f"GET {path} HTTP/1.0\r\nHost: {host}\r\n\r\n"
        s.send(request.encode())

        # Read response
        response = b''
        while True:
            try:
                chunk = s.recv(512)
                if not chunk:
                    break
                response += chunk
            except:
                break

        try:
            s.close()
        except:
            pass

        # Parse HTTP response
        response_str = response.decode('utf-8', 'ignore')

        if '\r\n\r\n' not in response_str:
            print("ERROR: Invalid HTTP response")
            return False

        headers, body = response_str.split('\r\n\r\n', 1)

        status_line = headers.split('\r\n')[0]
        if '200' not in status_line:
            print(f"ERROR: HTTP {status_line}")
            return False

        print("✓ HTTP 200 OK")

        # Parse JSON body
        server_config = json.loads(body)
        print(f"Received config: {len(server_config)} settings")

        # Merge into global config, only write flash if something changed
        changed = False
        for key, value in server_config.items():
            if key in config:
                old_value = config[key]
                if old_value != value:
                    changed = True
                config[key] = value
                if old_value != value:
                    print(f"  {key}: {old_value} -> {value}")
                else:
                    print(f"  {key}: unchanged")
            else:
                changed = True
                config[key] = value
                print(f"  {key}: {value} (new)")

        config['loaded'] = True

        if changed:
            _save_local()
            print("✓ Configuration loaded and saved")
        else:
            print("✓ Configuration loaded, no changes (flash write skipped)")

        return True

    except Exception as e:
        print(f"ERROR downloading config: {e}")
        if 's' in locals():
            try:
                s.close()
            except:
                pass
        return False

def print_config():
    print("\n=== Current Configuration ===")
    for key, value in config.items():
        print(f"  {key}: {value}")

if __name__ == '__main__':
    from network_init import w5x00_init, print_network_status
    nic = w5x00_init(use_dhcp=True)
    if nic:
        print_network_status()
        load_local()
        download_config('http://picontrol.sysctl.org')
    else:
        print("No network...")
    print_config()
