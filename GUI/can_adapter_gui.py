# -*- coding: utf-8 -*-
"""
STM32 CAN Adapter – Host GUI
============================
Connects to the STM32_CAN_Adapter firmware over a serial/USB port and provides:

  • RX Table  – one row per unique CAN ID, live-updating Count / Period / Data
  • TX Panel  – compose and send standard or extended CAN frames
  • Log View  – raw UART traffic for diagnostics

Protocol (firmware side):
  RX report : "RX ID:0x<ID> DLC:<N> DATA:<HEX>\r\n"
  TX command: "TX:0x<ID>:<DLC>:<HEX>\r\n"          (standard 11-bit)
               "TX:0x<ID>:E:<DLC>:<HEX>\r\n"        (extended 29-bit)

Requirements:  pip install pyserial
"""

import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont
import serial
import serial.tools.list_ports
import threading
import queue
import time
import re
from datetime import datetime

# ─── Colour palette ──────────────────────────────────────────────────────────
BG        = "#1e1e2e"   # main background
PANEL     = "#2a2a3e"   # panel / frame background
HEADER    = "#313144"   # table header row background
ACCENT    = "#7c6af7"   # accent (purple)
ACCENT2   = "#56cfab"   # secondary accent (teal)
FG        = "#cdd6f4"   # primary text
FG_DIM    = "#6c7086"   # dimmed text
GREEN     = "#a6e3a1"
RED       = "#f38ba8"
YELLOW    = "#f9e2af"
ORANGE    = "#fab387"
ROW_ODD   = "#252538"
ROW_EVEN  = "#2a2a3e"

FONT_FAMILY = "Consolas"
FONT_UI     = ("Segoe UI", 10)
FONT_MONO   = (FONT_FAMILY, 10)
FONT_MONO_S = (FONT_FAMILY, 9)
FONT_TITLE  = ("Segoe UI", 11, "bold")
FONT_LG     = ("Segoe UI", 14, "bold")


# ─── Regex ───────────────────────────────────────────────────────────────────
RX_RE = re.compile(
    r"RX ID:0x([0-9A-Fa-f]+)\s+DLC:(\d+)\s+DATA:([0-9A-Fa-f]*)",
    re.IGNORECASE
)


def fmt_hex(raw: str) -> str:
    """'0102030405060708' → '01 02 03 04 05 06 07 08'"""
    return " ".join(raw[i:i+2].upper() for i in range(0, len(raw), 2))


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


# ─── Main Application ─────────────────────────────────────────────────────────
class CanAdapterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("STM32 CAN Adapter Monitor")
        self.root.geometry("1280x820")
        self.root.configure(bg=BG)
        self.root.minsize(900, 600)

        # Serial state
        self.serial_port: serial.Serial | None = None
        self.is_connected = False
        self.rx_thread: threading.Thread | None = None
        self.ui_queue: queue.Queue = queue.Queue()

        # RX table state  { "ID_str": { values… } }
        self.rx_rows: dict[str, dict] = {}        # key → tree item id
        self.rx_meta: dict[str, dict] = {}        # key → {count, last_t, last_period}

        self._apply_theme()
        self._build_ui()
        self._poll_queue()
        self.refresh_ports()

    # ── Theme ──────────────────────────────────────────────────────────────────
    def _apply_theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(".",
                        background=BG, foreground=FG,
                        fieldbackground=PANEL, borderwidth=0,
                        font=FONT_UI)
        style.configure("TFrame",       background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel",       background=BG, foreground=FG)
        style.configure("Panel.TLabel", background=PANEL, foreground=FG)
        style.configure("Dim.TLabel",   background=BG, foreground=FG_DIM, font=FONT_MONO_S)
        style.configure("TLabelframe",  background=PANEL, foreground=FG_DIM,
                        bordercolor=HEADER, relief="flat")
        style.configure("TLabelframe.Label", background=PANEL, foreground=ACCENT,
                        font=FONT_TITLE)

        # Buttons
        style.configure("TButton",
                        background=PANEL, foreground=FG,
                        padding=(10, 5), relief="flat", borderwidth=0)
        style.map("TButton",
                  background=[("active", HEADER), ("pressed", ACCENT)])

        style.configure("Accent.TButton",
                        background=ACCENT, foreground="#ffffff",
                        padding=(14, 6), relief="flat", borderwidth=0,
                        font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton",
                  background=[("active", "#9a88ff"), ("pressed", "#5a4cc7")])

        style.configure("Green.TButton",
                        background="#2e6b4f", foreground=GREEN,
                        padding=(14, 6), relief="flat", borderwidth=0,
                        font=("Segoe UI", 10, "bold"))
        style.map("Green.TButton",
                  background=[("active", "#3a8a63")])

        style.configure("Red.TButton",
                        background="#6b2e2e", foreground=RED,
                        padding=(14, 6), relief="flat", borderwidth=0,
                        font=("Segoe UI", 10, "bold"))
        style.map("Red.TButton",
                  background=[("active", "#8a3a3a")])

        # Combobox – map fieldbackground for every state so it never
        # vanishes when the widget loses focus or is in readonly mode.
        style.configure("TCombobox",
                        fieldbackground=HEADER, background=PANEL,
                        foreground=FG, arrowcolor=FG_DIM,
                        selectbackground=ACCENT, selectforeground="#fff")
        style.map("TCombobox",
                  fieldbackground=[("readonly", HEADER), ("disabled", BG),
                                   ("focus", HEADER)],
                  foreground=[("disabled", FG_DIM)],
                  background=[("active", HEADER)])

        # Entry
        style.configure("TEntry",
                        fieldbackground=HEADER, foreground=FG,
                        insertcolor=FG, relief="flat")

        # Treeview (RX table)
        style.configure("Treeview",
                        background=ROW_ODD, foreground=FG,
                        fieldbackground=ROW_ODD,
                        rowheight=26, font=FONT_MONO)
        style.configure("Treeview.Heading",
                        background=HEADER, foreground=ACCENT,
                        relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Treeview",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "#fff")])

        # Scrollbar
        style.configure("TScrollbar",
                        background=PANEL, troughcolor=BG,
                        arrowcolor=FG_DIM, borderwidth=0)

        # Separator
        style.configure("TSeparator", background=HEADER)

        # Notebook (tabs)
        style.configure("TNotebook",
                        background=BG, tabmargins=[0, 0, 0, 0])
        style.configure("TNotebook.Tab",
                        background=PANEL, foreground=FG_DIM,
                        padding=[14, 7], font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "#fff")])

    # ── UI Construction ────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Title bar ──
        title_bar = tk.Frame(self.root, bg=PANEL, height=50)
        title_bar.pack(fill=tk.X, side=tk.TOP)
        title_bar.pack_propagate(False)

        tk.Label(title_bar, text="[CAN]  STM32 CAN Adapter", bg=PANEL,
                 fg=ACCENT, font=("Segoe UI", 15, "bold")).pack(
                 side=tk.LEFT, padx=16, pady=10)

        # Status dot
        self.status_dot = tk.Label(title_bar, text="●", bg=PANEL, fg=RED,
                                   font=("Segoe UI", 16))
        self.status_dot.pack(side=tk.RIGHT, padx=(0, 10))
        self.status_label = tk.Label(title_bar, text="Disconnected", bg=PANEL,
                                     fg=FG_DIM, font=FONT_UI)
        self.status_label.pack(side=tk.RIGHT)

        # ── Connection bar ──
        conn_bar = tk.Frame(self.root, bg=BG)
        conn_bar.pack(fill=tk.X, side=tk.TOP, padx=16, pady=(10, 0))

        # Port selector
        tk.Label(conn_bar, text="Port", bg=BG, fg=FG_DIM,
                 font=FONT_MONO_S).grid(row=0, column=0, padx=(0, 4), sticky="w")
        self.port_cb = ttk.Combobox(conn_bar, width=12, state="readonly", font=FONT_MONO)
        self.port_cb.grid(row=0, column=1, padx=(0, 12))

        ttk.Button(conn_bar, text="⟳", command=self.refresh_ports, width=3).grid(
            row=0, column=2, padx=(0, 18))

        # Baud rate
        tk.Label(conn_bar, text="Baud", bg=BG, fg=FG_DIM,
                 font=FONT_MONO_S).grid(row=0, column=3, padx=(0, 4), sticky="w")
        self.baud_cb = ttk.Combobox(conn_bar, width=10, state="readonly",
                                     values=["9600", "57600", "115200", "230400", "921600"],
                                     font=FONT_MONO)
        self.baud_cb.set("115200")
        self.baud_cb.grid(row=0, column=4, padx=(0, 18))

        # Connect / Disconnect
        self.connect_btn = ttk.Button(conn_bar, text="  Connect  ",
                                      style="Accent.TButton",
                                      command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=5, padx=(0, 8))

        # Clear
        ttk.Button(conn_bar, text="Clear RX", command=self.clear_rx).grid(
            row=0, column=6, padx=(0, 8))

        # RX counter badge
        self.rx_count_var = tk.StringVar(value="RX: 0")
        tk.Label(conn_bar, textvariable=self.rx_count_var, bg=BG, fg=ACCENT2,
                 font=FONT_MONO).grid(row=0, column=7, padx=(20, 0))

        ttk.Separator(self.root, orient="horizontal").pack(
            fill=tk.X, padx=16, pady=(12, 0))

        # ── Main notebook (RX / TX / Log) ──
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 0))

        self._build_rx_tab()
        self._build_tx_tab()
        self._build_log_tab()

        # ── Status bar ──
        status_bar = tk.Frame(self.root, bg=PANEL, height=24)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.sb_var = tk.StringVar(value="Ready")
        tk.Label(status_bar, textvariable=self.sb_var, bg=PANEL,
                 fg=FG_DIM, font=FONT_MONO_S, anchor="w").pack(
                 side=tk.LEFT, padx=10)

    # ── RX Tab ────────────────────────────────────────────────────────────────
    def _build_rx_tab(self):
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text="  RX Monitor  ")

        cols = ("id", "type", "dlc", "data", "count", "period_ms", "last_seen")
        headers = ("CAN ID", "Type", "DLC", "Data (Hex)", "Count", "Period (ms)", "Last Seen")
        widths   = (100,     70,     50,    480,          70,       100,           120)
        anchors  = ("center","center","center","w","center","center","center")

        self.rx_tree = ttk.Treeview(frame, columns=cols, show="headings",
                                     selectmode="extended")
        for col, hdr, w, anc in zip(cols, headers, widths, anchors):
            self.rx_tree.heading(col, text=hdr,
                                  command=lambda c=col: self._sort_tree(c))
            self.rx_tree.column(col, width=w, anchor=anc, stretch=(col == "data"))

        # Alternating row tags
        self.rx_tree.tag_configure("odd",  background=ROW_ODD)
        self.rx_tree.tag_configure("even", background=ROW_EVEN)
        self.rx_tree.tag_configure("ext",  foreground=YELLOW)
        self.rx_tree.tag_configure("std",  foreground=FG)

        vsb = ttk.Scrollbar(frame, orient="vertical",   command=self.rx_tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.rx_tree.xview)
        self.rx_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.rx_tree.pack(fill=tk.BOTH, expand=True)

        # Right-click context menu
        self.rx_menu = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=FG,
                                activebackground=ACCENT, activeforeground="#fff")
        self.rx_menu.add_command(label="Copy ID",   command=self._rx_copy_id)
        self.rx_menu.add_command(label="Copy Data", command=self._rx_copy_data)
        self.rx_menu.add_separator()
        self.rx_menu.add_command(label="Send to TX",command=self._rx_to_tx)
        self.rx_tree.bind("<Button-3>", self._rx_context_menu)

        # Total message counter
        self._total_rx = 0

    # ── TX Tab ────────────────────────────────────────────────────────────────
    def _build_tx_tab(self):
        outer = ttk.Frame(self.nb)
        self.nb.add(outer, text="  Transmit  ")

        # ── Compose card (top, packed) ──
        card = ttk.LabelFrame(outer, text="Compose CAN Frame", padding=20)
        card.pack(side=tk.TOP, fill=tk.X, padx=20, pady=(14, 8))

        # Frame type
        self.tx_type = tk.StringVar(value="STD")
        type_f = ttk.Frame(card, style="Panel.TFrame")
        ttk.Radiobutton(type_f, text="Standard (11-bit)",
                        variable=self.tx_type, value="STD",
                        command=self._update_id_hint).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Radiobutton(type_f, text="Extended (29-bit)",
                        variable=self.tx_type, value="EXT",
                        command=self._update_id_hint).pack(side=tk.LEFT)
        ttk.Label(card, text="Frame Type", style="Panel.TLabel",
                  width=16, anchor="e").grid(row=0, column=0, padx=(0, 12), pady=6, sticky="e")
        type_f.grid(row=0, column=1, sticky="w", pady=6)

        # CAN ID
        id_frame = ttk.Frame(card, style="Panel.TFrame")
        self.tx_id_var = tk.StringVar(value="123")
        self.tx_id = ttk.Entry(id_frame, textvariable=self.tx_id_var,
                               width=14, font=FONT_MONO)
        self.tx_id.pack(side=tk.LEFT)
        self.tx_id_hint = ttk.Label(id_frame, text="(11-bit, 0x000–0x7FF)",
                                     style="Panel.TLabel", foreground=FG_DIM,
                                     font=FONT_MONO_S)
        self.tx_id_hint.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(card, text="CAN ID (hex)", style="Panel.TLabel",
                  width=16, anchor="e").grid(row=1, column=0, padx=(0, 12), pady=6, sticky="e")
        id_frame.grid(row=1, column=1, sticky="w", pady=6)

        # DLC
        self.tx_dlc = ttk.Combobox(card, values=[str(i) for i in range(9)],
                                    width=5, state="readonly", font=FONT_MONO)
        self.tx_dlc.set("8")
        ttk.Label(card, text="DLC (0–8)", style="Panel.TLabel",
                  width=16, anchor="e").grid(row=2, column=0, padx=(0, 12), pady=6, sticky="e")
        self.tx_dlc.grid(row=2, column=1, sticky="w", pady=6)
        self.tx_dlc.bind("<<ComboboxSelected>>", lambda e: (self._dlc_changed(e), self._update_preview()))

        # Data bytes
        data_outer = ttk.Frame(card, style="Panel.TFrame")
        self.tx_data = ttk.Entry(data_outer, width=38, font=FONT_MONO)
        self.tx_data.insert(0, "01 02 03 04 05 06 07 08")
        self.tx_data.pack(side=tk.LEFT)
        ttk.Label(data_outer, text="space-separated hex",
                  foreground=FG_DIM, background=PANEL,
                  font=FONT_MONO_S).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(card, text="Data (hex)", style="Panel.TLabel",
                  width=16, anchor="e").grid(row=3, column=0, padx=(0, 12), pady=6, sticky="e")
        data_outer.grid(row=3, column=1, sticky="w", pady=6)

        # Preview line
        preview_f = ttk.Frame(card, style="Panel.TFrame")
        ttk.Label(preview_f, text="Preview:", foreground=FG_DIM,
                  background=PANEL, font=FONT_MONO_S).pack(side=tk.LEFT)
        self.tx_preview = ttk.Label(preview_f, text="", foreground=ACCENT2,
                                     background=PANEL, font=FONT_MONO_S)
        self.tx_preview.pack(side=tk.LEFT, padx=(6, 0))
        preview_f.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 8))

        # Send + Repeat row
        btn_row = ttk.Frame(card, style="Panel.TFrame")
        btn_row.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.send_btn = ttk.Button(btn_row, text="  Send  ",
                                    style="Green.TButton",
                                    command=self.transmit)
        self.send_btn.pack(side=tk.LEFT, padx=(0, 12))

        self.repeat_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(btn_row, text="Auto-repeat every",
                        variable=self.repeat_var,
                        command=self._toggle_repeat).pack(side=tk.LEFT)
        self.repeat_ms = ttk.Entry(btn_row, width=5, font=FONT_MONO)
        self.repeat_ms.insert(0, "100")
        self.repeat_ms.pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(btn_row, text="ms", background=PANEL,
                  foreground=FG_DIM).pack(side=tk.LEFT)

        self._repeat_job = None

        # Bind data change → preview update
        self.tx_id_var.trace_add("write", lambda *_: self._update_preview())
        self.tx_data.bind("<KeyRelease>", lambda _: self._update_preview())
        self.tx_type.trace_add("write", lambda *_: self._update_preview())
        # Note: <<ComboboxSelected>> for tx_dlc already calls _update_preview via _dlc_changed
        self._update_preview()

        # ── TX History (below card, expands to fill remaining space) ──
        hist_lf = ttk.LabelFrame(outer, text="TX History", padding=6)
        hist_lf.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))

        hist_cols = ("time", "id", "type", "dlc", "data", "status")
        hist_hdrs = ("Time", "CAN ID", "Type", "DLC", "Data", "Status")
        hist_widths = (110, 100, 70, 50, 420, 80)
        self.tx_tree = ttk.Treeview(hist_lf, columns=hist_cols, show="headings",
                                     height=6)
        for col, hdr, w in zip(hist_cols, hist_hdrs, hist_widths):
            self.tx_tree.heading(col, text=hdr)
            self.tx_tree.column(col, width=w, anchor="center" if col != "data" else "w")
        self.tx_tree.tag_configure("ok",  foreground=GREEN)
        self.tx_tree.tag_configure("err", foreground=RED)

        tx_vsb = ttk.Scrollbar(hist_lf, orient="vertical", command=self.tx_tree.yview)
        self.tx_tree.configure(yscrollcommand=tx_vsb.set)
        tx_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tx_tree.pack(fill=tk.BOTH, expand=True)

    # ── Log Tab ───────────────────────────────────────────────────────────────
    def _build_log_tab(self):
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text="  Raw Log  ")

        btn_f = ttk.Frame(frame)
        btn_f.pack(fill=tk.X, padx=8, pady=(6, 0))
        ttk.Button(btn_f, text="Clear Log", command=self._clear_log).pack(side=tk.LEFT)

        self.log_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(btn_f, text="Auto-scroll",
                        variable=self.log_autoscroll).pack(side=tk.LEFT, padx=10)

        self.log_text = tk.Text(frame, bg=BG, fg=FG_DIM,
                                 insertbackground=FG, relief="flat",
                                 font=FONT_MONO_S, state="disabled",
                                 wrap="none")
        log_vsb = ttk.Scrollbar(frame, orient="vertical",
                                  command=self.log_text.yview)
        log_hsb = ttk.Scrollbar(frame, orient="horizontal",
                                  command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=log_vsb.set,
                                 xscrollcommand=log_hsb.set)
        log_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        log_hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Colour tags in log
        self.log_text.tag_configure("rx",  foreground=GREEN)
        self.log_text.tag_configure("tx",  foreground=ACCENT)
        self.log_text.tag_configure("err", foreground=RED)
        self.log_text.tag_configure("sys", foreground=FG_DIM)

    # ── Serial Management ─────────────────────────────────────────────────────
    def refresh_ports(self):
        ports = sorted(p.device for p in serial.tools.list_ports.comports())
        self.port_cb["values"] = ports
        if ports and not self.port_cb.get():
            self.port_cb.set(ports[0])

    def toggle_connection(self):
        if self.is_connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_cb.get()
        baud = self.baud_cb.get()
        if not port:
            messagebox.showwarning("No Port", "Please select a COM port.")
            return
        try:
            self.serial_port = serial.Serial(port, int(baud), timeout=0.1)
            self.is_connected = True
            self.connect_btn.configure(text="  Disconnect  ", style="Red.TButton")
            self.port_cb.configure(state="disabled")
            self.baud_cb.configure(state="disabled")
            self.status_dot.configure(fg=GREEN)
            self.status_label.configure(text=f"Connected  {port} @ {baud} baud")
            self.sb_var.set(f"Connected to {port} at {baud} baud")
            self._log(f"Connected to {port} @ {baud}\n", "sys")
            self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
            self.rx_thread.start()
        except Exception as exc:
            messagebox.showerror("Connection Error", str(exc))

    def disconnect(self):
        self.is_connected = False
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        self.serial_port = None
        self._stop_repeat()
        self.connect_btn.configure(text="  Connect  ", style="Accent.TButton")
        self.port_cb.configure(state="readonly")
        self.baud_cb.configure(state="readonly")
        self.status_dot.configure(fg=RED)
        self.status_label.configure(text="Disconnected")
        self.sb_var.set("Disconnected")
        self._log("Disconnected.\n", "sys")

    # ── Receive Loop (background thread) ──────────────────────────────────────
    def _rx_loop(self):
        buf = b""
        while self.is_connected:
            try:
                chunk = self.serial_port.read(256)
                if chunk:
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        text = line.decode("utf-8", errors="replace").strip("\r\n \t")
                        if text:
                            self.ui_queue.put(("line", text))
            except Exception as exc:
                if self.is_connected:
                    self.ui_queue.put(("error", f"Serial read error: {exc}"))
                break

    # ── Queue Polling (UI thread) ─────────────────────────────────────────────
    def _poll_queue(self):
        try:
            while True:
                tag, payload = self.ui_queue.get_nowait()
                if tag == "line":
                    self._handle_line(payload)
                elif tag == "error":
                    self._log(payload + "\n", "err")
                    if self.is_connected:
                        self.disconnect()
        except queue.Empty:
            pass
        self.root.after(30, self._poll_queue)

    # ── Line Handler ──────────────────────────────────────────────────────────
    def _handle_line(self, line: str):
        self._log(line + "\n", "rx")

        m = RX_RE.search(line)
        if not m:
            return

        raw_id  = m.group(1).upper()
        dlc     = int(m.group(2))
        raw_hex = m.group(3).upper()
        data    = fmt_hex(raw_hex) if raw_hex else "(empty)"

        # Determine frame type from DLC and ID length
        frame_type = "EXT" if int(raw_id, 16) > 0x7FF else "STD"
        can_id = f"0x{raw_id}"

        now = time.monotonic()
        meta = self.rx_meta.get(raw_id)
        if meta is None:
            meta = {"count": 0, "last_t": None, "period": "—"}
            self.rx_meta[raw_id] = meta

        count = meta["count"] + 1
        if meta["last_t"] is not None:
            period = f"{(now - meta['last_t']) * 1000:.1f}"
        else:
            period = "—"
        meta.update(count=count, last_t=now, period=period)

        ts = now_str()
        values = (can_id, frame_type, str(dlc), data,
                  str(count), period + (" ms" if period != "—" else ""), ts)

        self._total_rx += 1
        self.rx_count_var.set(f"RX: {self._total_rx}")

        tag_type = "ext" if frame_type == "EXT" else "std"

        if raw_id in self.rx_rows:
            self.rx_tree.item(self.rx_rows[raw_id],
                               values=values,
                               tags=(tag_type,))
        else:
            row_tag = "odd" if len(self.rx_rows) % 2 == 0 else "even"
            iid = self.rx_tree.insert("", tk.END, values=values,
                                       tags=(tag_type, row_tag))
            self.rx_rows[raw_id] = iid

    # ── Transmit ──────────────────────────────────────────────────────────────
    def transmit(self):
        if not self.is_connected:
            messagebox.showwarning("Not Connected", "Please connect to a serial port first.")
            return
        cmd, err = self._build_tx_cmd()
        if err:
            messagebox.showerror("TX Error", err)
            return
        try:
            self.serial_port.write((cmd + "\r\n").encode("utf-8"))
            self._log(cmd + "\n", "tx")
            self._add_tx_history(cmd, "OK")
            self.sb_var.set(f"Sent: {cmd}")
        except Exception as exc:
            self._add_tx_history(cmd, "ERR")
            messagebox.showerror("TX Error", str(exc))

    def _build_tx_cmd(self) -> tuple[str, str]:
        """Returns (command_string, error_string). One of them is empty."""
        try:
            raw_id = self.tx_id_var.get().strip()
            # safely remove the optional 0x/0X prefix
            if raw_id.lower().startswith("0x"):
                raw_id = raw_id[2:]
            can_id = int(raw_id, 16) if raw_id else 0
        except ValueError:
            return "", "Invalid CAN ID. Enter a hex number (e.g. 1AB)."

        dlc = int(self.tx_dlc.get())
        frame_type = self.tx_type.get()

        if frame_type == "STD" and can_id > 0x7FF:
            return "", f"Standard ID 0x{can_id:X} exceeds 11-bit limit (0x7FF)."
        if frame_type == "EXT" and can_id > 0x1FFFFFFF:
            return "", f"Extended ID 0x{can_id:X} exceeds 29-bit limit."

        raw_hex = self.tx_data.get().replace(" ", "").replace(",", "")
        try:
            data_bytes = bytes.fromhex(raw_hex)
        except ValueError:
            return "", "Invalid data hex string. Use hex pairs (e.g. 01 AB FF)."

        if len(data_bytes) > 8:
            return "", "Data too long – CAN frames carry at most 8 bytes."

        hex_str = data_bytes[:dlc].hex().upper()

        if frame_type == "EXT":
            cmd = f"TX:0x{can_id:08X}:E:{dlc}:{hex_str}"
        else:
            cmd = f"TX:0x{can_id:03X}:{dlc}:{hex_str}"

        return cmd, ""

    def _add_tx_history(self, cmd: str, status: str):
        tag = "ok" if status == "OK" else "err"
        # Parse TX command back for display.
        # Formats:
        #   STD: TX:0x<ID>:<DLC>:<HEX>
        #   EXT: TX:0x<ID>:E:<DLC>:<HEX>
        try:
            # Strip the "TX:" prefix, then split on ":"
            body = cmd[3:]          # "0x<ID>:E:<DLC>:<HEX>" or "0x<ID>:<DLC>:<HEX>"
            parts = body.split(":")
            can_id = parts[0]       # "0x<ID>"
            if len(parts) >= 3 and parts[1].upper() == "E":
                frame_type = "EXT"
                dlc = parts[2]
                data = parts[3] if len(parts) > 3 else ""
            else:
                frame_type = "STD"
                dlc = parts[1] if len(parts) > 1 else "?"
                data = parts[2] if len(parts) > 2 else ""
            data_fmt = " ".join(data[i:i+2] for i in range(0, len(data), 2)).upper()
        except Exception:
            can_id, frame_type, dlc, data_fmt = "?", "?", "?", cmd

        values = (now_str(), can_id, frame_type, dlc, data_fmt, status)
        self.tx_tree.insert("", 0, values=values, tags=(tag,))
        # Limit to 200 rows
        children = self.tx_tree.get_children()
        if len(children) > 200:
            self.tx_tree.delete(children[-1])

    # ── Auto-repeat ───────────────────────────────────────────────────────────
    def _toggle_repeat(self):
        if self.repeat_var.get():
            self._schedule_repeat()
        else:
            self._stop_repeat()

    def _schedule_repeat(self):
        if not self.repeat_var.get():
            return
        self.transmit()
        try:
            ms = max(10, int(self.repeat_ms.get()))
        except ValueError:
            ms = 100
        self._repeat_job = self.root.after(ms, self._schedule_repeat)

    def _stop_repeat(self):
        if self._repeat_job is not None:
            self.root.after_cancel(self._repeat_job)
            self._repeat_job = None
        self.repeat_var.set(False)

    # ── TX helpers ────────────────────────────────────────────────────────────
    def _update_preview(self, *_):
        cmd, err = self._build_tx_cmd()
        if err:
            self.tx_preview.configure(text=f"⚠ {err}", foreground=RED)
        else:
            self.tx_preview.configure(text=cmd + "\\r\\n", foreground=ACCENT2)

    def _update_id_hint(self):
        if self.tx_type.get() == "STD":
            self.tx_id_hint.configure(text="(11-bit, 0x000–0x7FF)")
        else:
            self.tx_id_hint.configure(text="(29-bit, 0x00000000–0x1FFFFFFF)")
        self._update_preview()

    def _dlc_changed(self, _=None):
        dlc = int(self.tx_dlc.get())
        raw = self.tx_data.get().replace(" ", "")
        # Pad or trim data to match DLC
        padded = raw.ljust(dlc * 2, "0")[:dlc * 2]
        formatted = " ".join(padded[i:i+2] for i in range(0, len(padded), 2))
        self.tx_data.delete(0, tk.END)
        self.tx_data.insert(0, formatted.upper())
        self._update_preview()

    # ── RX Table helpers ──────────────────────────────────────────────────────
    def _sort_tree(self, col: str):
        data = [(self.rx_tree.set(k, col), k) for k in self.rx_tree.get_children("")]
        if col == "id":
            # Sort hex CAN IDs numerically
            def key_id(t):
                try:
                    return int(t[0].lstrip("0x").lstrip("0X") or "0", 16)
                except ValueError:
                    return 0
            data.sort(key=key_id)
        elif col in ("count",):
            data.sort(key=lambda t: int(t[0]) if t[0].isdigit() else 0)
        elif col in ("period_ms", "dlc"):
            def key_float(t):
                try:
                    return float(t[0].replace(" ms", "").replace("—", "9999999"))
                except ValueError:
                    return 9999999.0
            data.sort(key=key_float)
        else:
            data.sort()
        for idx, (_, iid) in enumerate(data):
            self.rx_tree.move(iid, "", idx)

    def _rx_context_menu(self, event):
        row = self.rx_tree.identify_row(event.y)
        if row:
            self.rx_tree.selection_set(row)
            self.rx_menu.tk_popup(event.x_root, event.y_root)

    def _rx_copy_id(self):
        sel = self.rx_tree.selection()
        if sel:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.rx_tree.set(sel[0], "id"))

    def _rx_copy_data(self):
        sel = self.rx_tree.selection()
        if sel:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.rx_tree.set(sel[0], "data"))

    def _rx_to_tx(self):
        """Populate TX panel from selected RX row."""
        sel = self.rx_tree.selection()
        if not sel:
            return
        iid = sel[0]
        raw = self.rx_tree.set(iid, "id")
        can_id = raw[2:] if raw.lower().startswith("0x") else raw
        frame_type = self.rx_tree.set(iid, "type")
        dlc = self.rx_tree.set(iid, "dlc")
        data = self.rx_tree.set(iid, "data")

        self.tx_id_var.set(can_id)
        self.tx_type.set(frame_type)
        self.tx_dlc.set(dlc)
        self.tx_data.delete(0, tk.END)
        self.tx_data.insert(0, data)
        self._update_preview()
        self.nb.select(1)   # Switch to TX tab

    def clear_rx(self):
        for iid in self.rx_tree.get_children():
            self.rx_tree.delete(iid)
        self.rx_rows.clear()
        self.rx_meta.clear()
        self._total_rx = 0
        self.rx_count_var.set("RX: 0")

    # ── Log helpers ───────────────────────────────────────────────────────────
    def _log(self, text: str, tag: str = "sys"):
        ts = now_str()
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"[{ts}] {text}", tag)
        if self.log_autoscroll.get():
            self.log_text.see(tk.END)
        # Cap log at 5000 lines
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 5000:
            self.log_text.delete("1.0", f"{lines - 4000}.0")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    # ── Shutdown ──────────────────────────────────────────────────────────────
    def on_close(self):
        self._stop_repeat()
        self.disconnect()
        self.root.destroy()


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = CanAdapterApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
