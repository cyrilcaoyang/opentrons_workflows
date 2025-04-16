import json
import secrets
import threading
from time import time
from queue import Queue, Empty

import paho.mqtt.client as paho

class color_sensor():
    def __init__(self):
        # Configuration
        self.course_id = "absurd-gazelle"
        self.username = "sgbaird"
        self.password = "D.Pq5gYtejYbU#L"
        self.host = "248cc294c37642359297f75b7b023374.s2.eu.hivemq.cloud"
        self.as7341_topic = f"{self.course_id}/as7341"
        self.session_id = secrets.token_hex(4)
        # Save session ID for autograding
        with open("session_id.txt", "w") as f:
            f.write(self.session_id)
        self.client = paho.Client()
        self.sensor_queue = self.get_client_and_queue()

    def get_client_and_queue(self, port=8883, tls=True):
        
        queue = Queue()
        connected_event = threading.Event()

        def on_message(client, userdata, msg):
            queue.put(json.loads(msg.payload))

        def on_connect(client, userdata, flags, rc):
            self.client.subscribe(self.as7341_topic, qos=2)
            connected_event.set()

        self.client.on_connect = on_connect
        self.client.on_message = on_message

        if tls:
            self.client.tls_set(tls_version=paho.ssl.PROTOCOL_TLS_CLIENT)  # type: ignore
        self.client.username_pw_set(self.username, self.password)
        self.client.connect(self.host, port)
        connected_event.wait(timeout=10.0)
        
        return queue

    def get_color_sensor_data(self, queue_timeout=30, function_timeout=300):
        command_topic = 'absurd-gazelle/neopixel'
        payload = '{"command": {"R": 0.2, "G": 0.5, "B": 0.3}, "experiment_id": "target", "session_id": "aaf818f3"}'
        self.client.publish(command_topic, payload, qos=2) # this act as a trigger
        self.client.loop_start()
        t0 = time()
        
        while time() - t0 <= function_timeout:
            try:
                results = self.sensor_queue.get(True,timeout=queue_timeout)
                print(results)
                if isinstance(results, dict):
                    self.client.loop_stop()
                    return results["sensor_data"]
            except Empty:
                raise TimeoutError(f"Sensor data retrieval timed out ({queue_timeout} seconds)")

        raise TimeoutError(f"Function timed out ({function_timeout} seconds)")

# # Set up MQTT client and subscribe to the topic
# mqtt_client, queue = get_client_and_queue(as7341_topic, host, username, password)

# # Retrieve and print sensor data
# sensor_data = get_color_sensor_data(mqtt_client, queue)
# print(f"Sensor data: {sensor_data}")

if __name__ == "__main__":
    color_sensor_1 = color_sensor()
    color_sensor_1.get_color_sensor_data()
