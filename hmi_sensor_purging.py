import sys
import time
from datetime import datetime
import RPi.GPIO as GPIO

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QTextEdit, QProgressBar
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont

# --- KONFIGURASI PIN GPIO ---
RELAY_WATER_PIN = 17  # GPIO 17 -> Valve Air
RELAY_AIR_PIN = 27    # GPIO 27 -> Valve Udara Tekan

# Kebanyakan modul relay bersifat Active-LOW (LOW = Relay On/Klep Buka)
RELAY_ON = GPIO.LOW
RELAY_OFF = GPIO.HIGH

# Inisialisasi Setup GPIO Raspberry Pi
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(RELAY_WATER_PIN, GPIO.OUT, initial=RELAY_OFF)
GPIO.setup(RELAY_AIR_PIN, GPIO.OUT, initial=RELAY_OFF)


class PurgingWorker(QThread):
    status_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)
    water_status_signal = pyqtSignal(bool)
    air_status_signal = pyqtSignal(bool)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal()

    def __init__(self, water_time=5, air_time=10):
        super().__init__()
        self.water_time = water_time
        self.air_time = air_time
        self._is_running = True

    def run(self):
        total_time = self.water_time + self.air_time
        current_progress = 0

        # TAHAP 1: WATER FLUSHING
        if not self._is_running:
            return
        self.log_signal.emit("START: Memulai Water Flushing (Melarutkan Kerak)...")
        self.status_signal.emit("WATER FLUSHING (PEMBERSIHAN AIR)")
        self.set_valves(water_on=True, air_on=False)

        for i in range(self.water_time):
            if not self._is_running:
                break
            time.sleep(1)
            current_progress += (1 / total_time) * 100
            self.progress_signal.emit(int(current_progress))
            self.log_signal.emit(f"-> Menyemprot air... Sisa: {self.water_time - (i + 1)} detik")

        # TAHAP 2: AIR PURGING & DRYING
        if self._is_running:
            self.log_signal.emit("START: Memulai Air Purging (Pengeringan Optik)...")
            self.status_signal.emit("AIR PURGING (PENGERINGAN UDARA)")
            self.set_valves(water_on=False, air_on=True)

            for i in range(self.air_time):
                if not self._is_running:
                    break
                time.sleep(1)
                current_progress += (1 / total_time) * 100
                self.progress_signal.emit(int(current_progress))
                self.log_signal.emit(f"-> Menyemprot udara... Sisa: {self.air_time - (i + 1)} detik")

        # STANDBY
        self.set_valves(water_on=False, air_on=False)
        if self._is_running:
            self.log_signal.emit("Siklus Pembersihan Selesai. Sensor Siap Digunakan.")
            self.status_signal.emit("STANDBY / MONITORING")
            self.progress_signal.emit(100)
        else:
            self.log_signal.emit("Pembersihan Dihentikan Paksa (Emergency Stop).")
            self.status_signal.emit("EMERGENCY STOPPED")

        self.finished_signal.emit()

    def set_valves(self, water_on: bool, air_on: bool):
        self.water_status_signal.emit(water_on)
        self.air_status_signal.emit(air_on)
        GPIO.output(RELAY_WATER_PIN, RELAY_ON if water_on else RELAY_OFF)
        GPIO.output(RELAY_AIR_PIN, RELAY_ON if air_on else RELAY_OFF)

    def stop(self):
        self._is_running = False
        self.set_valves(water_on=False, air_on=False)


class SensorHMIWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HMI Auto Purging - Teledyne LGA 4000Z")
        self.showFullScreen()  # Tampilan penuh layar HMI
        self.setStyleSheet("background-color: #111827; color: #ffffff;")

        self.worker = None
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout()

        # Header
        title = QLabel("SYSTEM HMI AUTOMATIC SENSOR CLEANING")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #38bdf8; padding: 12px; background-color: #1e293b; border-radius: 8px;")
        main_layout.addWidget(title)

        sub_title = QLabel("Area: Prilling Tower PT Pupuk Kujang Cikampek | Sensor: Teledyne LGA 4000Z")
        sub_title.setFont(QFont("Arial", 10))
        sub_title.setAlignment(Qt.AlignCenter)
        sub_title.setStyleSheet("color: #94a3b8; margin-bottom: 8px;")
        main_layout.addWidget(sub_title)

        # Indicator Frame
        status_frame = QFrame()
        status_frame.setStyleSheet("background-color: #1e293b; border-radius: 8px; padding: 10px;")
        status_layout = QHBoxLayout(status_frame)

        self.lbl_water = QLabel("SOLENOID WATER\n[ OFF ]")
        self.lbl_water.setFont(QFont("Arial", 12, QFont.Bold))
        self.lbl_water.setAlignment(Qt.AlignCenter)
        self.lbl_water.setStyleSheet("background-color: #334155; color: #94a3b8; border-radius: 6px; padding: 20px;")
        status_layout.addWidget(self.lbl_water)

        self.lbl_air = QLabel("SOLENOID AIR\n[ OFF ]")
        self.lbl_air.setFont(QFont("Arial", 12, QFont.Bold))
        self.lbl_air.setAlignment(Qt.AlignCenter)
        self.lbl_air.setStyleSheet("background-color: #334155; color: #94a3b8; border-radius: 6px; padding: 20px;")
        status_layout.addWidget(self.lbl_air)

        main_layout.addWidget(status_frame)

        # Status & Progress
        self.lbl_status = QLabel("STATUS SISTEM: STANDBY")
        self.lbl_status.setFont(QFont("Arial", 12, QFont.Bold))
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color: #4ade80; margin-top: 8px;")
        main_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(25)
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #475569; border-radius: 6px; text-align: center; color: white; background: #1e293b; }
            QProgressBar::chunk { background-color: #0284c7; }
        """)
        main_layout.addWidget(self.progress_bar)

        # Tombol Kontrol
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("START PURGING")
        self.btn_start.setFont(QFont("Arial", 11, QFont.Bold))
        self.btn_start.setStyleSheet("background-color: #22c55e; color: #000000; padding: 14px; border-radius: 6px;")
        self.btn_start.clicked.connect(self.start_purging)
        btn_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("EMERGENCY STOP")
        self.btn_stop.setFont(QFont("Arial", 11, QFont.Bold))
        self.btn_stop.setStyleSheet("background-color: #ef4444; color: #ffffff; padding: 14px; border-radius: 6px;")
        self.btn_stop.clicked.connect(self.stop_purging)
        self.btn_stop.setEnabled(False)
        btn_layout.addWidget(self.btn_stop)

        self.btn_exit = QPushButton("KELUAR HMI")
        self.btn_exit.setFont(QFont("Arial", 11, QFont.Bold))
        self.btn_exit.setStyleSheet("background-color: #64748b; color: #ffffff; padding: 14px; border-radius: 6px;")
        self.btn_exit.clicked.connect(self.close)
        btn_layout.addWidget(self.btn_exit)

        main_layout.addLayout(btn_layout)

        # Log Text Box
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #020617; color: #e2e8f0; font-family: Monospace; font-size: 10pt; border-radius: 6px;")
        main_layout.addWidget(self.txt_log)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.append_log("Sistem HMI Berhasil Dijalankan di Raspberry Pi.")

    def start_purging(self):
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)

        self.worker = PurgingWorker(water_time=5, air_time=10)
        self.worker.status_signal.connect(lambda txt: self.lbl_status.setText(f"STATUS SISTEM: {txt}"))
        self.worker.log_signal.connect(self.append_log)
        self.worker.water_status_signal.connect(self.update_water_indicator)
        self.worker.air_status_signal.connect(self.update_air_indicator)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def stop_purging(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()

    def on_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def update_water_indicator(self, is_active):
        if is_active:
            self.lbl_water.setText("SOLENOID WATER\n[ SPRAYING ON ]")
            self.lbl_water.setStyleSheet("background-color: #38bdf8; color: #0f172a; border-radius: 6px; padding: 20px; font-weight: bold;")
        else:
            self.lbl_water.setText("SOLENOID WATER\n[ OFF ]")
            self.lbl_water.setStyleSheet("background-color: #334155; color: #94a3b8; border-radius: 6px; padding: 20px;")

    def update_air_indicator(self, is_active):
        if is_active:
            self.lbl_air.setText("SOLENOID AIR\n[ PURGING ON ]")
            self.lbl_air.setStyleSheet("background-color: #f97316; color: #0f172a; border-radius: 6px; padding: 20px; font-weight: bold;")
        else:
            self.lbl_air.setText("SOLENOID AIR\n[ OFF ]")
            self.lbl_air.setStyleSheet("background-color: #334155; color: #94a3b8; border-radius: 6px; padding: 20px;")

    def append_log(self, msg):
        now = datetime.now().strftime("%H:%M:%S")
        self.txt_log.append(f"[{now}] {msg}")

    def closeEvent(self, event):
        GPIO.cleanup()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = SensorHMIWindow()
    win.show()
    sys.exit(app.exec_())
