"""
Models package for Surgeon Console.
"""
from .patient_vitals_model import PatientVitalsModel

# Re-export Robot-Console data_models if accessed via root models namespace
try:
    import sys
    import os
    import importlib.util
    _rc_models_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Robot-Console", "models", "data_models.py")
    if os.path.isfile(_rc_models_file):
        _spec = importlib.util.spec_from_file_location("models.data_models", _rc_models_file)
        if _spec and _spec.loader:
            data_models = importlib.util.module_from_spec(_spec)
            sys.modules["models.data_models"] = data_models
            _spec.loader.exec_module(data_models)
except Exception:
    pass

__all__ = ["PatientVitalsModel"]
