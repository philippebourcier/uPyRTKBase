import network
import time
from machine import Pin

_DEFAULTS = {
    # Auto-construct boards (no explicit PIO SPI)
    "w5100s-evb-pico":  {},
    "w5500-evb-pico":   {},
    "w6100-evb-pico":   {},
    "w5100s-evb-pico2": {},
    "w5500-evb-pico2":  {},
    "w6100-evb-pico2":  {},
}
_AUTO = {
    "w5100s-evb-pico", "w5500-evb-pico", "w6100-evb-pico",
    "w5100s-evb-pico2","w5500-evb-pico2","w6100-evb-pico2",
}
_QSPI   = {"w6300-evb-pico", "w6300-evb-pico2"}

def _pin(x): return x if isinstance(x, Pin) else Pin(x)

def wiznet(board="w5500-evb-pico2", dhcp=True, **kw):
    board = board.strip().lower()
    if board not in _DEFAULTS:
        raise ValueError("Unsupported board: {}".format(board))
    cfg = _DEFAULTS[board].copy()
    cfg.update(kw)
    if board in _AUTO:
        nic = network.WIZNET6K()
    else:
        raise ValueError("Unexpected board mapping")
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
