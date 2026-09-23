"""
Authoritative Patient Vitals Model.
Single source of truth for patient vitals in the Aether Surgeon Console.
"""
from datetime import datetime
from typing import Optional, Dict, Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class PatientVitalsModel(QObject):
    """Authoritative model for patient vitals received from Data Generator via Broker.

    Supported States:
        NO_DATA: Initial state before any valid data is received.
        LIVE: Valid vitals message received within the last watchdog interval (~3.5s).
        STALE: No valid vitals message received for >= 3.5s.
        DISCONNECTED: Broker connection lost or explicitly set disconnected.
        INVALID: Received payload is structurally invalid or unparseable.

    Signals:
        vitals_updated(dict): Emitted whenever vitals are updated.
        state_changed(str): Emitted whenever state transitions between states.
    """

    vitals_updated = pyqtSignal(dict)
    state_changed = pyqtSignal(str)

    STATE_NO_DATA = "NO_DATA"
    STATE_LIVE = "LIVE"
    STATE_STALE = "STALE"
    STATE_DISCONNECTED = "DISCONNECTED"
    STATE_INVALID = "INVALID"

    WATCHDOG_TIMEOUT_MS = 3500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = self.STATE_NO_DATA

        # Raw numeric / string vitals values
        self._hr: Optional[float] = None
        self._spo2: Optional[float] = None
        self._bp: Optional[str] = None
        self._systolic_bp: Optional[float] = None
        self._diastolic_bp: Optional[float] = None
        self._temperature: Optional[float] = None
        self._respiration: Optional[float] = None
        self._etco2: Optional[float] = None
        self._ecg_status: str = "---"
        self._last_timestamp: Optional[datetime] = None

        # Watchdog timer to detect staleness
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(self.WATCHDOG_TIMEOUT_MS)
        self._watchdog.timeout.connect(self._on_watchdog_timeout)

    @property
    def state(self) -> str:
        return self._state

    @property
    def heart_rate(self) -> Optional[float]:
        return self._hr

    @property
    def spo2(self) -> Optional[float]:
        return self._spo2

    @property
    def blood_pressure(self) -> Optional[str]:
        return self._bp

    @property
    def systolic_bp(self) -> Optional[float]:
        return self._systolic_bp

    @property
    def diastolic_bp(self) -> Optional[float]:
        return self._diastolic_bp

    @property
    def temperature(self) -> Optional[float]:
        return self._temperature

    @property
    def respiration(self) -> Optional[float]:
        return self._respiration

    @property
    def etco2(self) -> Optional[float]:
        return self._etco2

    @property
    def ecg_status(self) -> str:
        return self._ecg_status

    def _set_state(self, new_state: str):
        if self._state != new_state:
            self._state = new_state
            self.state_changed.emit(new_state)

    def _on_watchdog_timeout(self):
        """Called when no valid vitals message has been received within ~3.5s."""
        if self._state == self.STATE_LIVE:
            self._set_state(self.STATE_STALE)
            self.vitals_updated.emit(self.get_display_data())

    def set_disconnected(self):
        """Called when the broker connection is lost."""
        self._watchdog.stop()
        self._set_state(self.STATE_DISCONNECTED)
        self.vitals_updated.emit(self.get_display_data())

    def reset_to_no_data(self):
        """Reset state to NO_DATA."""
        self._watchdog.stop()
        self._hr = None
        self._spo2 = None
        self._bp = None
        self._systolic_bp = None
        self._diastolic_bp = None
        self._temperature = None
        self._respiration = None
        self._etco2 = None
        self._ecg_status = "---"
        self._last_timestamp = None
        self._set_state(self.STATE_NO_DATA)
        self.vitals_updated.emit(self.get_display_data())

    def update_vitals(self, payload: Any):
        """Validate and ingest an incoming patient_vitals message payload.

        Expected schema from Data Generator:
            {
                "heart_rate": 74.2,      # or "hr"
                "spo2": 98.0,
                "blood_pressure": "118/74", # or "bp"
                "temperature": 36.8,     # or "temp"
                "respiration": 16.0,     # or "rr" (optional)
                "etco2": 38.0,           # (optional)
                "ecg_status": "NORMAL",  # (optional)
                "timestamp": "..."
            }
        """
        if not isinstance(payload, dict):
            self._set_state(self.STATE_INVALID)
            self.vitals_updated.emit(self.get_display_data())
            return

        # 1. Heart Rate (must be plausible if present: 20–300 bpm)
        hr_val = payload.get("heart_rate", payload.get("hr"))
        parsed_hr = None
        if hr_val is not None:
            try:
                val = float(hr_val)
                if 20 <= val <= 300:
                    parsed_hr = val
                else:
                    self._set_state(self.STATE_INVALID)
                    return
            except (ValueError, TypeError):
                self._set_state(self.STATE_INVALID)
                return

        # 2. SpO2 (must be plausible if present: 0–100%)
        spo2_val = payload.get("spo2", payload.get("oxygen_saturation"))
        parsed_spo2 = None
        if spo2_val is not None:
            try:
                val = float(spo2_val)
                if 0 <= val <= 100:
                    parsed_spo2 = val
                else:
                    self._set_state(self.STATE_INVALID)
                    return
            except (ValueError, TypeError):
                self._set_state(self.STATE_INVALID)
                return

        # 3. Blood Pressure (must be format "SYS/DIA", systolic_bp/diastolic_bp, or nibp_s/nibp_d)
        parsed_bp = None
        parsed_sys = None
        parsed_dia = None

        sys_direct = payload.get("systolic_bp")
        dia_direct = payload.get("diastolic_bp")
        if sys_direct is not None and dia_direct is not None:
            try:
                s_val = float(sys_direct)
                d_val = float(dia_direct)
                if 30 <= s_val <= 300 and 20 <= d_val <= 200:
                    parsed_sys = s_val
                    parsed_dia = d_val
                    parsed_bp = f"{int(round(s_val))}/{int(round(d_val))}"
            except (ValueError, TypeError):
                pass

        if parsed_bp is None:
            bp_val = payload.get("blood_pressure", payload.get("bp"))
            if bp_val is not None:
                bp_str = str(bp_val).strip()
                if "/" in bp_str:
                    parts = bp_str.split("/")
                    if len(parts) == 2:
                        try:
                            sys_p, dia_p = float(parts[0]), float(parts[1])
                            if 30 <= sys_p <= 300 and 20 <= dia_p <= 200:
                                parsed_sys = sys_p
                                parsed_dia = dia_p
                                parsed_bp = f"{int(round(sys_p))}/{int(round(dia_p))}"
                            else:
                                self._set_state(self.STATE_INVALID)
                                return
                        except ValueError:
                            self._set_state(self.STATE_INVALID)
                            return
                else:
                    self._set_state(self.STATE_INVALID)
                    return
            elif "nibp_s" in payload and "nibp_d" in payload:
                try:
                    sys_p = float(payload["nibp_s"])
                    dia_p = float(payload["nibp_d"])
                    if 30 <= sys_p <= 300 and 20 <= dia_p <= 200:
                        parsed_sys = sys_p
                        parsed_dia = dia_p
                        parsed_bp = f"{int(round(sys_p))}/{int(round(dia_p))}"
                except (ValueError, TypeError):
                    pass

        # 4. Temperature (must be plausible if present: 25–45 °C)
        temp_val = payload.get("temperature", payload.get("temp", payload.get("body_temperature")))
        parsed_temp = None
        if temp_val is not None:
            try:
                val = float(temp_val)
                if 25.0 <= val <= 45.0:
                    parsed_temp = val
                else:
                    self._set_state(self.STATE_INVALID)
                    return
            except (ValueError, TypeError):
                self._set_state(self.STATE_INVALID)
                return

        # If payload contains no recognizable vital fields at all, treat as invalid
        if parsed_hr is None and parsed_spo2 is None and parsed_bp is None and parsed_temp is None:
            self._set_state(self.STATE_INVALID)
            self.vitals_updated.emit(self.get_display_data())
            return

        # Optional fields (respiration, etco2, ecg_status)
        resp_val = payload.get("respiration", payload.get("rr", payload.get("respiration_rate")))
        if resp_val is not None:
            try:
                self._respiration = float(resp_val)
            except (ValueError, TypeError):
                self._respiration = None
        else:
            self._respiration = None

        etco2_val = payload.get("etco2")
        if etco2_val is not None:
            try:
                self._etco2 = float(etco2_val)
            except (ValueError, TypeError):
                self._etco2 = None
        else:
            self._etco2 = None

        self._ecg_status = str(payload.get("ecg_status", "NORMAL SINUS"))

        # Commit parsed core fields
        self._hr = parsed_hr
        self._spo2 = parsed_spo2
        self._bp = parsed_bp
        self._systolic_bp = parsed_sys
        self._diastolic_bp = parsed_dia
        self._temperature = parsed_temp
        self._last_timestamp = datetime.now()

        # Transition to LIVE (from NO_DATA, STALE, DISCONNECTED, etc.)
        self._set_state(self.STATE_LIVE)

        # Restart watchdog timer (re-triggers STALE if silence >= 3.5s)
        self._watchdog.start(self.WATCHDOG_TIMEOUT_MS)

        # Broadcast update to all subscribers
        self.vitals_updated.emit(self.get_display_data())

    def get_numeric_snapshot(self) -> Dict[str, Any]:
        """Return authoritative raw numeric vitals snapshot."""
        return {
            "heart_rate": self._hr,
            "spo2": self._spo2,
            "systolic_bp": self._systolic_bp,
            "diastolic_bp": self._diastolic_bp,
            "temperature": self._temperature,
            "respiration": self._respiration,
            "etco2": self._etco2,
            "ecg_status": self._ecg_status,
            "status": self._state,
            "timestamp": self._last_timestamp.isoformat() if self._last_timestamp else None,
        }

    def get_display_data(self) -> Dict[str, Any]:
        """Return authoritative formatted dictionary suitable for UI presentation.

        Returns string values:
            hr: "74" or "--"
            spo2: "98" or "--"
            bp: "118/74" or "--"
            temperature: "36.8" or "--"
            respiration: "16" or "--"
            etco2: "38" or "--"
            ecg_status: "NORMAL SINUS"
            status: "LIVE" | "STALE" | "DISCONNECTED" | "NO DATA" | "INVALID"
        Also includes raw numeric values for programmatic consumers:
            systolic_bp: float or None
            diastolic_bp: float or None
            heart_rate: float or None
        """
        is_live = (self._state == self.STATE_LIVE)
        is_stale = (self._state == self.STATE_STALE)

        # Status text for UI badge
        status_map = {
            self.STATE_NO_DATA: "NO DATA",
            self.STATE_LIVE: "LIVE",
            self.STATE_STALE: "STALE",
            self.STATE_DISCONNECTED: "DISCONNECTED",
            self.STATE_INVALID: "INVALID",
        }
        ui_status = status_map.get(self._state, "NO DATA")

        # When disconnected, no data, or invalid, values must display as "--" or "N/A"
        if not (is_live or is_stale):
            return {
                "hr": "--",
                "spo2": "--",
                "bp": "--",
                "systolic_bp": None,
                "diastolic_bp": None,
                "heart_rate": None,
                "temperature": "--",
                "respiration": "--",
                "etco2": "--",
                "ecg_status": "---",
                "status": ui_status,
                "is_live": False,
                "is_stale": False,
                "is_disconnected": (self._state == self.STATE_DISCONNECTED),
            }

        # Format numeric values
        hr_str = f"{int(round(self._hr))}" if self._hr is not None else "--"
        spo2_str = f"{int(round(self._spo2))}" if self._spo2 is not None else "--"
        bp_str = self._bp if self._bp is not None else "--"
        temp_str = f"{self._temperature:.1f}" if self._temperature is not None else "--"
        resp_str = f"{int(round(self._respiration))}" if self._respiration is not None else "--"
        etco2_str = f"{self._etco2:.1f}" if self._etco2 is not None else "--"

        return {
            "hr": hr_str,
            "spo2": spo2_str,
            "bp": bp_str,
            "systolic_bp": self._systolic_bp,
            "diastolic_bp": self._diastolic_bp,
            "heart_rate": self._hr,
            "temperature": temp_str,
            "respiration": resp_str,
            "etco2": etco2_str,
            "ecg_status": self._ecg_status,
            "status": ui_status,
            "is_live": is_live,
            "is_stale": is_stale,
            "is_disconnected": False,
        }
