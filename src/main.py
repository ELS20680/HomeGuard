import time
import dht11
import RPi.GPIO as GPIO
from RPLCD.i2c import CharLCD
from datetime import datetime
from paho.mqtt import client as mqtt
from picamera2 import Picamera2
from pathlib import Path
import ssl

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
dht_sensor = dht11.DHT11(pin=4)

# Pin setup
DHT_PIN = 4
TRIG = 25
ECHO = 26
BUZZER = 24
RED = 16
YELLOW = 20
GREEN = 21

GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)
GPIO.setup(BUZZER, GPIO.OUT)
GPIO.setup(RED, GPIO.OUT)
GPIO.setup(YELLOW, GPIO.OUT)
GPIO.setup(GREEN, GPIO.OUT)

GPIO.output(RED, False)
GPIO.output(YELLOW, False)
GPIO.output(GREEN, False)

lcd = CharLCD('PCF8574', 0x27)
camera = Picamera2()

# MQTT setup
BROKER = "io.adafruit.com"
PORT = 1883
USERNAME = "elias_larhdaf"
KEY = "aio_fYTW56heXeedbs2YMdmhNsKP3ih2"
TOPICS = {
    "temp": f"{USERNAME}/feeds/temperature",
    "hum": f"{USERNAME}/feeds/humidity",
    "dist": f"{USERNAME}/feeds/distance",
    "led": f"{USERNAME}/feeds/led-status",
    "cam": f"{USERNAME}/feeds/camera-image",
    "party": f"{USERNAME}/feeds/party-mode"
}

client = mqtt.Client()
client.username_pw_set(USERNAME, KEY)
client.connect(BROKER, PORT, 60)
client.loop_start()

def publish(feed, value):
    try:
        client.publish(TOPICS[feed], str(value))
    except:
        pass

def measure_distance():
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    start = time.time()
    stop = time.time()

    while GPIO.input(ECHO) == 0:
        start = time.time()

    while GPIO.input(ECHO) == 1:
        stop = time.time()

    elapsed = stop - start
    distance = (elapsed * 34300) / 2
    return round(distance, 1)

def take_photo():
    Path("captured_images").mkdir(exist_ok=True)
    filename = f"captured_images/image_{datetime.now().strftime('%H%M%S')}.jpg"
    camera.capture_file(filename)
    lcd.clear()
    lcd.write_string("Image Taken")
    publish("cam", f"Image captured at {datetime.now().strftime('%H:%M:%S')}")

def buzz_alert():
    GPIO.output(BUZZER, True)
    time.sleep(0.1)
    GPIO.output(BUZZER, False)

def party_mode():
    lcd.clear()
    lcd.write_string("Party Mode Active")

    for _ in range(3):
        GPIO.output(RED, True)
        time.sleep(0.3)
        GPIO.output(RED, False)
        GPIO.output(YELLOW, True)
        time.sleep(0.3)
        GPIO.output(YELLOW, False)
        GPIO.output(GREEN, True)
        time.sleep(0.3)
        GPIO.output(GREEN, False)

    lcd.clear()
    lcd.write_string("Party Mode Stopped")

def on_message(client, userdata, msg):
    payload = msg.payload.decode().lower()

    if msg.topic == TOPICS["led"]:
        if payload == "on":
            GPIO.output(RED, True)
            GPIO.output(YELLOW, True)
            GPIO.output(GREEN, True)
            lcd.clear()
            lcd.write_string("LEDs On")
        else:
            GPIO.output(RED, False)
            GPIO.output(YELLOW, False)
            GPIO.output(GREEN, False)
            lcd.clear()
            lcd.write_string("LEDs Off")

    elif msg.topic == TOPICS["party"]:
        if payload == "on":
            lcd.clear()
            lcd.write_string("Party Mode Active")
            party_mode()
        else:
            lcd.clear()
            lcd.write_string("Party Mode Off")

    elif msg.topic == TOPICS["cam"]:
        if payload == "capture":
            take_photo()
            lcd.clear()
            lcd.write_string("Image Taken")


client.on_message = on_message
for topic in TOPICS.values():
    client.subscribe(topic)
    print(f"Subscribed to: {topic}")

try:
    while True:
        result = dht_sensor.read()
        if result.is_valid():
            temperature = result.temperature
            humidity = result.humidity
        else:
            temperature = None
            humidity = None

        distance = measure_distance()

        if distance < 10:
            buzz_alert()
            GPIO.output(RED, True)
            GPIO.output(GREEN, False)
            lcd.clear()
            lcd.write_string("Too Close!")
        else:
            GPIO.output(RED, False)
            GPIO.output(GREEN, True)
            lcd.clear()
            lcd.write_string(f"Dist:{distance:.1f}cm")

        if temperature is not None and humidity is not None:
            publish("temp", temperature)
            publish("hum", humidity)

        publish("dist", distance)
        time.sleep(2)

except KeyboardInterrupt:
    pass

finally:
    lcd.clear()
    GPIO.cleanup()
    client.loop_stop()
    client.disconnect()
