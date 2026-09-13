"""Generate the small media files the ingest tests run against.

Committed rather than generated at test time: the suite must not depend on ffmpeg
being present to *collect*, and a fixture that is built on the fly is a fixture that
changes under you. Re-run this only to add or replace one.

    python tests/make_fixtures.py
"""

import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def run(argv: list[str]) -> None:
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"failed: {' '.join(argv)}\n{result.stderr[-500:]}")


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)

    # Landscape video with audio, 2s, 320x240. Starts black on purpose for the first
    # half-second so the "don't grab frame zero" behaviour has something to prove.
    run([
        "ffmpeg", "-y", "-nostdin",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.5",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=1.5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(FIXTURES / "sample_video.mp4"),
    ])

    # Portrait video carrying real rotation, so the display-dimension handling is
    # tested against the case that actually occurs: phone footage.
    #
    # Written with -display_rotation on the input plus a stream copy, which produces a
    # Display Matrix side-data entry. The older `-metadata:s:v:0 rotate=90` is silently
    # ignored by ffmpeg 6 — it produces a file with no rotation at all, which made this
    # fixture pass vacuously until the assertion caught it.
    upright = FIXTURES / "_upright_tmp.mp4"
    run([
        "ffmpeg", "-y", "-nostdin",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(upright),
    ])
    run([
        "ffmpeg", "-y", "-nostdin",
        "-display_rotation", "90", "-i", str(upright),
        "-c", "copy", str(FIXTURES / "rotated_video.mp4"),
    ])
    upright.unlink()

    # Audio only, no video stream.
    run([
        "ffmpeg", "-y", "-nostdin",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:a", "libmp3lame", str(FIXTURES / "sample_audio.mp3"),
    ])

    from PIL import Image

    Image.new("RGB", (800, 600), (30, 90, 160)).save(FIXTURES / "sample_image.jpg", quality=90)
    # RGBA, to exercise the alpha-compositing path in the encoder.
    Image.new("RGBA", (400, 400), (200, 40, 40, 128)).save(FIXTURES / "sample_image.png")

    import pypdfium2  # noqa: F401  - imported to fail early if it is missing

    _write_minimal_pdf(FIXTURES / "sample_document.pdf")

    print("wrote:", ", ".join(sorted(p.name for p in FIXTURES.iterdir())))


def _write_minimal_pdf(target: Path) -> None:
    """A valid one-page PDF, written by hand.

    Hand-built rather than produced by a library so the fixture has no generator to
    drift with, and stays small enough to read in a diff.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length 62 >>\nstream\nBT /F1 18 Tf 20 100 Td (Gecko Asset Manager) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n".encode()
        + b"%%EOF\n"
    )
    target.write_bytes(bytes(out))


if __name__ == "__main__":
    main()
