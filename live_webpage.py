from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles  # Import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient
import matplotlib.pyplot as plt
import asyncio
import os
import uvicorn  # Import uvicorn to run the server

app = FastAPI()
client = AsyncIOMotorClient("mongodb://localhost:27017")
db = client['demo_db']
collection = db['numbers']

# Create 'static' folder if it doesn't exist
os.makedirs("static", exist_ok=True)

# Mount the 'static' directory to serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

async def create_plot():
    data = await collection.find().to_list(100)
    x = [i for i, _ in enumerate(data)]
    y = [item['number'] for item in data]

    plt.figure()
    plt.plot(x, y, marker='o')
    plt.savefig('static/plot.png')
    plt.close()

@app.get("/", response_class=HTMLResponse)
async def get():
    return """
    <html>
    <head><title>Dynamic Plot</title></head>
    <body>
        <h1>Real-time Data Plot</h1>
        <img id="plot" src="/static/plot.png" alt="Plot">
        <script>
            let ws = new WebSocket("ws://localhost:8000/ws");
            ws.onmessage = function(event) {
                document.getElementById("plot").src = '/static/plot.png?' + new Date().getTime();
            };
        </script>
    </body>
    </html>
    """

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        await create_plot()  # Update plot on each WebSocket message
        await websocket.send_text("updated")
        await asyncio.sleep(1)  # Send an update every 1 seconds

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
