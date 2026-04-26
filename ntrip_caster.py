import usocket as socket
import time
import _thread
import ubinascii as binascii
import gc
from wdt import feed_wdt

# Retry delays
_RETRY_DELAY_S     =  7    # generic connection failure
_RETRY_DELAY_409_S = 60    # 409 Conflict — wait for caster to release old session


class NTRIPCaster:
    """NTRIP Caster client for sending RTCM data from base station"""

    def __init__(self, server, port, mountpoint, username, password):
        self.server     = server
        self.port       = port
        self.mountpoint = mountpoint
        self.username   = username
        self.password   = password
        self.socket     = None
        self.connected  = False
        self.running    = False

    def _base64_encode(self, user, pwd):
        credentials = f"{user}:{pwd}"
        return binascii.b2a_base64(credentials.encode()).decode().strip()

    def _build_source_request(self):
        auth = self._base64_encode(self.username, self.password)
        return (
            f"POST /{self.mountpoint} HTTP/1.1\r\n"
            f"Host: {self.server}\r\n"
            f"Ntrip-Version: Ntrip/2.0\r\n"
            f"User-Agent: NTRIP MicroPython Base\r\n"
            f"Authorization: Basic {auth}\r\n"
            f"Connection: close\r\n\r\n"
        )

    def _close_socket(self):
        """Cleanly close and release socket — call before every reconnect attempt."""
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        self.connected = False

    def connect(self):
        """
        Connect to NTRIP caster.

        Returns:
            True     — connected successfully
            'retry'  — 409 Conflict, mountpoint busy (wait _RETRY_DELAY_409_S)
            False    — other failure (wait _RETRY_DELAY_S)
        """
        # Always close any existing socket before opening a new one
        self._close_socket()

        print(f"\n=== Connecting to NTRIP Caster ===")
        print(f"Server: {self.server}:{self.port}")
        print(f"Mountpoint: {self.mountpoint}")

        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)

            addr = socket.getaddrinfo(self.server, self.port)[0][-1]
            print(f"Resolved to: {addr}")

            self.socket.connect(addr)
            print("✓ TCP connection established")

            request = self._build_source_request()
            print("\nSending request:")
            print(request[:200] + "...")
            self.socket.send(request.encode())

            # Read response headers
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

            if 'ICY 200 OK' in response_str or 'HTTP/1.1 200 OK' in response_str:
                print("✓ Connected to NTRIP caster successfully")
                self.connected = True
                try:
                    self.socket.setblocking(False)
                except:
                    pass
                return True

            elif '409' in response_str:
                print("⚠ HTTP 409 Conflict — mountpoint busy, caster needs time to release")
                self._close_socket()
                return 'retry'

            else:
                print(f"✗ Connection failed: {response_str[:100]}")
                self._close_socket()
                return False

        except Exception as e:
            print(f"✗ Connection error: {e}")
            self._close_socket()
            return False

    def send_rtcm(self, data):
        """Send RTCM data to caster. Returns False on failure."""
        if not self.connected or not self.socket:
            return False

        try:
            total_sent = 0
            while total_sent < len(data):
                try:
                    sent = self.socket.send(data[total_sent:])
                    if sent == 0:
                        raise OSError("Socket connection broken")
                    total_sent += sent
                except OSError as e:
                    if e.args[0] == 11:   # EAGAIN — socket buffer full
                        time.sleep_ms(10)
                        continue
                    raise
            return True
        except Exception as e:
            print(f"Error sending RTCM: {e}")
            self._close_socket()
            return False

    def disconnect(self):
        """Disconnect from caster."""
        self.running = False
        self._close_socket()
        print("Disconnected from NTRIP caster")

    def _connect_with_retry(self, max_attempts, context=""):
        """
        Keep trying to connect until success or max_attempts exceeded.
        Waits 60s on 409, 7s on other failures.

        Returns True on success, False if max_attempts exhausted.
        """
        self.connected = False   # always show disconnected while retrying
        for attempt in range(1, max_attempts + 1):
            feed_wdt()
            result = self.connect()

            if result is True:
                return True

            if result == 'retry':
                print(f"{context}Mountpoint busy — waiting {_RETRY_DELAY_409_S}s for caster to release... (attempt {attempt}/{max_attempts})")
                steps = _RETRY_DELAY_409_S // 7
                for _ in range(steps):
                    feed_wdt()
                    time.sleep_ms(7000)
            else:
                print(f"{context}Retrying in {_RETRY_DELAY_S}s... (attempt {attempt}/{max_attempts})")
                time.sleep_ms(_RETRY_DELAY_S * 1000)

        print(f"ERROR: Failed to connect after {max_attempts} attempts")
        return False

    def run_threaded(self, data_uart):
        """Read RTCM from data_uart and stream to caster. Runs on core 1."""
        print("\n=== Starting NTRIP Thread ===")

        if not self._connect_with_retry(max_attempts=120, context="[initial] "):
            return

        self.running = True
        last_5min    = time.ticks_ms()
        bytes_window = 0

        try:
            while self.running:
                feed_wdt()

                # Every 5 minutes: GC + throughput summary
                if time.ticks_diff(time.ticks_ms(), last_5min) >= 300000:
                    gc.collect()
                    print(f"NTRIP: {bytes_window} bytes sent in last 5min")
                    bytes_window = 0
                    last_5min = time.ticks_ms()

                if data_uart.any():
                    data = data_uart.read(min(data_uart.any(), 2048))
                    if data:
                        if not self.send_rtcm(data):
                            print("Connection lost, reconnecting...")
                            if not self._connect_with_retry(max_attempts=3600, context="[reconnect] "):
                                self.running = False
                                return
                        else:
                            bytes_window += len(data)

                time.sleep_ms(10)

        except KeyboardInterrupt:
            print("\nStopped by user")
        except Exception as e:
            print(f"\nError in NTRIP thread: {e}")
            import sys
            sys.print_exception(e)
        finally:
            self.disconnect()


def start_ntrip_thread(ntrip_caster, data_uart):
    _thread.start_new_thread(ntrip_caster.run_threaded, (data_uart,))
    print("NTRIP thread started on core 1")


if __name__ == '__main__':
    from machine import UART, Pin
    from network_init import w5x00_init

    print("Initializing Ethernet...")
    nic = w5x00_init(board="W55RP20-EVB-Pico", use_dhcp=True)

    if not nic:
        print("Failed to initialize network")
    else:
        print(f"Connected: {nic.ifconfig()}")

        data_uart = UART(1, baudrate=115200, tx=Pin(8), rx=Pin(9))

        ntrip = NTRIPCaster(
            server='ntrip.example.com',
            port=2101,
            mountpoint='BASE001',
            username='user',
            password='pass'
        )

        start_ntrip_thread(ntrip, data_uart)

        print("Main thread running")
        while True:
            time.sleep_ms(1000)
