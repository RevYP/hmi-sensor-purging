import sys
import time
import os
from datetime import datetime

# --- IMPORT PYQT5 FOR HMI GUI ---
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QTextEdit, QProgressBar, QGridLayout
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QFont, QPixmap

# --- GPIO SETUP ---
RELAY_P_WATER = 17  # GPIO 17 -> Solenoid Air (Partikuler)
RELAY_P_AIR   = 27  # GPIO 27 -> Solenoid Udara (Partikuler)
RELAY_A_WATER = 22  # GPIO 22 -> Solenoid Air (Amonia)
RELAY_A_AIR   = 23  # GPIO 23 -> Solenoid Udara (Amonia)

RELAY_ON  = 0  # Active-LOW Relay
RELAY_OFF = 1

try:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in [RELAY_P_WATER, RELAY_P_AIR, RELAY_A_WATER, RELAY_A_AIR]:
        GPIO.setup(pin, GPIO.OUT, initial=RELAY_OFF)
    IS_PI = True
except (ImportError, RuntimeError):
    IS_PI = False

# --- ADC ADS1115 SETUP ---
try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c)
    chan_p = AnalogIn(ads, ADS.P0)  # Channel A0: Partikuler
    chan_a = AnalogIn(ads, ADS.P1)  # Channel A1: Amonia
    HAS_ADC = True
except Exception:
    HAS_ADC = False

# --- DURASI PURGING (15 Menit Water + 15 Menit Air) ---
WATER_DURATION_SEC = 15 * 60  # 900 detik
AIR_DURATION_SEC   = 15 * 60  # 900 detik


# ==============================================================================
# THREAD PURGING SEKUENSIAL (NON-BLOCKING HMI)
# ==============================================================================
class PurgingWorker(QThread):
    status_signal = pyqtSignal(str, str)     # unit, status text
    log_signal = pyqtSignal(str)              # log message
    valve_signal = pyqtSignal(str, bool, bool) # unit, water_on, air_on
    progress_signal = pyqtSignal(str, int)    # unit, progress %
    finished_signal = pyqtSignal(str)         # unit

    def __init__(self, unit_name, water_pin, air_pin):
        super().__init__()
        self.unit_name = unit_name
        self.water_pin = water_pin
        self.air_pin = air_pin
        self._is_running = True

    def run(self):
        total_time = WATER_DURATION_SEC + AIR_DURATION_SEC
        elapsed = 0

        # TAHAP 1: WATER FLUSHING (15 MENIT)
        if not self._is_running: return
        self.log_signal.emit(f"[{self.unit_name}] TAHAP 1: Memulai Water Flushing (15 Menit)...")
        self.status_signal.emit(self.unit_name, "WATER FLUSHING")
        self.set_valves(water_on=True, air_on=False)

        for _ in range(WATER_DURATION_SEC):
            if not self._is_running: break
            time.sleep(1)
            elapsed += 1
            self.progress_signal.emit(self.unit_name, int((elapsed / total_time) * 100))

        # TAHAP 2: AIR PURGING (15 MENIT)
        if self._is_running:
            self.log_signal.emit(f"[{self.unit_name}] TAHAP 2: Memulai Air Purging (15 Menit)...")
            self.status_signal.emit(self.unit_name, "AIR PURGING")
            self.set_valves(water_on=False, air_on=True)

            for _ in range(AIR_DURATION_SEC):
                if not self._is_running: break
                time.sleep(1)
                elapsed += 1
                self.progress_signal.emit(self.unit_name, int((elapsed / total_time) * 100))

        # SELESAI / STANDBY
        self.set_valves(water_on=False, air_on=False)
        if self._is_running:
            self.log_signal.emit(f"[{self.unit_name}] Pembersihan Sekuensial Selesai. System Standby.")
            self.status_signal.emit(self.unit_name, "STANDBY")
            self.progress_signal.emit(self.unit_name, 100)
        else:
            self.log_signal.emit(f"[{self.unit_name}] Pembersihan Dihentikan (Emergency Stop).")
            self.status_signal.emit(self.unit_name, "STOPPED")

        self.finished_signal.emit(self.unit_name)

    def set_valves(self, water_on: bool, air_on: bool):
        self.valve_signal.emit(self.unit_name, water_on, air_on)
        if IS_PI:
            GPIO.output(self.water_pin, RELAY_ON if water_on else RELAY_OFF)
            GPIO.output(self.air_pin, RELAY_ON if air_on else RELAY_OFF)

    def stop(self):
        self._is_running = False
        self.set_valves(water_on=False, air_on=False)


# ==============================================================================
# HMI MAIN WINDOW
# ==============================================================================
class SensorHMIWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HMI Auto Purging - PT Pupuk Kujang")
        self.showFullScreen()
        self.setStyleSheet("background-color: #0f172a; color: #ffffff;")

        self.worker_p = None
        self.worker_a = None
        self.last_purge_p = time.time()
        self.last_purge_a = time.time()

        self.init_ui()

        # Timer Pembacaan ADC & Trigger Automatis (Setiap 1 Detik)
        self.adc_timer = QTimer()
        self.adc_timer.timeout.connect(self.process_adc_and_triggers)
        self.adc_timer.start(1000)

    def init_ui(self):
        main_layout = QVBoxLayout()

        # --- 1. HEADER DENGAN LOGO PUPUK KUJANG ---
        header_frame = QFrame()
        header_frame.setStyleSheet("background-color: #1e293b; border-radius: 8px; padding: 10px;")
        header_layout = QHBoxLayout(header_frame)

        # Import Logo Pupuk Kujang
        lbl_logo = QLabel()
        logo_path = "logo_mobile (1).png"
        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            lbl_logo.setPixmap(pixmap.scaledToHeight(65, Qt.SmoothTransformation))
        else:
            lbl_logo.setText("[LOGO]")
            lbl_logo.setFont(QFont("Arial", 12, QFont.Bold))
        header_layout.addWidget(lbl_logo)

        # Judul Header
        title_box = QVBoxLayout()
        title_main = QLabel("SYSTEM HMI AUTOMATIC SENSOR PURGING")
        title_main.setFont(QFont("Arial", 16, QFont.Bold))
        title_main.setStyleSheet("color: #38bdf8;")
        
        title_sub = QLabel("Prilling Tower PT Pupuk Kujang Cikampek | Sensor Teledyne LGA 4000Z")
        title_sub.setFont(QFont("Arial", 10))
        title_sub.setStyleSheet("color: #94a3b8;")

        title_box.addWidget(title_main)
        title_box.addWidget(title_sub)
        header_layout.addLayout(title_box)
        header_layout.addStretch()

        main_layout.addWidget(header_frame)

        # --- 2. MONITORING PANEL (PARTIKULER & AMONIA) ---
        grid_panel = QGridLayout()

        # Card Partikuler
        grid_panel.addWidget(self.create_unit_card("PARTIKULER", "P"), 0, 0)
        # Card Amonia
        grid_panel.addWidget(self.create_unit_card("AMONIA", "A"), 0, 1)

        main_layout.addLayout(grid_panel)

        # --- 3. CONTROL BUTTONS ---
        btn_layout = QHBoxLayout()

        self.btn_start_p = QPushButton("START PURGING PARTIKULER")
        self.btn_start_p.setFont(QFont("Arial", 10, QFont.Bold))
        self.btn_start_p.setStyleSheet("background-color: #0284c7; color: white; padding: 12px; border-radius: 6px;")
        self.btn_start_p.clicked.connect(lambda: self.start_purging("P"))
        btn_layout.addWidget(self.btn_start_p)

        self.btn_start_a = QPushButton("START PURGING AMONIA")
        self.btn_start_a.setFont(QFont("Arial", 10, QFont.Bold))
        self.btn_start_a.setStyleSheet("background-color: #0d9488; color: white; padding: 12px; border-radius: 6px;")
        self.btn_start_a.clicked.connect(lambda: self.start_purging("A"))
        btn_layout.addWidget(self.btn_start_a)

        self.btn_stop = QPushButton("EMERGENCY STOP ALL")
        self.btn_stop.setFont(QFont("Arial", 10, QFont.Bold))
        self.btn_stop.setStyleSheet("background-color: #dc2626; color: white; padding: 12px; border-radius: 6px;")
        self.btn_stop.clicked.connect(self.stop_all_purging)
        btn_layout.addWidget(self.btn_stop)

        self.btn_exit = QPushButton("KELUAR HMI")
        self.btn_exit.setFont(QFont("Arial", 10, QFont.Bold))
        self.btn_exit.setStyleSheet("background-color: #475569; color: white; padding: 12px; border-radius: 6px;")
        self.btn_exit.clicked.connect(self.close)
        btn_layout.addWidget(self.btn_exit)

        main_layout.addLayout(btn_layout)

        # --- 4. LOG ACTIVITY BOX ---
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #020617; color: #38bdf8; font-family: Monospace; font-size: 9pt; border-radius: 6px;")
        main_layout.addWidget(self.txt_log)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.append_log("Sistem HMI Berhasil Dimuat. Logo Pupuk Kujang Terpasang.")

    def create_unit_card(self, title, code):
        card = QFrame()
        card.setStyleSheet("background-color: #1e293b; border-radius: 8px; padding: 10px;")
        layout = QVBoxLayout(card)

        lbl_title = QLabel(f"UNIT ANALYZER {title}")
        lbl_title.setFont(QFont("Arial", 12, QFont.Bold))
        lbl_title.setStyleSheet("color: #f8fafc;")
        layout.addWidget(lbl_title)

        # Current mA Display
        lbl_ma = QLabel("Sinyal Analog: 12.00 mA")
        lbl_ma.setFont(QFont("Arial", 11, QFont.Bold))
        lbl_ma.setStyleSheet("color: #34d399;")
        setattr(self, f"lbl_ma_{code}", lbl_ma)
        layout.addWidget(lbl_ma)

        # Valve Indicators
        valves_layout = QHBoxLayout()
        
        lbl_w = QLabel("WATER VALVE\n[ OFF ]")
        lbl_w.setAlignment(Qt.AlignCenter)
        lbl_w.setFont(QFont("Arial", 9, QFont.Bold))
        lbl_w.setStyleSheet("background-color: #334155; color: #94a3b8; border-radius: 4px; padding: 10px;")
        setattr(self, f"lbl_w_{code}", lbl_w)
        valves_layout.addWidget(lbl_w)

        lbl_a = QLabel("AIR VALVE\n[ OFF ]")
        lbl_a.setAlignment(Qt.AlignCenter)
        lbl_a.setFont(QFont("Arial", 9, QFont.Bold))
        lbl_a.setStyleSheet("background-color: #334155; color: #94a3b8; border-radius: 4px; padding: 10px;")
        setattr(self, f"lbl_a_{code}", lbl_a)
        valves_layout.addWidget(lbl_a)

        layout.addLayout(valves_layout)

        # Progress Bar
        pbar = QProgressBar()
        pbar.setValue(0)
        pbar.setFixedHeight(18)
        pbar.setStyleSheet("""
            QProgressBar { border: 1px solid #475569; border-radius: 4px; text-align: center; color: white; background: #0f172a; }
            QProgressBar::chunk { background-color: #38bdf8; }
        """)
        setattr(self, f"pbar_{code}", pbar)
        layout.addWidget(pbar)

        # Status Label
        lbl_status = QLabel("STATUS: STANDBY")
        lbl_status.setFont(QFont("Arial", 10, QFont.Bold))
        lbl_status.setStyleSheet("color: #4ade80;")
        setattr(self, f"lbl_status_{code}", lbl_status)
        layout.addWidget(lbl_status)

        return card

    # ==========================================================================
    # LOGIKA READ ADC & 3 TRIGGER CONDITIONS
    # ==========================================================================
    def process_adc_and_triggers(self):
        # Pembacaan Sinyal mA
        ma_p = self.read_current_ma("P")
        ma_a = self.read_current_ma("A")

        getattr(self, "lbl_ma_P").setText(f"Sinyal Analog: {ma_p:.2f} mA")
        getattr(self, "lbl_ma_A").setText(f"Sinyal Analog: {ma_a:.2f} mA")

        # --- CHECK TRIGGER 2: SENSOR FAULT (<4mA ATAU >20mA) ---
        if (ma_p < 4.0 or ma_p > 20.0) and (not self.worker_p or not self.worker_p.isRunning()):
            self.append_log(f"[TRIGGER SENSOR FAULT] Unit Partikuler terdeteksi fault ({ma_p:.2f} mA). Memulai Purging...")
            self.start_purging("P")

        if (ma_a < 4.0 or ma_a > 20.0) and (not self.worker_a or not self.worker_a.isRunning()):
            self.append_log(f"[TRIGGER SENSOR FAULT] Unit Amonia terdeteksi fault ({ma_a:.2f} mA). Memulai Purging...")
            self.start_purging("A")

        # --- CHECK TRIGGER 3: TIMER MINGGUAN (168 JAM) ---
        weekly_sec = 7 * 24 * 3600
        now = time.time()
        if (now - self.last_purge_p >= weekly_sec) and (not self.worker_p or not self.worker_p.isRunning()):
            self.append_log("[TRIGGER TIMER MINGGUAN] Rutin 1 Minggu Partikuler. Memulai Purging...")
            self.start_purging("P")

        if (now - self.last_purge_a >= weekly_sec) and (not self.worker_a or not self.worker_a.isRunning()):
            self.append_log("[TRIGGER TIMER MINGGUAN] Rutin 1 Minggu Amonia. Memulai Purging...")
            self.start_purging("A")

    def read_current_ma(self, code):
        if HAS_ADC:
            try:
                chan = chan_p if code == "P" else chan_a
                voltage = chan.voltage
                ma = (voltage / 3.3) * 20.0
                return max(0.0, min(ma, 25.0))
            except Exception:
                return 12.0
        return 12.00  # Default simulasi normal

    # ==========================================================================
    # PURGING CONTROL HANDLERS
    # ==========================================================================
    def start_purging(self, code):
        if code == "P":
            if self.worker_p and self.worker_p.isRunning(): return
            self.last_purge_p = time.time()
            self.worker_p = PurgingWorker("PARTIKULER", RELAY_P_WATER, RELAY_P_AIR)
            self.connect_worker_signals(self.worker_p, "P")
            self.worker_p.start()
        else:
            if self.worker_a and self.worker_a.isRunning(): return
            self.last_purge_a = time.time()
            self.worker_a = PurgingWorker("AMONIA", RELAY_A_WATER, RELAY_A_AIR)
            self.connect_worker_signals(self.worker_a, "A")
            self.worker_a.start()

    def connect_worker_signals(self, worker, code):
        worker.status_signal.connect(lambda unit, txt: getattr(self, f"lbl_status_{code}").setText(f"STATUS: {txt}"))
        worker.log_signal.connect(self.append_log)
        worker.valve_signal.connect(self.update_valve_indicators)
        worker.progress_signal.connect(lambda unit, val: getattr(self, f"pbar_{code}").setValue(val))

    def update_valve_indicators(self, unit_name, water_on, air_on):
        code = "P" if unit_name == "PARTIKULER" else "A"
        lbl_w = getattr(self, f"lbl_w_{code}")
        lbl_a = getattr(self, f"lbl_a_{code}")

        if water_on:
            lbl_w.setText("WATER VALVE\n[ SPRAYING ]")
            lbl_w.setStyleSheet("background-color: #38bdf8; color: #020617; border-radius: 4px; padding: 10px; font-weight: bold;")
        else:
            lbl_w.setText("WATER VALVE\n[ OFF ]")
            lbl_w.setStyleSheet("background-color: #334155; color: #94a3b8; border-radius: 4px; padding: 10px;")

        if air_on:
            lbl_a.setText("AIR VALVE\n[ PURGING ]")
            lbl_a.setStyleSheet("background-color: #f97316; color: #020617; border-radius: 4px; padding: 10px; font-weight: bold;")
        else:
            lbl_a.setText("AIR VALVE\n[ OFF ]")
            lbl_a.setStyleSheet("background-color: #334155; color: #94a3b8; border-radius: 4px; padding: 10px;")

    def stop_all_purging(self):
        if self.worker_p and self.worker_p.isRunning(): self.worker_p.stop()
        if self.worker_a and self.worker_a.isRunning(): self.worker_a.stop()

    def append_log(self, msg):
        now = datetime.now().strftime("%H:%M:%S")
        self.txt_log.append(f"[{now}] {msg}")

    def closeEvent(self, event):
        if IS_PI:
            GPIO.cleanup()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = SensorHMIWindow()
    win.show()
    sys.exit(app.exec_())
