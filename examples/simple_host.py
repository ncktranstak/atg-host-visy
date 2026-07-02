#!/usr/bin/env python3
"""
simple_host.py — minimal ATG host example in ~150 lines.

Shows the complete round trip to a FAFNIR VISY-Command (or any Veeder-Root
TLS-350-compatible tank gauge) with no GUI, no threads and no classes:

    open serial port -> send inventory request -> read reply
    -> verify checksum -> decode tanks -> print a table

Usage:
    python simple_host.py --demo              # no hardware needed
    python simple_host.py --port COM3         # real gauge, default 9600 8N1
    python simple_host.py --port COM3 --tank 2

Only dependency: pyserial (not needed for --demo).
The full-featured application with a GUI is ../atg_host.py.
"""

import argparse
import struct
import sys

# --------------------------------------------------------------------------
# 1. The protocol in five small functions
#
# A TLS-350 "computer format" exchange is plain ASCII between two control
# bytes: SOH (0x01) starts a frame, ETX (0x03) ends it.
#
#   Host  -> gauge:  <SOH> i201 TT <ETX>            (TT = tank, 00 = all)
#   Gauge -> host :  <SOH> i201TT YYMMDDHHmm {tank records} && CCCC <ETX>
# --------------------------------------------------------------------------

SOH = b"\x01"
ETX = b"\x03"


def build_request(tank: int) -> bytes:
    """An inventory request is just the function code wrapped in SOH/ETX."""
    return SOH + f"i201{tank:02d}".encode("ascii") + ETX


def checksum_ok(frame: bytes) -> bool:
    """Verify the response checksum.

    The last 4 characters before ETX are a 16-bit two's-complement checksum
    over every byte from SOH through the '&&' delimiter inclusive, chosen so
    that (byte sum + checksum) is a multiple of 0x10000.
    """
    body, checksum = frame[:-5], frame[-5:-1]      # split off "CCCC<ETX>"
    return (sum(body) + int(checksum, 16)) & 0xFFFF == 0


def hex_float(s: str) -> float:
    """Data fields are IEEE-754 singles sent as 8 hex chars, big-endian.

    Example: '464D767C' -> 13149.62 (litres).
    """
    return struct.unpack(">f", bytes.fromhex(s))[0]


def parse_inventory(frame: bytes) -> list[dict]:
    """Decode an i201 response frame into a list of tank dicts.

    Raises ValueError with a readable message on any malformed input.
    """
    if not (frame.startswith(SOH) and frame.endswith(ETX)):
        raise ValueError("frame is not delimited by SOH/ETX")

    text = frame[1:-1].decode("ascii")             # strip SOH/ETX
    if text.startswith("9999"):
        # The gauge answers 9999FF1B when it does not know the function --
        # usually a typo, or the wrong host protocol selected in VISY-Setup.
        raise ValueError("gauge rejected the command (9999)")

    if not checksum_ok(frame):
        raise ValueError("checksum mismatch — check line settings/cabling")

    # Layout: function echo (6) + timestamp YYMMDDHHmm (10) + records + &&CCCC
    records = text[16:-6]

    # Each record: tank(2) product(1) status-bits(4 hex) field-count(2 hex),
    # then <field-count> floats of 8 hex chars each.
    tanks = []
    pos = 0
    while pos + 9 <= len(records):
        nfields = int(records[pos + 7 : pos + 9], 16)
        fields = [hex_float(records[pos + 9 + i * 8 : pos + 17 + i * 8])
                  for i in range(nfields)]
        # The seven standard fields, in order:
        vol, tc_vol, ullage, height, water, temp, water_vol = (fields + [0.0] * 7)[:7]
        tanks.append({
            "tank": int(records[pos : pos + 2]),
            "product": records[pos + 2],
            "status": int(records[pos + 3 : pos + 7], 16),
            "volume": vol, "tc_volume": tc_vol, "ullage": ullage,
            "height": height, "water": water, "temperature": temp,
            "water_volume": water_vol,
        })
        pos += 9 + nfields * 8
    return tanks


# --------------------------------------------------------------------------
# 2. Transports: a real serial port, or a canned demo reply
# --------------------------------------------------------------------------

def exchange_serial(port: str, baud: float, request: bytes) -> bytes:
    """Send the request and collect bytes until ETX (or a 3 s timeout)."""
    import serial                                   # pyserial
    with serial.Serial(port, baud, timeout=3) as ser:
        ser.reset_input_buffer()
        ser.write(request)
        reply = ser.read_until(ETX)                # blocks until ETX/timeout
    if not reply.endswith(ETX):
        raise ValueError(f"no (complete) reply from the gauge on {port}")
    return reply


def exchange_demo(request: bytes) -> bytes:
    """Build the reply a real gauge would send: two tanks, valid checksum."""
    def f(x: float) -> str:                        # float -> 8 hex chars
        return struct.pack(">f", x).hex().upper()

    body = (request[1:-1].decode("ascii")          # function echo
            + "2607021645"                         # timestamp
            + "0110000" + "07" + f(13149.6) + f(13162.8) + f(16850.4)
            + f(1095.8) + f(0.3) + f(14.1) + f(3.5)
            + "0220000" + "07" + f(20913.0) + f(20934.1) + f(9087.0)
            + f(1743.1) + f(1.2) + f(13.9) + f(14.4))
    framed = "\x01" + body + "&&"
    checksum = format(-sum(framed.encode()) & 0xFFFF, "04X")
    return framed.encode() + checksum.encode() + ETX


# --------------------------------------------------------------------------
# 3. Put it together
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Minimal TLS-350 inventory poll")
    ap.add_argument("--port", help="serial port, e.g. COM3 or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600, help="default: 9600")
    ap.add_argument("--tank", type=int, default=0, help="1-16, 0 = all tanks")
    ap.add_argument("--demo", action="store_true", help="run without hardware")
    args = ap.parse_args()
    if not args.demo and not args.port:
        ap.error("--port is required (or use --demo)")

    request = build_request(args.tank)
    print(f"TX: {request.hex(' ').upper()}")

    reply = exchange_demo(request) if args.demo else \
        exchange_serial(args.port, args.baud, request)
    print(f"RX: {len(reply)} bytes, checksum OK\n")

    tanks = parse_inventory(reply)
    print(f"{'Tank':>4} {'Prod':>4} {'Volume L':>10} {'Ullage L':>10} "
          f"{'Height mm':>10} {'Water mm':>9} {'Temp C':>8}")
    for t in tanks:
        print(f"{t['tank']:>4} {t['product']:>4} {t['volume']:>10.1f} "
              f"{t['ullage']:>10.1f} {t['height']:>10.1f} "
              f"{t['water']:>9.1f} {t['temperature']:>8.2f}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
