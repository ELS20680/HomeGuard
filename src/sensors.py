import time
import board
import adafruit_dht
import RPi.GPIO as GPIO
from datetime import datetime, timezone
from .pinmap import PIR_GPIO, DHT_GPIO

class DHT11Reader:
    def __init__(self, gpio=DHT_GPIO):
        if gpio == 4:
            self._dht = adafruit_dht.DHT11(board.D4)
        else:
            self._dht = adafruit_dht.DHT11(getattr(board, f"D{gpio}"))

    def read(self):
        try:
            t = self._dht.temperature
            h = self._dht.humidity
            if t is None or h is None:
                return None, None
            return float(t), float(h)
        except Exception:
            return None, None

class PIRWatcher:
    def __init__(self, gpio=PIR_GPIO, debounce_ms=1500):
        self.gpio = gpio
        self.debounce_ms = debounce_ms
        self._last = 0
        GPIO.setup(self.gpio, GPIO.IN)

    def motion(self):
        now = time.time() * 1000
        if GPIO.input(self.gpio) == GPIO.HIGH and (now - self._last) > self.debounce_ms:
            self._last = now
            return True
        return False

def iso_now():
    return datetime.now(timezone.utc).isoformat()
