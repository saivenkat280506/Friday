import sounddevice as sd
import wave
import os

from stt.stt import transcribe_audio


def record_audio(filename="temp_audio.wav", duration=5, fs=16000):
    print("Listening...")
    recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype="int16")
    sd.wait()
    print("Done recording.")

    with wave.open(filename, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(recording.tobytes())
    return filename


def transcribe_audio_file(filename="temp_audio.wav"):
    """Transcribe a wav file with Groq whisper-large-v3."""
    if not os.path.exists(filename):
        return ""
    with wave.open(filename, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    return transcribe_audio(frames)
