import os
from fastapi import FastAPI, HTTPException
from Adafruit_IO import Client
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

app = FastAPI()

# Environment Variables
AIO_USERNAME = os.getenv("AIO_USERNAME")
AIO_KEY = os.getenv("AIO_KEY")
MONGO_URL = os.getenv("MONGO_URL")

@app.get("/")
def home():
    return {"status": "Online"}

@app.get("/send-vibe")
async def send_vibe(from_user: str, to_user: str, vibe: str):
    try:
        # 1. Initialize Clients inside the route
        aio = Client(AIO_USERNAME, AIO_KEY)
        # We add tlsAllowInvalidCertificates=True only if the Python version is still acting up
        db_client = AsyncIOMotorClient(MONGO_URL)
        db = db_client.ldr_lamp_db

        # 2. Log to Database (Async)
        log_entry = {
            "sender": from_user,
            "receiver": to_user,
            "vibe": vibe,
        }
        # Use await to make sure it's asynchronous
        await db.vibe_logs.insert_one(log_entry)

        # 3. Send to Adafruit (This is a synchronous library call)
        # We wrap it in a thread so it doesn't block the async server
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, aio.send_data, 'lamp-command', vibe)
        
        return {"status": "Success", "vibe_sent": vibe}

    except Exception as e:
        print(f"ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# import os
# from fastapi import FastAPI
# from Adafruit_IO import Client
# from motor.motor_asyncio import AsyncIOMotorClient
# from datetime import datetime

# app = FastAPI()

# # 1. Environment Variables (set these later in Render)
# AIO_USERNAME = os.getenv("AIO_USERNAME")
# AIO_KEY = os.getenv("AIO_KEY")
# MONGO_URL = os.getenv("MONGO_URL")

# # 2. Initialize Clients
# # We use try/except so the server doesn't crash if keys are missing initially
# try:
#     aio = Client(AIO_USERNAME, AIO_KEY)
#     db_client = AsyncIOMotorClient(MONGO_URL)
#     db = db_client.ldr_lamp_db
# except Exception as e:
#     print(f"Setup Error: {e}")

# @app.get("/")
# def home():
#     return {"status": "Online", "message": "LDR Server is Running"}

# @app.get("/send-vibe")
# async def send_vibe(from_user: str, to_user: str, vibe: str):
#     """
#     Example: /send-vibe?from_user=John&to_user=Jane&vibe=RED
#     """
#     # A. Log to Database
#     log_entry = {
#         "sender": from_user,
#         "receiver": to_user,
#         "vibe": vibe,
#         "timestamp": datetime.utcnow()
#     }
#     await db.vibe_logs.insert_one(log_entry)

#     # B. Send to Adafruit IO
#     # This sends the 'vibe' text to your feed
#     try:
#         aio.send_data('lamp-command', vibe)
#         return {"status": "Success", "vibe_sent": vibe}
#     except Exception as e:
#         return {"status": "Error", "message": str(e)}





# from fastapi import FastAPI
# from adafruit_io import Client
# from motor.motor_asyncio import AsyncIOMotorClient

# app = FastAPI()

# # 1. SETUP ADARUIT (The Light Link)
# # ADAFRUIT_IO_USERNAME = "YOUR_USERNAME"
# # ADAFRUIT_IO_KEY = "YOUR_KEY"
# ADAFRUIT_AIO_USERNAME = "Nomad23"
# ADAFRUIT_AIO_KEY      = "aio_YvfM3533sArTvhkA5GYt9Siw2WYQ"
# aio = Client(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)

# # 2. SETUP DATABASE (The Memory)
# # This assumes you have MongoDB installed locally. 
# # For now, it will just try to connect.
# MONGO_URL = "mongodb://localhost:27017"
# db_client = AsyncIOMotorClient(MONGO_URL)
# db = db_client.ldr_database

# # 3. THE COMMAND ENDPOINT
# # This is the "URL" your Android app will call later.
# @app.get("/send-color")
# async def send_color(vibe: str, receiver_id: str):
#     """
#     When you go to: http://localhost:8000/send-color?vibe=RED&receiver_id=user1
#     This function runs.
#     """
    
#     # A. Save the event to the database (The Memory)
#     log_entry = {"receiver": receiver_id, "message": vibe}
#     await db.logs.insert_one(log_entry)

#     # B. Send the command to Adafruit (The Light)
#     # We send the 'vibe' (e.g., RED) to the Adafruit feed
#     aio.send_data('lamp-command', vibe)

#     return {"status": "Success", "sent_to": receiver_id, "color": vibe}

# @app.get("/")
# def home():
#     return {"message": "The LDR Server is Running!"}

