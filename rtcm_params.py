RTCM_MESSAGES = [
    # --- Reference Station Information ---
    ('RTCM1005', 30),  # Station coordinates (ARP, no height)
    ('RTCM1006', 30),  # Station coordinates (ARP + height)
    ('RTCM1033', 10),  # Receiver and antenna descriptor (firmware, serial number)

    # --- GNSS Ephemerides (broadcast orbit parameters) ---
    ('RTCM1019', 10),  # GPS ephemerides
    ('RTCM1020', 10),  # GLONASS ephemerides
    ('RTCM1042', 10),  # BeiDou ephemerides
    ('RTCM1044', 10),  # QZSS ephemerides
    ('RTCM1045', 10),  # Galileo I/NAV ephemerides
    ('RTCM1046', 10),  # Galileo F/NAV ephemerides

    # --- Observation Data (MSM: Multiple Signal Messages) ---
    ('RTCM1077', 1),   # GPS MSM7 (full code + phase + SNR)
    ('RTCM1087', 1),   # GLONASS MSM7
    ('RTCM1097', 1),   # Galileo MSM7
    ('RTCM1107', 1),   # SBAS MSM7 (optional, low impact)
    ('RTCM1117', 1),   # QZSS MSM7
    ('RTCM1127', 1),   # BeiDou MSM7
]
"""
    # --- State Space Representation (SSR) Corrections ---
    # GPS SSR (1057–1062)
    ('RTCM1057', 1),   # GPS SSR Orbit Corrections
    ('RTCM1058', 1),   # GPS SSR Clock Corrections
    ('RTCM1059', 1),   # GPS SSR Code Biases
    ('RTCM1060', 1),   # GPS SSR Combined Orbit + Clock
    ('RTCM1061', 5),   # GPS SSR User Range Accuracy (URA) / consistency
    ('RTCM1062', 1),   # GPS SSR High-Rate Clock Corrections (optional)

    # GLONASS SSR (1063–1066)
    ('RTCM1063', 1),   # GLONASS SSR Orbit Corrections
    ('RTCM1064', 1),   # GLONASS SSR Clock Corrections
    ('RTCM1065', 1),   # GLONASS SSR Code Biases
    ('RTCM1066', 1),   # GLONASS SSR Combined Orbit + Clock

    # Galileo SSR (1240–1243) — used by Galileo HAS converters
    ('RTCM1240', 1),   # Galileo SSR Orbit Corrections
    ('RTCM1241', 1),   # Galileo SSR Clock Corrections
    ('RTCM1242', 1),   # Galileo SSR Code Biases
    ('RTCM1243', 1),   # Galileo SSR Combined Orbit + Clock
"""


