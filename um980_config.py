from machine import Pin, UART
import time
import rtcm_decoder
from rtcm_params import RTCM_MESSAGES
import gc
from machine import WDT
wdt = WDT(timeout=8000)

class UM980Config:
        
    def __init__(self, uart_id=0, tx_pin=0, rx_pin=1, baudrate=115200, en_pin=6, data_uart_id=None, data_tx_pin=None, data_rx_pin=None):
        """Initialize UM980 configuration"""
        self.baudrate = baudrate
        
        # Enable pin
        self.en_pin = Pin(en_pin, Pin.OUT)
        self.en_pin.value(1)
        print(f"Enabled pin {en_pin}")
        time.sleep(0.5)
        
        # Initialize control UART (COM1)
        self.uart = UART(uart_id, baudrate=baudrate, tx=Pin(tx_pin), rx=Pin(rx_pin), bits=8, parity=None, stop=1, timeout=2000)
        print(f"UART{uart_id} initialized at {baudrate} baud (Control - COM1)")
        
        # RTCM Decoder
        self.decoder = rtcm_decoder.RTCMDecoder()
        
        # Initialize data UART (COM2) if specified
        self.data_uart = None
        if data_uart_id is not None and data_tx_pin is not None and data_rx_pin is not None:
            self.data_uart = UART(data_uart_id, baudrate=baudrate, tx=Pin(data_tx_pin), rx=Pin(data_rx_pin), bits=8, parity=None, stop=1, timeout=2000, rxbuf=4096)
            print(f"UART{data_uart_id} initialized at {baudrate} baud (Data - COM2)")
        
    def _xor8_checksum(self, data):
        """Calculate XOR checksum"""
        checksum = 0
        for char in data:
            checksum ^= ord(char)
        return '{:02X}'.format(checksum & 0xFF)
    
    def _cmd_with_checksum(self, cmd):
        """Add $ prefix and checksum"""
        return f'${cmd}*{self._xor8_checksum(cmd)}'

    def _clear_buffer(self):
        # Clear buffer safely with size limit
        bytes_to_clear = self.uart.any()
        if bytes_to_clear > 0:
            # Clear in chunks to prevent memory issues
            while bytes_to_clear > 0:
                chunk_size = min(bytes_to_clear, 1024)
                self.uart.read(chunk_size)
                bytes_to_clear = self.uart.any()

    def send_query(self, cmd, timeout=5):
        """
        Send query command (no checksum, no $)
        Used for: MODE, CONFIG, UNILOGLIST, VERSIONA
        """
        self._clear_buffer()
        
        self.uart.write(cmd.encode() + b'\r\n')
        print(f"Sent: {cmd}")
        
        start = time.ticks_ms()
        response = b''
        last_data_time = None
        max_response_size = 4096
        
        while time.ticks_diff(time.ticks_ms(), start) < timeout * 1000 and len(response) < max_response_size:
            if self.uart.any():
                chunk = self.uart.read(self.uart.any())
                if chunk:
                    response += chunk
                    last_data_time = time.ticks_ms()
            
            if response and last_data_time is not None:
                if time.ticks_diff(time.ticks_ms(), last_data_time) > 500:
                    resp_str = response.decode('utf-8', 'ignore')
                    print(f"Response: {len(response)} bytes, {resp_str.count(chr(10))} lines")
                    return resp_str
            
            time.sleep(0.01)
        
        if response:
            return response.decode('utf-8', 'ignore')
        print("No response received")
        return None

    def send_command(self, cmd, timeout=2):
        """
        Send configuration command (with checksum and $)
        Used for: CONFIG xxx, MODE BASE, RTCM messages, SAVECONFIG, etc.
        """
        cmd_with_cs = self._cmd_with_checksum(cmd)

        self._clear_buffer()
        
        self.uart.write(cmd_with_cs.encode() + b'\r\n')
        print(f"Sent: {cmd_with_cs}")
        
        start = time.ticks_ms()
        response = b''
        last_data_time = None
        
        while time.ticks_diff(time.ticks_ms(), start) < timeout * 1000:
            if self.uart.any():
                chunk = self.uart.read(self.uart.any())
                if chunk:
                    response += chunk
                    last_data_time = time.ticks_ms()
            
            if response and last_data_time is not None:
                if time.ticks_diff(time.ticks_ms(), last_data_time) > 500:
                    resp_str = response.decode('utf-8', 'ignore')
                    if 'OK' in resp_str:
                        print(f"Response: OK")
                    else:
                        print(f"Response: {len(response)} bytes")
                    return resp_str
            
            time.sleep(0.01)
        
        if response:
            return response.decode('utf-8', 'ignore')
        print("No response received")
        return None

    def get_receiver_model(self):
        """Get UM980 model information"""
        print("\n=== Getting Receiver Model ===")
        resp_str = self.send_query('VERSIONA', timeout=3)
        
        if resp_str and '#VERSIONA' in resp_str:
            for line in resp_str.split('\n'):
                if '#VERSIONA' in line:
                    try:
                        parts = line.split(';')[-1].split(',')
                        model = parts[0].replace('"', '').strip()
                        firmware = parts[1].replace('"', '').strip()
                        print(f"Model: {model}, Firmware: {firmware}")
                        return model, firmware
                    except:
                        pass
        return None, None
    
    def get_current_config(self):
        """Get current receiver configuration"""
        print("\n=== Reading Current Configuration ===")
        config = {
            'signal_group': None,
            'sbas_enabled': None,
            'mode': None,
            'com_ports': {},
            'rtcm_messages': {}
        }
        
        # Get CONFIG using query
        resp = self.send_query('CONFIG', timeout=5)
        if resp:
            for line in resp.split('\n'):
                # Parse SIGNALGROUP
                if 'SIGNALGROUP' in line and 'CONFIG' in line:
                    try:
                        parts = line.split(',')
                        if len(parts) >= 3:
                            values = parts[2].split('*')[0].split()
                            if 'SIGNALGROUP' in values:
                                idx = values.index('SIGNALGROUP')
                                if idx + 1 < len(values):
                                    config['signal_group'] = int(values[idx + 1])
                                    print(f"✓ Signal Group: {config['signal_group']}")
                    except Exception as e:
                        print(f"Error parsing SIGNALGROUP: {e}")
                
                # Parse SBAS
                elif 'SBAS' in line and 'CONFIG' in line:
                    if 'ENABLE' in line.upper():
                        config['sbas_enabled'] = True
                        print("✓ SBAS: Enabled")
                    elif 'DISABLE' in line.upper():
                        config['sbas_enabled'] = False
                        print("✓ SBAS: Disabled")
                
                # Parse COM ports
                elif line.startswith('$CONFIG,COM'):
                    try:
                        parts = line.split(',')
                        if len(parts) >= 3:
                            port = parts[1]
                            baud = parts[2].split('*')[0].split()[2]
                            config['com_ports'][port] = baud
                            print(f"✓ {port}: {baud} baud")
                    except:
                        pass
        
        # Get MODE using query
        resp = self.send_query('MODE', timeout=5)
        if resp:
            for line in resp.split('\n'):
                if line.startswith('#MODE'):
                    try:
                        parts = line.split(';')
                        if len(parts) >= 2:
                            mode_str = parts[1].split('*')[0].strip().rstrip(',')
                            config['mode'] = mode_str
                            print(f"✓ Mode: {mode_str}")
                    except Exception as e:
                        print(f"Error parsing MODE: {e}")
        
        # Get RTCM messages using query
        resp = self.send_query('UNILOGLIST', timeout=5)
        if resp:
            print("\n=== Active RTCM Messages ===")
            for line in resp.split('\n'):
                if 'RTCM' in line and 'COM' in line:
                    try:
                        parts = line.strip().split()
                        if len(parts) >= 4 and parts[0] == '<':
                            msg_type = parts[1]
                            port = parts[2]
                            rate = parts[3]
                            config['rtcm_messages'][msg_type] = {'port': port, 'rate': rate}
                            print(f"  {msg_type} on {port} @ {rate}s")
                    except:
                        pass
            
            if not config['rtcm_messages']:
                print("  No RTCM messages active")
        
        return config
    
    def check_config_matches(self, desired_signal_group=2):
        """Check if configuration matches desired settings"""
        config = self.get_current_config()
        
        if not config:
            return True, None
        
        needs_update = False
        
        # Check signal group
        if config['signal_group'] != desired_signal_group:
            print(f"\n✗ Signal group: {config['signal_group']} (want {desired_signal_group})")
            needs_update = True
        
        # Check SBAS
        if not config['sbas_enabled']:
            print("✗ SBAS disabled (should be enabled)")
            needs_update = True
        
        # Check mode
        if config['mode'] and 'BASE' not in config['mode'].upper():
            print(f"✗ Not in base mode: {config['mode']}")
            needs_update = True
        
        # Check RTCM messages using class constant
        required_rtcm = {msg_type for msg_type, _ in RTCM_MESSAGES}
        
        active_on_com2 = {msg for msg, cfg in config['rtcm_messages'].items() 
                          if cfg['port'] == 'COM2'}
        missing = required_rtcm - active_on_com2
        
        if missing:
            print(f"\n✗ Missing RTCM on COM2: {missing}")
            needs_update = True
        
        if not needs_update:
            print("\n✓ Configuration is correct")
        
        return needs_update, config
    
    def configure_base_station(self, mode='time', duration=60, pdop=1):
        """Configure as RTK base station"""
        print("\n=== Configuring Base Station ===")
        
        print("Setting signal group 2...")
        self.send_command('CONFIG SIGNALGROUP 2')
        print("Waiting for reboot (10s)...")
        time.sleep(10)
        
        print("Enabling SBAS...")
        self.send_command('CONFIG SBAS ENABLE AUTO')
        time.sleep(0.5)
        
        cmd = f'MODE BASE 1 {mode.upper()} {duration} {pdop}'
        print(f"Setting base mode: {cmd}")
        self.send_command(cmd)
        time.sleep(0.5)
    
    def configure_rtcm_messages(self, com_port='COM2'):
        """Configure RTCM3 message output"""
        print(f"\n=== Configuring RTCM on {com_port} ===")
        
        for msg_type, interval in RTCM_MESSAGES:
            cmd = f'{msg_type} {com_port} {interval}'
            print(f"  {msg_type} @ {interval}s")
            self.send_command(cmd)
            time.sleep(0.2)

        gc.collect()
    
    def save_config(self):
        """Save configuration to NVM"""
        print("\n=== Saving Configuration ===")
        self.send_command('SAVECONFIG')
        time.sleep(1)
    
    def read_rtcm_data(self, duration=10, callback=None):
        """
        Read RTCM data from COM2 (data_uart)
        """
        if self.data_uart is None:
            print("ERROR: Data UART (COM2) not initialized")
            return None
        
        print(f"\n=== Reading RTCM Data from COM2 ===")
        
        total_bytes = 0
        start_time = time.ticks_ms()
        
        try:
            while True:
                # Check if data available
                bytes_available = self.data_uart.any()
                
                if bytes_available > 0:
                    data = self.data_uart.read(min(bytes_available,2048))
                    if data:
                        if callback:
                            callback(data)
                        else:
                            print(f"Received {len(data)} bytes")
                
                # Check timeout
                if duration > 0:
                    elapsed = time.ticks_diff(time.ticks_ms(), start_time) / 1000
                    if elapsed >= duration:
                        break
                
                # Small sleep to prevent busy-waiting
                time.sleep_ms(20)
        
        except KeyboardInterrupt:
            print("\nStopped by user")
        except Exception as e:
            print(f"\nError reading RTCM data: {e}")
        
        print(f"\nTotal bytes read: {total_bytes}")
        return total_bytes if not callback else None

    def get_agc_values(self, max_retries=9):
        """
        Get the automatic gain control values (UM980 single antenna)
        
        Args:
            max_retries: Number of attempts to get valid response (default 3)
        
        Returns:
            dict: AGC values {'L1': val, 'L2': val, 'L5': val}
                  Returns None if command fails
        """
        print("\n=== Getting AGC Values ===")
        
        for attempt in range(max_retries):
            wdt.feed()
            # Use send_query which returns both OK and #AGCA response
            resp_str = self.send_query('AGCA', timeout=3)
            
            if not resp_str:
                print(f"Attempt {attempt + 1}/{max_retries}: No response")
                time.sleep(0.5)
                continue
            
            # Check if we only got the $command response (not the actual data)
            if '$command' in resp_str and '#AGCA' not in resp_str:
                print(f"Attempt {attempt + 1}/{max_retries}: Got command ACK only, retrying...")
                time.sleep(0.5)
                continue
            
            # Parse the #AGCA response
            # Format: #AGCA,98,GPS,UNKNOWN,1,5544000,0,0,18,3;95,74,83,-1,-1,-1,-1,-1,-1,-1*f831d559
            # Values after semicolon: first 3 are L1, L2, L5
            if '#AGCA' in resp_str:
                for line in resp_str.split('\n'):
                    if '#AGCA' in line:
                        try:
                            # Split by semicolon to get the values section
                            values_part = line.split(';')[-1].split('*')[0]
                            values_list = [int(x.strip()) for x in values_part.split(',')]
                            
                            # Extract first 3 values (L1, L2, L5)
                            agc_values = {
                                'L1': values_list[0],
                                'L2': values_list[1],
                                'L5': values_list[2]
                            }
                            
                            print(f"L1: {agc_values['L1']}, L2: {agc_values['L2']}, L5: {agc_values['L5']}")
                            
                            return agc_values
                        except Exception as e:
                            print(f"Attempt {attempt + 1}/{max_retries}: ERROR parsing AGCA response: {e}")
                            print(f"Response was: {resp_str}")
                            time.sleep(0.5)
                            continue
        
        print(f"ERROR: Failed to get valid AGCA response after {max_retries} attempts")
        return None
    
    def get_agc_status(self, good_max=10):
        """
        Get the automatic gain control status (human readable)
        
        Args:
            good_max: Maximum value considered 'good' (default 10)
        
        Returns:
            dict: Status for each frequency {'L1': 'good/bad', 'L2': 'good/bad', 'L5': 'good/bad'}
                  Returns None if unable to get values
        """
        agc_values = self.get_agc_values()
        
        if agc_values is None:
            return None
        
        agc_status = {}
        
        print("\n=== AGC Status ===")
        
        for freq in ['L1', 'L2', 'L5']:
            value = agc_values[freq]
            
            if value == -1:
                status = 'unknown'
            elif value >= 0 and value < good_max:
                status = 'good'
            else:
                status = 'bad'
            
            agc_status[freq] = status
            print(f"{freq}: {status} (value: {value})")
        
        return agc_status
    
    def full_configuration(self, force_update=False):
        """Complete configuration sequence"""
        print("=" * 50)
        print("UM980 CONFIGURATION")
        print("=" * 50)
        
        model, firmware = self.get_receiver_model()
        
        if model and 'UM98' in model:
            print(f"\nDetected: {model}")
            
            if not force_update:
                needs_update, _ = self.check_config_matches()
                if not needs_update:
                    print("\n✓ Already configured correctly")
                    return
            
            self.configure_base_station(mode='time', duration=60, pdop=1)
            self.configure_rtcm_messages(com_port='COM2')
            self.save_config()
            
            print("\n" + "=" * 50)
            print("CONFIGURATION COMPLETE")
            print("=" * 50)
        else:
            print("ERROR: Could not detect UM980")

# Usage
if __name__ == '__main__':
    um980 = UM980Config(
        uart_id=0, tx_pin=0, rx_pin=1,
        data_uart_id=1, data_tx_pin=8, data_rx_pin=9,
        baudrate=115200, en_pin=6
    )
    
    #um980.send_query("FRESET")
    # Check current config
    #um980.get_current_config()
    
    # Configure (only if needed)
    #um980.full_configuration()

    um980.decoder.debug_crc = True
    um980.read_rtcm_data(duration=10,callback=um980.decoder.process)
    
    


