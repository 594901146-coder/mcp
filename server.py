"""
GenericHardwareBridge - MCP Server for Hardware Abstraction
===========================================================

A Model Context Protocol server that abstracts hardware operations into
atomic, generic primitives. The ESP32 acts as a "dumb executor" while
all business logic resides in this server.

Architecture Philosophy:
    - Logic Lifting: Hardware only executes atomic commands via MQTT
    - Hardware Abstraction: No device-specific functions, only atomic capabilities

Communication Channels:
    - Local: Serial (pyserial) - fallback mode
    - Remote: MQTT via Cloudflare Tunnel or public broker

Author: GenericHardwareBridge
License: MIT
"""

import asyncio
import json
import os
from typing import Literal, Optional
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from pydantic import Field

# ==============================================================================
# Configuration
# ==============================================================================

TRANSPORT_MODE: Literal["mqtt", "serial", "mock"] = os.getenv("TRANSPORT_MODE", "mock")

SERIAL_PORT: str = os.getenv("SERIAL_PORT", "COM3")
SERIAL_BAUDRATE: int = int(os.getenv("SERIAL_BAUDRATE", "115200"))
SERIAL_TIMEOUT: float = float(os.getenv("SERIAL_TIMEOUT", "1.0"))

MQTT_BROKER: str = os.getenv("MQTT_BROKER", "broker.emqx.io")
MQTT_PORT: int = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME: str = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD: str = os.getenv("MQTT_PASSWORD", "")
MQTT_BASE_TOPIC: str = os.getenv("MQTT_BASE_TOPIC", "hardware/bridge")

DEVICE_ID: str = os.getenv("DEVICE_ID", "esp32s3_001")

# ==============================================================================
# MQTT Message Types
# ==============================================================================

class MQTTMessage:
    OUTGOING = "outgoing"
    RESPONSE = "response"

# ==============================================================================
# Hardware Interface Base
# ==============================================================================

class HardwareInterface:
    async def connect(self) -> bool:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError

    async def send_command(self, command: str) -> str:
        raise NotImplementedError

# ==============================================================================
# Mock Interface (for testing without hardware)
# ==============================================================================

class MockInterface(HardwareInterface):
    async def connect(self) -> bool:
        print(f"[MOCK] Interface initialized")
        return True

    async def disconnect(self) -> None:
        print(f"[MOCK] Interface closed")

    async def send_command(self, command: str) -> str:
        print(f"[MOCK] TX: {command}")
        await asyncio.sleep(0.05)
        response = f"ACK:OK:mock_response"
        print(f"[MOCK] RX: {response}")
        return response

# ==============================================================================
# Serial Interface (fallback)
# ==============================================================================

class SerialInterface(HardwareInterface):
    def __init__(self):
        self._serial = None
        self._connected = False

    async def connect(self) -> bool:
        try:
            import serial
            self._serial = serial.Serial(
                port=SERIAL_PORT,
                baudrate=SERIAL_BAUDRATE,
                timeout=SERIAL_TIMEOUT
            )
            self._connected = True
            print(f"[SERIAL] Connected to {SERIAL_PORT}")
            return True
        except Exception as e:
            print(f"[ERROR] Serial connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._connected = False
        print("[SERIAL] Disconnected")

    async def send_command(self, command: str) -> str:
        if not self._connected or not self._serial:
            raise RuntimeError("Serial not connected")

        self._serial.write((command + "\n").encode('utf-8'))
        response = self._serial.readline().decode('utf-8').strip()
        return response

# ==============================================================================
# MQTT Interface (cloud-native)
# ==============================================================================

class MQTTInterface(HardwareInterface):
    def __init__(self):
        self._client = None
        self._connected = False
        self._response_event = asyncio.Event()
        self._last_response = ""

    async def connect(self) -> bool:
        try:
            import mqtt_as

            async def callback(topic, msg, retained):
                if "response" in topic:
                    self._last_response = msg.decode('utf-8')
                    self._response_event.set()

            config = {
                'broker': MQTT_BROKER,
                'port': MQTT_PORT,
                'username': MQTT_USERNAME or None,
                'password': MQTT_PASSWORD or None,
                'queue_len': 0,
            }

            self._client = mqtt_as.MQTTHub(**config)
            self._client.subscribe(f"{MQTT_BASE_TOPIC}/{DEVICE_ID}/response/+", 1)
            self._client.subscribe(f"{MQTT_BASE_TOPIC}/broadcast/response/+", 1)

            await self._client.connect()
            self._client.subscribe.callback = callback

            self._connected = True
            print(f"[MQTT] Connected to {MQTT_BROKER}:{MQTT_PORT}")
            return True
        except Exception as e:
            print(f"[ERROR] MQTT connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
        self._connected = False
        print("[MQTT] Disconnected")

    async def send_command(self, command: str, timeout: float = 5.0) -> str:
        if not self._connected:
            raise RuntimeError("MQTT not connected")

        self._response_event.clear()

        cmd_id = f"{DEVICE_ID}_{int(asyncio.get_event_loop().time() * 1000)}"
        topic = f"{MQTT_BASE_TOPIC}/{DEVICE_ID}/command/{cmd_id}"

        payload = json.dumps({
            "command": command,
            "device_id": DEVICE_ID,
            "command_id": cmd_id,
            "timestamp": asyncio.get_event_loop().time()
        })

        print(f"[MQTT] TX to {topic}: {command}")
        await self._client.publish(topic, payload, qos=1)

        try:
            await asyncio.wait_for(
                self._response_event.wait(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            print(f"[MQTT] Response timeout for command: {command}")
            return f"ACK:TIMEOUT:{command}"

        print(f"[MQTT] RX: {self._last_response}")
        return self._last_response

# ==============================================================================
# Interface Factory
# ==============================================================================

def create_interface() -> HardwareInterface:
    if TRANSPORT_MODE == "mqtt":
        return MQTTInterface()
    elif TRANSPORT_MODE == "serial":
        return SerialInterface()
    else:
        return MockInterface()

# ==============================================================================
# Global Interface
# ==============================================================================

hw_interface = create_interface()

# ==============================================================================
# Lifespan Context Manager
# ==============================================================================

@asynccontextmanager
async def lifespan(app):
    await hw_interface.connect()
    yield
    await hw_interface.disconnect()

# ==============================================================================
# MCP Server Instance
# ==============================================================================

mcp = FastMCP(
    "GenericHardwareBridge",
    lifespan=lifespan
)

# ==============================================================================
# Atomic Tools
# ==============================================================================

@mcp.tool()
async def set_level(
    port: int = Field(..., description="Port/pin number on the hardware (0-255)", ge=0, le=255),
    value: float = Field(..., description="Continuous value to output (0.0-100.0)", ge=0.0, le=100.0)
) -> str:
    """
    Set a continuous analog level on a specified port.

    This is a universal output primitive for controlling any continuous physical
    quantity. The interpretation of 'value' depends on the connected hardware:

    Physical Interpretations:
        - LED Brightness: 0.0 = off, 100.0 = maximum brightness
        - Servo Angle: 0.0 = 0°, 100.0 = 180° (or mapped range)
        - Motor Speed: 0.0 = stopped, 100.0 = maximum speed
        - PWM Duty Cycle: 0.0 = 0%, 100.0 = 100%
        - DAC Voltage: Linear mapping to voltage range

    Use Cases:
        - Dimming lights smoothly
        - Controlling servo positions
        - Adjusting motor speeds
        - Setting audio volume
        - Any PWM-controlled device

    Args:
        port: The hardware port/pin identifier.
        value: The continuous value to output (0.0-100.0).

    Returns:
        Physical confirmation string from hardware, e.g., "ACK:OK:PORT5=75.0"

    Example:
        >>> await set_level(port=5, value=75.0)
        "ACK:OK:PORT5=75.0"
    """
    command = f"CMD:SET_LEVEL:{port}:{value:.2f}"
    response = await hw_interface.send_command(command)
    return response


@mcp.tool()
async def toggle_state(
    port: int = Field(..., description="Port/pin number on the hardware (0-255)", ge=0, le=255),
    state: bool = Field(..., description="Target state: True=ON/HIGH, False=OFF/LOW")
) -> str:
    """
    Toggle a binary digital state on a specified port.

    This is a universal output primitive for controlling any on/off device.

    Physical Interpretations:
        - Relay: True = closed, False = open
        - LED: True = on, False = off
        - Solenoid: True = activated, False = deactivated
        - Digital Output: True = HIGH, False = LOW

    Args:
        port: The hardware port/pin identifier.
        state: The binary state to set.

    Returns:
        Physical confirmation string, e.g., "ACK:OK:PORT3=HIGH"

    Example:
        >>> await toggle_state(port=3, state=True)
        "ACK:OK:PORT3=HIGH"
    """
    state_str = "ON" if state else "OFF"
    command = f"CMD:TOGGLE:{port}:{state_str}"
    response = await hw_interface.send_command(command)
    return response


@mcp.tool()
async def get_sensor_data(
    port: int = Field(..., description="Port/pin number on the hardware (0-255)", ge=0, le=255),
    mode: Literal["raw", "voltage", "temperature", "humidity", "distance", "pressure", "light", "current"] = Field(
        "raw",
        description="Sensor reading mode determining how to interpret the data"
    )
) -> str:
    """
    Read sensor data from a specified port with configurable interpretation mode.

    Mode Interpretations:
        - raw: Raw ADC value (0-4095)
        - voltage: Converted voltage (0.0-3.3V)
        - temperature: Temperature in Celsius
        - humidity: Relative humidity (0-100%)
        - distance: Distance measurement (cm)
        - pressure: Pressure reading (hPa)
        - light: Light intensity (lux)
        - current: Current measurement (mA)

    Args:
        port: The hardware port/pin identifier.
        mode: The interpretation mode for the sensor data.

    Returns:
        Sensor reading string, e.g., "ACK:OK:TEMP=25.6"

    Example:
        >>> await get_sensor_data(port=12, mode="temperature")
        "ACK:OK:TEMP=25.6"
    """
    command = f"CMD:GET_SENSOR:{port}:{mode}"
    response = await hw_interface.send_command(command)
    return response


@mcp.tool()
async def send_raw_packet(
    payload: str = Field(..., description="Raw command string to send directly to hardware")
) -> str:
    """
    Send a raw command packet directly to the hardware executor.

    This is a fallback primitive for sending custom or device-specific commands
    that don't fit into the other atomic primitives.

    Warning:
        This tool bypasses the standardized abstraction layer.

    Args:
        payload: The raw command string to send.

    Returns:
        Raw response string from hardware.

    Example:
        >>> await send_raw_packet("CUSTOM:INIT:SERVO:90")
        "ACK:OK:CUSTOM_CMD_EXECUTED"
    """
    command = f"CMD:RAW:{payload}"
    response = await hw_interface.send_command(command)
    return response


# ==============================================================================
# Server Entry Point
# ==============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("GenericHardwareBridge MCP Server")
    print("=" * 60)
    print(f"Transport Mode: {TRANSPORT_MODE.upper()}")
    print(f"Environment Variables:")
    print(f"  TRANSPORT_MODE: {TRANSPORT_MODE}")
    print(f"  MQTT_BROKER: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  DEVICE_ID: {DEVICE_ID}")
    print(f"  MQTT_BASE_TOPIC: {MQTT_BASE_TOPIC}")
    print("=" * 60)
    print("\nAvailable Tools:")
    print("  - set_level(port, value): Continuous analog output")
    print("  - toggle_state(port, state): Binary digital output")
    print("  - get_sensor_data(port, mode): Sensor data acquisition")
    print("  - send_raw_packet(payload): Raw command passthrough")
    print("=" * 60)
    print("\nCloud Deployment:")
    print("  1. Railway: railway.json included")
    print("  2. Cloudflare Tunnel for remote access")
    print("=" * 60)

    mcp.run()


# ==============================================================================
# ESP32 MQTT Firmware Reference
# ==============================================================================
"""
ESP32 Side - MQTT Command Handler:

#include <WiFi.h>
#include <PubSubClient.h>

const char* ssid = "YOUR_WIFI";
const char* password = "YOUR_PASSWORD";
const char* mqtt_server = "broker.emqx.io";
const char* device_id = "esp32s3_001";
const char* command_topic = "hardware/bridge/esp32s3_001/command/#";
const char* response_topic = "hardware/bridge/esp32s3_001/response/";

WiFiClient espClient;
PubSubClient client(espClient);

void callback(char* topic, byte* payload, unsigned int length) {
    String command;
    for (int i = 0; i < length; i++) command += (char)payload[i];

    String response = execute_command(command);

    String cmd_id = String(topic).substring(String(command_topic).length());
    client.publish((response_topic + cmd_id).c_str(), response.c_str());
}

void setup() {
    Serial.begin(115200);
    WiFi.begin(ssid, password);
    client.setServer(mqtt_server, 1883);
    client.setCallback(callback);
}

void loop() {
    if (!client.connected()) {
        client.connect(device_id);
        client.subscribe(command_topic);
    }
    client.loop();
}

String execute_command(String cmd) {
    // Parse CMD:SET_LEVEL:5:75.0 format
    // Execute on hardware pins
    // Return "ACK:OK:PORT5=75.0" or "ACK:ERROR:reason"
}
"""
