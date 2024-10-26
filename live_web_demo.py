from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient
import matplotlib.pyplot as plt
import asyncio
import os

app = FastAPI()

# MongoDB configuration
MONGO_DETAILS = "mongodb://localhost:27017"
client = AsyncIOMotorClient(MONGO_DETAILS)
db = client['demo_db']
collection = db['numbers']
target_collection = db['target_rgb']

# Create 'static' folder if it doesn't exist
os.makedirs("static", exist_ok=True)

# Define initial target RGB channel
target_rgb = [0.4, 0.3, 0.3]  # Example target RGB ratios
plot_file = 'static/plot.png'  # Define plot file path

# List to store MNE history
mne_history = []

# Global variable for current RGB ratios
current_rgb_ratio = [0.33, 0.33, 0.34]  # Initial RGB ratios
num_iterations_to_run = 0  # Number of additional iterations specified by the user

async def load_data_from_mongodb():
    """
    Load data from MongoDB for the plot.
    """
    data = await collection.find().to_list(100)
    return [item['number'] for item in data]

async def load_target_from_db():
    """
    Load target RGB values from MongoDB.
    """
    global target_rgb
    target = await target_collection.find_one({}, sort=[('_id', -1)])
    if target:
        target_rgb = [target['R'], target['G'], target['B']]

async def save_target_to_db(new_target):
    """
    Save the target RGB values to MongoDB.
    """
    await target_collection.insert_one(new_target)

def calculate_mne(current_rgb, target_rgb):
    """
    Calculate Mean Normalized Error (MNE) for each RGB channel.
    """
    return np.abs((np.array(current_rgb) - np.array(target_rgb)) / np.array(target_rgb))

async def create_plots(current_rgb, iteration):
    """
    Create two plots: MNE over iterations and RGB bar chart.
    """
    # Load data from MongoDB for plotting
    data = await load_data_from_mongodb()

    # Calculate MNE for R, G, B channels and average MNE for the current iteration
    mne_values = calculate_mne(current_rgb, target_rgb)
    avg_mne = mne_values.mean()

    # Ensure mne_history and iteration have matching lengths
    mne_history.append(avg_mne)
    x_iterations = list(range(1, len(mne_history) + 1))

    channels = ['R', 'G', 'B']
    
    # Create MNE over iterations plot
    plt.figure(figsize=(12, 5))
    plt.subplot(1, 2, 1)
    plt.plot(x_iterations, mne_history, marker='o', color='blue')
    plt.title("Mean Normalized Error (MNE) over Iterations")
    plt.xlabel("Iteration")
    plt.ylabel("Average MNE")
    plt.ylim(0, 1)  # Limit y-axis for better visualization

    # Create RGB bar chart
    plt.subplot(1, 2, 2)
    width = 0.35
    x = np.arange(len(channels))

    # Plot current RGB values
    plt.bar(x - width/2, current_rgb, width=width, label='Current RGB', color='cyan')
    # Plot target RGB values
    plt.bar(x + width/2, target_rgb, width=width, label='Target RGB', color='magenta')

    plt.xticks(x, channels)
    plt.title("Current vs. Target RGB Ratios")
    plt.xlabel("Channel")
    plt.ylabel("Ratio")
    plt.legend(loc="upper right")
    
    plt.tight_layout()
    plt.savefig(plot_file)
    plt.close()

@app.get("/", response_class=HTMLResponse)
async def get():
    return """
    <html>
    <head>
        <title>Real-time RGB Plots</title>
        <style>
            .slider-container { margin: 10px; }
            .slider { width: 300px; }
            .button { margin: 10px; padding: 10px; }
        </style>
        <script>
            function updateSliders() {
                const r = parseFloat(document.getElementById('sliderR').value);
                const g = parseFloat(document.getElementById('sliderG').value);
                let b = 1 - r - g;
                if (b < 0) b = 0;

                document.getElementById('sliderB').value = b.toFixed(2);
                document.getElementById('valueR').innerText = r.toFixed(2);
                document.getElementById('valueG').innerText = g.toFixed(2);
                document.getElementById('valueB').innerText = b.toFixed(2);

                fetch('/update_rgb', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ R: r, G: g, B: b })
                });
            }

            function setTarget() {
                const r = parseFloat(document.getElementById('sliderR').value);
                const g = parseFloat(document.getElementById('sliderG').value);
                const b = parseFloat(document.getElementById('sliderB').value);

                fetch('/set_target', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ R: r, G: g, B: b })
                });
            }

            function setIterations() {
                const iterations = parseInt(document.getElementById('numIterations').value);
                if (iterations > 0) {
                    fetch('/set_iterations', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ iterations: iterations })
                    });
                }
            }

            setInterval(function() {
                document.getElementById("plot").src = '/static/plot.png?' + new Date().getTime();
            }, 1000);
        </script>
    </head>
    <body>
        <h1>Real-time RGB Channel Analysis</h1>
        <div style="display: flex; justify-content: center; align-items: center;">
            <img id="plot" src="/static/plot.png" alt="Plot" width="80%">
        </div>
        <div class="slider-container">
            <label>R: <span id="valueR">0.33</span></label>
            <input type="range" id="sliderR" class="slider" min="0" max="1" step="0.01" value="0.33" oninput="updateSliders()">
        </div>
        <div class="slider-container">
            <label>G: <span id="valueG">0.33</span></label>
            <input type="range" id="sliderG" class="slider" min="0" max="1" step="0.01" value="0.33" oninput="updateSliders()">
        </div>
        <div class="slider-container">
            <label>B: <span id="valueB">0.34</span></label>
            <input type="range" id="sliderB" class="slider" min="0" max="1" step="0.01" value="0.34" disabled>
        </div>
        <button class="button" onclick="setTarget()">Set Target</button>
        <div class="slider-container">
            <label>Iterations to run:</label>
            <input type="number" id="numIterations" min="1" value="1">
            <button class="button" onclick="setIterations()">Run Iterations</button>
        </div>
    </body>
    </html>
    """

@app.post("/update_rgb")
async def update_rgb(request: Request):
    """
    Update the current RGB ratios based on slider input.
    """
    global current_rgb_ratio
    data = await request.json()
    current_rgb_ratio = [data['R'], data['G'], data['B']]
    return JSONResponse({"status": "success"})

@app.post("/set_target")
async def set_target(request: Request):
    """
    Set the target RGB values and save to MongoDB.
    """
    global target_rgb
    data = await request.json()
    target_rgb = [data['R'], data['G'], data['B']]
    await save_target_to_db({"R": data['R'], "G": data['G'], "B": data['B']})
    return JSONResponse({"status": "target set successfully"})

@app.post("/set_iterations")
async def set_iterations(request: Request):
    """
    Set the number of additional iterations to run.
    """
    global num_iterations_to_run
    data = await request.json()
    num_iterations_to_run = data.get('iterations', 0)
    return JSONResponse({"status": f"Running {num_iterations_to_run} iterations"})

@app.get("/static/plot.png")
async def get_plot():
    """
    Serve the plot file, ensuring it exists.
    """
    if not os.path.exists(plot_file):
        await create_plots([0, 0, 0], 0)  # Create initial plot if it doesn't exist
    return FileResponse(plot_file)

async def update_plots_periodically():
    """
    Asynchronous task to update plots with the current RGB ratios.
    """
    global num_iterations_to_run
    iteration = 0
    while True:
        if num_iterations_to_run > 0:
            iteration += 1
            await create_plots(current_rgb_ratio, iteration)
            num_iterations_to_run -= 1
        
        # Wait for 1 second before checking again
        await asyncio.sleep(1)

# Start the background task to update plots
@app.on_event("startup")
async def start_background_tasks():
    # Load target from MongoDB at startup
    await load_target_from_db()

    # Ensure the initial plot exists
    await create_plots([0.33, 0.33, 0.34], 0)  # Initial plot with starting ratios
    asyncio.create_task(update_plots_periodically())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("live_web_demo:app", host="127.0.0.1", port=8080, reload=True)