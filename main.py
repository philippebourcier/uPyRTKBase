"""
Main workflow for RTK Base Station on W55RP20
Order of operations:
1. Initialize UM980 sensor
2. Initialize TCP/Ethernet
3. Download configuration from server
4. Launch NTRIP thread (COM2 -> NTRIP caster)
5. Main thread does other tasks
"""

import time
from um980_config import UM980Config
from config_manager import get_hardware_id, download_config, print_config, config
from network_init import w5x00_init, print_network_status
from ntrip_caster import NTRIPCaster, start_ntrip_thread

def main():
    print("=" * 60)
    print("RTK BASE STATION - W55RP20")
    print("=" * 60)
    
    # ===================================================================
    # STEP 1: Initialize TCP/Ethernet
    # ===================================================================
    print("\n[STEP 1] Initializing Ethernet...")
    
    nic = w5x00_init(board="W55RP20-EVB-Pico", use_dhcp=True)
    # Or static IP:
    # nic = w5x00_init(board="W55RP20-EVB-Pico",use_dhcp=False,
    #     static_ip='192.168.11.20',
    #     subnet='255.255.255.0',
    #     gateway='192.168.11.1',
    #     dns='8.8.8.8'
    # )
    
    if not nic:
        print("✗ Failed to initialize network")
        return
    
    print_network_status()

    
    # ===================================================================
    # STEP 2: Initialize UM980 Sensor
    # ===================================================================
    print("\n[STEP 2] Initializing UM980 Sensor...")
    
    um980 = UM980Config(
        uart_id=0, tx_pin=0, rx_pin=1,           # Control UART (COM1)
        data_uart_id=1, data_tx_pin=8, data_rx_pin=9,  # Data UART (COM2)
        baudrate=115200, en_pin=6
    )
    
    # Get receiver info
    model, firmware = um980.get_receiver_model()
    if not model:
        print("✗ Failed to detect UM980")
        return
    
    # Check if UM980 needs configuration
    print("\n[STEP 3b] Checking UM980 Configuration...")
    needs_update, current_config = um980.check_config_matches()
    
    if needs_update:
        print("\n⚠ UM980 needs configuration update")
        print("Configuring automatically...")
        um980.full_configuration()
        print("✓ UM980 configured")
    else:
        print("✓ UM980 already configured correctly")
    
    print(f"✓ UM980 detected: {model}, FW: {firmware}")
    
    # ===================================================================
    # STEP 3: Download Configuration from Server
    # ===================================================================
    print("\n[STEP 3] Downloading Configuration...")
    
    hw_id = get_hardware_id()
    print(f"Hardware ID: {hw_id}")
    
    # Try to download config from server
    config_server = 'https://cloud-cfg.crtk.net/'  # Change to your server
    if download_config(config_server):
        print("✓ Configuration downloaded")
        print_config()
    else:
        print("⚠ Using default configuration")
    
    # ===================================================================
    # STEP 4: Launch NTRIP Thread (COM2 -> NTRIP Caster)
    # ===================================================================
    print("\n[STEP 4] Starting NTRIP Caster Thread...")
    
    if not all([config['ntrip_server'], config['ntrip_mountpoint'], 
                config['ntrip_user'], config['ntrip_password']]):
        print("✗ Missing NTRIP configuration")
        print("  Please configure: ntrip_server, ntrip_mountpoint, ntrip_user, ntrip_password")
        print("\n  Skipping NTRIP - continuing without caster connection")
        ntrip = None
    else:
        ntrip = NTRIPCaster(
            server=config['ntrip_server'],
            port=int(config['ntrip_port']),
            mountpoint=config['ntrip_mountpoint'],
            username=config['ntrip_user'],
            password=config['ntrip_password']
        )
        
        # Start NTRIP on core 1
        start_ntrip_thread(ntrip, um980.data_uart)
        print("✓ NTRIP thread started on core 1")
    
    # ===================================================================
    # STEP 5: Main Thread - Periodic Tasks
    # ===================================================================
    print("\n[STEP 5] Main Thread Running...")
    print("=" * 60)
    print("Base station is operational!")
    print("Press Ctrl+C to stop")
    print("=" * 60)
    
    loop_count = 0

    try:
        while True:
            loop_count += 1
            
            # Every 30 seconds: Check AGC status
            if loop_count % 30 == 0:
                print("\n--- Periodic Check ---")
                agc_status = um980.get_agc_status()
                if agc_status:
                    bad_bands = [k for k, v in agc_status.items() if v == 'bad']
                    if bad_bands:
                        print(f"⚠ Poor AGC on: {bad_bands}")
                    else:
                        print("✓ AGC status good")
                
                # Print network status
                print_network_status()
            
            # Every 5 minutes: Print summary
            if loop_count % 300 == 0:
                print("\n=== 5 Minute Summary ===")
                print(f"Uptime: {loop_count} seconds")
                if ntrip:
                    print(f"NTRIP Connected: {ntrip.connected}")
                
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        if ntrip:
            ntrip.disconnect()
        print("Goodbye!")

if __name__ == '__main__':
    main()
