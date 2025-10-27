import network
import time
try:
    from machine import Pin, WIZNET_PIO_SPI
except ImportError:
    WIZNET_PIO_SPI = None
    Pin = None

_DEFAULTS = {
    # Auto-construct boards (no explicit PIO SPI)
    "w5100s-evb-pico":  {},
    "w5500-evb-pico":   {},
    "w6100-evb-pico":   {},
    "w5100s-evb-pico2": {},
    "w5500-evb-pico2":  {},
    "w6100-evb-pico2":  {},

    # W55RP20 — single SPI (PIO SPI)
    "w55rp20-evb-pico": {"baudrate": 31250000, "sck": 21, "cs": 20, "mosi": 23, "miso": 22, "reset": 25},

    # W6300 — QSPI QUAD(io0..io3)
    "w6300-evb-pico":  {"baudrate": 31250000, "sck": 17, "cs": 16, "io0": 18, "io1": 19, "io2": 20, "io3": 21, "reset": 22},
    "w6300-evb-pico2": {"baudrate": 31250000, "sck": 17, "cs": 16, "io0": 18, "io1": 19, "io2": 20, "io3": 21, "reset": 22},
}
_AUTO = {
    "w5100s-evb-pico", "w5500-evb-pico", "w6100-evb-pico",
    "w5100s-evb-pico2","w5500-evb-pico2","w6100-evb-pico2",
}
_SINGLE = {"w55rp20-evb-pico"}   # PIO single-SPI
_QSPI   = {"w6300-evb-pico", "w6300-evb-pico2"}

def _pin(x): return x if isinstance(x, Pin) else Pin(x)

def wiznet(board, *, dhcp=True, spi=None, cs=None, reset=None, **kw):
    board = board.strip().lower()
    if board not in _DEFAULTS:
        raise ValueError("Unsupported board: {}".format(board))
    cfg = _DEFAULTS[board].copy()
    cfg.update(kw)
    
    # Manual override path: if spi is provided, use it directly
    if spi is not None:
        if cs is None or reset is None:
            raise ValueError("When passing custom spi, also pass cs and reset")
        nic = network.WIZNET6K(spi, cs, reset)
    else:
        if board in _AUTO:
            nic = network.WIZNET6K()

        elif board in _SINGLE:
            if WIZNET_PIO_SPI is None or Pin is None:
                raise RuntimeError("WIZNET_PIO_SPI/Pin not available on this port")
            required = ["sck", "cs", "mosi", "miso", "reset"]
            missing = [k for k in required if k not in cfg]
            if missing:
                raise ValueError("Missing pins for W55RP20 single-SPI: " + ", ".join(missing))
            spi = WIZNET_PIO_SPI(
                baudrate=cfg.get("baudrate", 31250000),
                sck=_pin(cfg["sck"]), cs=_pin(cfg["cs"]),
                mosi=_pin(cfg["mosi"]), miso=_pin(cfg["miso"]),
            )
            nic = network.WIZNET6K(spi, _pin(cfg["cs"]), _pin(cfg["reset"]))

        elif board in _QSPI:
            if WIZNET_PIO_SPI is None or Pin is None:
                raise RuntimeError("WIZNET_PIO_SPI/Pin not available on this port")
            for k in ["sck","cs","io0","io1","io2","io3"]:
                if k not in cfg: raise ValueError("Missing pin '{}' for W6300 QSPI".format(k))
            spi = WIZNET_PIO_SPI(
                baudrate=cfg.get("baudrate", 31250000),
                sck=_pin(cfg["sck"]), cs=_pin(cfg["cs"]),
                io0=_pin(cfg["io0"]), io1=_pin(cfg["io1"]),
                io2=_pin(cfg["io2"]), io3=_pin(cfg["io3"]),
            )
            nic = network.WIZNET6K(spi, _pin(cfg["cs"]), _pin(cfg.get("reset", cfg["cs"])))

        else:
            raise ValueError("Unexpected board mapping")

    # Bring up (if supported)
    try: nic.active(True)
    except AttributeError: pass

    if dhcp:
        try: nic.ifconfig("dhcp")
        except Exception: pass
    else:
        ip = cfg.get("ip"); sn = cfg.get("sn"); gw = cfg.get("gw"); dns = cfg.get("dns", gw or "8.8.8.8")
        if not (ip and sn and gw): raise ValueError("Static mode requires ip/sn/gw")
        nic.ifconfig((ip, sn, gw, dns))
    while not nic.isconnected():
        print("Waiting for the network to connect...")
        time.sleep(1)

    print("MAC Address:", ":".join("%02x" % b for b in nic.config("mac")))
    print("IP Address:", nic.ifconfig())
    return nic

