import time
import math
from machine import I2C, Pin
from micropython import const

# ---------------------------------------------------------------------------
# Register map (LSM6DSV16X, from ST UM2554 + lsm6dsv16x_reg.h)
# ---------------------------------------------------------------------------
_WHO_AM_I   = const(0x0F)   # expected: 0x70
_CTRL1      = const(0x10)   # XL ODR / op-mode
_CTRL2      = const(0x11)   # GY ODR / op-mode
_CTRL3      = const(0x12)   # SW_RESET, BDU, IF_INC
_CTRL6      = const(0x15)   # GY full scale
_CTRL8      = const(0x17)   # XL full scale
_STATUS_REG = const(0x1E)   # bit0=XLDA, bit1=GDA
_OUTX_L_G   = const(0x22)   # GY  X/Y/Z (6 bytes)
_OUTX_L_A   = const(0x28)   # XL  X/Y/Z (6 bytes)

_DEVICE_ID  = const(0x70)

# CTRL3 bits
_BDU        = const(1 << 6)  # block data update
_IF_INC     = const(1 << 2)  # auto-increment address

# ODR codes (bits [3:0] of CTRL1 / CTRL2)
_ODR_OFF    = const(0x00)
_ODR_15HZ   = const(0x03)
_ODR_30HZ   = const(0x04)
_ODR_60HZ   = const(0x05)
_ODR_120HZ  = const(0x06)

# XL full scale: ±2g → 0x00 → 0.061 mg/LSB
_FS_XL_2G   = const(0x00)
_SENS_XL    = 0.061           # mg / LSB  → divide by 1000 for g

# GY full scale: ±250 dps → 0x01 → 8.750 mdps/LSB
_FS_GY_250  = const(0x01)
_SENS_GY    = 8.750           # mdps / LSB → divide by 1000 for dps


# ---------------------------------------------------------------------------
def _s16(raw):
    """Sign-extend a 16-bit unsigned int."""
    return raw - 0x10000 if raw & 0x8000 else raw


# ---------------------------------------------------------------------------
class LSM6DSV16X:
    """
    Driver for the ST LSM6DSV16X IMU.
    Implements two features for an RTK base station:
      • Tilt detection  — accelerometer, gravity-vector decomposition
      • Vibration alarm — gyroscope RMS over a sliding time window

    Both sensors run continuously (no sleep / inactivity logic).
    """

    def __init__(self, i2c, address=0x6A,
                 tilt_threshold_deg=2.0,
                 vibration_threshold_dps=0.5,
                 vibration_window_ms=1000):
        """
        Parameters
        ----------
        i2c                   : machine.I2C instance
        address               : I2C address (0x6A SDO=GND, 0x6B SDO=VCC)
        tilt_threshold_deg    : max allowed tilt angle for is_level()
        vibration_threshold_dps : RMS angular rate above which vibration is flagged
        vibration_window_ms   : integration window for RMS computation
        """
        self.bus      = i2c
        self.addr     = address
        self.tilt_thr = tilt_threshold_deg
        self.vib_thr  = vibration_threshold_dps
        self.vib_win  = vibration_window_ms

        # Verify device identity
        who = self._read(_WHO_AM_I, 1)[0]
        if who != _DEVICE_ID:
            raise OSError(f"LSM6DSV16X not found (WHO_AM_I=0x{who:02X}, expected 0x{_DEVICE_ID:02X})")

        # Software reset — clears all registers
        self._write(_CTRL3, 0x01)
        time.sleep_ms(15)

        # BDU + IF_INC: prevents reading stale half-words, enables burst reads
        self._write(_CTRL3, _BDU | _IF_INC)

        # XL full scale ±2g
        self._write(_CTRL8, _FS_XL_2G)

        # GY full scale ±250 dps
        self._write(_CTRL6, _FS_GY_250)

        # XL at 30 Hz — tilt is quasi-static, no need for more
        # GY at 120 Hz — enough bandwidth to catch pole sway (<5 Hz)
        self._write(_CTRL1, _ODR_30HZ)
        self._write(_CTRL2, _ODR_120HZ)

    # ------------------------------------------------------------------ I2C helpers
    def _read(self, reg, n):
        return self.bus.readfrom_mem(self.addr, reg, n)

    def _write(self, reg, val):
        self.bus.writeto_mem(self.addr, reg, bytearray([val]))

    def _wait_drdy(self, mask, timeout_ms=100):
        """Poll STATUS_REG until the requested DRDY bit is set."""
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while not (self._read(_STATUS_REG, 1)[0] & mask):
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise OSError("LSM6DSV16X DRDY timeout")

    def _read_raw_xl(self):
        """Return raw signed XL (ax, ay, az) in LSB."""
        d = self._read(_OUTX_L_A, 6)
        return (
            _s16((d[1] << 8) | d[0]),
            _s16((d[3] << 8) | d[2]),
            _s16((d[5] << 8) | d[4]),
        )

    def _read_raw_gy(self):
        """Return raw signed GY (gx, gy, gz) in LSB."""
        d = self._read(_OUTX_L_G, 6)
        return (
            _s16((d[1] << 8) | d[0]),
            _s16((d[3] << 8) | d[2]),
            _s16((d[5] << 8) | d[4]),
        )

    # ======================================================== Feature 1 : Tilt
    def get_tilt(self, n_avg=32):
        """
        Compute pitch and roll by averaging n_avg accelerometer samples,
        then decomposing the gravity vector.

        Only valid when the antenna is mechanically quiet (no vibration).
        Check is_vibrating() first if needed.

        Returns
        -------
        (pitch_deg, roll_deg)
        """
        sx = sy = sz = 0
        for _ in range(n_avg):
            self._wait_drdy(0x01)           # XLDA bit
            ax, ay, az = self._read_raw_xl()
            sx += ax
            sy += ay
            sz += az

        # Convert to mg, then compute angles
        ax_mg = (sx / n_avg) * _SENS_XL
        ay_mg = (sy / n_avg) * _SENS_XL
        az_mg = (sz / n_avg) * _SENS_XL

        pitch = math.degrees(math.atan2(ax_mg,
                             math.sqrt(ay_mg * ay_mg + az_mg * az_mg)))
        roll  = math.degrees(math.atan2(ay_mg,
                             math.sqrt(ax_mg * ax_mg + az_mg * az_mg)))
        return pitch, roll

    def is_level(self, n_avg=32):
        """
        Return True if both pitch and roll are within ±tilt_threshold_deg.
        """
        pitch, roll = self.get_tilt(n_avg)
        return abs(pitch) <= self.tilt_thr and abs(roll) <= self.tilt_thr

    # ==================================================== Feature 2 : Vibration
    def get_vibration_rms(self):
        """
        Collect gyroscope samples for vibration_window_ms milliseconds and
        compute the RMS angular rate on each axis.

        A pole swaying in wind produces low-frequency (<5 Hz) bursts;
        the RMS over a 1-second window catches both slow sway and
        high-frequency mechanical vibration.

        Returns
        -------
        (rms_x_dps, rms_y_dps, rms_z_dps)
        """
        sq_x = sq_y = sq_z = 0.0
        n = 0
        t_end = time.ticks_add(time.ticks_ms(), self.vib_win)

        while time.ticks_diff(t_end, time.ticks_ms()) > 0:
            self._wait_drdy(0x02)           # GDA bit
            gx, gy, gz = self._read_raw_gy()
            gx_dps = gx * _SENS_GY / 1000.0
            gy_dps = gy * _SENS_GY / 1000.0
            gz_dps = gz * _SENS_GY / 1000.0
            sq_x += gx_dps * gx_dps
            sq_y += gy_dps * gy_dps
            sq_z += gz_dps * gz_dps
            n += 1

        if n == 0:
            return 0.0, 0.0, 0.0

        return (
            math.sqrt(sq_x / n),
            math.sqrt(sq_y / n),
            math.sqrt(sq_z / n),
        )

    def is_vibrating(self):
        """
        Return True if any axis RMS exceeds vibration_threshold_dps.
        """
        rms_x, rms_y, rms_z = self.get_vibration_rms()
        return max(rms_x, rms_y, rms_z) > self.vib_thr

    # ================================================= Combined health check
    def check(self):
        """
        Full antenna health check.

        Vibration is always evaluated first — computing tilt during vibration
        is meaningless because the gravity vector gets polluted by dynamics.
        Tilt is only computed when the pole is calm.

        Returns
        -------
        dict with keys:
          vibrating   : bool
          rms_max_dps : float
          level       : bool | None  (None when vibrating)
          pitch_deg   : float | None
          roll_deg    : float | None
        """
        rms_x, rms_y, rms_z = self.get_vibration_rms()
        rms_max = max(rms_x, rms_y, rms_z)
        vibrating = rms_max > self.vib_thr

        if not vibrating:
            pitch, roll = self.get_tilt()
            level = abs(pitch) <= self.tilt_thr and abs(roll) <= self.tilt_thr
        else:
            pitch = roll = level = None

        return {
            "vibrating":   vibrating,
            "rms_max_dps": rms_max,
            "level":       level,
            "pitch_deg":   pitch,
            "roll_deg":    roll,
        }


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    i2c = I2C(0, sda=Pin(12), scl=Pin(13), freq=400_000)
    imu = LSM6DSV16X(
        i2c,
        address=0x6b,
        tilt_threshold_deg=2.0,
        vibration_threshold_dps=0.5,
        vibration_window_ms=1000,
    )
    print("LSM6DSV16X ready")

    while True:
        status = imu.check()

        if status["vibrating"]:
            print(f"⚠ VIBRATION  rms={status['rms_max_dps']:.3f} dps")
        elif not status["level"]:
            print(f"⚠ TILT       pitch={status['pitch_deg']:.2f}°  roll={status['roll_deg']:.2f}°")
        else:
            print(f"✓ OK         pitch={status['pitch_deg']:.2f}°  roll={status['roll_deg']:.2f}°  rms={status['rms_max_dps']:.3f} dps")

        time.sleep_ms(100)
