#!/usr/bin/env bash
# Exit on error
set -o errexit

# 1. Update PIP
python -m pip install --upgrade pip

# 2. NUCLEAR LOCK: Force-installation of stable versions, ignoring ALL cache
# This is the only way to purge the broken NumPy 2.x and MediaPipe submodules from Render.
python -m pip install --no-cache-dir -r requirements.txt

# 3. CLEAN UP: Ensure no GUI versions are present
python -m pip uninstall -y opencv-python opencv-contrib-python opencv-contrib-python-headless || true

# 4. FINAL VERIFICATION: Re-enforce the headless driver
python -m pip install --no-cache-dir opencv-python-headless==4.9.0.80
