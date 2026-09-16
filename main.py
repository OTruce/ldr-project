import os
import random
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query, Body
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from Adafruit_IO import Client
from fastapi import UploadFile, File, Form
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


## Weekly lovenotes

class LoveNote(Base):
    __tablename__ = "lovenotes"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    ldrid = Column(String)
    receiver_id = Column(String)
    lovenote = Column(Text)

# Send or overwrite a note
@app.post("/love-notes")
async def create_love_note(sender_id: str, receiver_id: str, note: str):
    db = SessionLocal()
    try:
        new_note = LoveNote(ldrid=sender_id, receiver_id=receiver_id, lovenote=note)
        db.add(new_note)
        db.commit()
        return {"status": "SUCCESS"}
    finally:
        db.close()

# Fetch active notes between two partners (last 7 days)
@app.get("/love-notes/active")
async def get_active_love_notes(user1_id: str, user2_id: str):
    db = SessionLocal()
    try:
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        notes = db.query(LoveNote).filter(
            LoveNote.created_at >= seven_days_ago,
            or_(
                (LoveNote.ldrid == user1_id) & (LoveNote.receiver_id == user2_id),
                (LoveNote.ldrid == user2_id) & (LoveNote.receiver_id == user1_id)
            )
        ).order_by(LoveNote.created_at.desc()).all()
        return notes
    finally:
        db.close()

## Locket posts(random pics that last 24 hours with captions and reactions)

class LocketPost(Base):
    __tablename__ = "locket_posts"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    sender_id = Column(String)
    receiver_id = Column(String)
    image_url = Column(Text)
    caption = Column(String, nullable=True)
    reaction = Column(String, nullable=True)

# 1. Post a new Locket image
@app.post("/locket/post")
async def create_locket_post(
    sender_id: str = Form(...),
    receiver_id: str = Form(...),
    caption: str = Form(None),
    image_url: str = Form(...) # URL returned after uploading to Supabase Storage
):
    db = SessionLocal()
    try:
        post = LocketPost(
            sender_id=sender_id,
            receiver_id=receiver_id,
            image_url=image_url,
            caption=caption
        )
        db.add(post)
        db.commit()
        return {"status": "SUCCESS"}
    finally:
        db.close()

# 2. Get active posts (last 24 hours only)
@app.get("/locket/active")
async def get_active_locket_posts(user_id: str, partner_id: str):
    db = SessionLocal()
    try:
        twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
        posts = db.query(LocketPost).filter(
            LocketPost.created_at >= twenty_four_hours_ago,
            or_(
                (LocketPost.sender_id == user_id) & (LocketPost.receiver_id == partner_id),
                (LocketPost.sender_id == partner_id) & (LocketPost.receiver_id == user_id)
            )
        ).order_by(LocketPost.created_at.desc()).all()
        return posts
    finally:
        db.close()

# 3. React to a photo
@app.post("/locket/react")
async def react_to_post(post_id: int, reaction: str):
    valid_reactions = {"HEART", "HEART_EYES", "LAUGH", "THUMBS_UP", "CRY"}
    if reaction not in valid_reactions:
        raise HTTPException(status_code=400, detail="Invalid reaction type")

    db = SessionLocal()
    try:
        post = db.query(LocketPost).filter(LocketPost.id == post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        post.reaction = reaction
        db.commit()
        return {"status": "SUCCESS", "reaction": reaction}
    finally:
        db.close()

# Include your existing request-otp, verify-otp, and health routes here...