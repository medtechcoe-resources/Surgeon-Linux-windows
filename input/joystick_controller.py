"""
Thrustmaster T.Flight Stick X input reader.

Current scope:
    Axis 0 -> virtual horizontal pan
    Axis 1 -> virtual vertical pan
    Axis 2 -> virtual video rotation
    Axis 3 -> Live Video zoom

This module does NOT send robot movement commands.
"""

import pygame


class JoystickController:
    """Read the Thrustmaster joystick for virtual Live Video controls."""

    JOYSTICK_INDEX = 0

    PAN_X_AXIS = 0
    PAN_Y_AXIS = 1
    ROTATION_AXIS = 2
    ZOOM_AXIS = 3

    ZOOM_MIN = 1.0
    ZOOM_MAX = 3.0

    ROTATION_MIN = -90.0
    ROTATION_MAX = 90.0

    DEAD_ZONE = 0.08

    def __init__(self):
        self._joystick = None
        self._initialized = False

    def initialize(self):
        """Initialize the first connected joystick."""
        if self._initialized:
            return True

        pygame.init()
        pygame.joystick.init()

        if pygame.joystick.get_count() == 0:
            return False

        self._joystick = pygame.joystick.Joystick(self.JOYSTICK_INDEX)
        self._joystick.init()
        self._initialized = True

        return True

    @property
    def connected(self):
        """Return True when the joystick is initialized."""
        return self._initialized and self._joystick is not None

    @property
    def name(self):
        """Return the connected joystick name."""
        if not self.connected:
            return ""

        return self._joystick.get_name()

    def _read_axis(self, axis_index):
        """Read one joystick axis with a small center dead zone."""
        if not self.connected:
            return 0.0

        pygame.event.pump()

        value = self._joystick.get_axis(axis_index)

        if abs(value) < self.DEAD_ZONE:
            value = 0.0

        return max(-1.0, min(1.0, value))

    def read_pan_x(self):
        """Return Axis 0 for horizontal virtual panning."""
        return self._read_axis(self.PAN_X_AXIS)

    def read_pan_y(self):
        """Return Axis 1 for vertical virtual panning."""
        return self._read_axis(self.PAN_Y_AXIS)

    def read_rotation_axis(self):
        """Return Axis 2 for virtual video rotation."""
        return self._read_axis(self.ROTATION_AXIS)

    def read_zoom_axis(self):
        """Return Axis 3 in the range -1.0 to +1.0."""
        return self._read_axis(self.ZOOM_AXIS)

    def get_zoom(self):
        """
        Convert Axis 3 to the existing Live Video zoom range.

        Axis -1.0 -> 1.0x
        Axis  0.0 -> 2.0x
        Axis +1.0 -> 3.0x
        """
        axis = self.read_zoom_axis()

        normalized = (axis + 1.0) / 2.0

        zoom = (
            self.ZOOM_MIN
            + normalized * (self.ZOOM_MAX - self.ZOOM_MIN)
        )

        return max(self.ZOOM_MIN, min(self.ZOOM_MAX, zoom))

    def get_rotation(self):
        """
        Convert Axis 2 to virtual video rotation.

        Axis -1.0 -> -90 degrees
        Axis  0.0 ->   0 degrees
        Axis +1.0 -> +90 degrees
        """
        axis = self.read_rotation_axis()

        return (
            self.ROTATION_MIN
            + ((axis + 1.0) / 2.0)
            * (self.ROTATION_MAX - self.ROTATION_MIN)
        )

    def is_button_pressed(self, button_index):
        """Return the current state of a joystick button."""
        if not self.connected:
            return False

        pygame.event.pump()
        return bool(self._joystick.get_button(button_index))

    def close(self):
        """Release joystick resources."""
        if self._joystick is not None:
            self._joystick.quit()
            self._joystick = None

        if self._initialized:
            pygame.joystick.quit()
            pygame.quit()
            self._initialized = False
