#!/usr/bin/env python3
"""Speaker verification for computa-listener voice activation."""

import argparse
import json
import sys
import wave
import numpy as np
from pathlib import Path

PROFILE_PATH = Path.home() / "computa-mode" / "voice_profile.npy"
DEFAULT_THRESHOLD = 0.70
SAMPLE_RATE = 16000
ENROLL_SAMPLES = 5
ENROLL_DURATION = 3  # seconds per sample

# Lazy-loaded encoder (heavy import)
_encoder = None


def get_encoder():
    global _encoder
    if _encoder is None:
        import io, os
        from resemblyzer import VoiceEncoder
        # Suppress resemblyzer's "Loaded" message so stdout stays clean JSON
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            _encoder = VoiceEncoder()
        finally:
            sys.stdout = old_stdout
    return _encoder


def get_embedding(audio_np, source_sr=SAMPLE_RATE):
    """Generate a speaker embedding from a numpy audio array."""
    from resemblyzer import preprocess_wav
    encoder = get_encoder()
    wav = preprocess_wav(audio_np, source_sr=source_sr)
    if len(wav) < 1600:  # too short for meaningful embedding
        return None
    return encoder.embed_utterance(wav)


def load_wav(wav_path):
    """Load a WAV file and return (audio_np, sample_rate)."""
    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth == 2:
        audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        audio_np = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        audio_np = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0

    # If stereo, take first channel
    if n_channels > 1:
        audio_np = audio_np[::n_channels]

    return audio_np, framerate


def cosine_similarity(a, b):
    """Compute cosine similarity between two vectors."""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def cmd_enroll(args):
    """Record samples and create a voice profile."""
    import sounddevice as sd

    print(f"Voice enrollment: say 'computa activate' {ENROLL_SAMPLES} times.")
    print(f"Each recording lasts {ENROLL_DURATION} seconds.\n")

    embeddings = []
    for i in range(ENROLL_SAMPLES):
        input(f"  Sample {i+1}/{ENROLL_SAMPLES}: Press Enter, then say 'computa activate'... ")
        print("  Recording...", end="", flush=True)
        audio = sd.rec(
            int(ENROLL_DURATION * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
        )
        sd.wait()
        print(" done.")

        audio_np = audio.flatten()
        emb = get_embedding(audio_np, source_sr=SAMPLE_RATE)
        if emb is None:
            print("  Warning: recording too quiet/short, skipping this sample.")
            continue
        embeddings.append(emb)

    if len(embeddings) < 2:
        print("\nError: not enough valid samples. Please try again in a quieter environment.")
        sys.exit(1)

    avg_embedding = np.mean(embeddings, axis=0)
    profile_path = Path(args.profile) if args.profile else PROFILE_PATH
    np.save(str(profile_path), avg_embedding)
    print(f"\nVoice profile saved to {profile_path}")
    print(f"Enrolled with {len(embeddings)} samples.")
    print("Speaker verification is now active for computa-listener.")


def cmd_verify(args):
    """Verify a WAV file against the enrolled voice profile."""
    profile_path = Path(args.profile) if args.profile else PROFILE_PATH

    if not profile_path.exists():
        json.dump({"verified": False, "score": 0.0, "error": "No voice profile found"}, sys.stdout)
        return

    # Load enrolled embedding
    enrolled = np.load(str(profile_path))

    # Load and process WAV
    audio_np, framerate = load_wav(args.audio)
    test_emb = get_embedding(audio_np, source_sr=framerate)

    if test_emb is None:
        json.dump({"verified": False, "score": 0.0, "error": "Audio too short"}, sys.stdout)
        return

    similarity = cosine_similarity(enrolled, test_emb)
    verified = similarity >= args.threshold

    json.dump({"verified": verified, "score": round(similarity, 4)}, sys.stdout)


def cmd_test(args):
    """Record a single sample and verify against the enrolled profile (for testing)."""
    import sounddevice as sd

    profile_path = Path(args.profile) if args.profile else PROFILE_PATH
    if not profile_path.exists():
        print("No voice profile found. Run 'enroll' first.")
        sys.exit(1)

    enrolled = np.load(str(profile_path))

    input("Press Enter, then say 'computa activate'... ")
    print("Recording...", end="", flush=True)
    audio = sd.rec(
        int(ENROLL_DURATION * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    print(" done.")

    audio_np = audio.flatten()
    test_emb = get_embedding(audio_np, source_sr=SAMPLE_RATE)

    if test_emb is None:
        print("Audio too short or quiet.")
        return

    similarity = cosine_similarity(enrolled, test_emb)
    verified = similarity >= args.threshold

    print(f"\nSimilarity score: {similarity:.4f}")
    print(f"Threshold:        {args.threshold}")
    print(f"Result:           {'VERIFIED' if verified else 'REJECTED'}")


def main():
    parser = argparse.ArgumentParser(description="Speaker verification for computa-listener")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Enroll subcommand
    enroll_p = subparsers.add_parser("enroll", help="Record voice samples and create profile")
    enroll_p.add_argument("--profile", default=None, help="Path to save voice profile")

    # Verify subcommand
    verify_p = subparsers.add_parser("verify", help="Verify a WAV file against profile")
    verify_p.add_argument("--audio", required=True, help="Path to WAV file")
    verify_p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    verify_p.add_argument("--profile", default=None, help="Path to voice profile")

    # Test subcommand (interactive)
    test_p = subparsers.add_parser("test", help="Record and verify interactively")
    test_p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    test_p.add_argument("--profile", default=None, help="Path to voice profile")

    args = parser.parse_args()
    if args.command == "enroll":
        cmd_enroll(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "test":
        cmd_test(args)


if __name__ == "__main__":
    main()
