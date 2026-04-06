#!/usr/bin/env bash
# Exit on error
set -o errexit

# Use the current python environment
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# AGGRESSIVE CLEAN: Wipe every possible version of OpenCV to prevent conflicts
python -m pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless opencv-contrib-python-headless || true

# SURGICAL INSTALL: Install the exact version known to work with YOLOv8 on headless servers
python -m pip install opencv-python-headless==4.9.0.80
