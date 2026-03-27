# STM32 CAN Adapter

A bidirectional **CAN ↔ UART** bridge running on an STM32F103C8 (BluePill), paired with a Python desktop GUI for real-time monitoring and transmission of CAN bus frames.

---

## Repository Layout

```
STM32_CAN_Adapter/
├── firmware/          # STM32CubeIDE project (C, HAL)
│   └── Core/
│       ├── Src/
│       │   ├── main.c              # Application logic & callbacks
│       │   └── stm32f1xx_hal_msp.c # Peripheral GPIO/clock/NVIC setup
│       └── Inc/
│           └── main.h
└── GUI/
    └── can_adapter_gui.py  # Python host application (Tkinter)
```

---

## Hardware

| Pin  | Function          | Notes                          |
|------|-------------------|--------------------------------|
| PA9  | USART1 TX         | To USB-UART adapter RX         |
| PA10 | USART1 RX         | To USB-UART adapter TX         |
| PB8  | CAN RX            | CAN remap 2 (`AFIO_MAPR`)     |
| PB9  | CAN TX            | To CAN transceiver (e.g. TJA1050) |
| PB0  | LED – RX activity | Blinks on every received frame |
| PB1  | LED – TX activity | Blinks on every transmitted frame |
| PA13 | SWD IO            | Debug / programming            |
| PA14 | SWD CLK           | Debug / programming            |

> **CAN transceiver required.** Connect a TJA1050 or equivalent between PB8/PB9 and the CAN bus. Do **not** connect PB8/PB9 directly to the bus.

**CAN bus parameters:** 500 kbps — APB1 = 36 MHz, Prescaler = 12, BS1 = 2 TQ, BS2 = 3 TQ, SJW = 1 TQ

**UART:** 115200 baud, 8N1

---

## Serial Protocol

All communication over UART is ASCII line-based (`\r\n` terminated).

### Firmware → Host (RX report)

```
RX ID:0x<ID> DLC:<N> DATA:<HEX>\r\n
```

| Frame type | Example                                    |
|------------|--------------------------------------------|
| Standard   | `RX ID:0x123 DLC:8 DATA:0102030405060708`  |
| Extended   | `RX ID:0x12345678 DLC:4 DATA:DEADBEEF`     |

### Host → Firmware (TX command)

```
TX:0x<ID>:<DLC>:<HEX>\r\n            ← standard 11-bit frame
TX:0x<ID>:E:<DLC>:<HEX>\r\n          ← extended 29-bit frame
```

| Example                                 | Description                   |
|-----------------------------------------|-------------------------------|
| `TX:0x123:8:0102030405060708`           | Send standard frame ID 0x123  |
| `TX:0x12345678:E:4:DEADBEEF`            | Send extended frame           |

On error the firmware replies `ERR:BAD_CMD\r\n`, `ERR:BAD_DATA\r\n`, or `ERR:TX_FAIL\r\n`.

---

## Firmware

### Build requirements

- **STM32CubeIDE** ≥ 1.12 (includes arm-none-eabi-gcc toolchain)
- STM32CubeMX HAL library for STM32F1

### Build & flash

1. Open STM32CubeIDE → *File → Open Projects from File System* → select the `firmware/` folder.
2. Build: **Project → Build Project** (`Ctrl+B`).
3. Flash: **Run → Debug** (ST-Link) or use `STM32CubeProgrammer` with the `.elf` from `firmware/Debug/`.

### Key source files

| File | Purpose |
|------|---------|
| `Core/Src/main.c` | CAN/UART init, protocol parsing, LED blink logic |
| `Core/Src/stm32f1xx_hal_msp.c` | Low-level GPIO, clock and NVIC configuration for CAN & UART |

---

## GUI

A dark-themed desktop application built with **Python 3 + Tkinter**.

### Features

- **RX Monitor tab** — live table keyed by CAN ID; shows frame type, DLC, data, message count, and inter-frame period.
- **Transmit tab** — compose standard or extended CAN frames with live protocol preview; auto-repeat with configurable interval.
- **Raw Log tab** — timestamped raw UART traffic for diagnostics.
- Column sorting on the RX table (hex-aware for CAN ID column).
- Right-click on any RX row to copy ID/data or load it into the TX panel.

### Requirements

```
pip install pyserial
```

Python ≥ 3.10 (uses `|` union type hints in annotations).

### Run

```bash
python GUI/can_adapter_gui.py
```

Select the correct COM port and baud rate (default **115200**), then click **Connect**.

---

## LED Indicators

| LED | Pin | Event                          | Duration |
|-----|-----|--------------------------------|----------|
| RX  | PB0 | CAN frame received             | 15 ms    |
| TX  | PB1 | CAN frame successfully sent    | 15 ms    |

LEDs are driven by a tick-based mechanism in the main loop — no blocking delays are used, keeping UART interrupt latency unaffected.

---

## License

MIT — see `LICENSE` for details.
