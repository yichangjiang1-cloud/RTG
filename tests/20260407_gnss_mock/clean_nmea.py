"""Extract NMEA sentences from log files, filter out binary/garbled data."""
import os
import re

# Valid NMEA talker IDs
NMEA_PATTERN = re.compile(rb'\$G[NPGLABQ][A-Z]{3},.*\*[0-9A-Fa-f]{2}')

# File list
LOG_DIR = r"/tests/20260407_gnss_mock"
files = [
    "COM10_mc282_qx_1.log",
    "COM20_lc29h_1.log",
    "COM34_km203_1.log",
    "COM6_km301_1.log",
]

for fname in files:
    src = os.path.join(LOG_DIR, fname)
    dst = os.path.join(LOG_DIR, fname.replace('.log', '_clean.log'))

    print(f"Processing: {fname}")
    print(f"  Source size: {os.path.getsize(src):,} bytes")

    with open(src, 'rb') as f:
        data = f.read()

    # Find all NMEA sentences
    matches = NMEA_PATTERN.findall(data)

    # Valid NMEA talker prefixes for post-filtering
    VALID_PREFIXES = ('$GP', '$GN', '$GL', '$GA', '$GB', '$GQ')

    # Decode and write
    written = 0
    with open(dst, 'w', encoding='utf-8') as out:
        for m in matches:
            try:
                line = m.decode('ascii', errors='ignore').strip()
                if line and line.startswith(VALID_PREFIXES):
                    out.write(line + '\n')
                    written += 1
            except:
                pass

    print(f"  NMEA sentences: {written}")
    print(f"  Output: {dst}")
    print(f"  Output size: {os.path.getsize(dst):,} bytes")
    print()
