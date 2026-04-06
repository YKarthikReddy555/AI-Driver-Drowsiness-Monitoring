#!/usr/bin/env bash
# Exit on error
set -o errexit

# Use the current python environment
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 1. WIPE EVERYTHING: Remove the "poisoned" installations
python -m pip uninstall -y mediapipe ultralytics opencv-python opencv-contrib-python opencv-python-headless opencv-contrib-python-headless || true

# 2. LOCK THE DRIVER FIRST: Install the stable headless driver before anything else
python -m pip install opencv-python-headless==4.9.0.80

# 3. LINK THE AI ENGINES: Install the AI libraries now so they connect to the headless driver we just fixed
python -m pip install mediapipe==0.10.33 ultralytics==8.1.0
