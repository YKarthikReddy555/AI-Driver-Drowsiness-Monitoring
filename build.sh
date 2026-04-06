#!/usr/bin/env bash
# Exit on error
set -o errexit

# Use the current python environment
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Force uninstall of any GUI opencv versions installed by dependencies like mediapipe
python -m pip uninstall -y opencv-python opencv-contrib-python || true

# Re-install the headless version to ensure libGL errors are gone
python -m pip install opencv-python-headless
