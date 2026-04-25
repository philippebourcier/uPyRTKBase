"""
Main workflow for RTK Base Station on W55RP20
Order of operations:
1. Load local config (determines DHCP vs static)
2. Initialize Ethernet using loaded config
3. Download remote config from server
4. Initialize UM980 sensor
5. Launch NTRIP thread (COM2 -> NTRIP caster)
6. Main loop: AGC checks, periodic summary, WDT feed

LED1 = Internet status
LED2 = GNSS signal status
"""

import gc
import time
from um980_config import UM980Config
from config_manager import get_hardware_id, load_local, download_config, print_config, config
from network_init import w5x00_init, print_network_status
from ntrip_caster import NTRIPCaster, start_ntrip_thread
from rgb_led_drv import LEDManager, Color
from wdt import feed_wdt

leds = LEDManager()

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

    if download_config('http://picontrol.sysctl.org'):
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
    # STEP 5: Launch NTRIP Thread (COM2 -> NTRIP Caster)
    # ===================================================================
    print("\n[STEP 5] Starting NTRIP Caster Thread...")

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
    # STEP 6: Main Loop
    # ===================================================================
    print("\n[STEP 6] Main Loop Running...")
    print("=" * 60)
    print("Base station is operational!")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    loop_count = 0
    LOOP_MAX = 86400
    
    try:
        while True:
            loop_count = (loop_count + 1) % LOOP_MAX

            # Every 30 seconds: AGC check → LED2 (GNSS signal)
            if loop_count % 30 == 0:
                print("\n--- Periodic Check ---")
                try:
                    agc_status = um980.get_agc_status()
                    if agc_status:
                        bad_bands = [k for k, v in agc_status.items() if v == 'bad']
                        if bad_bands:
                            print(f"⚠ Poor AGC on: {bad_bands}")
                            leds.led2.set(*Color.ORANGE)
                        else:
                            print("✓ AGC status good")
                            leds.led2.set(*Color.GREEN)
                except Exception as e:
                    print(f"✗ AGC check error: {e}")
                    leds.led2.set(*Color.RED)

            # Every 5 minutes: summary + NTRIP watchdog → LED1 (Internet)
            if loop_count % 300 == 0:
                print(f"\n=== 5 Minute Summary (uptime: {loop_count}s) ===")
                gc.collect()
                print_network_status()

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
            time.sleep(1)

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