# ═══════════════════════════════════════════════════════════════════
#  DATA GENERATOR — PATIENT VITALS GENERATOR
#  Produces realistic simulated patient vitals using sinusoidal
#  motion with Gaussian noise. Publishes to 'patient_vitals' topic.
#  Interval: every 1 000 ms (1 Hz).
# ═══════════════════════════════════════════════════════════════════

import math
import random
from datetime import datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class PatientVitalsGenerator(QObject):
    """Generates continuous, realistic simulated patient vital signs.

    Uses smooth sinusoidal oscillations with Gaussian noise to mimic
    real clinical monitor output.  Emits a ``vitals_ready`` signal
    each tick so the publisher can forward the payload to the broker.
    """

    vitals_ready = pyqtSignal(dict)   # emits the full vitals dict

    # ── Publish interval ──────────────────────────────────────────
    INTERVAL_MS = 1_000   # 1 second

    def __init__(self, parent=None):
        super().__init__(parent)

        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._generate)

        self._tick = 0          # internal time counter
        self._running = False
        self._msg_count = 0

        # Latest generated payload — readable by console UI
        self.latest: dict = {}

    # ── Public API ────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def message_count(self) -> int:
        return self._msg_count

    def start(self):
        """Start generating patient vitals."""
        if self._running:
            return
        self._running = True
        self._timer.start()

    def stop(self):
        """Stop generating patient vitals."""
        self._running = False
        self._timer.stop()

    def pause(self):
        """Pause without resetting tick counter."""
        if self._running:
            self._timer.stop()
            self._running = False

    def resume(self):
        """Resume from paused state."""
        if not self._running:
            self._running = True
            self._timer.start()

    # ── Internal ──────────────────────────────────────────────────

    def _generate(self):
        """Generate one vitals frame and emit it."""
        self._tick += 1
        t = self._tick * 0.1

        # Heart Rate  72–80 bpm with gentle drift
        hr = round(74 + 6 * math.sin(t * 0.3) + random.gauss(0, 1.0), 1)
        hr = max(40.0, min(200.0, hr))

        # SpO2  96–99 %
        spo2 = round(
            min(100.0, 97.5 + 1.5 * math.sin(t * 0.15) + random.gauss(0, 0.3)),
            1,
        )
        spo2 = max(80.0, spo2)

        # Blood Pressure  systolic 110–130, diastolic 70–82 (preserve systolic > diastolic)
        bp_sys = int(118 + 8 * math.sin(t * 0.2) + random.gauss(0, 2))
        bp_dia = int(74 + 4 * math.sin(t * 0.25) + random.gauss(0, 1))
        bp_dia = max(40, min(140, bp_dia))
        bp_sys = max(bp_dia + 20, min(250, bp_sys))
        blood_pressure = f"{bp_sys}/{bp_dia}"

        # Respiration Rate  14–20 br/min
        rr = round(
            16 + 2 * math.sin(t * 0.1) + random.gauss(0, 0.5), 0
        )
        rr = max(4.0, min(60.0, rr))

        # Body Temperature  36.5–37.1 °C
        temp = round(
            36.8 + 0.2 * math.sin(t * 0.05) + random.gauss(0, 0.05), 1
        )
        temp = max(34.0, min(42.0, temp))

        # EtCO2  35–45 mmHg
        etco2 = round(38.0 + 3.0 * math.sin(t * 0.08) + random.gauss(0, 0.5), 1)
        etco2 = max(20.0, min(60.0, etco2))

        # ECG status — mostly normal, occasional minor artefact / PAC
        ecg_roll = random.random()
        if ecg_roll < 0.008:
            ecg_status = "ARTEFACT"
        elif ecg_roll < 0.02:
            ecg_status = "PAC DETECTED"
        else:
            ecg_status = "NORMAL SINUS"

        vitals = {
            "heart_rate":     hr,
            "spo2":           spo2,
            "systolic_bp":    bp_sys,
            "diastolic_bp":   bp_dia,
            "blood_pressure": blood_pressure,
            "respiration":    rr,
            "temperature":    temp,
            "etco2":          etco2,
            "ecg_status":     ecg_status,
            "timestamp":      datetime.now().isoformat(),
        }

        self.latest = vitals
        self._msg_count += 1
        self.vitals_ready.emit(vitals)
