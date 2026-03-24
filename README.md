# GenericHardwareBridge MCP Server

Model Context Protocol 服务器，用于硬件抽象和控制。

## 功能

- `set_level(port, value)` - 连续量输出（亮度、角度、速度）
- `toggle_state(port, state)` - 开关量输出
- `get_sensor_data(port, mode)` - 传感器读取
- `send_raw_packet(payload)` - 原始指令透传

## 快速开始

```bash
pip install -r requirements.txt
python server.py
```

## 部署到 Railway

1. 上传到 GitHub
2. 在 Railway 中创建项目，选择该仓库
3. 配置环境变量：
   - `TRANSPORT_MODE=mqtt`
   - `MQTT_BROKER=broker.emqx.io`
   - `DEVICE_ID=esp32s3_001`
4. 部署

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TRANSPORT_MODE` | `mock` | 传输模式: mock/serial/mqtt |
| `SERIAL_PORT` | `COM3` | 串口端口 |
| `MQTT_BROKER` | `broker.emqx.io` | MQTT Broker 地址 |
| `MQTT_PORT` | `1883` | MQTT 端口 |
| `DEVICE_ID` | `esp32s3_001` | 设备标识 |
| `MQTT_BASE_TOPIC` | `hardware/bridge` | MQTT 主题前缀 |
