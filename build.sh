#!/usr/bin/env bash
# Exit on error
set -o errexit

# 1. Update PIP
python -m pip install --upgrade pip

# 2. Install base requirements (this installs everything, but might pull in wrong OpenCV/NumPy)
python -m pip install -r requirements.txt

# 3. DEEP CLEAN: Wipe all potentially conflicting packages
python -m pip uninstall -y mediapipe ultralytics opencv-python opencv-contrib-python opencv-python-headless opencv-contrib-python-headless numpy || true

# 4. TARGETED LOCK: Install the exact STABLE versions in the correct order
# We use --no-cache-dir to ensure we don't pick up a "poisoned" old build
python -m pip install --no-cache-dir numpy==1.26.4
python -m pip install --no-cache-dir opencv-python-headless==4.9.0.80
python -m pip install --no-cache-dir mediapipe==0.10.33 ultralytics==8.1.0
