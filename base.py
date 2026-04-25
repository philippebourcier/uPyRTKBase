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

LED1 = Internet / NTRIP status
LED2 = GNSS signal / antenna health
  ORANGE  boot / NTRIP reconnecting
  YELLOW  sensor init
  RED     sensor failed / AGC error / NTRIP reconnect failed
  CYAN    up, not yet checked
  GREEN   AGC good, level, no vibration
  ORANGE  AGC degraded
  PINK    vibrating or tilted
"""

import gc
import time
import socket
import ujson as json
from machine import I2C, Pin
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
CONFIG_URL = config['config_url']

leds = LEDManager()

# Shared I2C bus (SDA=GPIO12, SCL=GPIO13)
i2c = I2C(0, sda=Pin(12), scl=Pin(13), freq=400_000)


# ---------------------------------------------------------------------------
# Fire-and-forget telemetry POST — no retry, all errors silently ignored
# ---------------------------------------------------------------------------
def _send_telemetry(payload):
    try:
        host = TELEMETRY_URL[7:]   # strip http://
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
        s.connect(addr)
        s.send(request.encode())
        s.recv(256)
        s.close()
        print("✓ Telemetry sent")
    except Exception:
        pass   # fire-and-forget


def main():
    print("=" * 60)
    print("RTK BASE STATION")
    print("=" * 60)

    leds.led1.set(*Color.ORANGE)   # Internet: initializing
    leds.led2.set(*Color.ORANGE)   # GNSS: initializing

    # ===================================================================
    # STEP 1: Load local config (determines DHCP vs static)
    # ===================================================================
    print("\n[STEP 1] Loading Local Configuration...")
    load_local()
    feed_wdt()

    # ===================================================================
    # STEP 2: Initialize Ethernet using loaded config
    # ===================================================================
    print("\n[STEP 2] Initializing Ethernet...")

    if config['dhcp']:
        nic = w5x00_init(use_dhcp=True)
    else:
        nic = w5x00_init(use_dhcp=False, static_ip=config['ip'], subnet=config['subnet'], gateway=config['gateway'], dns=config['dns'])

    if not nic:
        print("✗ Failed to initialize network")
        leds.led1.set(*Color.RED)
        return

    print_network_status()
    leds.led1.set(*Color.CYAN)   # Internet: connected, fetching config
    feed_wdt()

    # ===================================================================
    # STEP 3: Download remote config (merges over local config)
    # ===================================================================
    print("\n[STEP 3] Downloading Remote Configuration...")
    print(f"Hardware ID: {get_hardware_id()}")

    if download_config(CONFIG_URL):
        print("✓ Configuration downloaded")
        print_config()
        leds.led1.set(*Color.GREEN)   # Internet: OK
    else:
        print("⚠ Using local configuration")
        leds.led1.set(*Color.YELLOW)  # Internet: degraded (no remote)
    feed_wdt()

    # ===================================================================
    # STEP 4: Initialize UM980 Sensor
    # ===================================================================
    print("\n[STEP 4] Initializing UM980 Sensor...")

    leds.led2.set(*Color.YELLOW)   # GNSS: initializing
    um980 = UM980Config()
    model, firmware = um980.start_sensor()

    if not model:
        print("✗ Failed to initialize UM980")
        leds.led2.set(*Color.RED)
        return

    print(f"✓ UM980 ready: {model}, FW: {firmware}")
    leds.led2.set(*Color.CYAN)   # GNSS: up, waiting for signal quality
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
        print(f"✓ SHT40 ready: {temp:.1f}°C  {hum:.1f}%")
    except Exception as e:
        print(f"⚠ SHT40 read failed: {e}")

    feed_wdt()

    # ===================================================================
    # STEP 6: Launch NTRIP Thread (COM2 -> NTRIP Caster)
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
    # STEP 7: Main Loop
    # ===================================================================
    print("\n[STEP 7] Main Loop Running...")
    print("=" * 60)
    print("Base station is operational!")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    loop_count = 0
    LOOP_MAX   = 86400

    # Cached values for 5-minute telemetry
    last_agc = {'L1': -1, 'L2': -1, 'L5': -1}
    last_imu = {'rms_max': 0.0, 'pitch': None, 'roll': None}
    last_env = {'temperature': None, 'humidity': None}

    try:
        while True:
            loop_count = (loop_count + 1) % LOOP_MAX

            # -----------------------------------------------------------------
            # Every iteration: IMU check → LED2 antenna health
            # get_vibration_rms() paces the loop naturally (~1s window)
            # When IMU is absent, fall back to sleep(1)
            # -----------------------------------------------------------------
            if imu is not None:
                try:
                    status = imu.check()
                    last_imu['rms_max'] = status['rms_max_dps']
                    last_imu['pitch']   = status['pitch_deg']
                    last_imu['roll']    = status['roll_deg']

                    if status['vibrating'] or status['level'] is False:
                        leds.led2.set(*Color.PINK)
                        if status['vibrating']:
                            print(f"⚠ Vibration: rms={status['rms_max_dps']:.3f} dps")
                        else:
                            print(f"⚠ Tilt: pitch={status['pitch_deg']:.2f}°  roll={status['roll_deg']:.2f}°")
                except Exception as e:
                    print(f"✗ IMU check error: {e}")
            else:
                time.sleep(1)

            feed_wdt()

            # -----------------------------------------------------------------
            # Every 30 seconds: AGC check → LED2 (only when antenna is calm)
            # -----------------------------------------------------------------
            if loop_count % 30 == 0:
                print("\n--- AGC Check ---")
                try:
                    agc_values = um980.get_agc_values()
                    agc_status = um980.get_agc_status()
                    if agc_values:
                        last_agc = agc_values
                    if agc_status:
                        bad_bands  = [k for k, v in agc_status.items() if v == 'bad']
                        antenna_ok = (imu is None or not (
                            last_imu['rms_max'] > 0.5 or last_imu['pitch'] is None
                        ))
                        if bad_bands:
                            print(f"⚠ Poor AGC on: {bad_bands}")
                            if antenna_ok:
                                leds.led2.set(*Color.ORANGE)
                        else:
                            print("✓ AGC status good")
                            if antenna_ok:
                                leds.led2.set(*Color.GREEN)
                except Exception as e:
                    print(f"✗ AGC check error: {e}")
                    leds.led2.set(*Color.RED)

            # -----------------------------------------------------------------
            # Every 5 minutes: telemetry + NTRIP watchdog + summary
            # -----------------------------------------------------------------
            if loop_count % 300 == 0:
                print(f"\n=== 5 Minute Summary (uptime: {loop_count}s) ===")
                gc.collect()
                print_network_status()

                # Read SHT40
                try:
                    temp, hum = read_sht40()
                    last_env['temperature'] = temp
                    last_env['humidity']    = hum
                    print(f"Temp: {temp:.1f}°C  Humidity: {hum:.1f}%")
                except Exception as e:
                    print(f"⚠ SHT40 read error: {e}")

                # Telemetry (fire and forget)
                _send_telemetry({
                    'hw':          get_hardware_id(),
                    'rms_max':     last_imu['rms_max'],
                    'pitch':       last_imu['pitch'],
                    'roll':        last_imu['roll'],
                    'temperature': last_env['temperature'],
                    'humidity':    last_env['humidity'],
                    'agc_l1':      last_agc.get('L1'),
                    'agc_l2':      last_agc.get('L2'),
                    'agc_l5':      last_agc.get('L5'),
                })

                # NTRIP watchdog → LED1
                if ntrip:
                    print(f"NTRIP connected: {ntrip.connected}")
                    if not ntrip.connected:
                        print("⚠ NTRIP disconnected, reconnecting...")
                        leds.led1.set(*Color.ORANGE)
                        try:
                            ntrip.connect()
                            leds.led1.set(*Color.GREEN)
                        except Exception as e:
                            print(f"✗ NTRIP reconnect failed: {e}")
                            leds.led1.set(*Color.RED)

            feed_wdt()

    except KeyboardInterrupt:
        print("\nShutting down...")
        leds.off()
        if ntrip:
            try:
                ntrip.disconnect()
            except Exception as e:
                print(f"Warning: NTRIP disconnect error: {e}")
        leds.deinit()
        print("Goodbye!")


if __name__ == '__main__':
    main()
