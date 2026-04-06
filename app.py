from __future__ import annotations

import os
import re
import time
import uuid
import argparse
from functools import wraps
from typing import Iterator
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv() # Load variables from .env if it exists

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_from_directory
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import cv2

# --- HEADLESS PRODUCTION SHIM: Prevent YOLOv8 from crashing on Render ---
if not hasattr(cv2, 'imshow'):
    def _fake_imshow(winname, mat): pass
    cv2.imshow = _fake_imshow
if not hasattr(cv2, 'waitKey'):
    def _fake_waitKey(delay=0): return -1
    cv2.waitKey = _fake_waitKey
if not hasattr(cv2, 'destroyAllWindows'):
    def _fake_destroy(): pass
    cv2.destroyAllWindows = _fake_destroy
# ------------------------------------------------------------------------

import base64
import numpy as np
import threading as _threading
import collections as _collections
import datetime as _datetime


from utils.database import EventLogger
from utils.pipeline import SafetyPipeline

UPLOAD_FOLDER = 'static/uploads/chat'
ALLOWED_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', # Images
    'webm', 'wav', 'mp3', 'ogg',                 # Audio
    'mp4', 'mov', 'avi', 'mkv',                  # Video
    'pdf', 'doc', 'docx', 'xls', 'xlsx',         # Documents
    'ppt', 'pptx', 'txt'                         # More Docs
}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me-9e8f7d6c")

# SMTP Configuration (Uses environment variables for security)
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'example@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '') # Must be set in .env
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', app.config['MAIL_USERNAME'])

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

pipeline = SafetyPipeline(
    camera_index=int(os.environ.get("CAMERA_INDEX", 1)),
    width=640,
    height=480,
    drowsy_warning_s=2.0,
    drowsy_danger_s=5.0,
    phone_warning_s=3.0,
    phone_danger_s=7.0,
    phone_critical_s=12.0,
    phone_detect_every_n=1,
    voice_repeat_s=3.0,
    db_path="database.db",
    enable_server_tts=False,
    ear_threshold=0.21,
    frame_buffer_size=100
)

# ---------------------------------------------------------------------------
# Incident Media Recorder  (lives in app.py — frame is fresh here, never black)
# ---------------------------------------------------------------------------
class IncidentRecorder:
    """Captures incident snapshots and videos directly from the request thread.
    Frames decoded in api_process_frame are guaranteed valid — no pipeline
    thread-hops, no numpy memory aliasing, no black frames."""

    VIDEO_FRAMES  = 60          # collect 60 post-incident frames (~12s at ~5fps)
    JPEG_QUALITY  = 92
    UPLOAD_DIR    = "static/uploads/chat"
    FPS_OUT       = 6.0         # video playback fps

    def __init__(self):
        self._lock = _threading.Lock()
        self._recording     = {}
        self._frames        = {}
        self._inc_type      = {}
        self._prev_level    = {}
        
        # New time-based tracking
        self._start_time    = {}
        self._last_frame_ts = {}

    def on_frame(self, driver_id, frame: np.ndarray, status) -> None:
        """Call once per decoded frame. Handles snapshot + video recording."""
        cur_level = status.overall_level
        import time 

        with self._lock:
            # ---- NEW INCIDENT: level >= 1 AND we are NOT currently in a 15s recording window ----
            if cur_level >= 1 and not self._recording.get(driver_id):
                ev_type  = "DROWSY" if status.drowsiness_level > 0 else "PHONE"
                sev      = {1:"WARNING", 2:"DANGER", 3:"CRITICAL"}.get(cur_level, "WARNING")
                dur      = status.drowsiness_duration_s if ev_type=="DROWSY" else status.phone_duration_s
                ts_str   = _datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                print(f"[Recorder] Incident started: {ev_type} lvl={cur_level} dur={dur:.1f}s")

                # --- SNAPSHOT: save synchronously right now (frame is valid here) ---
                snap_path = self._write_snapshot(frame, ev_type, sev, dur, ts_str)

                # --- START 15-SECOND VIDEO RECORDING ---
                self._recording[driver_id]     = True
                self._frames[driver_id]        = [frame.copy()]
                self._inc_type[driver_id]      = ev_type
                
                now = time.time()
                self._start_time[driver_id]    = now
                self._last_frame_ts[driver_id] = now
                self._prev_level[driver_id]    = cur_level

                # Upload snapshot + notify org in background (exactly ONE text & snapshot pair)
                if snap_path:
                    _threading.Thread(
                        target=self._notify_org,
                        args=(driver_id, snap_path, ev_type, sev, dur, ts_str),
                        daemon=True
                    ).start()

            # ---- COLLECTING VIDEO FRAMES ----
            elif self._recording.get(driver_id):
                now = time.time()
                elapsed = now - self._start_time[driver_id]
                
                # If 15 seconds have passed, stop and encode
                if elapsed >= self.VIDEO_DURATION_S:
                    frames_copy = list(self._frames[driver_id])
                    inc_type    = self._inc_type[driver_id]
                    self._recording[driver_id] = False
                    self._frames[driver_id]    = []
                    _threading.Thread(
                        target=self._encode_and_send_video,
                        args=(driver_id, frames_copy, inc_type),
                        daemon=True
                    ).start()
                else:
                    # Throttle frame capture to match FPS_OUT for realistic playback speed
                    if (now - self._last_frame_ts[driver_id]) >= (1.0 / self.FPS_OUT):
                        self._frames[driver_id].append(frame.copy())
                        self._last_frame_ts[driver_id] = now

            # ---- RESET: only go to 0 when level is genuinely safe AND recording is done ----
            if cur_level == 0 and not self._recording.get(driver_id):
                self._prev_level[driver_id] = 0

    # ------------------------------------------------------------------
    def _write_snapshot(self, frame, ev_type, sev, dur, ts_str):
        """Annotate and write JPEG. Called in request thread — frame is valid."""
        try:
            os.makedirs(self.UPLOAD_DIR, exist_ok=True)
            label = f"[!] {ev_type} | {sev} | {dur:.1f}s"
            ann   = self._annotate(frame.copy(), label, ts_str)
            name  = f"snap_{uuid.uuid4().hex[:8]}.jpg"
            path  = os.path.join(self.UPLOAD_DIR, name)
            ok    = cv2.imwrite(path, ann, [cv2.IMWRITE_JPEG_QUALITY, self.JPEG_QUALITY])
            if ok:
                print(f"[Recorder] Snapshot OK: {path}  "
                      f"pixels min={ann.min()} max={ann.max()}")
                return path
            print(f"[Recorder] imwrite failed: {path}")
        except Exception as e:
            print(f"[Recorder] Snapshot error: {e}")
        return None

    def _encode_and_send_video(self, driver_id, frames, inc_type):
        """Encode frames to WebM (vp80) and notify org. Background thread.
        WebM is requested because OpenCV Windows lacks H.264 (avc1) natively,
        and mp4v (MPEG-4 Part 2) won't play in most browsers."""
        try:
            os.makedirs(self.UPLOAD_DIR, exist_ok=True)
            ts_str   = _datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            filename = f"incident_{uuid.uuid4().hex[:8]}.webm"
            path     = os.path.join(self.UPLOAD_DIR, filename)
            vw, vh   = 640, 480

            # Use WebM VP8 tightly compressed for browser compatibility
            fourcc = cv2.VideoWriter_fourcc(*'vp80')
            out    = cv2.VideoWriter(path, fourcc, self.FPS_OUT, (vw, vh))
            
            if not out.isOpened():
                print(f"[Recorder] VideoWriter failed — codec vp80 unavailable")
                return

            label = f"[!] {inc_type}"
            for f in frames:
                sized = cv2.resize(f, (vw, vh), interpolation=cv2.INTER_AREA)
                ann   = self._annotate(sized, label, ts_str)
                out.write(ann)
            out.release()
            print(f"[Recorder] Video encoded: {path} ({len(frames)} frames)")

            # Upload & send
            from utils.cloud_storage import upload_file
            cloud_url  = upload_file(path, resource_type="video")
            
            # Force Cloudinary to transcode to H.264 MP4 for universal browser playback
            if cloud_url and 'cloudinary.com' in cloud_url:
                cloud_url = cloud_url.rsplit('.', 1)[0] + '.mp4'
                
            final_url  = cloud_url or f"/{path.replace(os.sep, '/')}"
            dur_s      = len(frames) / self.FPS_OUT
            driver = self._fetch_driver(driver_id)
            if driver and driver["organisation_id"]:
                org_id   = driver["organisation_id"]
                drv_name = driver["name"]
                ts       = _datetime.datetime.now().isoformat(timespec="seconds")
                vid_msg  = (
                    f"[VIDEO] INCIDENT RECORDED\n"
                    f"Driver : {drv_name} | {inc_type}\n"
                    f"Duration: {dur_s:.0f}s | Captured: {ts_str}"
                )
                logger.save_message("driver", driver_id, "org", org_id, vid_msg,  ts, message_type="text")
                logger.save_message("driver", driver_id, "org", org_id, final_url, ts, message_type="video")
                print(f"[Recorder] Video sent to org {org_id}: {final_url}")
        except Exception as e:
            print(f"[Recorder] Video error: {e}")

    def _notify_org(self, driver_id, snap_path, ev_type, sev, dur, ts_str):
        """Upload snapshot and send text + image to org chat. Background thread."""
        try:
            from utils.cloud_storage import upload_file
            cloud_url  = upload_file(snap_path, resource_type="image")
            final_url  = cloud_url or f"/{snap_path.replace(os.sep, '/')}"

            driver = self._fetch_driver(driver_id)
            if driver and driver["organisation_id"]:
                org_id   = driver["organisation_id"]
                drv_name = driver["name"]
                ts       = _datetime.datetime.now().isoformat(timespec="seconds")
                alert    = (
                    f"[ALERT] INCIDENT DETECTED\n"
                    f"Driver  : {drv_name}\n"
                    f"Type    : {ev_type} | Severity: {sev}\n"
                    f"Duration: {dur:.1f}s | Time: {ts_str}\n"
                    f"[snapshot attached]"
                )
                logger.save_message("driver", driver_id, "org", org_id, alert,     ts, message_type="text")
                logger.save_message("driver", driver_id, "org", org_id, final_url, ts, message_type="image")
                print(f"[Recorder] Snapshot sent to org {org_id}")
        except Exception as e:
            print(f"[Recorder] Notify error: {e}")

    def _fetch_driver(self, driver_id):
        try:
            with logger._connect() as conn:
                return conn.execute(
                    "SELECT organisation_id, name FROM drivers WHERE id=%s", (driver_id,)
                ).fetchone()
        except Exception:
            pass
        try:
            with logger._connect() as conn:
                return conn.execute(
                    "SELECT organisation_id, name FROM drivers WHERE id=?", (driver_id,)
                ).fetchone()
        except Exception:
            return None

    @staticmethod
    def _annotate(frame: np.ndarray, label: str, ts: str) -> np.ndarray:
        """Draw incident label + timestamp banners. ASCII only (cv2 limitation)."""
        h, w = frame.shape[:2]
        cv2.rectangle(frame, (0, 0), (w, 42), (15, 15, 15), -1)
        cv2.putText(frame, label, (8, 29), cv2.FONT_HERSHEY_SIMPLEX,
                    0.72, (30, 80, 230), 2, cv2.LINE_AA)
        cv2.rectangle(frame, (0, h - 28), (w, h), (15, 15, 15), -1)
        cv2.putText(frame, ts,    (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.50, (210, 210, 210), 1, cv2.LINE_AA)
        return frame

incident_recorder = IncidentRecorder()

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('.', 'manifest.json', mimetype='application/manifest+json')

@app.route('/sw.js')
def serve_sw():
    return send_from_directory('.', 'sw.js', mimetype='application/javascript')

logger = EventLogger("database.db")

# In-memory store for driver live status (so we don't spam DB)
# driver_id -> { "timestamp": ..., "drowsy": ..., "phone": ..., "speed": ..., "risk": ..., "overall": ... }
LIVE_STATUS_DB = {}

if not logger.get_admin_by_username("admin"):
    logger.create_admin("admin", generate_password_hash("admin"))

def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session or "role" not in session:
                return redirect(url_for("home"))
            if role and session.get("role") != role:
                flash("Unauthorized access.", "danger")
                return redirect(url_for("home"))
            
            # Real-time status check
            uid = session["user_id"]
            if session["role"] == "driver":
                driver = logger.get_driver_by_id(uid)
                if not driver or driver.status == 'blocked':
                    session.clear()
                    flash("Your account has been restricted. Please contact support.", "danger")
                    return redirect(url_for("home"))
            elif session["role"] == "org":
                org = logger.get_organisation_by_id(uid)
                if not org or org.status == 'blocked':
                    session.clear()
                    flash("Your organisation account has been restricted.", "danger")
                    return redirect(url_for("home"))
                    
            return view(*args, **kwargs)
        return wrapped
    return decorator

def is_strong_password(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r'[a-z]', password): # Added missing lowercase check
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r'\d', password):
        return False, "Password must contain at least one number."
    if not re.search(r'[!@#$%^&*(),.?":{}|<>+=\[\]\\;\'/`~_]', password):
        return False, "Password must contain at least one special character."
    return True, ""

def send_reset_email(to_email, reset_link):
    msg = MIMEMultipart()
    msg['From'] = f"Driver Safety AI <{app.config['MAIL_USERNAME']}>"
    msg['To'] = to_email
    msg['Subject'] = "Password Reset Request"

    html = render_template("forgot_password_email.html", reset_link=reset_link)
    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT']) as server:
            server.starttls()
            server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

# --- Landing & Auth ---
@app.get("/")
def home():
    if "role" in session:
        role = session["role"]
        if role == "admin": return redirect(url_for("admin_dashboard"))
        elif role == "org": return redirect(url_for("org_dashboard"))
        elif role == "driver": return redirect(url_for("driver_dashboard"))
    return render_template("landing.html")

@app.post("/logout")
def logout():
    session.clear()
    flash("Logout successful.", "info")
    return redirect(url_for("home"))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin = logger.get_admin_by_username(username)
        if admin and check_password_hash(admin.password_hash, password):
            session["user_id"] = admin.id
            session["role"] = "admin"
            flash("Welcome back, Admin!", "success")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin credentials.", "danger")
    return render_template("admin_login.html")

@app.get("/admin/dashboard")
@login_required(role="admin")
def admin_dashboard():
    return render_template("admin_dashboard.html")

@app.route("/org/register", methods=["GET", "POST"])
def org_register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if password != confirm:
            flash("Passwords do not match.", "danger")
        else:
            is_strong, msg = is_strong_password(password)
            if not is_strong:
                flash(msg, "danger")
            else:
                pw_hash = generate_password_hash(password)
                org_code = logger.create_organisation(name, email, pw_hash)
                if org_code:
                    flash(f"Organisation created! Your Org Code is {org_code}. You can share this with drivers.", "success")
                    return redirect(url_for("org_login"))
                else:
                    flash("An error occurred. Please try again.", "danger")
    return render_template("org_register.html")

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        role = request.args.get("role", "")
        user = None
        
        if role == "driver":
            user = logger.get_driver_by_email(email)
        elif role == "org":
            user = logger.get_organisation_by_email(email)
        else:
            # Fallback if no role specified
            user = logger.get_driver_by_email(email)
            role = "driver"
            if not user:
                user = logger.get_organisation_by_email(email)
                role = "org"
        
        if user:
            token = uuid.uuid4().hex
            # Set expiry to 1 hour from now
            expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            logger.create_reset_token(email, token, expires_at, role)
            
            # Send actual email
            reset_url = url_for("reset_password", token=token, _external=True)
            if send_reset_email(email, reset_url):
                flash("A password reset link has been sent to your email.", "success")
            else:
                flash("Failed to send reset email. Please try again later.", "danger")
        else:
            role_msg = "Organisation" if role == "org" else "Driver"
            flash(f"That email is not registered as an {role_msg}.", "danger")
        return redirect(url_for("forgot_password", role=role))
    return render_template("forgot_password.html")

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    reset_info = logger.get_reset_token_info(token)
    if not reset_info:
        flash("Invalid or expired reset token.", "danger")
        return redirect(url_for("home"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        
        if password != confirm:
            flash("Passwords do not match.", "danger")
        else:
            is_strong, msg = is_strong_password(password)
            if not is_strong:
                flash(msg, "danger")
            else:
                pw_hash = generate_password_hash(password)
                if reset_info["role"] == "driver":
                    logger.update_driver_password(reset_info["email"], pw_hash)
                else:
                    logger.update_organisation_password(reset_info["email"], pw_hash)
                
                logger.delete_reset_token(token)
                flash("Password has been reset successfully. Please log in.", "success")
                return redirect(url_for("home"))
                
    return render_template("reset_password.html", token=token)
@app.route("/org/login", methods=["GET", "POST"])
def org_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        org = logger.get_organisation_by_email(email)
        if org and check_password_hash(org.password_hash, password):
            if org.status != "active":
                flash("Organisation account is disabled. Contact Admin.", "danger")
                return redirect(url_for("org_login"))
            session["user_id"] = org.id
            session["role"] = "org"
            session["org_name"] = org.name
            flash(f"Welcome back, {org.name}!", "success")
            return redirect(url_for("org_dashboard"))
        flash("Invalid credentials.", "danger")
    return render_template("org_login.html")

@app.get("/org/dashboard")
@login_required(role="org")
def org_dashboard():
    org = logger.get_organisation_by_id(session["user_id"])
    return render_template("org_dashboard.html", org_name=org.name if org else "Organisation", can_clear=bool(org.can_clear_data if org else False))

@app.route("/driver/register", methods=["GET", "POST"])
def driver_register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        acc_type = request.form.get("account_type", "personal")
        org_code = request.form.get("org_code", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        
        if password != confirm:
            flash("Passwords do not match.", "danger")
        else:
            org_id = None
            status = 'active'
            
            if acc_type == 'organisation':
                if not org_code:
                    flash("Organisation Code is required for Organisation accounts.", "danger")
                    return redirect(url_for("driver_register"))
                org_record = logger.get_organisation_by_code(org_code)
                if not org_record:
                    flash("Invalid Organisation Code.", "danger")
                    return redirect(url_for("driver_register"))
                org_id = org_record["id"]
                
            is_strong, msg = is_strong_password(password)
            if not is_strong:
                flash(msg, "danger")
            else:
                pw_hash = generate_password_hash(password)
                if logger.create_driver(name, email, pw_hash, org_id, datetime.now().isoformat(), status=status):
                    flash("Driver account created. You can now login.", "success")
                    return redirect(url_for("driver_login"))
                else:
                    flash("Email already registered.", "danger")
                    
    return render_template("driver_register.html")

@app.route("/driver/login", methods=["GET", "POST"])
def driver_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        driver = logger.get_driver_by_email(email)
        if driver and check_password_hash(driver.password_hash, password):
            if driver.status == 'blocked':
                flash("Your account is currently blocked. Please contact support.", "danger")
                return redirect(url_for("driver_login"))
            session["user_id"] = driver.id
            session["role"] = "driver"
            session["driver_name"] = driver.name
            session["org_id"] = driver.organisation_id
            flash(f"Welcome back, {driver.name}!", "success")
            return redirect(url_for("driver_dashboard"))
        flash("Invalid credentials.", "danger")
    return render_template("driver_login.html")

@app.route("/signin", methods=["GET", "POST"])
def signin(): return driver_login()
@app.route("/signup", methods=["GET", "POST"])
def signup(): return driver_register()

@app.get("/driver/dashboard")
@login_required(role="driver")
def driver_dashboard():
    driver = logger.get_driver_by_id(session["user_id"])
    org_name = "Independent"
    orgs = []
    
    hired_org_id = None
    if driver and driver.organisation_id and driver.status == 'active':
        org = logger.get_organisation_by_id(driver.organisation_id)
        if org:
            org_name = org.name
            hired_org_id = org.id

    if org_name == "Independent":
        # Always allow applying to more orgs until hired
        orgs = [o for o in logger.get_all_organisations() if o['status'] == 'active']
        
    status = driver.status if driver else 'active'
    return render_template("dashboard.html", org_name=org_name, driver_status=status, orgs=orgs, hired_org_id=hired_org_id)

@app.get("/api/org/data")
@login_required(role="org")
def api_org_data():
    org_id = session["user_id"]
    org = logger.get_organisation_by_id(org_id)
    active_drivers = logger.get_drivers_by_org(org_id)
    pending_apps = logger.get_pending_applications_for_org(org_id)
    
    return jsonify({
        "org_code": org.org_code if org else "",
        "drivers": [
            {"id": d.id, "name": d.name, "email": d.email, "status": d.status}
            for d in active_drivers
        ],
        "pending": [
            {"id": a["id"], "name": a["name"], "email": a["email"]}
            for a in pending_apps
        ]
    })

@app.post("/api/driver/apply")
@login_required(role="driver")
def api_driver_apply():
    driver_id = session["user_id"]
    data = request.json
    org_id = data.get("org_id")
    if not org_id: return jsonify({"error": "No org selected"}), 400
    
    if logger.create_driver_application(driver_id, org_id):
        return jsonify({"success": True})
    return jsonify({"error": "Already applied to this organisation"}), 400

@app.get("/api/driver/applications")
@login_required(role="driver")
def api_driver_applications():
    apps = logger.get_driver_applications(session["user_id"])
    return jsonify(apps)

@app.post("/api/org/driver/<int:driver_id>/accept")
@login_required(role="org")
def api_accept_driver(driver_id):
    org_id = session["user_id"]
    org_name = session.get("org_name", "An organisation")
    with logger._connect() as conn:
        conn.execute("UPDATE drivers SET organisation_id = ?, status = 'active' WHERE id = ?", (org_id, driver_id))
        conn.execute("UPDATE driver_applications SET status = 'accepted' WHERE driver_id = ? AND organisation_id = ?", (driver_id, org_id))
        
        # Get other pending orgs for notifications
        cur = conn.execute("SELECT organisation_id FROM driver_applications WHERE driver_id = ? AND organisation_id != ? AND status = 'pending'", (driver_id, org_id))
        other_org_ids = [r[0] for r in cur.fetchall()]
        
        conn.execute("UPDATE driver_applications SET status = 'withdrawn' WHERE driver_id = ? AND organisation_id != ? AND status = 'pending'", (driver_id, org_id))
    
    # Notifications
    logger.create_notification(driver_id, "Application Accepted", f"{org_name} has accepted your application! You are now part of their fleet.", "success")
    for oid in other_org_ids:
        other_org = logger.get_organisation_by_id(oid)
        o_name = other_org.name if other_org else "Another organisation"
        logger.create_notification(driver_id, "Application Withdrawn", f"Your application to {o_name} was withdrawn because you joined {org_name}.", "info")
        
    return jsonify({"success": True})

@app.post("/api/org/driver/<int:driver_id>/remove")
@login_required(role="org")
def api_remove_driver(driver_id):
    org_id = session["user_id"]
    with logger._connect() as conn:
        conn.execute("DELETE FROM driver_applications WHERE driver_id = ? AND organisation_id = ?", (driver_id, org_id))
        conn.execute("UPDATE drivers SET organisation_id = NULL, status = 'active' WHERE id = ? AND organisation_id = ?", (driver_id, org_id))
    return jsonify({"success": True})

@app.get("/dashboard")
@login_required(role="driver")
def dashboard():
    return redirect(url_for("driver_dashboard"))

@app.get("/history")
@login_required(role="driver")
def history():
    events = logger.get_recent_events(limit=250, driver_id=session.get("user_id"))
    return render_template("history.html", events=events, driver_id=session.get("user_id"))

@app.get("/org/driver/<int:driver_id>/history")
@login_required(role="org")
def org_driver_history(driver_id):
    driver = logger.get_driver_by_id(driver_id)
    org_id = session["user_id"]
    
    # Check if active OR has a pending application
    is_authorized = False
    if driver:
        if driver.organisation_id == org_id:
            is_authorized = True
        else:
            apps = logger.get_driver_applications(driver_id)
            if any(a['organisation_id'] == org_id for a in apps):
                is_authorized = True

    if not is_authorized:
        flash("Unauthorized access to driver history.", "danger")
        return redirect(url_for("org_dashboard"))
    
    events = logger.get_recent_events(limit=100, driver_id=driver_id)
    org = logger.get_organisation_by_id(session["user_id"])
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    return render_template("history.html", events=events, driver_name=driver.name, driver_id=driver_id, is_org_view=True, date_now=now_str, can_clear=bool(org.can_clear_data))

# consolidated chat/history clear endpoints moved below to recruitment section

# --- ADMIN APIs ---
ADMIN_STATS_CACHE = {"data": None, "expiry": 0}

@app.get("/api/admin/stats")
@login_required(role="admin")
def api_admin_stats():
    global ADMIN_STATS_CACHE
    now = time.time()
    
    if ADMIN_STATS_CACHE["data"] and now < ADMIN_STATS_CACHE["expiry"]:
        return jsonify(ADMIN_STATS_CACHE["data"])
        
    try:
        stats = {
            "total_orgs": logger.get_all_organizations_count(),
            "total_drivers": logger.get_all_drivers_count(),
            "organisations": logger.get_all_organisations(),
            "drivers": logger.get_all_drivers()
        }
        ADMIN_STATS_CACHE = {
            "data": stats,
            "expiry": now + 30 # Cache for 30 seconds
        }
        return jsonify(stats)
    except Exception as e:
        print(f"[ADMIN STATS ERROR] {e}")
        # Return previous cache if possible, or error
        if ADMIN_STATS_CACHE["data"]: return jsonify(ADMIN_STATS_CACHE["data"])
        return jsonify({"error": str(e)}), 500

@app.post("/api/admin/driver/<int:driver_id>/status")
@login_required(role="admin")
def api_admin_driver_status(driver_id):
    data = request.json
    logger.update_driver_status(driver_id, data.get("status", "active"))
    return jsonify({"ok": True})

@app.post("/api/admin/driver/<int:driver_id>/delete")
@login_required(role="admin")
def api_admin_driver_delete(driver_id):
    logger.delete_driver(driver_id)
    return jsonify({"ok": True})

@app.post("/api/admin/org/<int:org_id>/status")
@login_required(role="admin")
def api_admin_org_status(org_id):
    data = request.json
    logger.update_organisation_status(org_id, data.get("status", "active"))
    return jsonify({"ok": True})

@app.post("/api/admin/org/<int:org_id>/delete")
@login_required(role="admin")
def api_admin_org_delete(org_id):
    logger.delete_organisation(org_id)
    return jsonify({"ok": True})

@app.post("/api/admin/org/<int:org_id>/toggle_clear")
@login_required(role="admin")
def api_admin_toggle_clear(org_id):
    new_val = logger.toggle_org_clear_permission(org_id)
    return jsonify({"ok": True, "can_clear": new_val})

# --- ORG APIs ---
@app.get("/api/org/driver_status/<int:driver_id>")
@login_required(role="org")
def api_org_driver_status(driver_id):
    status = LIVE_STATUS_DB.get(driver_id, {})
    return jsonify(status)

# --- DRIVER APIs ---
@app.post("/api/driver/status")
@login_required(role="driver")
def api_driver_status_update():
    data = request.json
    driver_id = session["user_id"]
    LIVE_STATUS_DB[driver_id] = {
        "timestamp": time.time(),
        "speed": data.get("speed"),
        "drowsiness": data.get("drowsiness"),
        "phone": data.get("phone"),
        "risk_score": data.get("risk_score"),
        "overall": data.get("overall")
    }
    return jsonify({"ok": True})

@app.get("/api/driver/events")
@login_required(role="driver")
def api_driver_events():
    limit = request.args.get("limit", 10, type=int)
    events = logger.get_recent_events(limit=limit, driver_id=session["user_id"])
    return jsonify([
        {
            "id": e.id,
            "ts": e.ts,
            "event_type": e.event_type,
            "severity": e.severity,
            "duration_s": e.duration_s,
            "message": e.message
        }
        for e in events
    ])

# --- CHAT APIs ---
@app.post("/api/chat/upload")
def api_chat_upload():
    role = session.get("role")
    if role not in ["admin", "org", "driver"]: return jsonify({"error": "unauthorized"}), 403
    
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    if file and allowed_file(file.filename):
        from werkzeug.utils import secure_filename
        # Generate a unique filename while preserving the original name for display
        ext = file.filename.rsplit('.', 1)[1].lower()
        original_base = secure_filename(file.filename.rsplit('.', 1)[0])
        new_filename = f"{uuid.uuid4().hex}_{original_base}.{ext}"
        
        # Ensure directory exists
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
        file.save(filepath)
        
        # Cloud Storage Integration
        from utils.cloud_storage import upload_file
        cloud_url = upload_file(filepath, resource_type="auto")
        
        if cloud_url:
            final_path = cloud_url
        else:
            final_path = f"/static/uploads/chat/{new_filename}"

        # Return the relative path for the chat message
        return jsonify({
            "ok": True, 
            "filepath": final_path,
            "original_name": file.filename
        })
    
    return jsonify({"error": "File type not allowed"}), 400

@app.post("/api/chat/send")
def api_chat_send():
    data = request.json
    role = session.get("role")
    if role not in ["admin", "org", "driver"]: return jsonify({"error": "unauthorized"}), 403
    
    sender_id = session["user_id"]
    message = data.get("message", "")
    message_type = data.get("message_type", "text")
    
    receiver_id = data.get("receiver_id")
    receiver_type = data.get("receiver_type")
    
    # Robustness/Compatibility check
    if role == "driver":
        if not receiver_type: receiver_type = "org"
        if not receiver_id: receiver_id = session.get("org_id")
    
    if not message or not receiver_id or not receiver_type:
        print(f"[DEBUG] Chat Send Failed: msg={bool(message)}, r_id={receiver_id}, r_type={receiver_type}")
        return jsonify({"error": "bad request - check recipient"}), 400
        
    try:
        timestamp = datetime.now().isoformat()
        logger.save_message(role, sender_id, receiver_type, int(receiver_id), message, timestamp, message_type=message_type)
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[DEBUG] Chat Save Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.post("/api/org/driver/<int:driver_id>/clear_chat")
@login_required(role="org")
def api_org_driver_clear_chat(driver_id):
    org_id = session["user_id"]
    org = logger.get_organisation_by_id(org_id)
    if not org or not org.can_clear_data:
        return jsonify({"error": "unauthorized"}), 403
    
    # Authorisation Check
    driver = logger.get_driver_by_id(driver_id)
    is_authorized = False
    if driver:
        if driver.organisation_id == org_id:
            is_authorized = True
        else:
            apps = logger.get_driver_applications(driver_id)
            if any(a['organisation_id'] == org_id for a in apps):
                is_authorized = True

    if not is_authorized:
        return jsonify({"error": "unauthorized"}), 403

    logger.clear_messages("org", org_id, "driver", driver_id)
    return jsonify({"success": True})

@app.post("/api/org/driver/<int:driver_id>/clear_history")
@login_required(role="org")
def api_org_driver_clear_history(driver_id):
    org_id = session["user_id"]
    org = logger.get_organisation_by_id(org_id)
    if not org or not org.can_clear_data:
        return jsonify({"error": "unauthorized"}), 403
    
    driver = logger.get_driver_by_id(driver_id)
    is_authorized = False
    if driver:
        if driver.organisation_id == org_id:
            is_authorized = True
        else:
            apps = logger.get_driver_applications(driver_id)
            if any(a['organisation_id'] == org_id for a in apps):
                is_authorized = True

    if not is_authorized:
        return jsonify({"error": "unauthorized"}), 403

    logger.clear_driver_events(driver_id)
    return jsonify({"success": True})


@app.get("/api/chat/messages")
def api_chat_messages():
    role = session.get("role")
    if role not in ["admin", "org", "driver"]: return jsonify({"error": "unauthorized"}), 403
    
    user_id = request.args.get("user_id")
    user_role = request.args.get("user_role")

    if role == "admin":
        if not user_id or not user_role: return jsonify([])
        msgs = logger.get_messages('admin', session["user_id"], user_role, int(user_id))
    elif role == "org":
        # Can be chatting with driver OR admin
        target_role = user_role or "driver"
        target_id = user_id or request.args.get("driver_id")
        if not target_id: return jsonify([])
        msgs = logger.get_messages('org', session["user_id"], target_role, int(target_id))
    elif role == "driver":
        # Can be chatting with org OR admin
        target_role = user_role or "org"
        if target_role == "org":
            # Primary: use passed org_id (for applications/interviews)
            target_id = request.args.get("org_id") or session.get("org_id")
            if not target_id: return jsonify([])
        else: # admin
            target_id = user_id # Admin ID passed from frontend
            if not target_id: return jsonify([])
        msgs = logger.get_messages('driver', session["user_id"], target_role, int(target_id))
    else:
        return jsonify([])
        
    return jsonify(msgs)

@app.post("/api/chat/read")
def api_chat_read():
    data = request.json
    role = session.get("role")
    if role not in ["admin", "org", "driver"]: return jsonify({"error": "unauthorized"}), 403
    
    sender_id = data.get("sender_id")
    sender_type = data.get("sender_type")
    
    if sender_id and sender_type:
        receiver_id = session["user_id"]
        logger.mark_messages_read(role, receiver_id, sender_type, int(sender_id))
    
    return jsonify({"ok": True})

@app.post("/api/admin/clear_chat")
@login_required(role="admin")
def api_admin_clear_chat():
    data = request.json
    user_id = data.get("user_id")
    user_role = data.get("user_role")
    if not user_id or not user_role:
        return jsonify({"error": "missing data"}), 400
    
    logger.clear_chat_history('admin', session["user_id"], user_role, int(user_id))
    return jsonify({"ok": True})

# --- Video Stream ---
def mjpeg_stream(driver_id: int) -> Iterator[bytes]:
    if not pipeline.get_status().camera_running:
        pipeline.start()
        time.sleep(0.1)
    while True:
        jpg = pipeline.get_latest_jpeg()
        if jpg is None:
            time.sleep(0.01)
            continue
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        time.sleep(0.01)

@app.get("/video_feed")
@login_required(role="driver")
def video_feed():
    return Response(mjpeg_stream(session["user_id"]), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.get("/api/status")
@login_required(role="driver")
def api_status():
    pipeline.set_driver_id(session["user_id"])
    s = pipeline.get_status()
    # Let's map events to the driver
    if s.overall_message != "Stopped" and s.overall_level > 0:
        # log_event happens in pipeline currently, but it doesn't know driver_id.
        # we can just use the memory pipeline as is.
        pass
    
    return jsonify({
        "camera_running": s.camera_running,
        "overall_level": s.overall_level,
        "overall_message": s.overall_message,
        "drowsiness_level": s.drowsiness_level,
        "drowsiness_message": s.drowsiness_message,
        "drowsiness_duration_s": s.drowsiness_duration_s,
        "phone_level": s.phone_level,
        "phone_message": s.phone_message,
        "phone_duration_s": s.phone_duration_s,
        "voice_token": s.voice_token,
        "voice_text": s.voice_text,
        "ear": s.ear,
        "phone_conf": s.phone_conf,
        "fps_capture": s.fps_capture,
        "fps_process": s.fps_process,
    })

@app.get("/api/notifications")
@login_required(role="driver")
def api_notifications():
    notifs = logger.get_notifications(session["user_id"])
    return jsonify(notifs)

@app.post("/api/notifications/clear")
@login_required(role="driver")
def api_clear_notifications():
    logger.clear_notifications(session["user_id"])
    return jsonify({"success": True})

@app.post("/api/notifications/delete/<int:notif_id>")
@login_required(role="driver")
def api_delete_notification(notif_id):
    logger.delete_notification(notif_id, session["user_id"])
    return jsonify({"success": True})

@app.post("/api/camera/start")
@login_required(role="driver")
def api_camera_start():
    pipeline.set_driver_id(session["user_id"])
    pipeline.start()
    return jsonify({"ok": True})

@app.post("/api/camera/stop")
@login_required(role="driver")
def api_camera_stop():
    pipeline.stop()
    pipeline.set_driver_id(None)
    return jsonify({"ok": True})

@app.post("/api/process_frame")
@login_required(role="driver")
def api_process_frame():
    try:
        data = request.json
        if not data or 'image' not in data:
            return jsonify({"error": "No image data"}), 400

        # Decode base64 JPEG — frame is valid and local to this thread
        header, img_str = data['image'].split(',', 1)
        nparr  = np.frombuffer(base64.b64decode(img_str), np.uint8)
        frame  = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            return jsonify({"error": "Invalid image"}), 400

        driver_id = session["user_id"]
        
        # --- COPY FRAME FIRST: prevent pipeline memory mutation causing black snapshots ---
        pristine_frame = frame.copy()
        
        pipeline.set_driver_id(driver_id)
        status = pipeline.process_external_frame(frame)

        # Skip recording if the frontend sent a pitch-black frame (e.g. camera stutter or tab switch)
        # We don't want black snapshots for real incidents.
        if pristine_frame.mean() > 5.0:
            # --- INCIDENT MEDIA: snapshot + video captured HERE while frame is pristine ---
            incident_recorder.on_frame(driver_id, pristine_frame, status)

        # Live status cache (in-memory, zero DB I/O)
        LIVE_STATUS_DB[driver_id] = {
            "timestamp": time.time(),
            "overall":    status.overall_message,
            "drowsiness": status.drowsiness_message,
            "phone":      status.phone_message,
            "risk_score": status.overall_level,
            "ear":        status.ear
        }

        return jsonify(status.__dict__)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Driver Safety App")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)), help="Port to run the server on")
    args = parser.parse_args()
    
    app.run(host="0.0.0.0", port=args.port, debug=True, threaded=True)