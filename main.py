# import os
import random
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, Body
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from Adafruit_IO import Client
import resend

# --- 1. SETUP ---
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"sslmode": "require"})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- 2. MODELS ---

class User(Base):
    __tablename__ = "users"
    ldrid = Column(String, primary_key=True)
    name = Column(String)
    email = Column(String, unique=True)
    deviceid = Column(String)

class Relationship(Base):
    __tablename__ = "relationships"
    id = Column(Integer, primary_key=True)
    user1ldrid = Column(String)
    user2ldrid = Column(String)
    relationship = Column(String)

class TextColor(Base):
    __tablename__ = "text_colors"
    textid = Column(Integer, primary_key=True)
    text = Column(String)
    color = Column(String)
    color_code = Column(String) # The Hex code e.g. #FF0000

class VibeLog(Base):
    __tablename__ = "vibe_logs"
    id = Column(Integer, primary_key=True, index=True)
    sender_ldrid = Column(String)
    receiver_ldrid = Column(String)
    vibe_type = Column(String)
    hex_color = Column(String)
    status = Column(String, default="pending")
    timestamp = Column(DateTime, default=datetime.utcnow)

class OtpCode(Base):
    __tablename__ = "otp_codes"
    email = Column(String, primary_key=True)
    code = Column(String)

app = FastAPI()

# --- 3. ROUTES ---

@app.get("/get-partners")
async def get_partners(my_id: str):
    db = SessionLocal()
    rel_list = db.query(Relationship).filter(or_(Relationship.user1ldrid == my_id, Relationship.user2ldrid == my_id)).all()
    partners = []
    for rel in rel_list:
        p_id = rel.user2ldrid if rel.user1ldrid == my_id else rel.user1ldrid
        p_user = db.query(User).filter(User.ldrid == p_id).first()
        if p_user:
            partners.append({"name": p_user.name, "ldrid": p_user.ldrid, "type": rel.relationship})
    db.close()
    return partners

@app.get("/get-text-colors")
async def get_vibes():
    db = SessionLocal()
    vibes = db.query(TextColor).all()
    db.close()
    return vibes

@app.get("/send-vibe")
async def send_vibe(from_id: str, to_id: str, vibe_text: str):
    db = SessionLocal()
    try:
        # 1. Lookup Hex code from the text_colors table
        vibe_info = db.query(TextColor).filter(TextColor.text == vibe_text).first()
        hex_color = vibe_info.color_code if vibe_info else "#FFFFFF"

        # 2. Find receiver's lamp
        receiver = db.query(User).filter(User.ldrid == to_id).first()
        if not receiver or not receiver.deviceid:
            raise HTTPException(status_code=404, detail="Device not found")

        # 3. Log to database
        new_log = VibeLog(sender_ldrid=from_id, receiver_ldrid=to_id, vibe_type=vibe_text, hex_color=hex_color)
        db.add(new_log)
        db.commit()

        # 4. Push to Adafruit
        aio = Client(os.getenv("AIO_USERNAME"), os.getenv("AIO_KEY"))
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, aio.send_data, receiver.deviceid, hex_color)

        return {"status": "Success"}
    finally:
        db.close()

# Include your existing request-otp, verify-otp, and health routes here...