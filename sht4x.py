import time
from machine import I2C, Pin

# Centipede Base Station pinout
i2c = I2C(0, sda=Pin(12), scl=Pin(13), freq=400_000)

SHT40_ADDR = 0x44
CMD_MEASURE_HIGH = b'\xFD'  # high precision, ~10ms

def read_sht40():
    i2c.writeto(SHT40_ADDR, CMD_MEASURE_HIGH)
    time.sleep_ms(10)
    data = i2c.readfrom(SHT40_ADDR, 6)

    # Bytes 0-1: temp raw, byte 2: CRC
    # Bytes 3-4: hum raw,  byte 5: CRC
    t_raw = (data[0] << 8) | data[1]
    h_raw = (data[3] << 8) | data[4]

    temperature = -45 + 175 * (t_raw / 65535)
    humidity    = -6  + 125 * (h_raw / 65535)
    humidity    = max(0.0, min(100.0, humidity))  # clamp 0–100%

    return temperature, humidity

if __name__ == '__main__':
    while True:
        temp, hum = read_sht40()
        print(f"Temp: {temp:.2f} °C  |  Humidity: {hum:.1f} %")
        time.sleep(1)
