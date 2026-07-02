"""
ATG Host — VISY-Command Web host software.

Polls a FAFNIR VISY-Command (Web) automatic tank gauge over its serial host
interface (RS-232 / RS-485, see manual §4.4.3) using the Veeder-Root TLS-350
"computer format" protocol emulation (select the corresponding host code with
VISY-Setup).

Supported functions:
  i201 — In-tank inventory (volume, TC volume, ullage, height, water, temp)
  i202 — In-tank delivery report
  Manual command entry for any other function code, or raw bytes with the
  "hex:" / "text:" prefixes (sent unframed, exactly as entered).

A built-in demo simulator allows testing the UI without hardware.
"""

import random
import struct
import sys
import time
from datetime import datetime

import serial
import serial.tools.list_ports
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QStatusBar, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

SOH = b"\x01"
ETX = b"\x03"

# Probe status codes of the VI-4 board (manual §5.2.1)
PROBE_STATUS = {
    0: "Probe running",
    1: "Probe not running",
    2: "Mounting error",
    5: "No temperature",
    6: "No filling level",
    7: "Reduced accuracy",
    8: "Checksum error probe-RF",
    9: "RF: no data from probe",
    10: "Checksum error probe-unit",
    11: "No communication",
    12: "Incompatible data",
    13: "Waiting for RF data",
    99: "Not configured",
}


# --------------------------------------------------------------------------
# TLS-350 computer-format protocol helpers
# --------------------------------------------------------------------------

def tls_checksum(body: str) -> str:
    """Checksum over all chars from SOH through '&&' (two's complement, 16 bit)."""
    total = sum(ord(c) for c in body) & 0xFFFF
    return format((-total) & 0xFFFF, "04X")


def build_command(func: str) -> bytes:
    """Wrap a function code (e.g. 'i20100') into a computer-format request."""
    return SOH + func.encode("ascii") + ETX


def hex_to_float(h: str) -> float:
    """8 hex chars -> IEEE-754 single precision (big endian)."""
    return struct.unpack(">f", bytes.fromhex(h))[0]


def float_to_hex(f: float) -> str:
    return struct.pack(">f", f).hex().upper()


class TlsError(Exception):
    pass


def parse_response(frame: bytes) -> dict:
    """Parse a computer-format response frame into a dict.

    Returns {'func', 'datetime', 'payload', 'checksum_ok'} or raises TlsError.
    """
    text = frame.decode("ascii", errors="replace")
    if not text.startswith("\x01") or not text.endswith("\x03"):
        raise TlsError("Frame not delimited by SOH/ETX")
    inner = text[1:-1]
    if inner.startswith("9999"):
        raise TlsError("ATG rejected the command (9999FF1B)")

    amp = inner.rfind("&&")
    checksum_ok = None
    if amp != -1 and len(inner) >= amp + 6:
        checked = "\x01" + inner[: amp + 2]
        checksum_ok = tls_checksum(checked) == inner[amp + 2 : amp + 6].upper()
        inner = inner[:amp]

    func = inner[:6]
    dt = inner[6:16]
    payload = inner[16:]
    return {
        "func": func,
        "datetime": dt,
        "payload": payload,
        "checksum_ok": checksum_ok,
    }


def parse_i201(payload: str) -> list:
    """Parse in-tank inventory records.

    Record: TT P SSSS NN f1..fN  (NN hex field count, fields 8-hex floats)
    Field order: volume, TC volume, ullage, height, water, temperature, water volume.
    """
    tanks = []
    pos = 0
    while pos + 9 <= len(payload):
        tank = payload[pos : pos + 2]
        product = payload[pos + 2]
        status = payload[pos + 3 : pos + 7]
        nfields = int(payload[pos + 7 : pos + 9], 16)
        pos += 9
        fields = []
        for _ in range(nfields):
            fields.append(hex_to_float(payload[pos : pos + 8]))
            pos += 8
        tanks.append({
            "tank": tank,
            "product": product,
            "status": status,
            "fields": fields,
        })
    return tanks


def parse_i202(payload: str) -> list:
    """Parse in-tank delivery records.

    Record: TT P DD then per delivery: start(10) end(10) NN(hex) floats.
    """
    deliveries = []
    pos = 0
    while pos + 5 <= len(payload):
        tank = payload[pos : pos + 2]
        product = payload[pos + 2]
        count = int(payload[pos + 3 : pos + 5])
        pos += 5
        for _ in range(count):
            start = payload[pos : pos + 10]
            end = payload[pos + 10 : pos + 20]
            nfields = int(payload[pos + 20 : pos + 22], 16)
            pos += 22
            fields = []
            for _ in range(nfields):
                fields.append(hex_to_float(payload[pos : pos + 8]))
                pos += 8
            deliveries.append({
                "tank": tank,
                "product": product,
                "start": start,
                "end": end,
                "fields": fields,
            })
    return deliveries


def fmt_tls_datetime(dt: str) -> str:
    """YYMMDDHHmm -> readable string."""
    if len(dt) == 10 and dt.isdigit():
        return f"20{dt[0:2]}-{dt[2:4]}-{dt[4:6]} {dt[6:8]}:{dt[8:10]}"
    return dt


# --------------------------------------------------------------------------
# Demo simulator — builds valid i201/i202 responses without hardware
# --------------------------------------------------------------------------

class AtgSimulator:
    def __init__(self, tank_count: int = 4):
        self.tank_count = tank_count
        self._base = [random.uniform(8000, 24000) for _ in range(tank_count)]

    def respond(self, func: str) -> bytes:
        now = datetime.now().strftime("%y%m%d%H%M")
        if func.lower().startswith("i201"):
            body = func + now + self._inventory()
        elif func.lower().startswith("i202"):
            body = func + now + self._deliveries()
        else:
            body = "9999FF1B"
        body_soh = "\x01" + body + "&&"
        return body_soh.encode("ascii") + tls_checksum(body_soh).encode() + ETX

    def _inventory(self) -> str:
        out = []
        for i in range(self.tank_count):
            vol = self._base[i] + random.uniform(-40, 15)
            self._base[i] = max(500.0, vol)
            height = vol / 30000 * 2500
            temp = 14.0 + random.uniform(-0.3, 0.3)
            water = random.uniform(0, 4)
            fields = [vol, vol * (1 - (temp - 15) * 0.0011), 30000 - vol,
                      height, water, temp, water * 12]
            out.append(f"{i + 1:02d}{i + 1}0000" + "07"
                       + "".join(float_to_hex(f) for f in fields))
        return "".join(out)

    def _deliveries(self) -> str:
        out = []
        for i in range(self.tank_count):
            sv = self._base[i]
            ev = sv + random.uniform(3000, 9000)
            t = 14.2
            fields = [sv, sv, 1.0, t, ev, ev, 1.0, t, sv / 12, ev / 12]
            start = datetime.now().strftime("%y%m%d") + "0655"
            end = datetime.now().strftime("%y%m%d") + "0731"
            out.append(f"{i + 1:02d}{i + 1}01" + start + end + "0A"
                       + "".join(float_to_hex(f) for f in fields))
        return "".join(out)


# --------------------------------------------------------------------------
# Serial worker thread
# --------------------------------------------------------------------------

class SerialWorker(QThread):
    frame_received = pyqtSignal(bytes)
    bytes_sent = pyqtSignal(int)
    bytes_received = pyqtSignal(int)
    error = pyqtSignal(str)
    opened = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = None
        self._pending = []
        self._running = False

    def configure(self, port, baud, bytesize, parity, stopbits):
        self._settings = dict(port=port, baudrate=baud, bytesize=bytesize,
                              parity=parity, stopbits=stopbits, timeout=0.05)

    def send(self, data: bytes):
        self._pending.append(data)

    def stop(self):
        self._running = False

    def run(self):
        try:
            ser = serial.Serial(**self._settings)
        except serial.SerialException as exc:
            self.error.emit(str(exc))
            self.opened.emit(False)
            return
        self.opened.emit(True)
        self._running = True
        buffer = b""
        last_rx = time.monotonic()
        try:
            while self._running:
                while self._pending:
                    data = self._pending.pop(0)
                    ser.write(data)
                    self.bytes_sent.emit(len(data))
                chunk = ser.read(256)
                if chunk:
                    buffer += chunk
                    self.bytes_received.emit(len(chunk))
                    last_rx = time.monotonic()
                    while ETX in buffer:
                        frame, buffer = buffer.split(ETX, 1)
                        self.frame_received.emit(frame + ETX)
                elif buffer and time.monotonic() - last_rx > 1.0:
                    # stale partial frame — flush so it is at least visible
                    self.frame_received.emit(buffer)
                    buffer = b""
        except serial.SerialException as exc:
            self.error.emit(str(exc))
        finally:
            ser.close()
            self.opened.emit(False)


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

INVENTORY_HEADERS = [
    "Tank", "Product", "Status", "Volume (L)", "TC Volume (L)", "Ullage (L)",
    "Height (mm)", "Water (mm)", "Temp (°C)", "Water Vol (L)", "Updated",
]
DELIVERY_HEADERS = [
    "Tank", "Product", "Start", "End", "Start Vol (L)", "End Vol (L)",
    "Delivered (L)", "Start Height", "End Height",
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ATG Host — VISY-Command Web (TLS-350 host protocol)")
        self.resize(1150, 700)

        self.worker = None
        self.simulator = None
        self.rx_count = 0
        self.tx_count = 0

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_inventory)
        self.port_refresh_timer = QTimer(self)
        self.port_refresh_timer.timeout.connect(self.refresh_ports)
        self.port_refresh_timer.start(2000)

        self._setup_ui()
        self.refresh_ports()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        central = QWidget()
        root = QHBoxLayout(central)

        # ---- left column: connection + polling + commands
        left = QVBoxLayout()

        conn_box = QGroupBox("Host interface (RS-232 / RS-485)")
        grid = QGridLayout(conn_box)
        self.port_combo = QComboBox()
        self.baud_combo = QComboBox()
        for b in ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]:
            self.baud_combo.addItem(b)
        self.baud_combo.setCurrentText("9600")
        self.data_combo = QComboBox()
        self.data_combo.addItems(["8", "7"])
        self.parity_combo = QComboBox()
        self.parity_combo.addItems(["None", "Even", "Odd"])
        self.stop_combo = QComboBox()
        self.stop_combo.addItems(["1", "2"])
        self.demo_check = QCheckBox("Demo mode (simulated ATG)")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connection)

        grid.addWidget(QLabel("Port:"), 0, 0)
        grid.addWidget(self.port_combo, 0, 1)
        grid.addWidget(QLabel("Baud:"), 1, 0)
        grid.addWidget(self.baud_combo, 1, 1)
        grid.addWidget(QLabel("Data bits:"), 2, 0)
        grid.addWidget(self.data_combo, 2, 1)
        grid.addWidget(QLabel("Parity:"), 3, 0)
        grid.addWidget(self.parity_combo, 3, 1)
        grid.addWidget(QLabel("Stop bits:"), 4, 0)
        grid.addWidget(self.stop_combo, 4, 1)
        grid.addWidget(self.demo_check, 5, 0, 1, 2)
        grid.addWidget(self.connect_btn, 6, 0, 1, 2)
        left.addWidget(conn_box)

        poll_box = QGroupBox("Polling")
        pgrid = QGridLayout(poll_box)
        self.tank_combo = QComboBox()
        self.tank_combo.addItem("All tanks", "00")
        for t in range(1, 17):
            self.tank_combo.addItem(f"Tank {t}", f"{t:02d}")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setValue(5)
        self.interval_spin.setSuffix(" s")
        self.autopoll_check = QCheckBox("Auto-poll inventory")
        self.autopoll_check.toggled.connect(self.toggle_autopoll)
        pgrid.addWidget(QLabel("Tank:"), 0, 0)
        pgrid.addWidget(self.tank_combo, 0, 1)
        pgrid.addWidget(QLabel("Interval:"), 1, 0)
        pgrid.addWidget(self.interval_spin, 1, 1)
        pgrid.addWidget(self.autopoll_check, 2, 0, 1, 2)
        left.addWidget(poll_box)

        cmd_box = QGroupBox("Commands")
        cgrid = QGridLayout(cmd_box)
        inv_btn = QPushButton("Inventory (i201)")
        inv_btn.clicked.connect(self.poll_inventory)
        del_btn = QPushButton("Deliveries (i202)")
        del_btn.clicked.connect(self.poll_deliveries)
        self.manual_edit = QLineEdit()
        self.manual_edit.setPlaceholderText("i20100  |  hex:01 69 32 …  |  text:i20100")
        self.manual_edit.setToolTip(
            "Function code (framed with SOH/ETX automatically), or raw data:\n"
            "  hex:01 69 32 30 31 30 30 03   — bytes, sent exactly as given\n"
            "  text:i20100                   — ASCII, sent without framing\n"
            "In text mode, <SOH> and <ETX> insert the control characters.")
        self.manual_edit.returnPressed.connect(self.send_manual)
        manual_btn = QPushButton("Send")
        manual_btn.clicked.connect(self.send_manual)
        cgrid.addWidget(inv_btn, 0, 0, 1, 2)
        cgrid.addWidget(del_btn, 1, 0, 1, 2)
        cgrid.addWidget(self.manual_edit, 2, 0)
        cgrid.addWidget(manual_btn, 2, 1)
        left.addWidget(cmd_box)
        left.addStretch(1)

        # ---- right column: tabs
        self.tabs = QTabWidget()

        self.tank_table = QTableWidget(0, len(INVENTORY_HEADERS))
        self.tank_table.setHorizontalHeaderLabels(INVENTORY_HEADERS)
        self.tank_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tank_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabs.addTab(self.tank_table, "Tanks")

        self.delivery_table = QTableWidget(0, len(DELIVERY_HEADERS))
        self.delivery_table.setHorizontalHeaderLabels(DELIVERY_HEADERS)
        self.delivery_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.delivery_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabs.addTab(self.delivery_table, "Deliveries")

        console_widget = QWidget()
        cv = QVBoxLayout(console_widget)
        bar = QHBoxLayout()
        self.hex_check = QCheckBox("Hex view")
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self.console.clear())
        save_btn = QPushButton("Save log…")
        save_btn.clicked.connect(self.save_log)
        bar.addWidget(self.hex_check)
        bar.addStretch(1)
        bar.addWidget(save_btn)
        bar.addWidget(clear_btn)
        cv.addLayout(bar)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Consolas", 9))
        self.console.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4;")
        cv.addWidget(self.console)
        self.tabs.addTab(console_widget, "Console")

        root.addLayout(left, 0)
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.conn_label = QLabel("Disconnected")
        self.counter_label = QLabel("RX: 0 B   TX: 0 B")
        self.status.addWidget(self.conn_label, 1)
        self.status.addPermanentWidget(self.counter_label)

    # ------------------------------------------------------- connection
    def refresh_ports(self):
        current = self.port_combo.currentText()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        existing = [self.port_combo.itemText(i) for i in range(self.port_combo.count())]
        if ports != existing:
            self.port_combo.clear()
            self.port_combo.addItems(ports)
            if current in ports:
                self.port_combo.setCurrentText(current)

    def toggle_connection(self):
        if self.worker or self.simulator:
            self.disconnect_port()
        else:
            self.connect_port()

    def connect_port(self):
        if self.demo_check.isChecked():
            self.simulator = AtgSimulator()
            self.on_opened(True)
            self.log("--- demo mode: simulated ATG with 4 tanks ---")
            return
        port = self.port_combo.currentText()
        if not port:
            QMessageBox.warning(self, "ATG Host", "No serial port selected.")
            return
        parity = {"None": serial.PARITY_NONE, "Even": serial.PARITY_EVEN,
                  "Odd": serial.PARITY_ODD}[self.parity_combo.currentText()]
        stopbits = {"1": serial.STOPBITS_ONE, "2": serial.STOPBITS_TWO}[
            self.stop_combo.currentText()]
        bytesize = {"8": serial.EIGHTBITS, "7": serial.SEVENBITS}[
            self.data_combo.currentText()]
        self.worker = SerialWorker(self)
        self.worker.configure(port, int(self.baud_combo.currentText()),
                              bytesize, parity, stopbits)
        self.worker.frame_received.connect(self.on_frame)
        self.worker.bytes_sent.connect(self.on_tx_bytes)
        self.worker.bytes_received.connect(self.on_rx_bytes)
        self.worker.error.connect(self.on_error)
        self.worker.opened.connect(self.on_opened)
        self.worker.start()

    def disconnect_port(self):
        self.autopoll_check.setChecked(False)
        if self.worker:
            self.worker.stop()
            self.worker.wait(2000)
            self.worker = None
        self.simulator = None
        self.on_opened(False)

    def on_opened(self, ok: bool):
        if ok:
            name = "Demo" if self.simulator else self.port_combo.currentText()
            self.conn_label.setText(f"Connected — {name}")
            self.connect_btn.setText("Disconnect")
        else:
            self.conn_label.setText("Disconnected")
            self.connect_btn.setText("Connect")
            if not self.simulator:
                self.worker = None

    def on_error(self, msg: str):
        self.log(f"!! serial error: {msg}")
        self.conn_label.setText(f"Error: {msg}")

    def on_tx_bytes(self, n: int):
        self.tx_count += n
        self._update_counters()

    def on_rx_bytes(self, n: int):
        self.rx_count += n
        self._update_counters()

    def _update_counters(self):
        self.counter_label.setText(f"RX: {self.rx_count} B   TX: {self.tx_count} B")

    # ---------------------------------------------------------- commands
    def toggle_autopoll(self, on: bool):
        if on:
            self.poll_timer.start(self.interval_spin.value() * 1000)
            self.poll_inventory()
        else:
            self.poll_timer.stop()

    def poll_inventory(self):
        self.send_function("i201" + self.tank_combo.currentData())

    def poll_deliveries(self):
        self.send_function("i202" + self.tank_combo.currentData())

    def send_manual(self):
        entry = self.manual_edit.text().strip()
        if not entry:
            return
        lowered = entry.lower()
        if lowered.startswith("hex:"):
            try:
                data = bytes.fromhex(entry[4:].replace(",", " "))
            except ValueError:
                self.log("!! invalid hex string — use pairs like 01 69 32 30 31 30 30 03")
                return
            if not data:
                self.log("!! nothing to send")
                return
            self.send_raw(data)
        elif lowered.startswith("text:"):
            text = entry[5:].replace("<SOH>", "\x01").replace("<ETX>", "\x03")
            try:
                self.send_raw(text.encode("ascii"))
            except UnicodeEncodeError:
                self.log("!! text mode accepts ASCII only — use hex: for other bytes")
        else:
            self.send_function(entry)

    def send_function(self, func: str):
        try:
            self.send_raw(build_command(func))
        except UnicodeEncodeError:
            self.log("!! function codes must be ASCII")

    def send_raw(self, data: bytes):
        if self.simulator:
            self.log_frame("TX", data)
            self.on_tx_bytes(len(data))
            if data.startswith(SOH) and data.endswith(ETX):
                func = data[1:-1].decode("ascii", errors="replace")
            else:
                func = ""  # unframed request — the gauge answers 9999
            reply = self.simulator.respond(func)
            QTimer.singleShot(120, lambda: (self.on_rx_bytes(len(reply)),
                                            self.on_frame(reply)))
        elif self.worker:
            self.log_frame("TX", data)
            self.worker.send(data)
        else:
            self.log("!! not connected")

    # ---------------------------------------------------------- responses
    def on_frame(self, frame: bytes):
        self.log_frame("RX", frame)
        try:
            resp = parse_response(frame)
        except TlsError as exc:
            self.log(f"!! {exc}")
            return
        if resp["checksum_ok"] is False:
            self.log("!! checksum mismatch — data may be corrupted")
        func = resp["func"].lower()
        try:
            if func.startswith("i201"):
                self.update_tanks(parse_i201(resp["payload"]))
            elif func.startswith("i202"):
                self.update_deliveries(parse_i202(resp["payload"]))
        except (ValueError, struct.error) as exc:
            self.log(f"!! parse error: {exc}")

    def update_tanks(self, tanks: list):
        now = datetime.now().strftime("%H:%M:%S")
        for rec in tanks:
            row = self._find_or_add_row(self.tank_table, rec["tank"])
            f = rec["fields"] + [0.0] * 7
            status_bits = int(rec["status"], 16)
            notes = []
            if status_bits & 0x1:
                notes.append("delivery")
            if status_bits & 0x2:
                notes.append("leak test")
            status_text = rec["status"] + (" (" + ", ".join(notes) + ")" if notes else "")
            values = [
                rec["tank"], rec["product"], status_text,
                f"{f[0]:.1f}", f"{f[1]:.1f}", f"{f[2]:.1f}",
                f"{f[3]:.1f}", f"{f[4]:.1f}", f"{f[5]:.2f}", f"{f[6]:.1f}", now,
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                if col == 7 and f[4] > 20:  # high water warning
                    item.setForeground(QColor("#e05555"))
                self.tank_table.setItem(row, col, item)

    def update_deliveries(self, deliveries: list):
        self.delivery_table.setRowCount(0)
        for rec in deliveries:
            f = rec["fields"] + [0.0] * 10
            row = self.delivery_table.rowCount()
            self.delivery_table.insertRow(row)
            values = [
                rec["tank"], rec["product"],
                fmt_tls_datetime(rec["start"]), fmt_tls_datetime(rec["end"]),
                f"{f[0]:.1f}", f"{f[4]:.1f}", f"{f[4] - f[0]:.1f}",
                f"{f[8]:.1f}", f"{f[9]:.1f}",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                self.delivery_table.setItem(row, col, item)

    def _find_or_add_row(self, table: QTableWidget, tank: str) -> int:
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and item.text() == tank:
                return row
        row = table.rowCount()
        table.insertRow(row)
        return row

    # ------------------------------------------------------------ logging
    def log(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.console.append(f"[{ts}] {text}")

    def log_frame(self, direction: str, frame: bytes):
        if self.hex_check.isChecked():
            body = frame.hex(" ").upper()
        else:
            body = (frame.decode("ascii", errors="replace")
                    .replace("\x01", "<SOH>").replace("\x03", "<ETX>"))
        self.log(f"{direction} {body}")

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log", "atg_host_log.txt", "Text files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.console.toPlainText())
            self.log(f"--- log saved to {path} ---")

    # ------------------------------------------------------------- close
    def closeEvent(self, event):
        self.poll_timer.stop()
        if self.worker:
            self.worker.stop()
            self.worker.wait(2000)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
