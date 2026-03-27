/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : STM32_CAN_Adapter – CAN ↔ UART1 bidirectional bridge
  *
  * CAN → UART1 : Every received CAN frame is printed as:
  *                 "RX ID:0x<ID> DLC:<N> DATA:<HEX>\r\n"
  *
  * UART1 → CAN : Commands received as ASCII lines:
  *                 "TX:0x<ID>:<DLC>:<HEX_DATA>\r\n"
  *               are parsed and transmitted on the CAN bus.
  *
  * Hardware (BluePill STM32F103C8Tx):
  *   PA9  – USART1 TX   PA10 – USART1 RX
  *   PB8  – CAN RX      PB9  – CAN TX   (remap 2)
  *   PB0  – LED RX (blink on CAN recv)  PB1 – LED TX (blink on CAN send)
  *   PD0  – HSE IN      PD1  – HSE OUT
  *   PA13 – SWD IO      PA14 – SWD CLK
  *
  * CAN:  500 kbps  (APB1=36MHz, Prescaler=12, BS1=2TQ, BS2=3TQ, SJW=1TQ)
  * UART: 115200 8N1
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define UART_TX_BUF_SIZE   128
#define CMD_BUF_SIZE        64
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
CAN_HandleTypeDef hcan;

UART_HandleTypeDef huart1;

/* USER CODE BEGIN PV */

/* ---- CAN RX ---- */
static CAN_RxHeaderTypeDef RxHeader;
static uint8_t             RxData[8];

/* ---- CAN TX ---- */
static CAN_TxHeaderTypeDef TxHeader;
static uint8_t             TxData[8];
static uint32_t            TxMailbox;

/* ---- CAN filter (accept all) ---- */
static CAN_FilterTypeDef   CanFilter;

/* ---- UART RX (interrupt-driven, byte-by-byte) ---- */
static uint8_t  uart_rx_byte;
static char     cmd_buf[CMD_BUF_SIZE];
static uint8_t  cmd_idx = 0;

/* ---- LED blink (tick-based, ISR-safe) ---- */
#define LED_RX_PIN   GPIO_PIN_0   /* PB0 – blinks on CAN RX */
#define LED_TX_PIN   GPIO_PIN_1   /* PB1 – blinks on CAN TX */
#define LED_PORT     GPIOB
#define LED_BLINK_MS 15           /* blink duration in milliseconds */

static volatile uint32_t led_rx_off_tick = 0; /* tick when PB0 should turn off */
static volatile uint32_t led_tx_off_tick = 0; /* tick when PB1 should turn off */

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART1_UART_Init(void);
static void MX_CAN_Init(void);
/* USER CODE BEGIN PFP */
static void UART_Printf(const char *fmt, ...);
static void CAN_ConfigFilter(void);
static void Process_Command(char *cmd);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/**
 * @brief  Printf over UART1 (blocking, for small strings).
 */
static void UART_Printf(const char *fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    char buf[UART_TX_BUF_SIZE];
    int len = vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    if (len > 0)
    {
        HAL_UART_Transmit(&huart1, (uint8_t *)buf, (uint16_t)len, HAL_MAX_DELAY);
    }
}

/**
 * @brief  Configure CAN filter to accept all frames into FIFO0.
 */
static void CAN_ConfigFilter(void)
{
    CanFilter.FilterBank           = 0;
    CanFilter.FilterMode           = CAN_FILTERMODE_IDMASK;
    CanFilter.FilterScale          = CAN_FILTERSCALE_32BIT;
    CanFilter.FilterIdHigh         = 0x0000;
    CanFilter.FilterIdLow          = 0x0000;
    CanFilter.FilterMaskIdHigh     = 0x0000;
    CanFilter.FilterMaskIdLow      = 0x0000;
    CanFilter.FilterFIFOAssignment = CAN_RX_FIFO0;
    CanFilter.FilterActivation     = ENABLE;
    CanFilter.SlaveStartFilterBank = 14;

    if (HAL_CAN_ConfigFilter(&hcan, &CanFilter) != HAL_OK)
    {
        Error_Handler();
    }
}

/**
 * @brief  CAN RX FIFO0 message pending callback.
 *         Called from USB_LP_CAN1_RX0_IRQHandler via HAL.
 *         Forwards each received frame to UART1 as ASCII.
 *
 * Output format:
 *   "RX ID:0x123 DLC:8 DATA:0102030405060708\r\n"
 */
void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *hcan_p)
{
    if (HAL_CAN_GetRxMessage(hcan_p, CAN_RX_FIFO0, &RxHeader, RxData) == HAL_OK)
    {
        /* Blink RX LED (PB0) – set pin high; main loop will clear it */
        HAL_GPIO_WritePin(LED_PORT, LED_RX_PIN, GPIO_PIN_SET);
        led_rx_off_tick = HAL_GetTick() + LED_BLINK_MS;

        /* Choose format based on frame type */
        if (RxHeader.IDE == CAN_ID_STD)
        {
            UART_Printf("RX ID:0x%03lX DLC:%lu DATA:", RxHeader.StdId, RxHeader.DLC);
        }
        else
        {
            UART_Printf("RX ID:0x%08lX DLC:%lu DATA:", RxHeader.ExtId, RxHeader.DLC);
        }

        for (uint8_t i = 0; i < RxHeader.DLC; i++)
        {
            UART_Printf("%02X", RxData[i]);
        }
        UART_Printf("\r\n");
    }
}

/**
 * @brief  UART RX complete callback (1 byte at a time).
 *         Accumulates bytes into cmd_buf until CR or LF, then
 *         dispatches to Process_Command().
 */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        if (uart_rx_byte == '\n' || uart_rx_byte == '\r')
        {
            cmd_buf[cmd_idx] = '\0';
            if (cmd_idx > 0)
            {
                Process_Command(cmd_buf);
            }
            cmd_idx = 0;
        }
        else
        {
            if (cmd_idx < (CMD_BUF_SIZE - 1))
            {
                cmd_buf[cmd_idx++] = (char)uart_rx_byte;
            }
        }
        /* Re-arm receive interrupt */
        HAL_UART_Receive_IT(&huart1, &uart_rx_byte, 1);
    }
}

/**
 * @brief  Parse and execute a UART command.
 *
 * Accepted formats:
 *   TX:0x<ID>:<DLC>:<HEXDATA>   – transmit standard CAN frame
 *   TX:0x<ID>:E:<DLC>:<HEXDATA> – transmit extended CAN frame
 *
 * Examples:
 *   TX:0x123:8:0102030405060708
 *   TX:0x12345678:E:4:DEADBEEF
 */
static void Process_Command(char *cmd)
{
    uint32_t   id;
    uint32_t   dlc;
    char       data_str[17];  /* max 8 bytes = 16 hex chars + NUL */
    char       ext_flag;

    /* ----- Extended frame: TX:0x<ID>:E:<DLC>:<HEX> ----- */
    if (sscanf(cmd, "TX:0x%lX:E:%lu:%16s", &id, &dlc, data_str) == 3)
    {
        TxHeader.ExtId              = id;
        TxHeader.IDE                = CAN_ID_EXT;
        TxHeader.RTR                = CAN_RTR_DATA;
        TxHeader.DLC                = (dlc > 8) ? 8 : dlc;
        TxHeader.TransmitGlobalTime = DISABLE;
    }
    /* ----- Standard frame: TX:0x<ID>:<DLC>:<HEX> ----- */
    else if (sscanf(cmd, "TX:0x%lX:%lu:%16s", &id, &dlc, data_str) == 3)
    {
        TxHeader.StdId              = id & 0x7FF;
        TxHeader.IDE                = CAN_ID_STD;
        TxHeader.RTR                = CAN_RTR_DATA;
        TxHeader.DLC                = (dlc > 8) ? 8 : dlc;
        TxHeader.TransmitGlobalTime = DISABLE;
    }
    else
    {
        UART_Printf("ERR:BAD_CMD\r\n");
        return;
    }

    /* Parse hex data bytes */
    for (uint8_t i = 0; i < TxHeader.DLC; i++)
    {
        unsigned int val = 0;
        if (sscanf(&data_str[i * 2], "%02X", &val) != 1)
        {
            UART_Printf("ERR:BAD_DATA\r\n");
            return;
        }
        TxData[i] = (uint8_t)val;
    }

    if (HAL_CAN_AddTxMessage(&hcan, &TxHeader, TxData, &TxMailbox) != HAL_OK)
    {
        UART_Printf("ERR:TX_FAIL\r\n");
    }
    else
    {
        /* Blink TX LED (PB1) on successful transmit */
        HAL_GPIO_WritePin(LED_PORT, LED_TX_PIN, GPIO_PIN_SET);
        led_tx_off_tick = HAL_GetTick() + LED_BLINK_MS;
    }
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{
  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USART1_UART_Init();
  MX_CAN_Init();
  /* USER CODE BEGIN 2 */

  /* Configure CAN acceptance filter (accept all frames) */
  CAN_ConfigFilter();

  /* Start CAN bus */
  if (HAL_CAN_Start(&hcan) != HAL_OK)
  {
      Error_Handler();
  }

  /* Enable CAN RX FIFO0 notification interrupt */
  if (HAL_CAN_ActivateNotification(&hcan, CAN_IT_RX_FIFO0_MSG_PENDING) != HAL_OK)
  {
      Error_Handler();
  }

  /* Start UART receive interrupt (1 byte at a time) */
  HAL_UART_Receive_IT(&huart1, &uart_rx_byte, 1);

  /* Ready banner */
  UART_Printf("STM32_CAN_Adapter ready. CAN 500kbps, UART 115200 8N1\r\n");
  UART_Printf("TX format:  TX:0x<ID>:<DLC>:<HEX>  (std)\r\n");
  UART_Printf("            TX:0x<ID>:E:<DLC>:<HEX> (ext)\r\n");

  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */

    /* ---- LED blink timeout ---- */
    uint32_t now = HAL_GetTick();
    if (led_rx_off_tick && now >= led_rx_off_tick)
    {
        HAL_GPIO_WritePin(LED_PORT, LED_RX_PIN, GPIO_PIN_RESET);
        led_rx_off_tick = 0;
    }
    if (led_tx_off_tick && now >= led_tx_off_tick)
    {
        HAL_GPIO_WritePin(LED_PORT, LED_TX_PIN, GPIO_PIN_RESET);
        led_tx_off_tick = 0;
    }
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.HSEPredivValue = RCC_HSE_PREDIV_DIV1;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLMUL = RCC_PLL_MUL9;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }
  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief CAN Initialization Function
  * @param None
  * @retval None
  */
static void MX_CAN_Init(void)
{

  /* USER CODE BEGIN CAN_Init 0 */

  /* USER CODE END CAN_Init 0 */

  /* USER CODE BEGIN CAN_Init 1 */

  /* USER CODE END CAN_Init 1 */
  hcan.Instance = CAN1;
  hcan.Init.Prescaler = 12;
  hcan.Init.Mode = CAN_MODE_NORMAL;
  hcan.Init.SyncJumpWidth = CAN_SJW_1TQ;
  hcan.Init.TimeSeg1 = CAN_BS1_2TQ;
  hcan.Init.TimeSeg2 = CAN_BS2_3TQ;
  hcan.Init.TimeTriggeredMode = DISABLE;
  hcan.Init.AutoBusOff = DISABLE;
  hcan.Init.AutoWakeUp = DISABLE;
  hcan.Init.AutoRetransmission = DISABLE;
  hcan.Init.ReceiveFifoLocked = DISABLE;
  hcan.Init.TransmitFifoPriority = DISABLE;
  if (HAL_CAN_Init(&hcan) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN CAN_Init 2 */

  /* USER CODE END CAN_Init 2 */

}

/**
  * @brief USART1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART1_UART_Init(void)
{

  /* USER CODE BEGIN USART1_Init 0 */

  /* USER CODE END USART1_Init 0 */

  /* USER CODE BEGIN USART1_Init 1 */

  /* USER CODE END USART1_Init 1 */
  huart1.Instance = USART1;
  huart1.Init.BaudRate = 115200;
  huart1.Init.WordLength = UART_WORDLENGTH_8B;
  huart1.Init.StopBits = UART_STOPBITS_1;
  huart1.Init.Parity = UART_PARITY_NONE;
  huart1.Init.Mode = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART1_Init 2 */

  /* USER CODE END USART1_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /* Configure PB0 (LED RX) and PB1 (LED TX) as push-pull outputs, initially low */
  HAL_GPIO_WritePin(LED_PORT, LED_RX_PIN | LED_TX_PIN, GPIO_PIN_RESET);

  GPIO_InitStruct.Pin   = LED_RX_PIN | LED_TX_PIN;
  GPIO_InitStruct.Mode  = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull  = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(LED_PORT, &GPIO_InitStruct);

}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  (void)file;
  (void)line;
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */

/************************ (C) COPYRIGHT STMicroelectronics *****END OF FILE****/
