import time
import paho.mqtt.client as mqtt

class AioClient:
    def __init__(self, username, key, host, port, feeds, on_control_cb):
        self.u = username; self.k = key
        self.host = host; self.port = port
        self.feeds = feeds
        self._on_control_cb = on_control_cb
        self._cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                client_id=f"homeguardian-{int(time.time())}")
        self._cli.username_pw_set(self.u, self.k)
        self._cli.on_connect = self._on_connect
        self._cli.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc, props=None):
        for k in ["ctrl_light","ctrl_fan","ctrl_mode"]:
            client.subscribe(f"{self.u}/feeds/{self.feeds[k]}")
        client.publish(f"{self.u}/feeds/{self.feeds['heartbeat']}", "online", retain=True)

    def _on_message(self, client, userdata, msg):
        try:
            self._on_control_cb(msg.topic, msg.payload.decode().strip())
        except Exception:
            pass

    def start(self):
        self._cli.connect(self.host, self.port, 60)
        self._cli.loop_start()

    def stop(self):
        self._cli.loop_stop()

    def pub(self, feed_key, val, retain=False):
        self._cli.publish(f"{self.u}/feeds/{self.feeds[feed_key]}", str(val), retain=retain)
