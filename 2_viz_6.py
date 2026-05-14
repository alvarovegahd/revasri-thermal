import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

outdir = Path("unpack14_test")
outdir.mkdir(exist_ok=True)

raw = open("thermal_dump.bin", "rb").read()

H, W = 192, 256
PIXELS = H * W

def unpack_14bit(data):
    """Unpack 14-bit pixels packed across bytes."""
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    
    # group into 14-bit pixels
    usable = (len(bits) // 14) * 14
    bits = bits[:usable]
    
    pixels = bits.reshape(-1, 14)
    
    # convert to uint16
    values = pixels.dot(1 << np.arange(13, -1, -1))
    return values.astype(np.uint16)

print("Unpacking…")

vals = unpack_14bit(raw)

print("Recovered pixels:", len(vals))

if len(vals) >= PIXELS:
    img = vals[:PIXELS].reshape(H, W)

    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title("unpacked_14bit")
    plt.savefig(outdir / "unpacked_14bit.png", dpi=150)
    plt.close()

    print("Saved to unpack14_test/")
else:
    print("Not enough pixels recovered")

