# AGENTS.md — atg-host-visy

Guidance for AI coding agents working in this repository.

## What this project is

A single-file PyQt5 desktop application (`atg_host.py`, ~700 lines) that talks
to a FAFNIR VISY-Command automatic tank gauge over a serial port using the
Veeder-Root TLS-350 *computer format* (the gauge emulates it when a
Veeder-Root-compatible host code is set via VISY-Setup). It polls in-tank
inventory (`i201`) and deliveries (`i202`), decodes them into tables, and has a
console plus a manual command box that can send any function code — or raw
bytes via `hex:` / `text:` prefixes. A sibling repository,
`atg-esp32-gateway`, implements the same protocol in C on an ESP32; keep the
two codecs semantically in sync.

## Run and test

```bash
pip install -r requirements.txt   # PyQt5, pyserial
python atg_host.py
```

- No hardware needed: tick **Demo mode** — `AtgSimulator` produces valid
  framed `i201`/`i202` responses (with correct checksums) and answers anything
  else with the `9999FF1B` rejection frame, including unframed raw input.
- There is no test suite; verify changes by scripting the UI headlessly
  (instantiate `MainWindow`, enable demo mode, drive `manual_edit` +
  `send_manual()`, assert on `console.toPlainText()` and table row counts —
  see git history for examples). Pure helpers (`tls_checksum`,
  `parse_response`, `parse_i201`, `parse_i202`, `hex_to_float`) are directly
  importable and the easiest place to add real tests.

## Architecture (one file, four parts)

1. **Protocol helpers** (top of file) — pure functions, no Qt. The reference
   implementation of the TLS-350 codec for this project family.
2. **AtgSimulator** — demo-mode gauge. If you extend the protocol, extend the
   simulator too, otherwise demo mode silently diverges from hardware.
3. **SerialWorker (QThread)** — owns the pyserial handle; frames are split on
   ETX, with a 1 s stale-buffer flush so partial junk still becomes visible.
   UI code must never touch the serial port directly; use `worker.send()`.
4. **MainWindow** — UI. Transmission funnels through `send_raw()` (logging,
   demo-mode reply emulation); `send_function()` adds SOH/ETX framing. Keep
   that funnel: any new send path must go through `send_raw()`.

## Protocol invariants

- Frame: `SOH(0x01) … ETX(0x03)`. Requests carry no checksum; responses end
  with `&&` + 4 hex digits = 16-bit two's complement of the byte sum from SOH
  through `&&` inclusive.
- Data fields: IEEE-754 single precision, big-endian, as 8 hex chars.
- Function code format `iFFFTT`, TT `00` = all tanks, `01`–`16` single tank.
- `9999…` echo = command rejected. Checksum mismatch is reported but the
  payload is still shown — keep that behaviour (diagnostics tool, not a
  gatekeeper).

## Manual command box contract (documented in docs/command-reference.pdf)

| Entry | Behaviour |
|---|---|
| `i20100` | framed automatically with SOH/ETX |
| `hex:01 69 …` | exact bytes, no framing, spaces/commas ignored |
| `text:<SOH>i20100<ETX>` | ASCII, no framing; `<SOH>`/`<ETX>` escapes only |

Error cases must not transmit anything: invalid hex → `!! invalid hex string`,
non-ASCII in text mode → `!! text mode accepts ASCII only`. This table is
user-facing documentation; if you change the syntax, update
`docs/command-reference.html`, regenerate the PDF, and update README.md.

## Documentation workflow (docs/)

`command-reference.pdf` is generated from `command-reference.html`
(`thesis.css` + vendored `paged.polyfill.js`). Plain
`chrome --print-to-pdf` yields a BLANK page (Chrome prints before Paged.js
paginates). Correct recipe: headless Chrome via the DevTools protocol with
`--allow-file-access-from-files`, wait for `.pagedjs_page` elements to appear
and stabilise, then `Page.printToPDF {printBackground: true,
preferCSSPageSize: true}`. Regenerate and commit the PDF with any HTML edit.

## Conventions

- Python: PEP 8, 4-space indent, type hints on protocol helpers, comments only
  where the protocol forces non-obvious code (offsets, checksums).
- Keep the application single-file unless it grows substantially; the
  simplicity is deliberate (field technicians copy one script).
- Commit style: conventional commits (`feat:`, `fix:`, `docs:`).
- Remote: public repo `ncktranstak/atg-host-visy` — never commit credentials,
  site names, or real station data (logs included).
- Safety note that must survive edits: the UI/docs warn against improvising
  write/configuration commands against production gauges. Don't weaken it.
