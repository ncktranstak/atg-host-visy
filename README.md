# ATG Host — VISY-Command Web

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![PyQt5](https://img.shields.io/badge/GUI-PyQt5-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

PyQt5 host software for the **FAFNIR VISY-Command / VISY-Command Web** automatic
tank gauge (ATG). It connects to the galvanically isolated serial **host
interface** (RS-232 or RS-485) of the VI-4 board and polls tank data using the
**Veeder-Root TLS-350 computer-format** protocol emulation.

## Features

- **In-tank inventory (`i201`)** — product volume, TC volume, ullage, product
  height, water level, temperature and water volume for up to 16 tanks
- **Delivery report (`i202`)** — start/end volumes and heights per delivery
- **Auto-polling** with configurable interval (1 s … 1 h), single tank or all tanks
- **Manual command entry** for any other TLS function code
- **Console** with timestamps, ASCII/hex view, TLS checksum verification,
  RX/TX byte counters and log export
- **Demo mode** — built-in ATG simulator (4 tanks) for testing without hardware
- Probe status decoding per the VI-4 status codes (0–13, 99)

## Requirements

- Python 3.8+
- PyQt5, pyserial (`pip install -r requirements.txt`)
- RS-232 cable (or RS-232–USB adapter) to the VI-4 host interface, terminals
  1 (RxD), 2 (TxD), 3 (GND) — or RS-485 on terminals 4 (A+), 5 (B–)

## ATG configuration

On the VISY-Command side (configured with **VISY-Setup** via the service
interface):

1. Select a **Veeder-Root-compatible host protocol** by entering the
   corresponding *host code*.
2. Note the configured baud rate / parity and mirror the settings in this app
   (default 9600 8N1).
3. RS-232 and RS-485 host operation are mutually exclusive; the VI-4 board
   auto-detects which one is wired.

## Usage

```bash
pip install -r requirements.txt
python atg_host.py
```

1. Pick the COM port and line settings, then **Connect**
   (or tick **Demo mode** to try the app without hardware).
2. Press **Inventory (i201)** or enable **Auto-poll**.
3. Watch raw traffic in the **Console** tab; send arbitrary function codes
   with the manual command field (e.g. `i20100`).

## Protocol notes

Computer-format framing:

```
Host → ATG:  <SOH> i201 TT <ETX>          TT = 01…16, 00 = all tanks
ATG → Host:  <SOH> i201TT YYMMDDHHmm {records} && CCCC <ETX>
```

Each inventory record is `TT P SSSS NN` followed by `NN` IEEE-754 floats as
8-char hex (volume, TC volume, ullage, height, water, temperature, water
volume). `CCCC` is the 16-bit two's-complement checksum over SOH…`&&`.

## Disclaimer

This is an independent host implementation based on publicly documented
protocol behaviour. VISY-Command, VISY-Setup and SECON are products of
FAFNIR GmbH; Veeder-Root TLS is a trademark of its respective owner.
