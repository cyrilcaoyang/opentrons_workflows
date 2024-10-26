# Description: Send commands to HiveMQ and receive sensor data from HiveMQ

# NOTE: for this to work properly, you are expected to be running the
# microcontroller.py script on your Pico W
import os
import json
import secrets
import threading
from time import time

import numpy as np
import pandas as pd

from queue import Queue, Empty
import paho.mqtt.client as paho

from ax.service.ax_client import AxClient, ObjectiveProperties
import plotly.graph_objects as go
from sklearn.metrics import mean_absolute_error

course_id = "absurd-gazelle"

username = "sgbaird"
password = "D.Pq5gYtejYbU#L"
host = "248cc294c37642359297f75b7b023374.s2.eu.hivemq.cloud"

# Topics
neopixel_topic = f"{course_id}/neopixel"
as7341_topic = f"{course_id}/as7341"

# random session id to keep track of the session and filter out old data
session_id = secrets.token_hex(4)

with open("session_id.txt", "w") as f:  # for autograding
    f.write(session_id)  # for autograding

num_iter = 20
target_command = {"R": 0.2, "G": 0.5, "B": 0.3}

# %% MQTT Communication


def get_client_and_queue(
    subscribe_topic, host, username, password=None, port=8883, tls=True
):
    """
    This function creates a new Paho MQTT client, connects it to the specified
    host, and subscribes it to the specified topic. It also creates a queue for
    storing incoming messages and sets up event handlers for handling connection
    and message events.

    Parameters
    ----------
    subscribe_topic : str
        The MQTT topic that the client should subscribe to.
    host : str
        The hostname or IP address of the MQTT server to connect to.
    username : str
        The username to use for MQTT authentication.
    password : str, optional
        The password to use for MQTT authentication, by default None.
    port : int, optional
        The port number to connect to at the MQTT server, by default 8883.
    tls : bool, optional
        Whether to use TLS for the connection, by default True.

    Returns
    -------
    tuple
        A tuple containing the Paho MQTT client and the queue for storing
        incoming messages.

    Examples
    --------
    >>> client, queue = get_client_and_queue("test/topic", "mqtt.example.com", "username", "password")
    """
    client = paho.Client()  # create new instance
    queue = Queue()  # Create queue to store sensor data
    connected_event = threading.Event()  # event to wait for connection

    def on_message(client, userdata, msg):
        print(f"Received message on topic {msg.topic}: {msg.payload}")
        # Convert msg (a JSON string) into a dictionary
        msg_dict = json.loads(msg.payload)
        # Put the dictionary into the queue
        queue.put(msg_dict)

    def on_connect(client, userdata, flags, rc):
        client.subscribe(subscribe_topic, qos=2)
        connected_event.set()

    client.on_connect = on_connect
    client.on_message = on_message

    # enable TLS for secure connection
    if tls:
        client.tls_set(tls_version=paho.ssl.PROTOCOL_TLS_CLIENT)  # type: ignore

    # set username and password
    client.username_pw_set(username, password)

    # connect to HiveMQ Cloud on port 8883 (default for MQTT)
    client.connect(host, port)

    client.subscribe(subscribe_topic, qos=2)
    # wait for connection to be established

    connected_event.wait(timeout=10.0)
    return client, queue


# Function to send a command to the neopixel and wait for sensor data
def run_experiment(
    client, queue, command_topic, payload_dict, queue_timeout=30, function_timeout=300
):
    """
    This function sends a command to the neopixel, waits for sensor data, and
    returns the results.

    Parameters
    ----------
    client : paho.mqtt.client.Client
        The Paho MQTT client to use for sending the command and receiving the
        results.
    queue : queue.Queue
        The queue where incoming messages from the MQTT client will be stored.
    command_topic : str
        The MQTT topic to publish the command to.
    payload_dict : dict
        The dictionary containing the command and experiment_id. The command
        should be a dictionary with 'R', 'G', and 'B' keys.
    queue_timeout : int, optional
        The number of seconds to wait for a message in the queue before timing
        out, by default 30.
    function_timeout : int, optional
        The number of seconds to wait for the function to complete before timing
        out, by default 300.

    Returns
    -------
    dict
        The results of the experiment, as a dictionary.

    Raises
    ------
    TimeoutError
        If the function does not complete within the specified function_timeout.
    queue.Empty
        If no message is received in the queue within the specified
        queue_timeout.

    Examples
    --------
    >>> run_experiment(client, queue, "test/topic", {"command": {"R": 255, "G": 255, "B": 255}, "experiment_id": 1})
    {"experiment_id": 1, "sensor_data": {<sensor_data>}, "command": {"R": 255, "G": 255, "B": 255}}
    """
    assert {"command", "experiment_id"}.issubset(
        payload_dict.keys()
    ), "payload_dict must contain at least the keys 'command', and 'experiment_id'"

    assert {"R", "G", "B"} == set(
        payload_dict["command"].keys()
    ), "payload_dict['command'] must contain the keys 'R', 'G', and 'B'"

    # Convert payload_dict into a JSON string
    payload = json.dumps(payload_dict)
    # Publish the JSON string to the command_topic with qos=2
    client.publish(command_topic, payload, qos=2)

    client.loop_start()

    t0 = time()
    while True:
        if time() - t0 > function_timeout:
            raise TimeoutError(
                f"Function timed out without valid data ({function_timeout} seconds)"
            )
        try:
            results = queue.get(True, timeout=queue_timeout)
        except Empty as e:
            raise Empty(
                f"Sensor data retrieval timed out ({queue_timeout} seconds)"
            ) from e

        # only return the data if it matches the expected experiment id
        if (
            isinstance(results, dict)
            and results["experiment_id"] == payload_dict["experiment_id"]
        ):
            client.loop_stop()
            return results


# Orchestrator subscribes to the sensor data topic
mqtt_client, queue = get_client_and_queue(
    as7341_topic, host, username, password=password
)

# %% Bayesian Optimization

target_payload_dict = {
    "command": target_command,
    "experiment_id": "target",
    "session_id": session_id,
}

target_results = run_experiment(mqtt_client, queue, neopixel_topic, target_payload_dict)
print(f"Target results: {target_results}")
target_sensor_data = target_results["sensor_data"]

# For autograding
payload_dicts = []
results_dicts = []

obj_name = "mae"


def evaluate(command):
    """
    This function sends a command to the neopixel, waits for sensor data,
    calculates the mean absolute error (MAE) between the received sensor data
    and target sensor data, and returns a dictionary with the object name and
    MAE.

    Parameters
    ----------
    command : dict
        The command to be sent to the neopixel. This should be a dictionary with
        'R', 'G', and 'B' keys.

    Returns
    -------
    dict
        A dictionary with the object name as the key and the calculated MAE as
        the value.

    Examples
    --------
    >>> evaluate({"R": 255, "G": 255, "B": 255})
    {"mae": 12.34}
    """
    # random experiment id to keep track where the sensor data is froms
    experiment_id = secrets.token_hex(4)  # 4 bytes = 8 characters
    payload_dict = {
        "command": command,
        "experiment_id": experiment_id,
        "session_id": session_id,
    }
    payload_dicts.append(payload_dict)  # For autograding

    print(f"Sending {payload_dict} to {neopixel_topic}")

    results_dict = run_experiment(mqtt_client, queue, neopixel_topic, payload_dict)

    sensor_data = results_dict["sensor_data"]

    keys = sensor_data.keys()
    sensor_vals = [sensor_data[key] for key in keys]
    target_sensor_vals = [target_sensor_data[key] for key in keys]

    mae = mean_absolute_error(sensor_vals, target_sensor_vals)

    assert isinstance(mae, float)

    results_dict["mae"] = mae
    results_dicts.append(results_dict)  # For autograding


    return {obj_name: mae}

# Define total for compositional constraint, where x1 + x2 + x3 == total
total = 1.0

# Define the parameters per the README.md file
parameters = [
    {"name": "R", "type": "range", "bounds": [0.0, 1.0], "value_type": "float"},
    {"name": "G", "type": "range", "bounds": [0.0, 1.0], "value_type": "float"},
]

# Instantiate the AxClient class with a random seed for reproducibility
ax_client = AxClient(random_seed=42)

# define the objective as "mae" (mean absolute error) with minimize=True
objectives = {obj_name: ObjectiveProperties(minimize=True)}

# Use the create_experiment method from the AxClient class to pass the
# parameters and objective(s)
ax_client.create_experiment(parameters=parameters, objectives=objectives, parameter_constraints=[
        f"R + G <= {total}",  # reparameterized compositional constraint, which is a type of sum constraint
    ])

for _ in range(num_iter):
    parameterization, trial_index = ax_client.get_next_trial()
    parameterization["B"] = total - parameterization["R"] - parameterization["G"]
    # e.g., parameterization={"R": 10, "G": 20, "B": 15} and trial_index=0
    results = evaluate(parameterization)
    ax_client.complete_trial(trial_index=trial_index, raw_data=results)

# FROM MEMORY GET THE BEST PARMS THAT MATCH THE METRICS 
best_parameters, metrics = ax_client.get_best_parameters()
best_parameters["B"] = total - best_parameters["R"] - best_parameters["G"]
true_mismatch = mean_absolute_error(
    list(target_command.values()), list(best_parameters.values())
)

print(f"Target color: {target_command}")
print(f"Best observed color: {best_parameters}")
print(f"Color misfit: {np.round(true_mismatch, 1)}")

# Save the entire Ax experiment to a JSON file
ax_client.save_to_json_file()


def to_plotly(axplotconfig):
    """Converts AxPlotConfig to plotly Figure."""
    data = axplotconfig[0]["data"]
    layout = axplotconfig[0]["layout"]
    fig = go.Figure({"data": data, "layout": layout})
    return fig


# Convert the optimization trace to a Plotly figure and save it as an image
fig = to_plotly(ax_client.get_optimization_trace())
image_name = "optimization_trace.png"

# would need to `pip install kaleido==0.1.0.post1` for Windows
fig.write_image(image_name)
# Open the image file in Codespaces (or Visual Studio Code)
os.system(f"code {image_name}")

# write the commands with experiment ids to a file (for autograding)
with open("payload_dicts.json", "w") as f:
    json.dump(payload_dicts, f)

# write the sensor data to a file (for autograding)
with open("results.json", "w") as f:
    json.dump(results_dicts, f)