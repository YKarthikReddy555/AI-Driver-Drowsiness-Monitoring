# AI-Based Driver Drowsiness & Mobile Usage Detection System

Real-time Driver Safety Monitoring Web App using **Flask + OpenCV + MediaPipe (EAR) + YOLOv8 (cell phone)** with **threaded capture + threaded processing** to avoid camera lag.

## Features

- **Live camera streaming** via MJPEG (`/video_feed`)
- **Drowsiness detection** (EAR)
  - >2s: **ORANGE** + voice: "Please stay alert"
  - >5s: **RED** + voice: "You are feeling drowsy. Stop the vehicle."
  - Instant reset when eyes reopen
- **Phone usage detection** (YOLOv8 COCO class 67 = `cell phone`)
  - >3s: **ORANGE** + voice: "Please don't talk and drive"
  - >7s: **RED** + voice: "Focus on driving immediately"
  - >12s: **CRITICAL overlay** + voice: "Critical alert. Stop using phone now"
  - Repeats voice every **3 seconds** until phone disappears
  - Instant reset when phone disappears
- **SQLite event logging** + `/history` page
- **Start/Stop camera** buttons
- **Client-side voice alerts** (browser SpeechSynthesis) so backend never freezes

## Folder Structure

```
.
├── app.py
├── requirements.txt
├── database.db                 # auto-created on first run
├── templates/
│   ├── dashboard.html
│   └── history.html
├── static/
│   ├── css/app.css
│   └── js/app.js
└── utils/
    ├── __init__.py
    ├── audio_alert.py          # optional server-side TTS
    ├── database.py             # SQLite events
    ├── drowsiness_detector.py  # MediaPipe FaceMesh EAR
    ├── phone_detector.py       # YOLOv8n cell phone
    └── pipeline.py             # threaded capture + processing + overlay
```

## Setup (Windows)

1) Create/activate a virtual environment (recommended)

```bash
python -m venv venv
venv\Scripts\activate
```

2) Install dependencies

```bash
pip install -r requirements.txt
```

3) Run the app

```bash
python app.py
```

Open: `http://127.0.0.1:5000/dashboard`

If you want a different port:

```bash
set PORT=5500
venv\Scripts\python app.py
```

## Notes on Performance

- Camera capture runs in a dedicated thread (`CameraCapture`).
- Detection + overlay + JPEG encoding run in a separate processing thread (`SafetyPipeline`).
- YOLO runs every `phone_detect_every_n` frames (default **3**) to reduce CPU usage.
- MJPEG endpoint serves the *latest pre-encoded JPEG*, so multiple clients do not multiply compute.

## Troubleshooting

- If YOLO weights download is slow: the first run may download `yolov8n.pt`.
- If the camera is busy: close other apps using the webcam.
- If you want server-side audio (local machine speakers): set `enable_server_tts=True` in `app.py` (client voice is still recommended).





Here is a highly detailed, slide-by-slide project breakdown that you can easily copy and paste into your PowerPoint presentation for Review 3.

Slide 1: Title Slide
Project Title: Intelligent Driver Monitoring & Fleet Management System
Goal: To detect driver fatigue and phone distractions in real-time, instantly alerting both the driver and their managing organization to prevent accidents.
Slide 2: Introduction & Problem Statement
The Problem: Drowsy driving and mobile phone distractions are leading causes of severe road accidents. Traditional systems lack real-time oversight mapping to fleet organizations.
The Solution: A web-based, AI-driven monitoring system that actively watches the driver's face, issues immediate localized alerts, and bridges communication directly to a live dashboard managed by their organization.
Slide 3: Core Technologies & Architecture
Backend: Python Flask framework with an SQLite relational database.
Computer Vision (AI Pipeline):
FaceMesh (MediaPipe): Calculates Eye Aspect Ratio (EAR) to continuously detect drowsiness/sleeping.
YOLO (Ultralytics): Highly accurate object detection neural network deployed to identify mobile phone usage while driving.
Frontend: HTML5, CSS3, JavaScript, Bootstrap 5 UI, and Chart.js for real-time analytics.
Streaming Strategy: MJPEG web-socket feeds bridging webcam hardware securely to the web interface.
Slide 4: Real-Time Detection & Alerts
Drowsiness Pipeline: Evaluates blink duration. Triggers Warning (>2 secs) and Danger (>5 secs) states.
Distraction Pipeline: Evaluates phone usage duration. Triggers Warning, Danger, and Critical (>12 secs) states.
Overlays & Audio: The driver's live feed displays a dynamic color-coded bounding box and text. Both browser-side text-to-speech (TTS) and server-side robotic audio immediately command the driver to regain focus.
Slide 5: Advanced Incident Reporting (New for Review 3)
Temporal Rolling Buffer: The system actively stores the last 150 frames (~5 seconds) of footage in RAM continuously.
Automated Video Generation: When a severe incident fires, the system saves the 5 seconds before the event and 5 seconds after, compiling a definitive 10-second vp80 / .webm video clip.
Instant Snapshotting: The exact moment a phone or drowsiness is detected, a high-quality 

.jpg
 is captured instantly.
Seamless Syncing: Both the snapshot and the video clip are instantly forwarded to the Organization's Chat Dashboard as irrefutable evidence.
Slide 6: Multi-Role Dashboard Ecosystem
Driver Dashboard: Displays the live camera feed, real-time safety status overlays, a live speedometer, recent localized event logs, and a dedicated support chat.
Organization Portal:
Manages multiple registered drivers.
Views live "History" tables detailing every timestamped incident a specific driver has had.
Features a direct communication chat system linked to active drivers.
Administrator Dashboard: The overarching "God Mode." Can manage, block, or delete entire organizations and drivers. Toggles global permissions (like data-clearing) for organizations.
Slide 7: Integrated Communication & Push Notifications
Secure Chat System: Real-time Ajax-polling messaging architecture connecting Admins, Orgs, and Drivers. Supports text, image, voice notes, and video rendering natively in HTML5.
End-to-End Visuals: Includes " WhatsApp-style" Smart Date Dividers (Today/Yesterday) and encryption badges.
Read Receipts: Features single white ticks (✓ - sent) and double blue ticks (✓✓ - explicitly read by the recipient) synced to the database.
Native OS Push Notifications: Uses the Web Push API to trigger native Windows/Android background pop-ups when a message or critical incident video arrives while the browser is minimized.
Slide 8: Security, Recovery, and UX
Authentication: Password hashing (Werkzeug) mapping secure sessions securely via cookies.
Password Reset Pipeline: Full Gmail SMTP integration sending professional, secure tokenized reset links directly to user emails. Enforces rigorous password strength validation meters.
UI/UX Design: A highly flexible, premium "Dark Mode" and "Light Mode" aesthetic. Uses global Toast functionality to flash success/error events gracefully in the corner of the screen without interrupting the user.
Presenter Tips for Review 3:
Make sure you emphasize the Automated Video Buffer and Read Receipts / Push Notifications, as these are the most complex additions that elevate this from a simple script to a production-ready application.
If you demo the project, minimize the browser window while recording a face to show the reviewers the Windows Native popup notifications firing from the background!