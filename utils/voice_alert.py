'''import pyttsx3

engine = pyttsx3.init()
engine.setProperty('rate', 150)

def speak_warning(message):
    engine.say(message)
    engine.runAndWait()'''

'''import pyttsx3
import threading

engine = pyttsx3.init()
engine.setProperty('rate', 150)

speaking = False

def speak_warning(message):
    global speaking

    # prevent multiple voices overlapping
    if speaking:
        return

    def speak():
        global speaking
        speaking = True
        engine.say(message)
        engine.runAndWait()
        speaking = False

    # run speech in background thread
    threading.Thread(target=speak, daemon=True).start()'''


import pyttsx3
import threading
import time

engine = pyttsx3.init()
engine.setProperty('rate', 150)

last_spoken_time = 0
cooldown = 3   # seconds between warnings


def speak_warning(message):
    global last_spoken_time

    current_time = time.time()

    # speak only every few seconds
    if current_time - last_spoken_time < cooldown:
        return

    last_spoken_time = current_time

    def speak():
        engine.say(message)
        engine.runAndWait()

    threading.Thread(target=speak, daemon=True).start()