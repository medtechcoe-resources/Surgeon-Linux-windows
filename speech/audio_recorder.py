import subprocess
from pathlib import Path
from .audio_device_manager import AudioDeviceManager

class AudioRecorder:
    def record(self, output_file: str, duration: int = 5) -> bool:
        device = AudioDeviceManager().get_default_input()

        if device is None:
            print("NO MICROPHONE AVAILABLE")
            return False

        print(f"Recording from ID={device.node_id} NAME={device.name}")

        command = [
            "pw-record",
            "--target", str(device.node_id),
            "--rate", "16000",
            "--channels", "1",
            output_file,
        ]

        try:
            result = subprocess.run(
                ["timeout", f"{duration}s"] + command
            )
            return Path(output_file).exists() and result.returncode in (0, 124)
        except OSError:
            return False

if __name__ == "__main__":
    recorder = AudioRecorder()
    result = recorder.record("/tmp/aether_dynamic_mic_test.wav", 5)
    print("RECORDING SUCCESS" if result else "RECORDING FAILED")
