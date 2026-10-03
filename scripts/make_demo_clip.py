"""Write a short stand-in dashcam clip if data/drive.mp4 is missing.

Replace that file with a real drive before the demo. Gemini is reading drawn
text, not a photograph of a sign.
"""

from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "drive.mp4"


def card(title: str, subtitle: str) -> np.ndarray:
    image = np.full((720, 1280, 3), 235, dtype=np.uint8)
    cv2.rectangle(image, (390, 180), (890, 540), (40, 40, 200), -1)
    cv2.putText(image, title, (430, 340), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (255, 255, 255), 6)
    cv2.putText(image, subtitle, (450, 430), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
    return image


def main() -> None:
    if OUT.exists():
        print(f"left existing {OUT}")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(OUT), cv2.VideoWriter_fourcc(*"mp4v"), 10, (1280, 720))
    frames = [card("STOP", "")] * 40 + [card("SPEED", "LIMIT 25")] * 40
    for frame in frames:
        writer.write(frame)
    writer.release()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
