# uPyRTKBase MCU-based RTK Base Station Firmware

MicroPython firmware for the **W5500-EVB-Pico2** board driving a **UM980** RTK GNSS receiver.  
Streams RTCM3 corrections to a Centipede NTRIP caster, monitors antenna health, and reports telemetry.

---

## Hardware

| Component | Part |
|-----------|------|
| MCU + Ethernet | WIZnet W5500-EVB-Pico2 (RP2350 + W5500) |
| GNSS receiver | Unicore UM980 (tri-band RTK) |
| GNSS antenna | Tri-band RTK antenna |
| IMU | ST LSM6DSV16X (vibration + tilt) |
| Environment | Sensirion SHT40 (internal temperature + humidity) |

### Pinout

| GPIO | Signal | Description |
|------|--------|-------------|
| 0 | RTK_TX0 | UART0 TX → UM980 COM1 RX (control) |
| 1 | RTK_RX0 | UART0 RX ← UM980 COM1 TX (control) |
| 8 | RTK_RX1 | UART1 TX → UM980 COM2 RX (RTCM data) |
| 9 | RTK_TX1 | UART1 RX ← UM980 COM2 TX (RTCM data) |
| 12 | I2C_SDA | I2C bus — LSM6DSV16X + SHT40 |
| 13 | I2C_SCL | I2C bus — LSM6DSV16X + SHT40 |
| 16 | W5500_MISO | SPI — Ethernet |
| 17 | W5500_CS | SPI — Ethernet |
| 18 | W5500_SCK | SPI — Ethernet |
| 19 | W5500_MOSI | SPI — Ethernet |
| 20 | W5500_RST | Ethernet reset |
| 21 | W5500_INT | Ethernet interrupt |
| 23 | LED2_B | RGB LED2 blue |
| 24 | LED2_G | RGB LED2 green |
| 25 | LED2_R | RGB LED2 red |
| 27 | LED1_G | RGB LED1 green |
| 28 | LED1_R | RGB LED1 red |
| 29 | LED1_B | RGB LED1 blue |

Both LEDs are **common-anode to 3V3** (active LOW: 0 = ON, 1 = OFF).

---

## File Structure

```
├── base.py               # Main to be
├── config_manager.py     # Local config, remote config download, .env loader
├── um980_config.py       # UM980 driver and base station configuration
├── network_init.py       # W5500 Ethernet initialization
├── ntrip_caster.py       # NTRIP caster client (runs on core 1)
├── rgb_led_drv.py        # RGB LED driver (PWM, common-anode)
├── lsm6dsv.py            # LSM6DSV16X IMU driver
├── sht4x.py              # SHT40 temperature/humidity reader
├── rtcm_decoder.py       # RTCM3 frame decoder
├── rtcm_params.py        # RTCM message list and intervals
├── wdt.py                # Watchdog timer (shared feed_wdt())
├── config.json           # Persisted local configuration (auto-generated)
└── .env                  # Cloud URLs (see below)
```

---

## Configuration

### `.env`

Secrets and environment-specific URLs. Loaded at boot before the remote config fetch.  
Create this file on the device flash:

```env
TELEMETRY_URL=http(s)://your_webserver/telemetry
CONFIG_URL=http(s)://your_webserver/config.json
```

### Remote config (`config.json`)

Served by the config server at `CONFIG_URL`. The device fetches this at every boot and merges it into its local config. Flash is only written if the values have changed.

Full schema:

```json
{
  "dhcp":             true,
  "ip":               null,
  "subnet":           null,
  "gateway":          null,
  "dns":              null,
  "base_mode":        "time",
  "base_duration":    60,
  "base_pdop":        1,
  "base_lat":         0,
  "base_lon":         0,
  "base_alt":         0,
  "signal_group":     2,
  "sbas_enabled":     true,
  "rtcm_interval":    1,
  "ntrip_server":     "crtk.net",
  "ntrip_port":       2101,
  "ntrip_mountpoint": null,
  "ntrip_user":       null,
  "ntrip_password":   null
}
```

| Key | Type | Description |
|-----|------|-------------|
| `dhcp` | bool | Use DHCP if true, static IP if false |
| `ip` / `subnet` / `gateway` / `dns` | string | Static IP settings (required if `dhcp` is false) |
| `base_mode` | `"time"` \| `"fixed"` | Self-survey or fixed known position |
| `base_duration` | int | Self-survey duration in seconds |
| `base_pdop` | float | Maximum PDOP for self-survey |
| `base_lat` / `base_lon` | float | Fixed position — lat/lon in decimal degrees (−90…90 / −180…180) |
| `base_alt` | float | Fixed position — altitude in metres (−30000…30000) |
| `signal_group` | int | UM980 signal group (2 = GPS+GLONASS+Galileo+BDS) |
| `sbas_enabled` | bool | Enable SBAS augmentation |
| `rtcm_interval` | int | RTCM message output interval in seconds |
| `ntrip_server` | string | NTRIP caster hostname |
| `ntrip_port` | int | NTRIP caster port |
| `ntrip_mountpoint` | string | NTRIP mountpoint name |
| `ntrip_user` | string | NTRIP username |
| `ntrip_password` | string | NTRIP password |

---

## Boot Sequence

```
1. Load local config        → determines DHCP vs static
2. Initialize Ethernet      → W5500 via SPI
3. Download remote config   → merges into local config, saves only if changed
4. Initialize UM980         → detect, check config, reconfigure if needed
5. Initialize sensors       → LSM6DSV16X (I2C 0x6B) + SHT40 (I2C 0x44)
6. Start NTRIP thread       → core 1, streams COM2 RTCM to caster
7. Main loop                → see below
```

---

## Main Loop

| Interval | Action |
|----------|--------|
| Every iteration (~1–2 s) | IMU check: vibration + tilt → update LED2 |
| Every 30 s | AGC check on L1/L2/L5 → update LED2 |
| Every 5 min | Read SHT40, send telemetry, check NTRIP → update LED1 |

---

## LED Status Codes

### LED1 — Internet / NTRIP

| Color | Meaning |
|-------|---------|
| 🟠 Orange | Booting / NTRIP reconnecting |
| 🔵 Cyan | Ethernet up, downloading config |
| 🟢 Green | Config downloaded, NTRIP connected |
| 🟡 Yellow | Ethernet up, remote config failed (using local) |
| 🔴 Red | Ethernet init failed / NTRIP reconnect failed |

### LED2 — GNSS / Antenna Health

| Color | Meaning |
|-------|---------|
| 🟠 Orange | Booting |
| 🟡 Yellow | UM980 initializing |
| 🔵 Cyan | UM980 up, checks pending |
| 🟢 Green | AGC good, level, no vibration |
| 🔵 Blue   | AGC degraded on one or more bands |
| 🟣 Pink | Vibrating or tilted (IMU alarm) |
| 🔴 Red | UM980 init failed / AGC check error |

---

## Telemetry

Every 5 minutes the device POSTs a JSON payload to `TELEMETRY_URL`.  
The call is fire-and-forget — no retry, errors silently ignored.

```json
{
  "hw":          "<hardware_unique_id>",
  "rms_max":     0.012,
  "pitch":       0.43,
  "roll":        -0.21,
  "temperature": 23.4,
  "humidity":    61.2,
  "agc_l1":      4,
  "agc_l2":      7,
  "agc_l5":      3
}
```

| Field | Source | Description |
|-------|--------|-------------|
| `hw` | RP2350 `unique_id()` | Device hardware ID |
| `rms_max` | LSM6DSV16X | Max RMS angular rate across X/Y/Z axes (dps) |
| `pitch` / `roll` | LSM6DSV16X | Antenna tilt in degrees (`null` when vibrating) |
| `temperature` | SHT40 | Ambient temperature in °C |
| `humidity` | SHT40 | Relative humidity in % (0–100) |
| `agc_l1` / `agc_l2` / `agc_l5` | UM980 | AGC values per band (lower = better; −1 = unknown) |

---

## Watchdog

The WDT is initialized at boot with an **8-second timeout**.  
`feed_wdt()` is called inside all UART polling loops and at every main loop iteration.  
The 10-second post-reboot wait after `CONFIG SIGNALGROUP` is fed in 500 ms increments.

To disable the WDT for debugging, set at the top of `wdt.py`:

```python
DISABLE_WDT = True
```

---

## UM980 Base Station Modes

### Self-survey (default)

The receiver averages its position over `base_duration` seconds before starting RTCM output.  
Configured automatically via `full_configuration()` if the device is not already in base mode.

### Fixed known position

Call `set_base_coordinates()` after `start_sensor()` when the antenna position is already known:

```python
um980.set_base_coordinates(
    latitude   = 42.50881200,
    longitude  =  1.53037157,
    altitude   = 1097.0,
    station_id = 1        # optional, 0–4095, embedded in RTCM 1005/1006
)
um980.save_config()
```

Coordinate limits: lat −90…90, lon −180…180, alt −30000…30000 m (signed decimal degrees).  
Values outside these ranges are ignored.

---

## Dependencies

All pure MicroPython — no external packages required.

| Module | Purpose |
|--------|---------|
| `machine.UART` | UM980 serial communication |
| `machine.I2C` | LSM6DSV16X + SHT40 |
| `machine.PWM` | LED dimming |
| `machine.WDT` | Watchdog timer |
| `ujson` | JSON encode/decode |
| `socket` | Raw TCP for HTTP (urequests not compatible with W5500 driver) |
| `_thread` | NTRIP client on core 1 |
