from machine import unique_id
import socket
import ssl
import ujson as json

# Global configuration variables
config = {
    'loaded': False,
    'base_mode': 'time',
    'base_duration': 60,
    'base_pdop': 1,
    'signal_group': 2,
    'sbas_enabled': True,
    'rtcm_interval': 1,
    'ntrip_server': "crtk.net",
    'ntrip_port': 2101,
    'ntrip_mountpoint': None,
    'ntrip_user': None,
    'ntrip_password': None,
}

def get_hardware_id():
    """
    Get unique hardware ID for W55RP20
    Returns hex string of the unique ID
    """
    uid = unique_id()
    return ''.join('{:02x}'.format(b) for b in uid)

def download_config(server_url, timeout=10):
    """
    Download configuration from server and update global config
    Supports both HTTP and HTTPS
    
    Args:
        server_url: Full URL (e.g., 'http://192.168.1.100' or 'https://example.com')
        timeout: Request timeout in seconds
    
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
            host = server_url[8:]  # Remove 'https://'
            port = 443
        elif server_url.startswith('http://'):
            use_ssl = False
            host = server_url[7:]  # Remove 'http://'
            port = 80
        else:
            print("ERROR: URL must start with http:// or https://")
            return False
        
        # Remove trailing slash and extract path if present
        if '/' in host:
            host, path_prefix = host.split('/', 1)
            path_prefix = '/' + path_prefix
        else:
            path_prefix = ''
        
        # Handle port in host
        if ':' in host:
            host, port_str = host.split(':', 1)
            port = int(port_str)
        
        path = f"{path_prefix}/config.json?b={hw_id}"
        
        print(f"Host: {host}")
        print(f"Port: {port}")
        print(f"Path: {path}")
        print(f"SSL: {use_ssl}")
        
        # Lookup server address
        try:
            ai = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except Exception as e:
            print(f"ERROR: DNS lookup failed: {e}")
            return False
        ai = ai[0]
        
        # Create socket
        s = socket.socket(ai[0], ai[1], ai[2])
        s.settimeout(timeout)
        
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
        s.write(request.encode())
        
        # Read response
        response = b''
        while True:
            try:
                chunk = s.read(512)
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
        
        # Split headers and body
        if '\r\n\r\n' not in response_str:
            print("ERROR: Invalid HTTP response")
            return False
        
        headers, body = response_str.split('\r\n\r\n', 1)
        
        # Check status code
        status_line = headers.split('\r\n')[0]
        if '200' not in status_line:
            print(f"ERROR: HTTP {status_line}")
            return False
        
        print("✓ HTTP 200 OK")
        
        # Parse JSON body
        server_config = json.loads(body)
        
        print(f"Received config: {len(server_config)} settings")
        
        # Update global config with server values
        for key, value in server_config.items():
            if key in config:
                old_value = config[key]
                config[key] = value
                print(f"  {key}: {old_value} -> {value}")
            else:
                config[key] = value
                print(f"  {key}: {value} (new)")
        
        config['loaded'] = True
        print("✓ Configuration loaded successfully")
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
    """Print current configuration"""
    print("\n=== Current Configuration ===")
    for key, value in config.items():
        print(f"  {key}: {value}")

# Usage
if __name__ == '__main__':
    hw_id = get_hardware_id()
    print(f"Hardware ID: {hw_id}")
    
    # HTTP example
    # if download_config('http://192.168.11.100'):
    #     print_config()
    
    # HTTPS example
    if download_config('https://example.com'):
        print_config()

