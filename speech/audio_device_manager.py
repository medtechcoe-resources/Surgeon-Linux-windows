import re
import subprocess
from dataclasses import dataclass
from typing import Optional

@dataclass
class AudioInputDevice:
    node_id: int
    name: str
    state: str

class AudioDeviceManager:
    def list_input_devices(self) -> list[AudioInputDevice]:
        try:
            output = subprocess.check_output(["wpctl", "status"], text=True)
        except (subprocess.SubprocessError, OSError):
            return []

        if "Sources:" not in output:
            return []

        section = output.split("Sources:", 1)[1]
        if "Source endpoints:" in section:
            section = section.split("Source endpoints:", 1)[0]

        devices = []
        for line in section.splitlines():
            match = re.search(r"\*?\s*(\d+)\.\s+(.+?)(?:\s+\[.*\])?\s*$", line)
            if not match:
                continue

            node_id = int(match.group(1))
            name = match.group(2).strip()
            state = "default" if "*" in line else "available"

            devices.append(AudioInputDevice(node_id, name, state))

        return devices

    def get_default_input(self) -> Optional[AudioInputDevice]:
        devices = self.list_input_devices()

        for device in devices:
            if device.state == "default":
                return device

        return devices[0] if devices else None

if __name__ == "__main__":
    manager = AudioDeviceManager()
    devices = manager.list_input_devices()

    print("===== AVAILABLE AUDIO INPUT DEVICES =====")

    if not devices:
        print("NO AUDIO INPUT DEVICES FOUND")
    else:
        for device in devices:
            marker = "*" if device.state == "default" else " "
            print(f"{marker} ID={device.node_id} NAME={device.name} STATE={device.state}")

        selected = manager.get_default_input()

        print()
        print("===== SELECTED INPUT =====")

        if selected:
            print(f"ID: {selected.node_id}")
            print(f"Name: {selected.name}")
            print(f"State: {selected.state}")
        else:
            print("NO INPUT SELECTED")
