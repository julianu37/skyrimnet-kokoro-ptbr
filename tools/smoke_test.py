"""Smoke test for a running Kokoro adapter. Usage: python tools/smoke_test.py [url]"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8020"


def post(text: str, speaker: str) -> bytes:
    body = json.dumps(
        {"text": text, "speaker_wav": speaker, "language": "pt"}
    ).encode("utf-8")
    request = urllib.request.Request(
        BASE + "/tts_to_audio/",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def main() -> None:
    with urllib.request.urlopen(BASE + "/health", timeout=10) as response:
        health = json.load(response)
    print("health:", health["status"], "| voices:", len(health["voices"]))

    cases = [
        ("male_nord", "O que você procura em Whiterun, viajante?"),
        ("female_nord", "Bem-vindo à taverna. Sente-se e descanse."),
        ("narrator", "O Dragonborn segue para o norte sob a aurora."),
    ]
    for speaker, text in cases:
        started = time.perf_counter()
        audio = post(text, speaker)
        elapsed = time.perf_counter() - started
        seconds = max(0.0, (len(audio) - 44) / 2 / 24000)
        print(
            f"{speaker:12} {len(audio):>7} bytes | {seconds:4.2f}s audio | "
            f"{elapsed:4.2f}s wall | rtf={elapsed / max(seconds, 0.01):.2f}"
        )


if __name__ == "__main__":
    main()
