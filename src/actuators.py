import time
import RPi.GPIO as GPIO
from .pinmap import LED_STATUS, LED_WARN, LED_OK, FAN_GPIO, BUZZER

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

for pin in [LED_STATUS, LED_WARN, LED_OK]:
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

# GPIO.setup(FAN_GPIO, GPIO.OUT, initial=GPIO.LOW)
# GPIO.setup(BUZZER, GPIO.OUT, initial=GPIO.LOW)

def set_leds(status=None, warn=None, ok=None):
    if status is not None: GPIO.output(LED_STATUS, GPIO.HIGH if status else GPIO.LOW)
    if warn   is not None: GPIO.output(LED_WARN,   GPIO.HIGH if warn   else GPIO.LOW)
    if ok     is not None: GPIO.output(LED_OK,     GPIO.HIGH if ok     else GPIO.LOW)

# def set_fan(on: bool):
#     GPIO.output(FAN_GPIO, GPIO.HIGH if on else GPIO.LOW)

# def buzz(ms=120):
#     GPIO.output(BUZZER, GPIO.HIGH)
#     time.sleep(ms/1000)
#     GPIO.output(BUZZER, GPIO.LOW)

def cleanup():
    GPIO.output(LED_STATUS, GPIO.LOW)
    GPIO.output(LED_WARN, GPIO.LOW)
    GPIO.output(LED_OK, GPIO.LOW)
    GPIO.cleanup()
