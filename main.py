import os
import random
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query, Body
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, text, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from Adafruit_IO import Client
from fastapi import UploadFile, File, Form
import resend
import uuid
from fastapi import UploadFile, File, Form, HTTPException
from supabase import create_client, Client as SupabaseClient

# --- 1. SETUP ---
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"sslmode": "require"})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
resend.api_key = os.getenv("RESEND_API_KEY")


# Supabase Storage Client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)




# --- 2. MODELS ---

class User(Base):
    __tablename__ = "users"
    ldrid = Column(String, primary_key=True)
    name = Column(String)
    email = Column(String, unique=True)
    deviceid = Column(String)
    phone = Column(String, nullable=True)
    image_url = Column(String, nullable=True) # Profile picture URL
    gender = Column(String, default="male")   # "male" or "female"

class Relationship(Base):
    __tablename__ = "relationships"
    id = Column(Integer, primary_key=True)
    user1ldrid = Column(String)
    user2ldrid = Column(String)
    relationship = Column(String)

class Device(Base):
    __tablename__ = "devices"
    deviceid = Column(String, primary_key=True)
    connection = Column(String, default="offline") # "online" or "offline"
    bt_status = Column(String, default="inactive") # "active" or "inactive"

class TextColor(Base):
    __tablename__ = "text_colors"
    textid = Column(Integer, primary_key=True)
    text = Column(String)
    color = Column(String)
    color_code = Column(String) # The Hex code e.g. #FF0000
    emoji_male = Column(String, nullable=True)   # URL for male user
    emoji_female = Column(String, nullable=True) # URL for female user

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


# Code for OTP checks
# ==========================================
# 1. HEALTH CHECK ROUTE (For Keep-Alive & Cron)
# ==========================================
@app.get("/")
@app.get("/health")
def health_check():
    """Tiny endpoint for WorkManager and Cron-job to keep the server awake."""
    return "ok"


# ==========================================
# 2. REQUEST OTP ROUTE (Generates & Emails Code)
# ==========================================
@app.post("/request-otp")
async def request_otp(email: str):
    db = SessionLocal()
    try:
        clean_email = email.strip().lower()

        # Generate a secure random 6-digit code
        otp_val = str(random.randint(100000, 999999))

        # Save or update the OTP in the otp_codes table
        db.execute(text(
            "INSERT INTO otp_codes (email, code) VALUES (:e, :c) "
            "ON CONFLICT (email) DO UPDATE SET code = :c"
        ), {"e": clean_email, "c": otp_val})
        db.commit()

        # Send email via Resend
        try:
            resend.Emails.send({
                "from": "LDR Lamp <onboarding@resend.dev>",
                "to": [clean_email],
                "subject": f"{otp_val} is your login code",
                "html": f"""
                    <div style="font-family: sans-serif; padding: 20px;">
                        <h2>Your Login Code</h2>
                        <p style="font-size: 32px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;">
                            {otp_val}
                        </p>
                        <p style="color: #6B7280;">Enter this code in your LDR App to log in.</p>
                    </div>
                """
            })
        except Exception as email_err:
            print(f"Resend Email Error: {email_err}")
            raise HTTPException(status_code=500, detail=f"Failed to send email: {str(email_err)}")

        return {"status": "OTP_SENT"}
    finally:
        db.close()


# ==========================================
# 3. VERIFY OTP ROUTE (Logs In & Returns User Profile)
# ==========================================
@app.post("/verify-otp")
async def verify_otp(email: str, otp: str):
    db = SessionLocal()
    try:
        clean_email = email.strip().lower()

        # Check if the code matches what we stored
        record = db.query(OtpCode).filter(OtpCode.email == clean_email).first()

        if record and record.code == otp.strip():
            # Code is valid! Now retrieve user profile
            user = db.query(User).filter(User.email.ilike(clean_email)).first()

            # Delete the used code so it cannot be reused
            db.delete(record)
            db.commit()

            if user:
                return {
                    "status": "SUCCESS",
                    "ldrid": user.ldrid,
                    "name": user.name,
                    "gender": user.gender if user.gender else "male",
                    "image_url": user.image_url
                }
            else:
                # Fallback if user record wasn't manually created yet
                return {
                    "status": "SUCCESS",
                    "ldrid": "guest",
                    "name": "User",
                    "gender": "male",
                    "image_url": None
                }

        raise HTTPException(status_code=401, detail="Invalid OTP code")
    finally:
        db.close()

# --- 3. ROUTES ---

@app.get("/get-partners")
async def get_partners(my_id: str):
    db = SessionLocal()
    try:
        rel_list = db.query(Relationship).filter(
            or_(Relationship.user1ldrid == my_id, Relationship.user2ldrid == my_id)
        ).all()
        
        partners = []
        for rel in rel_list:
            p_id = rel.user2ldrid if rel.user1ldrid == my_id else rel.user1ldrid
            p_user = db.query(User).filter(User.ldrid == p_id).first()
            if p_user:
                # Check connection status from devices table
                device_rec = db.query(Device).filter(Device.deviceid == p_user.deviceid).first()
                conn_status = device_rec.connection if device_rec and device_rec.connection else "offline"
                bluetooth = device_rec.bt_status if device_rec and device_rec.bt_status else "inactive"

                partners.append({
                    "name": p_user.name,
                    "ldrid": p_user.ldrid,
                    "type": rel.relationship,
                    "image_url": p_user.image_url,
                    "connection": conn_status.lower(), # "online" or "offline"
                    "bt_status": bluetooth.lower() #"nearby" or "away"
                })
        return partners
    finally:
        db.close()

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

# 1. SEND NOTE: Rejects if you already sent a note in the last 7 days
@app.post("/love-notes")
async def create_love_note(sender_id: str, receiver_id: str, note: str):
    db = SessionLocal()
    try:
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        
        # Check if an active note from this sender already exists
        existing_active = db.query(LoveNote).filter(
            LoveNote.ldrid == sender_id,
            LoveNote.receiver_id == receiver_id,
            LoveNote.created_at >= seven_days_ago
        ).first()

        if existing_active:
            raise HTTPException(
                status_code=400, 
                detail="You have already posted an active note for this week."
            )

        new_note = LoveNote(ldrid=sender_id, receiver_id=receiver_id, lovenote=note)
        db.add(new_note)
        db.commit()
        return {"status": "SUCCESS"}
    finally:
        db.close()


# 2. GET ACTIVE NOTES: Orders by newest first (latest note always wins)
@app.get("/love-notes/active")
async def get_active_love_notes(user1_id: str, user2_id: str):
    db = SessionLocal()
    try:
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        
        # Newest first (.desc()) ensures the latest message takes priority
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
    image_url = Column(Text)
    caption = Column(String, nullable=True)

class LocketView(Base):
    __tablename__ = "locket_views"
    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer)
    viewer_id = Column(String)
    viewed_at = Column(DateTime(timezone=True), default=datetime.utcnow)

class LocketReaction(Base):
    __tablename__ = "locket_reactions"
    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer)
    reactor_id = Column(String)
    reaction = Column(String)

@app.post("/locket/upload")
async def upload_locket_photo(
    sender_id: str = Form(...),
    caption: str = Form(None),
    file: UploadFile = File(...)
):
    db = SessionLocal()
    try:
        file_bytes = await file.read()
        file_ext = file.filename.split(".")[-1].lower() if "." in file.filename else "jpg"
        file_name = f"{sender_id}_{uuid.uuid4().hex[:8]}.{file_ext}"

        is_video = file_ext in ["mp4", "mov", "mkv", "3gp"]
        media_type = "VIDEO" if is_video else "IMAGE"
        content_type = "video/mp4" if is_video else "image/jpeg"

        supabase.storage.from_("locket_images").upload(
            file_name,
            file_bytes,
            file_options={"content-type": content_type}
        )
        public_url = supabase.storage.from_("locket_images").get_public_url(file_name)

        post = LocketPost(
            sender_id=sender_id,
            image_url=public_url,
            caption=caption,
            media_type=media_type
        )
        db.add(post)
        db.commit()
        return {"status": "SUCCESS", "post_id": post.id, "image_url": public_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# 2. GET ACTIVE UNOPENED STACK (Only unviewed posts from partners)
@app.get("/locket/stack")
async def get_locket_stack(my_id: str):
    db = SessionLocal()
    try:
        # Find all partners
        relations = db.query(Relationship).filter(
            or_(Relationship.user1ldrid == my_id, Relationship.user2ldrid == my_id)
        ).all()
        partner_ids = [r.user2ldrid if r.user1ldrid == my_id else r.user1ldrid for r in relations]
        if not partner_ids:
            return []

        # Find posts already opened by me
        viewed_subquery = db.query(LocketView.post_id).filter(LocketView.viewer_id == my_id)

        # Fetch unviewed posts from partners
        unopened_posts = db.query(LocketPost).filter(
            LocketPost.sender_id.in_(partner_ids),
            ~LocketPost.id.in_(viewed_subquery)
        ).order_by(LocketPost.created_at.asc()).all() # Oldest first so user views in order

        stack = []
        for p in unopened_posts:
            author = db.query(User).filter(User.ldrid == p.sender_id).first()
            my_rx = db.query(LocketReaction).filter(
                LocketReaction.post_id == p.id,
                LocketReaction.reactor_id == my_id
            ).first()
            stack.append({
                "id": p.id,
                "sender_id": p.sender_id,
                "sender_name": author.name if author else "Partner",
                "image_url": p.image_url,
                "caption": p.caption,
                "created_at": p.created_at.isoformat(),
                "my_reaction": my_rx.reaction if my_rx else None
            })
        return stack
    finally:
        db.close()


# 3. MARK POST AS VIEWED (Makes it disappear from the active stack)
@app.post("/locket/mark-viewed")
async def mark_viewed(post_id: int = Query(...), viewer_id: str = Query(...)):
    db = SessionLocal()
    try:
        exists = db.query(LocketView).filter(
            LocketView.post_id == post_id,
            LocketView.viewer_id == viewer_id
        ).first()
        if not exists:
            db.add(LocketView(post_id=post_id, viewer_id=viewer_id))
            db.commit()
        return {"status": "SUCCESS"}
    finally:
        db.close()


# 4. REACT TO POST
@app.post("/locket/react")
async def react_to_post(post_id: int = Query(...), reactor_id: str = Query(...), reaction: str = Query(...)):
    db = SessionLocal()
    try:
        existing = db.query(LocketReaction).filter(
            LocketReaction.post_id == post_id,
            LocketReaction.reactor_id == reactor_id
        ).first()
        if existing:
            existing.reaction = reaction
        else:
            db.add(LocketReaction(post_id=post_id, reactor_id=reactor_id, reaction=reaction))
        db.commit()
        return {"status": "SUCCESS"}
    finally:
        db.close()


# 5. MY HISTORY (Everything I posted)
@app.get("/locket/my-history")
async def get_my_history(my_id: str):
    db = SessionLocal()
    try:
        posts = db.query(LocketPost).filter(
            LocketPost.sender_id == my_id
        ).order_by(LocketPost.created_at.desc()).all()

        history = []
        for p in posts:
            reactions = db.query(LocketReaction).filter(LocketReaction.post_id == p.id).all()
            history.append({
                "id": p.id,
                "image_url": p.image_url,
                "caption": p.caption,
                "created_at": p.created_at.isoformat(),
                "reactions": [r.reaction for r in reactions]
            })
        return history
    finally:
        db.close()


# 6. DELETE A POST
@app.delete("/locket/delete/{post_id}")
async def delete_post(post_id: int, my_id: str = Query(...)):
    db = SessionLocal()
    try:
        post = db.query(LocketPost).filter(LocketPost.id == post_id, LocketPost.sender_id == my_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found or unauthorized")
        db.delete(post)
        db.commit()
        return {"status": "DELETED"}
    finally:
        db.close()


# GET MEMORIES FEED (EACH MEMORY LASTS 60 DAYS)
@app.get("/memories/feed")
async def get_memories_feed(my_id: str):
    db = SessionLocal()
    try:
        # 1. Find all partners
        relations = db.query(Relationship).filter(
            or_(Relationship.user1ldrid == my_id, Relationship.user2ldrid == my_id)
        ).all()
        partner_ids = [r.user2ldrid if r.user1ldrid == my_id else r.user1ldrid for r in relations]
        all_involved_ids = partner_ids + [my_id]

        # 2. Get posts from the last 60 days
        sixty_days_ago = datetime.utcnow() - timedelta(days=60)
        posts = db.query(LocketPost).filter(
            LocketPost.sender_id.in_(all_involved_ids),
            LocketPost.created_at >= sixty_days_ago
        ).order_by(LocketPost.created_at.desc()).all()

        results = []
        for post in posts:
            author = db.query(User).filter(User.ldrid == post.sender_id).first()
            is_owner = (post.sender_id == my_id)

            # ONLY the owner can see who reacted and what their reaction was
            reactions_data = []
            if is_owner:
                rx_records = db.query(LocketReaction).filter(LocketReaction.post_id == post.id).all()
                for rx in rx_records:
                    reactor = db.query(User).filter(User.ldrid == rx.reactor_id).first()
                    reactions_data.append({
                        "reactor_name": reactor.name if reactor else "Partner",
                        "reaction": rx.reaction
                    })

            # Detect if media is video or image
            media_type = getattr(post, 'media_type', 'IMAGE')
            if not media_type:
                media_type = 'VIDEO' if any(post.image_url.lower().endswith(ext) for ext in ['.mp4', '.mov', '.mkv']) else 'IMAGE'

            results.append({
                "id": post.id,
                "sender_id": post.sender_id,
                "sender_name": author.name if author else "Partner",
                "media_url": post.image_url,
                "media_type": media_type,
                "caption": post.caption,
                "created_at": post.created_at.strftime("%b %d, %Y"),
                "is_owner": is_owner,
                "reactions": reactions_data
            })
        return results
    finally:
        db.close()


# Include your existing request-otp, verify-otp, and health routes here...