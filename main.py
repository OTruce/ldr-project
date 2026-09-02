import os
import random
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, Body
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from Adafruit_IO import Client
import resend

# --- 1. SETUP & CONNECTIONS ---

# Database URL (Fix for Render/Supabase IPv4)
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL, 
    pool_pre_ping=True, 
    connect_args={"sslmode": "require"}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Adafruit IO & Resend Setup
AIO_USERNAME = os.getenv("AIO_USERNAME")
AIO_KEY = os.getenv("AIO_KEY")
resend.api_key = os.getenv("RESEND_API_KEY")

# Color Map for the Lamp
COLOR_MAP = {
    "LOVE": "#FF0000",      # Red
    "MISS": "#0000FF",      # Blue
    "SORRY": "#FFFF00",     # Yellow
    "MAD": "#FFA500",       # Orange
    "THINKING": "#FFC0CB",   # Pink
    "FEELING": "#800080", #PURPLE
    "SAD":"#00008B"
}

# --- 2. DATABASE MODELS (Tables) ---

class User(Base):
    __tablename__ = "users"
    ldrid = Column(String, primary_key=True) # e.g., ldr001
    name = Column(String)
    email = Column(String, unique=True)
    deviceid = Column(String) # e.g., esp001

class VibeLog(Base):
    __tablename__ = "vibe_logs"
    id = Column(Integer, primary_key=True, index=True)
    sender_ldrid = Column(String)
    receiver_ldrid = Column(String)
    vibe_type = Column(String)
    hex_color = Column(String)
    status = Column(String, default="pending") # 'pending' or 'executed'
    timestamp = Column(DateTime, default=datetime.utcnow)

class OtpCode(Base):
    __tablename__ = "otp_codes"
    email = Column(String, primary_key=True)
    code = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

# Create all tables automatically
Base.metadata.create_all(bind=engine)

app = FastAPI()

# --- 3. LOGIN & AUTH ROUTES ---

@app.post("/request-otp")
async def request_otp(email: str):
    db = SessionLocal()
    try:
        # Check if user exists
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail="Email not found")

        # Generate 6-digit OTP
        otp_val = str(random.randint(100000, 999999))

        # Save to DB (Upsert)
        db.execute(text(
            "INSERT INTO otp_codes (email, code) VALUES (:e, :c) "
            "ON CONFLICT (email) DO UPDATE SET code = :c, created_at = now()"
        ), {"e": email, "c": otp_val})
        db.commit()

        # Send Email via Resend
        resend.Emails.send({
            "from": "LDR Lamp <onboarding@resend.dev>",
            "to": [email],
            "subject": f"{otp_val} is your LDR login code",
            "html": f"<p>Your login code is: <strong>{otp_val}</strong></p>"
        })

        return {"status": "OTP_SENT"}
    finally:
        db.close()

@app.post("/verify-otp")
async def verify_otp(email: str, otp: str):
    db = SessionLocal()
    try:
        record = db.query(OtpCode).filter(OtpCode.email == email).first()
        if record and record.code == otp:
            user = db.query(User).filter(User.email == email).first()
            # Delete code after use
            db.delete(record)
            db.commit()
            return {"status": "SUCCESS", "ldrid": user.ldrid, "name": user.name}
        raise HTTPException(status_code=401, detail="Invalid code")
    finally:
        db.close()

# --- 4. VIBE & DEVICE ROUTES ---

@app.get("/send-vibe")
async def send_vibe(from_id: str, to_id: str, vibe: str):
    db = SessionLocal()
    try:
        vibe_upper = vibe.upper()
        hex_color = COLOR_MAP.get(vibe_upper, "#FFFFFF")

        # Find receiver's device
        receiver = db.query(User).filter(User.ldrid == to_id).first()
        if not receiver or not receiver.deviceid:
            raise HTTPException(status_code=404, detail="Device not found")

        # Log vibe as 'pending'
        new_log = VibeLog(
            sender_ldrid=from_id, 
            receiver_ldrid=to_id, 
            vibe_type=vibe_upper, 
            hex_color=hex_color,
            status="pending"
        )
        db.add(new_log)
        db.commit()

        # Push to Adafruit
        aio = Client(AIO_USERNAME, AIO_KEY)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, aio.send_data, receiver.deviceid, hex_color)

        return {"status": "Success", "color": hex_color}
    finally:
        db.close()

# Used by the ESP32 to check for missed vibes
@app.get("/get-pending")
async def get_pending(ldrid: str):
    db = SessionLocal()
    vibes = db.query(VibeLog).filter(VibeLog.receiver_ldrid == ldrid, VibeLog.status == "pending").all()
    db.close()
    return vibes

# Used by the ESP32 to clear the queue
@app.post("/mark-executed")
async def mark_executed(vibe_ids: list[int] = Body(...)):
    db = SessionLocal()
    db.query(VibeLog).filter(VibeLog.id.in_(vibe_ids)).update({"status": "executed"}, synchronize_session=False)
    db.commit()
    db.close()
    return {"status": "updated"}

# Helper to keep server awake
@app.get("/health")
def health():
    return "ok"

# Helper to manually add users initially
@app.get("/register-user")
def register(ldrid: str, name: str, email: str, deviceid: str):
    db = SessionLocal()
    try:
        new_user = User(ldrid=ldrid, name=name, email=email, deviceid=deviceid)
        db.add(new_user)
        db.commit()
        return "User Registered"
    finally:
        db.close()


# import os
# import asyncio
# from datetime import datetime
# from fastapi import FastAPI, HTTPException, Query
# from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey
# from sqlalchemy.ext.declarative import declarative_base
# from sqlalchemy.orm import sessionmaker
# from Adafruit_IO import Client

# # --- DATABASE SETUP (Supabase/PostgreSQL) ---
# # Your DATABASE_URL should look like: postgresql://postgres:[password]@db.[project].supabase.co:5432/postgres
# DATABASE_URL = os.getenv("DATABASE_URL")
# if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
#     DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# engine = create_engine(
#     DATABASE_URL, 
#     pool_pre_ping=True,
#     connect_args={"sslmode": "require"} # Supabase requires SSL
# )
# SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# Base = declarative_base()

# # # --- DATABASE MODELS (The Tables) ---
# # class User(Base):
# #     __tablename__ = "users"
# #     id = Column(Integer, primary_key=True, index=True)
# #     ldrid = Column(String, unique=True, index=True) # e.g., ldr001
# #     email = Column(String, unique=True)
# #     deviceid = Column(String) # The Adafruit Feed name for their specific lamp

# # # --- DATABASE MODELS ---
# # class User(Base):
# #     __tablename__ = "users"
# #     # We tell SQLAlchemy that 'ldrid' is the Primary Key, not 'id'
# #     ldrid = Column(String, primary_key=True) 
# #     name = Column(String)
# #     email = Column(String, unique=True)
# #     phone = Column(String) # Matches the 'numeric' column in your Supabase
# #     deviceid = Column(String)

# # class VibeLog(Base):
# #     __tablename__ = "vibe_logs"
# #     id = Column(Integer, primary_key=True, index=True)
# #     sender_ldrid = Column(String)
# #     receiver_ldrid = Column(String)
# #     vibe_type = Column(String)
# #     hex_color = Column(String)
# #     timestamp = Column(DateTime, default=datetime.utcnow)

# # --- DATABASE MODELS ---

# class User(Base):
#     __tablename__ = "users"
#     # We tell SQLAlchemy that 'ldrid' is the Primary Key. 
#     # Do NOT include a line for 'id' here.
#     ldrid = Column(String, primary_key=True) 
#     name = Column(String)
#     email = Column(String, unique=True)
#     deviceid = Column(String)

# # class VibeLog(Base):
# #     __tablename__ = "vibe_logs"
# #     # If your vibe_logs table in Supabase doesn't have an 'id', 
# #     # we use 'timestamp' as the primary key for the code to work.
# #     # If it DOES have an 'id' column, you can add: id = Column(Integer, primary_key=True)
# #     timestamp = Column(DateTime, primary_key=True, default=datetime.utcnow)
# #     sender_ldrid = Column(String)
# #     receiver_ldrid = Column(String)
# #     vibe_type = Column(String)
# #     hex_color = Column(String)

# class VibeLog(Base):
#     __tablename__ = "vibe_logs"
#     id = Column(Integer, primary_key=True, index=True) # Unique ID for each message
#     sender_ldrid = Column(String)
#     receiver_ldrid = Column(String)
#     vibe_type = Column(String)
#     hex_color = Column(String)
#     status = Column(String, default="pending") # 'pending' or 'executed'
#     timestamp = Column(DateTime, default=datetime.utcnow)

# # Create the tables in Supabase if they don't exist
# Base.metadata.create_all(bind=engine)

# # --- APP SETUP ---
# app = FastAPI()

# # Adafruit IO Credentials
# AIO_USERNAME = os.getenv("AIO_USERNAME")
# AIO_KEY = os.getenv("AIO_KEY")

# # Color Map: Translates "Vibes" into Hex codes for the LED hardware
# COLOR_MAP = {
#     "LOVE": "#FF0000",      # Red
#     "MISS": "#ADD8E6",      # Blue
#     "SORRY": "#FFFF00",     # Yellow
#     "MAD": "#FFA500",       # Orange
#     "THINKING": "#FFC0CB",   # Pink
#     "FEELING": "#800080", #PURPLE
#     "SAD":"#00008B"
# }

# # --- ROUTES ---

# @app.get("/")
# @app.get("/health")
# def health_check():
#     """Endpoint for Cron-job.org to keep the server awake."""
#     return {"status": "ok", "message": "Server is healthy"}

# @app.get("/send-vibe")
# async def send_vibe(
#     from_id: str = Query(..., description="The LDRID of the sender"),
#     to_id: str = Query(..., description="The LDRID of the receiver"),
#     vibe: str = Query(..., description="The vibe type: LOVE, MISS, SORRY, etc.")
# ):
#     db = SessionLocal()
#     try:
#         # 1. Translate vibe to color
#         vibe_upper = vibe.upper()
#         hex_color = COLOR_MAP.get(vibe_upper, "#FFFFFF") # Default to White

#         # 2. Find the receiver in the database to get their Device ID
#         receiver = db.query(User).filter(User.ldrid == to_id).first()
#         if not receiver:
#             raise HTTPException(status_code=404, detail=f"Receiver {to_id} not found")

#         if not receiver.deviceid:
#             raise HTTPException(status_code=400, detail="Receiver has no device linked")

#         # 3. Log the interaction to the SQL Database
#         new_log = VibeLog(
#             sender_ldrid=from_id,
#             receiver_ldrid=to_id,
#             vibe_type=vibe_upper,
#             hex_color=hex_color
#         )
#         db.add(new_log)
#         db.commit()

#         # 4. Push the command to Adafruit IO
#         # We wrap this in a thread so it doesn't slow down the response
#         aio = Client(AIO_USERNAME, AIO_KEY)
#         loop = asyncio.get_event_loop()
        
#         # NOTE: 'receiver.deviceid' must match your Feed Name on Adafruit exactly
#         await loop.run_in_executor(None, aio.send_data, receiver.deviceid, hex_color)

#         return {
#             "status": "Success",
#             "message": f"Vibe sent to {to_id}",
#             "color_sent": hex_color
#         }

#     except Exception as e:
#         print(f"ERROR: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))
#     finally:
#         db.close()

# # 1. The Device asks: "What did I miss?"
# @app.get("/get-pending")
# async def get_pending(ldrid: str):
#     db = SessionLocal()
#     # Find all vibes for this user that are still 'pending'
#     pending_vibes = db.query(VibeLog).filter(
#         VibeLog.receiver_ldrid == ldrid, 
#         VibeLog.status == "pending"
#     ).all()
#     db.close()
#     return pending_vibes

# # 2. The Device says: "I'm done with these!"
# @app.post("/mark-executed")
# async def mark_executed(vibe_ids: list[int]):
#     db = SessionLocal()
#     # Update the status of all these IDs to 'executed'
#     db.query(VibeLog).filter(VibeLog.id.in_(vibe_ids)).update(
#         {"status": "executed"}, synchronize_session=False
#     )
#     db.commit()
#     db.close()
#     return {"status": "Updated"}

# # --- ADMIN ROUTE: Register a User ---
# # You can use this to add yourself and your partner to the database initially
# @app.get("/register-user")
# def register(ldrid: str, email: str, deviceid: str):
#     db = SessionLocal()
#     try:
#         new_user = User(ldrid=ldrid, email=email, deviceid=deviceid)
#         db.add(new_user)
#         db.commit()
#         return {"message": f"User {ldrid} registered successfully"}
#     except Exception as e:
#         db.rollback()
#         raise HTTPException(status_code=400, detail="User already exists or data error")
#     finally:
#         db.close()


# # import os
# # from fastapi import FastAPI, HTTPException
# # from Adafruit_IO import Client
# # from motor.motor_asyncio import AsyncIOMotorClient
# # import asyncio

# # app = FastAPI()

# # # Environment Variables
# # AIO_USERNAME = os.getenv("AIO_USERNAME")
# # AIO_KEY = os.getenv("AIO_KEY")
# # MONGO_URL = os.getenv("MONGO_URL")

# # @app.get("/")
# # def home():
# #     return {"status": "Online"}

# # @app.get("/send-vibe")
# # async def send_vibe(from_user: str, to_user: str, vibe: str):
# #     try:
# #         # 1. Create a "Translation Map"
# #         # This translates your vibes into Hex colors the dashboard understands
# #         colors = {
# #             "BLUE": "#0000FF",      # I Miss You
# #             "RED": "#FF0000",       # I Love You
# #             "YELLOW": "#FFFF00",    # I'm Sorry
# #             "ORANGE": "#FFA500",    # I'm Mad
# #             "PINK": "#FFC0CB"       # Thinking of you
# #         }

# #         # 2. Get the Hex code. If the vibe isn't in the list, default to White (#FFFFFF)
# #         hex_color = colors.get(vibe.upper(), "#FFFFFF")

# #         # 3. Connect to clients
# #         db_client = AsyncIOMotorClient(MONGO_URL, tlsAllowInvalidCertificates=True)
# #         db = db_client.ldr_lamp_db
# #         aio = Client(AIO_USERNAME, AIO_KEY)

# #         # 4. Log to Database
# #         log_entry = {"sender": from_user, "receiver": to_user, "vibe": vibe, "hex": hex_color}
# #         await db.vibe_logs.insert_one(log_entry)

# #         # 5. Send the HEX CODE to Adafruit, not the word
# #         loop = asyncio.get_event_loop()
# #         await loop.run_in_executor(None, aio.send_data, 'lamp-command', hex_color)
        
# #         return {"status": "Success", "vibe_sent": vibe, "hex": hex_color}

# #     except Exception as e:
# #         print(f"ERROR: {str(e)}")
# #         raise HTTPException(status_code=500, detail=str(e))

# # # @app.get("/send-vibe")
# # # async def send_vibe(from_user: str, to_user: str, vibe: str):
# # #     try:
# # #         # 1. Initialize Clients inside the route
# # #         aio = Client(AIO_USERNAME, AIO_KEY)
# # #         # We add tlsAllowInvalidCertificates=True only if the Python version is still acting up
# # #         db_client = AsyncIOMotorClient(MONGO_URL)
# # #         db = db_client.ldr_lamp_db

# # #         # 2. Log to Database (Async)
# # #         log_entry = {
# # #             "sender": from_user,
# # #             "receiver": to_user,
# # #             "vibe": vibe,
# # #         }
# # #         # Use await to make sure it's asynchronous
# # #         await db.vibe_logs.insert_one(log_entry)

# # #         # 3. Send to Adafruit (This is a synchronous library call)
# # #         # We wrap it in a thread so it doesn't block the async server
# # #         loop = asyncio.get_event_loop()
# # #         await loop.run_in_executor(None, aio.send_data, 'lamp-command', vibe)
        
# # #         return {"status": "Success", "vibe_sent": vibe}

# # #     except Exception as e:
# # #         print(f"ERROR: {str(e)}")
# # #         raise HTTPException(status_code=500, detail=str(e))


# # # import os
# # # from fastapi import FastAPI
# # # from Adafruit_IO import Client
# # # from motor.motor_asyncio import AsyncIOMotorClient
# # # from datetime import datetime

# # # app = FastAPI()

# # # # 1. Environment Variables (set these later in Render)
# # # AIO_USERNAME = os.getenv("AIO_USERNAME")
# # # AIO_KEY = os.getenv("AIO_KEY")
# # # MONGO_URL = os.getenv("MONGO_URL")

# # # # 2. Initialize Clients
# # # # We use try/except so the server doesn't crash if keys are missing initially
# # # try:
# # #     aio = Client(AIO_USERNAME, AIO_KEY)
# # #     db_client = AsyncIOMotorClient(MONGO_URL)
# # #     db = db_client.ldr_lamp_db
# # # except Exception as e:
# # #     print(f"Setup Error: {e}")

# # # @app.get("/")
# # # def home():
# # #     return {"status": "Online", "message": "LDR Server is Running"}

# # # @app.get("/send-vibe")
# # # async def send_vibe(from_user: str, to_user: str, vibe: str):
# # #     """
# # #     Example: /send-vibe?from_user=John&to_user=Jane&vibe=RED
# # #     """
# # #     # A. Log to Database
# # #     log_entry = {
# # #         "sender": from_user,
# # #         "receiver": to_user,
# # #         "vibe": vibe,
# # #         "timestamp": datetime.utcnow()
# # #     }
# # #     await db.vibe_logs.insert_one(log_entry)

# # #     # B. Send to Adafruit IO
# # #     # This sends the 'vibe' text to your feed
# # #     try:
# # #         aio.send_data('lamp-command', vibe)
# # #         return {"status": "Success", "vibe_sent": vibe}
# # #     except Exception as e:
# # #         return {"status": "Error", "message": str(e)}





# # # from fastapi import FastAPI
# # # from adafruit_io import Client
# # # from motor.motor_asyncio import AsyncIOMotorClient

# # # app = FastAPI()

# # # # 1. SETUP ADARUIT (The Light Link)
# # # # ADAFRUIT_IO_USERNAME = "YOUR_USERNAME"
# # # # ADAFRUIT_IO_KEY = "YOUR_KEY"
# # # ADAFRUIT_AIO_USERNAME = "Nomad23"
# # # ADAFRUIT_AIO_KEY      = "aio_YvfM3533sArTvhkA5GYt9Siw2WYQ"
# # # aio = Client(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)

# # # # 2. SETUP DATABASE (The Memory)
# # # # This assumes you have MongoDB installed locally. 
# # # # For now, it will just try to connect.
# # # MONGO_URL = "mongodb://localhost:27017"
# # # db_client = AsyncIOMotorClient(MONGO_URL)
# # # db = db_client.ldr_database

# # # # 3. THE COMMAND ENDPOINT
# # # # This is the "URL" your Android app will call later.
# # # @app.get("/send-color")
# # # async def send_color(vibe: str, receiver_id: str):
# # #     """
# # #     When you go to: http://localhost:8000/send-color?vibe=RED&receiver_id=user1
# # #     This function runs.
# # #     """
    
# # #     # A. Save the event to the database (The Memory)
# # #     log_entry = {"receiver": receiver_id, "message": vibe}
# # #     await db.logs.insert_one(log_entry)

# # #     # B. Send the command to Adafruit (The Light)
# # #     # We send the 'vibe' (e.g., RED) to the Adafruit feed
# # #     aio.send_data('lamp-command', vibe)

# # #     return {"status": "Success", "sent_to": receiver_id, "color": vibe}

# # # @app.get("/")
# # # def home():
# # #     return {"message": "The LDR Server is Running!"}

