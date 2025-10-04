from wiznet_init import wiznet
import time

# Global network interface
nic = None

def w5x00_init(board="W55RP20-EVB-Pico", use_dhcp=True, static_ip=None, subnet=None, gateway=None, dns=None):
    """
    Initialize W5x00 Ethernet chip on W55RP20
    
    Args:
        board: Board name (default: "W55RP20-EVB-Pico")
        use_dhcp: Use DHCP if True, static IP if False
        static_ip: Static IP address (if not using DHCP)
        subnet: Subnet mask (if not using DHCP)
        gateway: Gateway address (if not using DHCP)
        dns: DNS server (if not using DHCP)
    
    Returns:
        Network interface object or None on failure
    """
    global nic
    
    print("\n=== Initializing W5x00 Ethernet ===")
    print(f"Board: {board}")
    
    try:
        if use_dhcp:
            print("Using DHCP...")
            nic = wiznet(board, dhcp=True)
        else:
            if not all([static_ip, subnet, gateway, dns]):
                print("✗ Missing static IP configuration")
                return None
            print(f"Using static IP: {static_ip}")
            nic = wiznet(board, dhcp=False, ip=static_ip, sn=subnet, gw=gateway, dns=dns)
        
        if nic is None:
            print("✗ Failed to initialize network interface")
            return None
        
        print(f"IP address: {nic.ifconfig()}")
        
        # Wait for connection
        print("Waiting for Ethernet connection", end='')
        timeout = 15
        start = time.time()
        while not nic.isconnected():
            if time.time() - start > timeout:
                print("\n✗ Ethernet connection timeout")
                return None
            time.sleep(0.5)
            print(".", end='')
        
        print("\n✓ Ethernet connected")
        print(f"Network config: {nic.ifconfig()}")
        return nic
        
    except Exception as e:
        print(f"\n✗ Network initialization failed: {e}")
        return None

def get_network_status():
    """Get current network status"""
    global nic
    
    if nic is None:
        return {'connected': False, 'ip': None}
    
    try:
        return {
            'connected': nic.isconnected(),
            'ip': nic.ifconfig()[0] if nic.isconnected() else None,
            'subnet': nic.ifconfig()[1] if nic.isconnected() else None,
            'gateway': nic.ifconfig()[2] if nic.isconnected() else None,
            'dns': nic.ifconfig()[3] if nic.isconnected() else None
        }
    except:
        return {'connected': False, 'ip': None}

def print_network_status():
    """Print current network status"""
    status = get_network_status()
    print("\n=== Network Status ===")
    print(f"Connected: {status['connected']}")
    if status['connected']:
        print(f"IP:      {status['ip']}")
        print(f"Subnet:  {status['subnet']}")
        print(f"Gateway: {status['gateway']}")
        print(f"DNS:     {status['dns']}")

# Usage
if __name__ == '__main__':
    # DHCP example
    nic = w5x00_init(board="W55RP20-EVB-Pico", use_dhcp=True)
    
    # Static IP example
    # nic = w5x00_init(
    #     board="W55RP20-EVB-Pico",
    #     use_dhcp=False,
    #     static_ip='192.168.11.20',
    #     subnet='255.255.255.0',
    #     gateway='192.168.11.1',
    #     dns='8.8.8.8'
    # )
    
    if nic:
        print_network_status()
