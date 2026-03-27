# STM32 CAN Adapter – Host GUI

Python GUI to monitor and control the STM32_CAN_Adapter firmware over a
serial/USB connection.

## Features

| Tab | Description |
|-----|-------------|
| **📥 RX Monitor** | One row per unique CAN ID, live-updating Count / Period / Data. Click column headers to sort. Right-click a row to copy or load into TX panel. |
| **📤 Transmit** | Compose standard (11-bit) or extended (29-bit) frames. Live preview of the command string. Auto-repeat at configurable interval. TX history log. |
| **🔍 Raw Log** | Raw UART lines colour-coded (green = RX, blue = TX, red = error). |

## Setup

```bash
pip install -r requirements.txt
python can_adapter_gui.py
```

## Usage

1. Select the COM port assigned to your USB-UART adapter.
2. Set baud rate to **115200** (firmware default).
3. Click **Connect**.
4. CAN frames received by the STM32 appear in the **RX Monitor** tab.
5. Switch to **Transmit**, fill in ID + data, and click **Send**.

## TX Command Format

The GUI generates commands matching the firmware protocol:

```
Standard frame:  TX:0x1AB:8:0102030405060708
Extended frame:  TX:0x12345678:E:4:DEADBEEF
```

## RX Line Format (from firmware)

```
RX ID:0x123 DLC:8 DATA:0102030405060708
```
