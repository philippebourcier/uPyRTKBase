"""
Main workflow for RTK Base Station on W55RP20
Order of operations:
1. Load local config (determines DHCP vs static)
2. Initialize Ethernet using loaded config
3. Download remote config from server
4. Initialize UM980 sensor
5. Initialize IMU (LSM6DSV16X) and environment sensor (SHT40)
6. Launch NTRIP thread (COM2 -> NTRIP caster)
7. Main loop: IMU check, AGC check, periodic telemetry + summary, WDT feed
             + non-blocking HTTP status page on port 80
             + BTN_USER (GPIO26) hold >3s = reboot

LED1 = Internet / NTRIP status
LED2 = GNSS signal / antenna health
  ORANGE  boot / NTRIP reconnecting
  YELLOW  sensor init
  RED     sensor failed / AGC error / NTRIP reconnect failed
  CYAN    up, not yet checked
  GREEN   AGC good, level, no vibration
  BLUE    AGC degraded
  PINK    vibrating or tilted
"""

import gc
import time
import socket
import ujson as json
from machine import I2C, Pin, reset
from um980_config import UM980Config
from config_manager import get_hardware_id, load_local, download_config, print_config, config, load_env
from network_init import w5x00_init, print_network_status
from ntrip_caster import NTRIPCaster, start_ntrip_thread
from rgb_led_drv import LEDManager, Color
from wdt import feed_wdt
from lsm6dsv import LSM6DSV16X
from sht4x import read_sht40

load_env()
TELEMETRY_URL = config['telemetry_url']
CONFIG_URL    = config['config_url']

leds = LEDManager()

# Shared I2C bus (SDA=GPIO12, SCL=GPIO13)
i2c = I2C(0, sda=Pin(12), scl=Pin(13), freq=400_000)

btn = Pin(26, Pin.IN)   # external 10K pull-down R4 — active HIGH

REBOOT_HOLD_MS = 3000   # hold duration required to trigger reboot

# ---------------------------------------------------------------------------
# LED color metadata — CSS color + meaning per LED
# ---------------------------------------------------------------------------
_LED1_META = {
    'ORANGE': ('#f97316', 'Booting / NTRIP reconnecting'),
    'CYAN':   ('#22d3ee', 'Ethernet up, downloading config'),
    'GREEN':  ('#22c55e', 'Config downloaded, NTRIP connected'),
    'YELLOW': ('#eab308', 'Ethernet up, remote config failed (using local)'),
    'RED':    ('#ef4444', 'Ethernet init failed / NTRIP reconnect failed'),
}

_LED2_META = {
    'ORANGE': ('#f97316', 'Booting'),
    'YELLOW': ('#eab308', 'UM980 initializing'),
    'CYAN':   ('#22d3ee', 'UM980 up, checks pending'),
    'GREEN':  ('#22c55e', 'AGC good, level, no vibration'),
    'BLUE':   ('#3b82f6', 'AGC degraded on one or more bands'),
    'PINK':   ('#ec4899', 'Vibrating or tilted (IMU alarm)'),
    'RED':    ('#ef4444', 'UM980 init failed / AGC check error'),
}


def _set_led1(color_name, status):
    # INTERNET LED
    leds.led1.set(*getattr(Color, color_name))
    status['led1'] = color_name


def _set_led2(color_name, status):
    # GNSS/RTK LED
    leds.led2.set(*getattr(Color, color_name))
    status['led2'] = color_name


# ---------------------------------------------------------------------------
# Button handler — call once per loop iteration, non-blocking
# ---------------------------------------------------------------------------
_btn_pressed_at = None

def _check_button(status):
    global _btn_pressed_at

    if btn.value() == 1:   # pressed (active HIGH)
        if _btn_pressed_at is None:
            _btn_pressed_at = time.ticks_ms()
            print("Button held...")
        else:
            held_ms = time.ticks_diff(time.ticks_ms(), _btn_pressed_at)
            if (held_ms // 200) % 2 == 0:
                leds.both(*Color.WHITE)
            else:
                leds.off()
            if held_ms >= REBOOT_HOLD_MS:
                print("Button held >3s — rebooting...")
                leds.both(*Color.WHITE)
                time.sleep_ms(300)
                leds.off()
                reset()
    else:
        if _btn_pressed_at is not None:
            held_ms = time.ticks_diff(time.ticks_ms(), _btn_pressed_at)
            print(f"Button released after {held_ms}ms — ignoring")
            _btn_pressed_at = None
            leds.led1.set(*getattr(Color, status.get('led1', 'ORANGE')))
            leds.led2.set(*getattr(Color, status.get('led2', 'ORANGE')))


# ---------------------------------------------------------------------------
# Fire-and-forget telemetry POST — no retry, all errors silently ignored
# ---------------------------------------------------------------------------
def _send_telemetry(payload):
    try:
        host = TELEMETRY_URL[7:]
        port = 80
        if ':' in host.split('/')[0]:
            h, p = host.split('/')[0].split(':', 1)
            host = host.replace(host.split('/')[0], h)
            port = int(p)
        path = '/' + '/'.join(host.split('/')[1:]) if '/' in host else '/'
        host = host.split('/')[0]
        body = json.dumps(payload)
        request = (
            f"POST {path} HTTP/1.0\r\n"
            f"Host: {host}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
            f"{body}"
        )
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(5)
        s.connect(addr)
        s.send(request.encode())
        try:
            s.recv(256)
        except:
            pass
        s.close()
        print("✓ Telemetry sent")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Status page HTML
# Returns bytes with correct Content-Length based on UTF-8 byte count
# ---------------------------------------------------------------------------
def _led_indicator(label, color_name, meta):
    css, meaning = meta.get(color_name, ('#444', 'Unknown'))
    return f"""
    <div class="led-block">
      <div class="led-label">{label}</div>
      <div class="led-circle" style="background:{css};box-shadow:0 0 24px 8px {css}"></div>
      <div class="led-meaning">{meaning}</div>
    </div>"""


def _build_status_page(status):

    def _row(label, value):
        return f"<tr><td>{label}</td><td><b>{value}</b></td></tr>"

    def _fmt(v, unit='', decimals=1):
        if v is None:
            return '-'
        return f"{v:.{decimals}f}{unit}"

    rows = [
        _row("Hardware ID",  status['hw']),
        _row("NTRIP",        "Connected" if status['ntrip'] else "Disconnected"),
        _row("Mountpoint",   config.get('ntrip_mountpoint') or '-'),
        _row("Temperature",  _fmt(status['temperature'], ' C')),
        _row("Humidity",     _fmt(status['humidity'], ' %')),
        _row("AGC L1",       _fmt(status['agc_l1'], decimals=1)),
        _row("AGC L2",       _fmt(status['agc_l2'], decimals=1)),
        _row("AGC L5",       _fmt(status['agc_l5'], decimals=1)),
        _row("RMS max d",    _fmt(status['rms_max_delta'], ' dps', 3)),
        _row("Pitch d",      _fmt(status['pitch_delta'], ' deg', 2)),
        _row("Roll d",       _fmt(status['roll_delta'], ' deg', 2)),
        _row("Vibrating",    "YES" if status['vibrating'] else "No"),
        _row("Level",        "Yes" if status['level'] else "NO"),
    ]

    led1_html = _led_indicator("LED1 - Internet / NTRIP", status.get('led1', 'ORANGE'), _LED1_META)
    led2_html = _led_indicator("LED2 - GNSS / Antenna",   status.get('led2', 'ORANGE'), _LED2_META)

    body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="45">
  <title>RTK Base Station</title>
  <style>
    body        {{ font-family: monospace; background: #111; color: #eee; padding: 2em; margin: 0; }}
    .container  {{ max-width: 480px; margin: 0 auto; }}
    h1          {{ color: #7cf; margin-bottom: 0.5em; }}
    .leds       {{ display: flex; gap: 3em; margin-bottom: 2em; }}
    .led-block  {{ display: flex; flex-direction: column; align-items: center; gap: 0.4em; }}
    .led-label  {{ font-size: 0.85em; color: #aaa; text-align: center; }}
    .led-circle {{ width: 64px; height: 64px; border-radius: 50%; }}
    .led-meaning{{ font-size: 0.8em; color: #ccc; text-align: center; max-width: 160px; }}
    table       {{ border-collapse: collapse; width: 100%; }}
    td          {{ padding: 6px 16px; border-bottom: 1px solid #333; }}
    td:first-child {{ color: #aaa; }}
    .footer     {{ color: #555; font-size: 0.8em; margin-top: 1.5em; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>RTK Base Station</h1>
    <div class="leds">
      {led1_html}
      {led2_html}
    </div>
    <table>{''.join(rows)}</table>
    <p class="footer">Hold BTN_USER &gt;3s to reboot</p>
  </div>
</body>
</html>"""

    body_bytes = body.encode('utf-8')
    header = (
        "HTTP/1.0 200 OK\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        "\r\n"
    ).encode('utf-8')
    return header + body_bytes

_404 = b"HTTP/1.0 404 Not Found\r\nContent-Length: 0\r\n\r\n"

def _reset_server_sock(server_sock):
    """Close and recreate the server socket after it goes bad."""
    try:
        server_sock.close()
    except:
        pass
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', 80))
    s.listen(5)
    s.setblocking(False)
    print("[HTTP] Server socket recreated")
    return s

def _serve(server_sock, status):
    try:
        conn, addr = server_sock.accept()
        print(f"[HTTP] Connection from {addr}")
        req = conn.recv(4096)
        print(f"[HTTP] Request: {req[:80]}")
        if b'favicon.ico' in req:
            print("[HTTP] 404 favicon")
            conn.send(_404)
        else:
            data = _build_status_page(status)
            total = 0
            while total < len(data):
                chunk = data[total:total+1024]
                sent = conn.send(chunk)
                if sent == 0:
                    print(f"[HTTP] Send stalled at {total}/{len(data)}")
                    break
                total += sent
            print(f"[HTTP] Sent {total}/{len(data)} bytes")
        conn.close()
        print("[HTTP] Connection closed")
    except OSError as e:
        if e.args[0] == 11:
            pass
        else:
            print(f"[HTTP] OSError: {e}")
            return True
    return False

def main():

    print("=" * 60)
    print("RTK BASE STATION")
    print("=" * 60)

    status = {
        'hw':            get_hardware_id(),
        'led1':          'ORANGE',
        'led2':          'ORANGE',
        'ntrip':         False,
        'temperature':   None,
        'humidity':      None,
        'agc_l1':        None,
        'agc_l2':        None,
        'agc_l5':        None,
        'rms_max_delta': None,
        'pitch_delta':   None,
        'roll_delta':    None,
        'vibrating':     False,
        'level':         None,
    }

    _set_led1('ORANGE', status)
    _set_led2('ORANGE', status)

    # ===================================================================
    # STEP 1: Load local config
    # ===================================================================
    print("\n[STEP 1] Loading Local Configuration...")
    load_local()
    feed_wdt()

    # ===================================================================
    # STEP 2: Initialize Ethernet
    # ===================================================================
    print("\n[STEP 2] Initializing Ethernet...")

    if config['dhcp']:
        nic = w5x00_init(use_dhcp=True)
    else:
        nic = w5x00_init(use_dhcp=False, static_ip=config['ip'], subnet=config['subnet'], gateway=config['gateway'], dns=config['dns'])

    if not nic:
        print("✗ Failed to initialize network")
        _set_led1('RED', status)
        return

    print_network_status()
    _set_led1('CYAN', status)
    feed_wdt()

    # ===================================================================
    # STEP 3: Download remote config
    # ===================================================================
    print("\n[STEP 3] Downloading Remote Configuration...")
    print(f"Hardware ID: {get_hardware_id()}")

    if download_config(CONFIG_URL):
        print("✓ Configuration downloaded")
        print_config()
        _set_led1('GREEN', status)
    else:
        print("⚠ Using local configuration")
        _set_led1('YELLOW', status)
    feed_wdt()

    # ===================================================================
    # STEP 4: Initialize UM980
    # ===================================================================
    print("\n[STEP 4] Initializing UM980 Sensor...")

    _set_led2('YELLOW', status)
    um980 = UM980Config()
    model, firmware = um980.start_sensor()

    if not model:
        print("✗ Failed to initialize UM980")
        _set_led2('RED', status)
        return

    print(f"✓ UM980 ready: {model}, FW: {firmware}")
    _set_led2('CYAN', status)
    feed_wdt()

    # ===================================================================
    # STEP 5: Initialize IMU and environment sensor
    # ===================================================================
    print("\n[STEP 5] Initializing Sensors...")

    imu = None
    try:
        imu = LSM6DSV16X(
            i2c,
            address=0x6B,
            tilt_threshold_deg=2.0,
            vibration_threshold_dps=0.5,
            vibration_window_ms=1000,
        )
        print("✓ LSM6DSV16X ready")
    except Exception as e:
        print(f"⚠ LSM6DSV16X init failed: {e}")

    try:
        temp, hum = read_sht40()
        print(f"✓ SHT40 ready: {temp:.1f}C  {hum:.1f}%")
    except Exception as e:
        print(f"⚠ SHT40 read failed: {e}")

    feed_wdt()

    # ===================================================================
    # STEP 6: Launch NTRIP Thread
    # ===================================================================
    print("\n[STEP 6] Starting NTRIP Caster Thread...")

    ntrip = None
    if not all([config['ntrip_server'], config['ntrip_mountpoint'],
                config['ntrip_user'], config['ntrip_password']]):
        print("⚠ Missing NTRIP configuration — skipping")
        print("  Required: ntrip_server, ntrip_mountpoint, ntrip_user, ntrip_password")
    else:
        ntrip = NTRIPCaster(
            server=config['ntrip_server'],
            port=int(config['ntrip_port']),
            mountpoint=config['ntrip_mountpoint'],
            username=config['ntrip_user'],
            password=config['ntrip_password']
        )
        start_ntrip_thread(ntrip, um980.data_uart)
        print("✓ NTRIP thread started on core 1")
        feed_wdt()

    # ===================================================================
    # STEP 7: Start non-blocking HTTP server
    # ===================================================================
    print("\n[STEP 7] Starting HTTP Status Server on port 80...")

    server_sock = socket.socket()
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(('0.0.0.0', 80))
    server_sock.listen(5)
    server_sock.setblocking(False)
    print("✓ HTTP server listening on port 80")

    # ===================================================================
    # STEP 8: Main Loop
    # ===================================================================
    print("\n[STEP 8] Main Loop Running...")
    print("=" * 60)
    print("Base station is operational!")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    agc_buf = []
    env_buf = []
    imu_min = {'rms_max': None, 'pitch': None, 'roll': None}
    imu_max = {'rms_max': None, 'pitch': None, 'roll': None}

    def _avg(lst):
        return sum(lst) / len(lst) if lst else None

    def _delta(key):
        if imu_min[key] is not None and imu_max[key] is not None:
            return imu_max[key] - imu_min[key]
        return None

    last_60s  = time.ticks_ms()
    last_300s = time.ticks_ms()

    try:
        while True:

            # -----------------------------------------------------------------
            # Button check (non-blocking) — hold >3s to reboot
            # -----------------------------------------------------------------
            _check_button(status)

            # -----------------------------------------------------------------
            # Serve any pending HTTP request (non-blocking)
            # -----------------------------------------------------------------
            status['ntrip'] = ntrip.connected if ntrip else False
            if _serve(server_sock, status):
                server_sock = _reset_server_sock(server_sock)

            # -----------------------------------------------------------------
            # IMU check -> LED2 + status snapshot
            # -----------------------------------------------------------------
            if imu is not None:
                try:
                    imu_status = imu.check()

                    for key, val in [('rms_max', imu_status['rms_max_dps']),
                                     ('pitch',   imu_status['pitch_deg']),
                                     ('roll',    imu_status['roll_deg'])]:
                        if val is not None:
                            if imu_min[key] is None or val < imu_min[key]:
                                imu_min[key] = val
                            if imu_max[key] is None or val > imu_max[key]:
                                imu_max[key] = val

                    status['vibrating']     = imu_status['vibrating']
                    status['level']         = imu_status['level']
                    status['rms_max_delta'] = _delta('rms_max')
                    status['pitch_delta']   = _delta('pitch')
                    status['roll_delta']    = _delta('roll')

                    if imu_status['vibrating'] or imu_status['level'] is False:
                        _set_led2('PINK', status)
                        if imu_status['vibrating']:
                            print(f"⚠ Vibration: rms={imu_status['rms_max_dps']:.3f} dps")
                        else:
                            print(f"⚠ Tilt: pitch={imu_status['pitch_deg']:.2f}  roll={imu_status['roll_deg']:.2f}")
                except Exception as e:
                    print(f"✗ IMU check error: {e}")
            else:
                time.sleep_ms(1000)

            feed_wdt()

            # -----------------------------------------------------------------
            # Every ~60s: sample SHT40 + AGC, update LED2 + status snapshot
            # -----------------------------------------------------------------
            if time.ticks_diff(time.ticks_ms(), last_60s) >= 60000:
                last_60s = time.ticks_ms()

                try:
                    temp, hum = read_sht40()
                    env_buf.append((temp, hum))
                    if len(env_buf) > 5:
                        env_buf.pop(0)
                    status['temperature'] = temp
                    status['humidity']    = hum
                except Exception as e:
                    print(f"⚠ SHT40 sample error: {e}")

                try:
                    agc_values = um980.get_agc_values()
                    if agc_values:
                        agc_buf.append(agc_values)
                        if len(agc_buf) > 5:
                            agc_buf.pop(0)
                        status['agc_l1'] = agc_values['L1']
                        status['agc_l2'] = agc_values['L2']
                        status['agc_l5'] = agc_values['L5']
                        agc_status = um980.get_agc_status()
                        if agc_status:
                            bad_bands  = [k for k, v in agc_status.items() if v == 'bad']
                            antenna_ok = (imu is None or not (
                                imu_max['rms_max'] is not None and imu_max['rms_max'] > 0.5
                                or imu_min['pitch'] is None
                            ))
                            if bad_bands:
                                print(f"⚠ Poor AGC on: {bad_bands}")
                                if antenna_ok:
                                    _set_led2('BLUE', status)
                            else:
                                print("✓ AGC status good")
                                if antenna_ok:
                                    _set_led2('GREEN', status)
                except Exception as e:
                    print(f"⚠ AGC sample error: {e}")
                    _set_led2('RED', status)

            # -----------------------------------------------------------------
            # Every ~5 minutes: telemetry + NTRIP watchdog + summary
            # -----------------------------------------------------------------
            if time.ticks_diff(time.ticks_ms(), last_300s) >= 300000:
                last_300s = time.ticks_ms()

                print(f"\n=== 5 Minute Summary ===")
                gc.collect()
                print_network_status()

                avg_temp = _avg([s[0] for s in env_buf]) if env_buf else None
                avg_hum  = _avg([s[1] for s in env_buf]) if env_buf else None
                avg_l1   = _avg([s['L1'] for s in agc_buf if s['L1'] != -1]) if agc_buf else None
                avg_l2   = _avg([s['L2'] for s in agc_buf if s['L2'] != -1]) if agc_buf else None
                avg_l5   = _avg([s['L5'] for s in agc_buf if s['L5'] != -1]) if agc_buf else None

                if avg_temp is not None:
                    print(f"Temp: {avg_temp:.1f}C  Humidity: {avg_hum:.1f}%")

                status['rms_max_delta'] = _delta('rms_max')
                status['pitch_delta']   = _delta('pitch')
                status['roll_delta']    = _delta('roll')
                status['ntrip']         = ntrip.connected if ntrip else False

                _send_telemetry({
                    'hw':            get_hardware_id(),
                    'rms_max_delta': _delta('rms_max'),
                    'pitch_delta':   _delta('pitch'),
                    'roll_delta':    _delta('roll'),
                    'temperature':   avg_temp,
                    'humidity':      avg_hum,
                    'agc_l1':        avg_l1,
                    'agc_l2':        avg_l2,
                    'agc_l5':        avg_l5,
                })

                imu_min = {'rms_max': None, 'pitch': None, 'roll': None}
                imu_max = {'rms_max': None, 'pitch': None, 'roll': None}

                if ntrip:
                    print(f"NTRIP connected: {ntrip.connected}")
                    if not ntrip.connected:
                        print("⚠ NTRIP disconnected, reconnecting...")
                        _set_led1('ORANGE', status)
                        try:
                            ntrip.connect()
                            _set_led1('GREEN', status)
                        except Exception as e:
                            print(f"✗ NTRIP reconnect failed: {e}")
                            _set_led1('RED', status)

            feed_wdt()

    except KeyboardInterrupt:
        print("\nShutting down...")
        leds.off()
        try:
            server_sock.close()
        except:
            pass
        if ntrip:
            try:
                ntrip.disconnect()
            except Exception as e:
                print(f"Warning: NTRIP disconnect error: {e}")
        leds.deinit()
        print("Goodbye!")


if __name__ == '__main__':
    main()
