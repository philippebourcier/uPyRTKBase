import usocket as socket
import time
import _thread
import ubinascii as binascii
    
class NTRIPCaster:
    """NTRIP Caster client for sending RTCM data from base station"""
    
    def __init__(self, server, port, mountpoint, username, password):
        """
        Initialize NTRIP Caster connection
        
        Args:
            server: NTRIP caster hostname/IP
            port: NTRIP caster port (usually 2101)
            mountpoint: Mountpoint name on caster
            username: Authentication username
            password: Authentication password
        """
        self.server = server
        self.port = port
        self.mountpoint = mountpoint
        self.username = username
        self.password = password
        self.socket = None
        self.connected = False
        self.running = False
        
    def _base64_encode(self, user, pwd):
        """Encode username:password in base64 for HTTP Basic Auth"""
        credentials = f"{user}:{pwd}"
        encoded = binascii.b2a_base64(credentials.encode()).decode().strip()
        return encoded
    
    def _build_source_request(self):
        """Build NTRIP source table request (HTTP POST for base station)"""
        request = (
            f"POST /{self.mountpoint} HTTP/1.1\r\n"
            f"Host: {self.server}\r\n"
            f"Ntrip-Version: Ntrip/2.0\r\n"
            f"User-Agent: NTRIP MicroPython Base\r\n"
        )
        
        # Add authentication
        auth = self._base64_encode(self.username, self.password)
        request += f"Authorization: Basic {auth}\r\n"
        
        request += "Connection: close\r\n\r\n"
        return request
    
    def connect(self):
        """Connect to NTRIP caster"""
        print(f"\n=== Connecting to NTRIP Caster ===")
        print(f"Server: {self.server}:{self.port}")
        print(f"Mountpoint: {self.mountpoint}")
        
        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)
            
            # Resolve hostname and connect
            addr_info = socket.getaddrinfo(self.server, self.port)[0]
            addr = addr_info[-1]
            print(f"Resolved to: {addr}")
            
            self.socket.connect(addr)
            print("✓ TCP connection established")
            
            # Send NTRIP request
            request = self._build_source_request()
            print("\nSending request:")
            print(request[:200] + "...")
            
            self.socket.send(request.encode())
            
            # Wait for response
            response = b''
            start = time.time()
            while time.time() - start < 5:
                try:
                    chunk = self.socket.recv(512)
                    if chunk:
                        response += chunk
                        if b'\r\n\r\n' in response:
                            break
                except:
                    break
            
            response_str = response.decode('utf-8', 'ignore')
            print("\nReceived response:")
            print(response_str[:200])
            
            # Check for success
            if 'ICY 200 OK' in response_str or 'HTTP/1.1 200 OK' in response_str:
                print("\n✓ Connected to NTRIP caster successfully")
                self.connected = True
                return True
            elif 'HTTP/1.1 409' in response_str or 'HTTP/1.0 409' in response_str:
                print(f"\n⚠ HTTP 409 Conflict - Mountpoint already in use")
                self.socket.close()
                self.socket = None
                return 'retry'
            else:
                print(f"\n✗ Connection failed: {response_str[:100]}")
                self.socket.close()
                self.socket = None
                return False
                
        except Exception as e:
            print(f"\n✗ Connection error: {e}")
            if self.socket:
                self.socket.close()
                self.socket = None
            return False
    
    def send_rtcm(self, data):
        """
        Send RTCM data to caster
        
        Args:
            data: bytes object containing RTCM frame(s)
        
        Returns:
            bool: True if sent successfully
        """
        if not self.connected or not self.socket:
            return False
        
        try:
            self.socket.send(data)
            return True
        except Exception as e:
            print(f"Error sending RTCM: {e}")
            self.connected = False
            return False
    
    def disconnect(self):
        """Disconnect from caster"""
        self.running = False
        self.connected = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        print("Disconnected from NTRIP caster")
    
    def run_threaded(self, data_uart):
        """
        Run NTRIP client on separate thread
        Reads RTCM from data_uart and sends to caster
        
        Args:
            data_uart: UART object to read RTCM data from
        """
        print("\n=== Starting NTRIP Thread ===")
        
        # Try to connect, retry indefinitely on 409
        while True:
            result = self.connect()
            if result == True:
                break
            elif result == 'retry':
                print("Waiting 60 seconds before retry...")
                time.sleep(60)
            else:
                print("Failed to connect to caster")
                return
        
        self.running = True
        bytes_sent = 0
        
        try:
            while self.running:
                # Read RTCM data from UART
                if data_uart.any():
                    data = data_uart.read(data_uart.any())
                    if data:
                        if self.send_rtcm(data):
                            bytes_sent += len(data)
                            if bytes_sent % 100000 < len(data):  # Print roughly every 1000 bytes
                                print(f"Sent {bytes_sent} bytes to caster")
                                bytes_sent = 0
                        else:
                            print("Connection lost, reconnecting...")
                            # Retry indefinitely on reconnection
                            while True:
                                result = self.connect()
                                if result == True:
                                    break
                                elif result == 'retry':
                                    print("Waiting 60 seconds before retry...")
                                    time.sleep(60)
                                else:
                                    print("Reconnection failed, retrying in 60s...")
                                    time.sleep(60)
                
                time.sleep(0.01)
                
        except KeyboardInterrupt:
            print("\nStopped by user")
        except Exception as e:
            print(f"\nError in NTRIP thread: {e}")
        finally:
            self.disconnect()


def start_ntrip_thread(ntrip_caster, data_uart):
    """
    Helper to start NTRIP caster on second core
    
    Args:
        ntrip_caster: NTRIPCaster instance
        data_uart: UART object for reading RTCM data
    """
    _thread.start_new_thread(ntrip_caster.run_threaded, (data_uart,))
    print("NTRIP thread started on core 1")


# Usage example
if __name__ == '__main__':
    from machine import UART, Pin
    from network_init import w5x00_init
    
    # Step 1: Initialize Ethernet
    print("Initializing Ethernet...")
    nic = w5x00_init(board="W55RP20-EVB-Pico", use_dhcp=True)
    
    if not nic:
        print("Failed to initialize network")
    else:
        print(f"Connected: {nic.ifconfig()}")
        
        # Step 2: Initialize data UART (COM2)
        data_uart = UART(1, baudrate=115200, tx=Pin(8), rx=Pin(9))
        
        # Step 3: Create NTRIP caster client (no position needed for base)
        ntrip = NTRIPCaster(
            server='ntrip.example.com',
            port=2101,
            mountpoint='BASE001',
            username='user',
            password='pass'
        )
        
        # Step 4: Start on second core
        start_ntrip_thread(ntrip, data_uart)
        
        # Main thread continues...
        print("Main thread running")
        while True:
            time.sleep(1)
