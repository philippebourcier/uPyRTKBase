from machine import Pin, PWM
import time

# ---------------------------------------------------------------------------
# LED1 and LED2 are common-anode RGB LEDs tied to 3V3.
# Active LOW: pin LOW (0) = channel ON, pin HIGH (1) = channel OFF.
# ---------------------------------------------------------------------------

# PWM frequency for LED dimming (Hz)
_PWM_FREQ = 1000

class RGBLed:
    """Single RGB LED driven by three GPIO pins with optional PWM dimming."""

    def __init__(self, r_pin, g_pin, b_pin, active_low=True, pwm=True):
        """
        Args:
            r_pin:      GPIO number for red channel
            g_pin:      GPIO number for green channel
            b_pin:      GPIO number for blue channel
            active_low: True  = common anode, 3V3 (pin LOW = ON)  ← confirmed on this board
                        False = common cathode  (pin HIGH = ON)
            pwm:        Enable PWM dimming (True by default)
        """
        self.active_low = active_low
        self.pwm_enabled = pwm
        self._color = (0, 0, 0)

        if pwm:
            self._r = PWM(Pin(r_pin), freq=_PWM_FREQ, duty_u16=0)
            self._g = PWM(Pin(g_pin), freq=_PWM_FREQ, duty_u16=0)
            self._b = PWM(Pin(b_pin), freq=_PWM_FREQ, duty_u16=0)
        else:
            self._r = Pin(r_pin, Pin.OUT)
            self._g = Pin(g_pin, Pin.OUT)
            self._b = Pin(b_pin, Pin.OUT)

        self.off()

    def _duty(self, value):
        """Convert 0-255 brightness to 16-bit duty cycle, respecting active_low."""
        # value 0 = off, 255 = full brightness
        ratio = value / 255
        if self.active_low:
            ratio = 1.0 - ratio
        return int(ratio * 65535)

    def _write_pin(self, pin, value):
        """Write a 0-255 value to a pin (PWM or digital)."""
        if self.pwm_enabled:
            pin.duty_u16(self._duty(value))
        else:
            # Digital only: treat any non-zero as on
            on = value > 0
            pin.value(0 if (on and self.active_low) else
                      1 if on else
                      1 if self.active_low else 0)

    def set(self, r, g, b):
        """
        Set LED colour.

        Args:
            r, g, b: Brightness 0-255 for each channel
        """
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        self._color = (r, g, b)
        self._write_pin(self._r, r)
        self._write_pin(self._g, g)
        self._write_pin(self._b, b)

    def set_hex(self, hex_color):
        """
        Set colour from a hex string or integer.

        Args:
            hex_color: '#RRGGBB', 'RRGGBB', or 0xRRGGBB integer
        """
        if isinstance(hex_color, str):
            hex_color = hex_color.lstrip('#')
            value = int(hex_color, 16)
        else:
            value = hex_color
        r = (value >> 16) & 0xFF
        g = (value >>  8) & 0xFF
        b =  value        & 0xFF
        self.set(r, g, b)

    def off(self):
        """Turn LED off."""
        self.set(0, 0, 0)

    def on(self, r=255, g=255, b=255):
        """Turn LED on (white by default)."""
        self.set(r, g, b)

    @property
    def color(self):
        """Current colour as (r, g, b) tuple."""
        return self._color

    def deinit(self):
        """Release PWM resources."""
        self.off()
        if self.pwm_enabled:
            self._r.deinit()
            self._g.deinit()
            self._b.deinit()


# ---------------------------------------------------------------------------
# Preset colours
# ---------------------------------------------------------------------------
class Color:
    OFF     = (  0,   0,   0)
    WHITE   = (255, 255, 255)
    RED     = (255,   0,   0)
    GREEN   = (  0, 255,   0)
    BLUE    = (  0,   0, 255)
    YELLOW  = (255, 255,   0)
    CYAN    = (  0, 255, 255)
    MAGENTA = (255,   0, 255)
    ORANGE  = (255, 128,   0)
    PURPLE  = (128,   0, 255)
    PINK    = (255,  20, 147)
    DIM_RED = ( 32,   0,   0)
    DIM_GRN = (  0,  32,   0)


# ---------------------------------------------------------------------------
# Board LED manager
# ---------------------------------------------------------------------------
class LEDManager:
    """
    Manages the two RGB LEDs on the W55RP20 board.

    Board pinout:
        LED1: R=GPIO28, G=GPIO27, B=GPIO29
        LED2: R=GPIO25, G=GPIO24, B=GPIO23
    """

    def __init__(self, active_low=True, pwm=True):
        """
        Args:
            active_low: True (default) — confirmed common-anode 3V3 on this board
            pwm:        Enable PWM brightness control
        """
        self.led1 = RGBLed(r_pin=28, g_pin=27, b_pin=29, active_low=active_low, pwm=pwm)
        self.led2 = RGBLed(r_pin=25, g_pin=24, b_pin=23, active_low=active_low, pwm=pwm)

    def both(self, r, g, b):
        """Set both LEDs to the same colour."""
        self.led1.set(r, g, b)
        self.led2.set(r, g, b)

    def off(self):
        """Turn both LEDs off."""
        self.led1.off()
        self.led2.off()

    def blink(self, led, color, times=3, on_ms=200, off_ms=200):
        """
        Blink a single LED.

        Args:
            led:    self.led1 or self.led2
            color:  (r, g, b) tuple or Color constant
            times:  Number of blinks
            on_ms:  On duration in ms
            off_ms: Off duration in ms
        """
        for _ in range(times):
            led.set(*color)
            time.sleep_ms(on_ms)
            led.off()
            time.sleep_ms(off_ms)

    def blink_both(self, color, times=3, on_ms=200, off_ms=200):
        """Blink both LEDs together."""
        for _ in range(times):
            self.both(*color)
            time.sleep_ms(on_ms)
            self.off()
            time.sleep_ms(off_ms)

    def alternate(self, color1, color2, times=3, delay_ms=300):
        """
        Alternate LED1 and LED2 between two colours.

        Args:
            color1: Colour for LED1 (r, g, b)
            color2: Colour for LED2 (r, g, b)
            times:  Number of alternations
            delay_ms: Delay between alternations in ms
        """
        for _ in range(times):
            self.led1.set(*color1)
            self.led2.set(*color2)
            time.sleep_ms(delay_ms)
            self.led1.set(*color2)
            self.led2.set(*color1)
            time.sleep_ms(delay_ms)

    def status(self, ok):
        """
        Convenience: green = ok, red = error on both LEDs.

        Args:
            ok: True for green, False for red
        """
        if ok:
            self.both(*Color.GREEN)
        else:
            self.both(*Color.RED)

    def deinit(self):
        """Release all PWM resources."""
        self.led1.deinit()
        self.led2.deinit()


# ---------------------------------------------------------------------------
# Usage / identification helper
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    # Common-anode 3V3 confirmed: active_low=True, pwm=True
    leds = LEDManager()

    print("LED1 red...")
    leds.led1.set(*Color.RED)
    time.sleep(1)

    print("LED2 green...")
    leds.led2.set(*Color.GREEN)
    time.sleep(1)

    print("Both blue...")
    leds.both(*Color.BLUE)
    time.sleep(1)

    print("Blink yellow x3...")
    leds.blink_both(Color.YELLOW, times=3)

    print("Alternate red/blue x3...")
    leds.alternate(Color.RED, Color.BLUE, times=3)

    print("Status OK...")
    leds.status(ok=True)
    time.sleep(1)

    print("Status ERROR...")
    leds.status(ok=False)
    time.sleep(1)

    leds.off()
    leds.deinit()
    print("Done.")