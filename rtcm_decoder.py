class RTCMDecoder:
    """
    Lightweight RTCM3 message decoder for embedded systems
    Based on RTCM 10403.3 specification
    """
    
    # RTCM3 message types (matching UM980Config.RTCM_MESSAGES)
    MSG_TYPES = {
        1005: "Stationary RTK Reference Station ARP",
        1006: "Stationary RTK Reference Station ARP with Height",
        1033: "Receiver and Antenna Descriptors",
        1019: "GPS Ephemeris",
        1020: "GLONASS Ephemeris",
        1042: "BeiDou Ephemeris",
        1044: "QZSS Ephemeris",
        1045: "Galileo F/NAV Ephemeris",
        1046: "Galileo I/NAV Ephemeris",
        1077: "GPS MSM7",
        1087: "GLONASS MSM7",
        1097: "Galileo MSM7",
        1107: "SBAS MSM7",
        1117: "QZSS MSM7",
        1127: "BeiDou MSM7",
    }
    
    def __init__(self, debug_crc=False):
        self.buffer = bytearray()
        self.msg_count = 0
        self.error_count = 0
        self.debug_crc = debug_crc
    
    def process(self, data):
        """
        Process incoming RTCM data bytes
        
        Args:
            data: bytes object containing RTCM data
        """
        self.buffer.extend(data)
        
        # Process all complete messages in buffer
        while len(self.buffer) >= 3:
            # Look for RTCM3 preamble (0xD3)
            if self.buffer[0] != 0xD3:
                # Skip byte and continue searching
                self.buffer = self.buffer[1:]
                continue
            
            # Check if we have enough bytes to read header
            if len(self.buffer) < 6:
                break
            
            # Parse message length (10 bits after reserved 6 bits)
            msg_len = ((self.buffer[1] & 0x03) << 8) | self.buffer[2]
            
            # Total frame length: 3 (header) + msg_len + 3 (CRC)
            frame_len = 3 + msg_len + 3
            
            # Wait for complete message
            if len(self.buffer) < frame_len:
                break
            
            # Extract complete frame
            frame = self.buffer[:frame_len]
            
            # Verify CRC24Q
            if self._verify_crc(frame):
                self._decode_message(frame)
                self.msg_count += 1
            else:
                if self.debug_crc:
                    self._dump_failed_frame(frame)
                else:
                    print(f"[ERROR] CRC failed for message")
                self.error_count += 1
            
            # Remove processed frame from buffer
            self.buffer = self.buffer[frame_len:]
    
    def _verify_crc(self, frame):
        """
        Verify RTCM3 CRC24Q
        
        Args:
            frame: Complete RTCM frame including CRC
            
        Returns:
            True if CRC is valid
        """
        # Extract CRC from last 3 bytes
        received_crc = (frame[-3] << 16) | (frame[-2] << 8) | frame[-1]
        
        # Calculate CRC on everything except last 3 bytes
        calculated_crc = self._crc24q(frame[:-3])
        
        return received_crc == calculated_crc
    
    def _dump_failed_frame(self, frame):
        """
        Dump hex data for failed CRC messages
        
        Args:
            frame: Complete RTCM frame with failed CRC
        """
        # Extract message info
        msg_len = ((frame[1] & 0x03) << 8) | frame[2]
        payload = frame[3:-3]
        msg_type = (payload[0] << 4) | (payload[1] >> 4) if len(payload) >= 2 else 0
        
        # Get CRCs
        received_crc = (frame[-3] << 16) | (frame[-2] << 8) | frame[-1]
        calculated_crc = self._crc24q(frame[:-3])
        
        print(f"[ERROR] CRC failed for Type {msg_type} ({msg_len} bytes)")
        print(f"  Received CRC:   0x{received_crc:06X}")
        print(f"  Calculated CRC: 0x{calculated_crc:06X}")
        print(f"  Frame (first 32 bytes): {self._to_hex(frame[:32])}")
        if len(frame) > 32:
            print(f"  Frame (last 16 bytes):  {self._to_hex(frame[-16:])}")
    
    def _to_hex(self, data):
        """Convert bytes to hex string"""
        return ' '.join(f'{b:02X}' for b in data)
    
    def _crc24q(self, data):
        """
        Calculate CRC24Q (Qualcomm CRC-24)
        Polynomial: 0x1864CFB
        """
        crc = 0
        for byte in data:
            crc ^= (byte << 16)
            for _ in range(8):
                crc <<= 1
                if crc & 0x1000000:
                    crc ^= 0x1864CFB
        return crc & 0xFFFFFF
    
    def _decode_message(self, frame):
        """
        Decode and display RTCM message information
        
        Args:
            frame: Complete validated RTCM frame
        """
        # Extract message payload (skip 3-byte header, exclude 3-byte CRC)
        payload = frame[3:-3]
        
        # Message type is first 12 bits of payload
        msg_type = (payload[0] << 4) | (payload[1] >> 4)
        
        # Get message name
        msg_name = self.MSG_TYPES.get(msg_type, "Unknown")
        
        # Extract common fields based on message type
        details = ""
        
        if msg_type in [1005, 1006]:
            # Station position messages
            station_id = ((payload[1] & 0x0F) << 8) | payload[2]
            details = f"Station ID: {station_id}"
        
        elif 1001 <= msg_type <= 1004:
            # GPS observables
            station_id = ((payload[1] & 0x0F) << 8) | payload[2]
            details = f"Station ID: {station_id}"
        
        elif 1009 <= msg_type <= 1012:
            # GLONASS observables
            station_id = ((payload[1] & 0x0F) << 8) | payload[2]
            details = f"Station ID: {station_id}"
        
        elif msg_type in [1019, 1020, 1042, 1044, 1045, 1046]:
            # Ephemeris messages
            sat_id = (payload[1] & 0x0F) << 2 | (payload[2] >> 6)
            constellation = {
                1019: "GPS",
                1020: "GLONASS", 
                1042: "BeiDou",
                1044: "QZSS",
                1045: "Galileo F/NAV",
                1046: "Galileo I/NAV"
            }
            details = f"Satellite: {constellation[msg_type]} {sat_id}"
        
        elif msg_type in [1077, 1087, 1097, 1107, 1117, 1127]:
            # MSM7 messages (Multi-Signal Messages)
            station_id = ((payload[1] & 0x0F) << 8) | payload[2]
            constellation = {
                1077: "GPS",
                1087: "GLONASS",
                1097: "Galileo",
                1107: "SBAS",
                1117: "QZSS",
                1127: "BeiDou"
            }
            details = f"Station ID: {station_id}, {constellation[msg_type]}"
        
        # Display message info
        msg_len = len(payload)
        print(f"[{self.msg_count:04d}] Type {msg_type:4d}: {msg_name:40s} ({msg_len:3d} bytes) {details}")
    
    def get_stats(self):
        """Return decoder statistics"""
        return {
            'messages': self.msg_count,
            'errors': self.error_count,
            'buffer_size': len(self.buffer)
        }


