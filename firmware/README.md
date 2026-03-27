# STM32_CAN_Adapter

Bidirectional **CAN ↔ UART1** bridge firmware for the STM32F103C8Tx (BluePill).

## Hardware

| Pin  | Function      | Notes                        |
|------|---------------|------------------------------|
| PA9  | USART1 TX     | Connect to host RX           |
| PA10 | USART1 RX     | Connect to host TX           |
| PA8  | CAN RX        | Via SN65HVD230 or TJA1050    |
| PA9  | CAN TX        | Via SN65HVD230 or TJA1050    |
| PD0  | HSE OSC IN    | 8 MHz crystal                |
| PD1  | HSE OSC OUT   |                              |
| PA13 | SWD IO        | Debugger                     |
| PA14 | SWD CLK       | Debugger                     |

## CAN Settings

| Parameter | Value |
|-----------|-------|
| Bitrate   | 500 kbps |
| Prescaler | 12 |
| BS1       | 2 TQ |
| BS2       | 3 TQ |
| SJW       | 1 TQ |
| APB1 clock| 36 MHz (SYSCLK 72 MHz / 2) |

> Bit time = (1+2+3) × 12 / 36 000 000 = 2 µs → **500 kbps**

## UART Settings

`115200 baud, 8N1, no flow control`

## Wire Protocol

### CAN → UART (RX report)

Each received frame is sent as one ASCII line:

```
RX ID:0x123 DLC:8 DATA:0102030405060708
RX ID:0x12345678 DLC:4 DATA:DEADBEEF      ← extended frame
```

### UART → CAN (TX command)

Send one line (terminated by `\r` or `\n`):

```
# Standard frame (11-bit ID):
TX:0x123:8:0102030405060708

# Extended frame (29-bit ID):
TX:0x12345678:E:4:DEADBEEF
```

| Field | Description |
|-------|-------------|
| `TX:0x<ID>` | Frame ID (hex) |
| `:<DLC>:` | Data length (0–8) |
| `E:` | Present only for extended (29-bit) frames |
| `<HEX>` | Data bytes as hex string (2 chars per byte) |

On error the firmware replies with `ERR:BAD_CMD\r\n` or `ERR:TX_FAIL\r\n`.

## Getting Started

### Option A – CubeMX regeneration (recommended)

1. Open **STM32CubeMX**
2. *File → Load Project* → select `firmware/STM32_CAN_Adapter.ioc`
3. Verify the pin/clock/NVIC configuration, then click **Generate Code**
4. CubeMX will download the correct HAL drivers (`STM32Cube FW_F1 V1.8.x`)
   and regenerate boilerplate – your user code in `USER CODE` sections is preserved.

### Option B – Copy drivers from existing project

```powershell
# From your workspace root:
Copy-Item -Recurse stm32_can_bridge\firmware\Drivers `
          STM32_CAN_Adapter\firmware\Drivers
```

### Build in STM32CubeIDE

1. *File → Import → Existing Projects into Workspace*
2. Browse to `STM32_CAN_Adapter/firmware/`
3. Select `STM32_CAN_Adapter` → *Finish*
4. Right-click project → **Build Project** (Debug)

### Flash

Use **STM32CubeProgrammer** or the CubeIDE built-in debugger with an ST-Link.

## Project Structure

```
STM32_CAN_Adapter/firmware/
├── STM32_CAN_Adapter.ioc       ← CubeMX project (open this to regenerate)
├── .project / .cproject        ← Eclipse / CubeIDE project files
├── STM32F103C8TX_FLASH.ld      ← Linker script
├── Core/
│   ├── Inc/
│   │   ├── main.h
│   │   ├── stm32f1xx_hal_conf.h
│   │   └── stm32f1xx_it.h
│   ├── Src/
│   │   ├── main.c              ← Application logic (CAN↔UART bridge)
│   │   ├── stm32f1xx_hal_msp.c ← GPIO/clock/NVIC hardware setup
│   │   ├── stm32f1xx_it.c      ← ISR handlers
│   │   ├── system_stm32f1xx.c
│   │   ├── syscalls.c
│   │   └── sysmem.c
│   └── Startup/
│       └── startup_stm32f103c8tx.s
└── Drivers/                    ← HAL + CMSIS (generate via CubeMX or copy)
```
