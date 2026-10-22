import sys
import time
from datetime import datetime

# --- IMPORT PYQT5 (UNTUK TAMPILAN HMI) ---
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QTextEdit, QProgressBar
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont

# --- SETUP GPIO & MOCK UNTUK LAPTOP ---
RELAY_WATER_PIN = 17  # GPIO 17 -> Relay Solenoid Water
RELAY_AIR_PIN = 27    # GPIO 27 -> Relay Solenoid Air

# Catatan: Kebanyakan modul Relay Aktif LOW (LOW = Nyala/Valve Buka, HIGH = Mati/Valve Tutup)
RELAY_ON = 0   # GPIO.LOW
RELAY_OFF = 1  # GPIO.HIGH

try:
    import RPi.GPIO as GPIO
    IS_RASPBERRY_PI = True
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(RELAY_WATER_PIN, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(RELAY_AIR_PIN, GPIO.OUT, initial=GPIO.HIGH)
except (ImportError, RuntimeError):
    IS_RASPBERRY_PI = False
    print("[INFO] Menjalankan dalam Mode Simulasi (Bukan di Raspberry Pi)")


# ==============================================================================
# THREAD PROSES PEMBERSIHAN SEKUENSIAL (Agar HMI Tidak Freeze)
# ==============================================================================
class PurgingWorker(QThread):
    # Signal untuk mengirim update ke HMI
    status_signal = pyqtSignal(str)        # Pesan status
    log_signal = pyqtSignal(str)           # Pesan log
    water_status_signal = pyqtSignal(bool) # Status valve air (True/False)
    air_status_signal = pyqtSignal(bool)   # Status valve udara (True/False)
    progress_signal = pyqtSignal(int)      # Persentase progress (0-100%)
    finished_signal = pyqtSignal()         # Selesai

    def __init__(self, water_time=5, air_time=10):
        super().__init__()
        self.water_time = water_time
        self.air_time = air_time
        self._is_running = True

    def run(self):
        total_time = self.water_time + self.air_time
        current_progress = 0

        # ----------------------------------------------------------------------
        # TAHAP 1: WATER FLUSHING (Pembersihan Kerak Urea dengan Air)
        # ----------------------------------------------------------------------
        if not self._is_running: return
        self.log_signal.emit("TAHAP 1: Memulai Water Flushing...")
        self.status_signal.emit("WATER FLUSHING (PEMBERSIHAN AIR)")
        
        # Buka Valve Air, Tutup Valve Udara
        self.set_valves(water_on=True, air_on=False)
        
        for i in range(self.water_time):
            if not self._is_running: break
            time.sleep(1)
            current_progress += (1 / total_time) * 100
            self.progress_signal.emit(int(current_progress))
            sisa = self.water_time - (i + 1)
            self.log_signal.emit(f"-> Menyemprot air... Sisa waktu: {sisa} detik")

        # ----------------------------------------------------------------------
        # TAHAP 2: AIR PURGING & DRYING (Pengeringan Optik dengan Udara Tekan)
        # ----------------------------------------------------------------------
        if self._is_running:
            self.log_signal.emit("TAHAP 2: Memulai Air Purging & Drying...")
            self.status_signal.emit("AIR PURGING (PENGERINGAN UDARA)")
            
            # Tutup Valve Air, Buka Valve Udara (Sekuensial Terpisah!)
            self.set_valves(water_on=False, air_on=True)

            for i in range(self.air_time):
                if not self._is_running: break
                time.sleep(1)
                current_progress += (1 / total_time) * 100
                self.progress_signal.emit(int(current_progress))
                sisa = self.air_time - (i + 1)
                self.log_signal.emit(f"-> Menyemprot udara... Sisa waktu: {sisa} detik")

        # ----------------------------------------------------------------------
        # TAHAP 3: FINISH / STANDBY
        # ----------------------------------------------------------------------
        self.set_valves(water_on=False, air_on=False)
        if self._is_running:
            self.log_signal.emit("Pembersihan Sekuensial Selesai. Sensor Standby.")
            self.status_signal.emit("STANDBY / MONITORING")
            self.progress_signal.emit(100)
        else:
            self.log_signal.emit("Pembersihan Dihentikan (Emergency Stop).")
            self.status_signal.emit("STOPPED")

        self.finished_signal.emit()

    def set_valves(self, water_on: bool, air_on: bool):
        """Mengontrol Hardware GPIO atau Simulasi Terminal"""
        self.water_status_signal.emit(water_on)
        self.air_status_signal.emit(air_on)

        if IS_RASPBERRY_PI:
            GPIO.output(RELAY_WATER_PIN, RELAY_ON if water_on else RELAY_OFF)
            GPIO.output(RELAY_AIR_PIN, RELAY_ON if air_on else RELAY_OFF)

    def stop(self):
        self._is_running = False
        self.set_valves(water_on=False, air_on=False)


# ==============================================================================
# DESAIN TAMPILAN HMI (MAIN WINDOW)
# ==============================================================================
class SensorHMIWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HMI System - Teledyne LGA 4000Z Auto Purging")
        self.setGeometry(100, 100, 800, 520)
        self.setStyleSheet("background-color: #1e1e2e; color: #ffffff;")

        self.worker = None
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout()

        # 1. HEADER TITLE
        title_label = QLabel("HMI AUTOMATIC SENSOR CLEANING SYSTEM")
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #89b4fa; padding: 10px; background-color: #181825; border-radius: 8px;")
        main_layout.addWidget(title_label)

        sub_title = QLabel("Sensor Target: Teledyne LGA 4000Z (Prilling Tower PT Pupuk Kujang)")
        sub_title.setFont(QFont("Arial", 10))
        sub_title.setAlignment(Qt.AlignCenter)
        sub_title.setStyleSheet("color: #a6adc8; margin-bottom: 10px;")
        main_layout.addWidget(sub_title)

        # 2. STATUS SYSTEM & VALVE INDICATORS
        status_frame = QFrame()
        status_frame.setStyleSheet("background-color: #181825; border-radius: 8px; padding: 15px;")
        status_layout = QHBoxLayout(status_frame)

        # Indikator Valve Air
        self.lbl_water = QLabel("SOLENOID WATER\n[ OFF ]")
        self.lbl_water.setFont(QFont("Arial", 11, QFont.Bold))
        self.lbl_water.setAlignment(Qt.AlignCenter)
        self.lbl_water.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 15px;")
        status_layout.addWidget(self.lbl_water)

        # Indikator Valve Udara
        self.lbl_air = QLabel("SOLENOID AIR\n[ OFF ]")
        self.lbl_air.setFont(QFont("Arial", 11, QFont.Bold))
        self.lbl_air.setAlignment(Qt.AlignCenter)
        self.lbl_air.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 15px;")
        status_layout.addWidget(self.lbl_air)

        main_layout.addWidget(status_frame)

        # 3. SYSTEM STATE & PROGRESS BAR
        self.lbl_system_state = QLabel("STATUS SISTEM: STANDBY")
        self.lbl_system_state.setFont(QFont("Arial", 12, QFont.Bold))
        self.lbl_system_state.setAlignment(Qt.AlignCenter)
        self.lbl_system_state.setStyleSheet("color: #a6e3a1; margin-top: 10px;")
        main_layout.addWidget(self.lbl_system_state)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid #45475a; border-radius: 5px; text-align: center; color: white; background: #313244;
            }
            QProgressBar::chunk { background-color: #89b4fa; }
        """)
        main_layout.addWidget(self.progress_bar)

        # 4. CONTROL BUTTONS
        btn_layout = QHBoxLayout()
        
        self.btn_start = QPushButton("START PURGING CYCLE")
        self.btn_start.setFont(QFont("Arial", 11, QFont.Bold))
        self.btn_start.setStyleSheet("background-color: #a6e3a1; color: #11111b; padding: 12px; border-radius: 6px;")
        self.btn_start.clicked.connect(self.start_purging)
        btn_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("EMERGENCY STOP")
        self.btn_stop.setFont(QFont("Arial", 11, QFont.Bold))
        self.btn_stop.setStyleSheet("background-color: #f38ba8; color: #11111b; padding: 12px; border-radius: 6px;")
        self.btn_stop.clicked.connect(self.stop_purging)
        self.btn_stop.setEnabled(False)
        btn_layout.addWidget(self.btn_stop)

        main_layout.addLayout(btn_layout)

        # 5. ACTIVITY LOG BOX
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #11111b; color: #cdd6f4; font-family: Monospace; font-size: 10pt; border-radius: 6px;")
        main_layout.addWidget(self.txt_log)

        # Set Container
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.append_log("Sistem HMI Siap. Mode: " + ("Raspberry Pi GPIO" if IS_RASPBERRY_PI else "Simulasi Laptop"))

    # ==========================================================================
    # LOGIC HANDLERS
    # ==========================================================================
    def start_purging(self):
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)
        
        # Inisialisasi Thread Sekuensial (Durasi Water = 5s, Air = 10s)
        self.worker = PurgingWorker(water_time=5, air_time=10)
        self.worker.status_signal.connect(self.update_system_status)
        self.worker.log_signal.connect(self.append_log)
        self.worker.water_status_signal.connect(self.update_water_indicator)
        self.worker.air_status_signal.connect(self.update_air_indicator)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self.on_purging_finished)
        self.worker.start()

    def stop_purging(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn_stop.setEnabled(False)

    def on_purging_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def update_system_status(self, text):
        self.lbl_system_state.setText(f"STATUS SISTEM: {text}")

    def update_water_indicator(self, is_active):
        if is_active:
            self.lbl_water.setText("SOLENOID WATER\n[ SPRAYING ON ]")
            self.lbl_water.setStyleSheet("background-color: #89b4fa; color: #11111b; border-radius: 6px; padding: 15px; font-weight: bold;")
        else:
            self.lbl_water.setText("SOLENOID WATER\n[ OFF ]")
            self.lbl_water.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 15px;")

    def update_air_indicator(self, is_active):
        if is_active:
            self.lbl_air.setText("SOLENOID AIR\n[ PURGING ON ]")
            self.lbl_air.setStyleSheet("background-color: #fab387; color: #11111b; border-radius: 6px; padding: 15px; font-weight: bold;")
        else:
            self.lbl_air.setText("SOLENOID AIR\n[ OFF ]")
            self.lbl_air.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 15px;")

    def append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.txt_log.append(f"[{timestamp}] {message}")


# ==============================================================================
# MAIN PROGRAM ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SensorHMIWindow()
    window.show()
    
    try:
        sys.exit(app.exec_())
    finally:
        if IS_RASPBERRY_PI:
            GPIO.cleanup()  # Reset GPIO saat aplikasi ditutup