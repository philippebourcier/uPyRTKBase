
# Set to True to disable the watchdog timer (for testing/debugging only)
DISABLE_WDT = True

wdt = None

if DISABLE_WDT:
    print("WARNING: WDT disabled (testing mode)")
    wdt = None
else:
    from machine import WDT
    wdt = WDT(timeout=8000)

def feed_wdt():
    """Feed the watchdog timer if enabled"""
    if wdt is not None:
        wdt.feed()
