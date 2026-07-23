from __future__ import annotations

import csv
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"
MAIN_FILE = PROJECT_ROOT / "main.py"
COLUMN_NAMES = {
    "Ticker": "代码", "Name": "名称", "Sector": "板块", "Industry": "行业", "IsETF": "类型", "AssetType": "类型", "Style": "风格", "Quality": "质量",
    "Close": "收盘价", "Score": "综合评分", "BacktestScore": "回测评分", "CompositeScore": "综合回测评分", "BacktestSamples": "回测样本数",
    "BacktestWinRate20D": "20日胜率", "BacktestWinRate60D": "60日胜率", "BacktestAverageReturn20D": "20日平均收益", "BacktestAverageReturn60D": "60日平均收益", "BacktestObjectiveValue": "回测目标值", "UniverseType": "股票池类型", "SurvivorshipBiasWarning": "幸存者偏差警告", "TrendScore": "趋势分", "VolumeScore": "成交量分",
    "AccumulationScore": "吸筹分", "CompressionScore": "波动分", "StructureScore": "结构分",
    "OBV": "OBV", "CMF": "CMF", "AD": "A/D", "ATR14": "ATR14", "RSI14": "RSI14",
    "DistToLow52W": "距52周低点", "WyckoffPhase": "威科夫阶段", "Stage": "阶段", "MarketRegime": "市场环境",
    "IndustryRelativeStrength": "行业强度", "DataSource": "数据源", "DataAsOf": "数据日期", "DataAgeDays": "数据延迟天数", "DataCoverage": "数据覆盖率",
    "VolAccumDays": "放量天数", "SignalCount": "信号数", "FilterCount": "通过项数", "PassedFilters": "通过筛选", "OBV_Div": "OBV背离", "CMF_Pos": "CMF为正", "AD_SlopePos": "A/D上升",
    "BearMarket": "熊市条件", "Consolidation": "横盘整理", "VolAccum": "放量吸筹",
    "VolContract": "波动收缩", "Error": "错误",
}
DISPLAY_COLUMNS = (
    "Ticker", "Name", "AssetType", "Sector", "Industry", "Quality", "Score",
    "BacktestScore", "CompositeScore", "BacktestObjectiveValue", "ScoreConfidence", "ScoreMissingIndicators", "BacktestSamples", "Close", "DistToLow52W", "WyckoffPhase", "Stage", "VolAccumDays",
    "UniverseType", "SurvivorshipBiasWarning", "SignalCount", "PassedFilters",
)
COLUMN_WIDTHS = {
    "Ticker": 105, "Name": 150, "AssetType": 68, "Sector": 100, "Industry": 115,
    "Quality": 78, "Score": 88, "BacktestScore": 96, "CompositeScore": 112, "BacktestObjectiveValue": 104, "ScoreConfidence": 96, "ScoreMissingIndicators": 96, "BacktestSamples": 100, "Close": 92, "DistToLow52W": 110,
    "WyckoffPhase": 112, "Stage": 88, "VolAccumDays": 88, "UniverseType": 150, "SurvivorshipBiasWarning": 120, "SignalCount": 78,
    "PassedFilters": 88,
}
NUMBER_COLUMNS = {"Score", "BacktestScore", "CompositeScore", "BacktestObjectiveValue", "ScoreConfidence", "ScoreMissingIndicators", "BacktestSamples", "Close", "DistToLow52W", "VolAccumDays", "SignalCount"}
TEXT_COLUMNS = {"Name", "Sector", "Industry", "WyckoffPhase", "Stage"}
MAX_RENDERED_ROWS = 500


class ScannerGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("A股机构吸筹扫描器")
        self.root.geometry("1440x900")
        self.root.minsize(1100, 650)
        self.process: subprocess.Popen[str] | None = None
        self.scan_running = False
        self.backtest_running = False
        self.scope = tk.StringVar(value="全部股票和ETF")
        self.tickers = tk.StringVar()
        self.search = tk.StringVar()
        self.sector_filter = tk.StringVar(value="全部板块")
        self.industry_filter = tk.StringVar(value="全部行业")
        self.quality_filter = tk.StringVar(value="全部质量")
        self.no_resume = tk.BooleanVar(value=False)
        self.force_download = tk.BooleanVar(value=False)
        self.data_source = tk.StringVar(value="eastmoney")
        self.data_source_label = tk.StringVar(value="当前：东方财富")
        self.status = tk.StringVar(value="就绪")
        self._row_details: dict[str, dict[str, str]] = {}
        self.filtered_tickers: list[str] = []
        self._csv_headers: list[str] = []
        self._csv_rows: list[list[str]] = []
        self._csv_path: Path | None = None
        self._filter_job: str | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._log_job = self.root.after(150, self._flush_log_queue)
        self._configure_style()
        self._build_ui()
        self.search.trace_add("write", self._schedule_filter_refresh)
        self.sector_filter.trace_add("write", self._schedule_filter_refresh)
        self.industry_filter.trace_add("write", self._schedule_filter_refresh)
        self.quality_filter.trace_add("write", self._schedule_filter_refresh)
        self._load_best_available_results()

    def _configure_style(self) -> None:
        self.root.configure(background="#f4f7fb")
        self.root.option_add("*Font", ("Microsoft YaHei UI", 9))
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f7fb")
        style.configure("TLabel", background="#f4f7fb", foreground="#243b53")
        style.configure("TLabelframe", background="#f4f7fb", bordercolor="#d7e2ee")
        style.configure("TLabelframe.Label", background="#f4f7fb", foreground="#17324d", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Header.TFrame", background="#17324d")
        style.configure("Title.TLabel", background="#17324d", foreground="white", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Sub.TLabel", background="#17324d", foreground="#cbd9e8", font=("Microsoft YaHei UI", 9))
        style.configure("Accent.TButton", foreground="white", background="#1677ff", padding=(16, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#4096ff")])
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 9), background="white", fieldbackground="white", foreground="#243b53")
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#17324d")])
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"), background="#eaf2fb", foreground="#17324d", padding=(8, 6))
        style.configure("TCombobox", padding=4)
        style.configure("TEntry", padding=4)
        style.configure("Accent.TButton", foreground="white", background="#1677ff", padding=(18, 9), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#4096ff")])
        style.configure("Status.TLabel", background="#f4f7fb", foreground="#55708a", font=("Microsoft YaHei UI", 9))

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(24, 18))
        header.pack(fill=tk.X)
        ttk.Label(header, text="A股机构吸筹扫描器", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(header, text="全市场股票与ETF · 技术指标 · 评分筛选", style="Sub.TLabel").pack(anchor=tk.W, pady=(4, 0))

        controls = ttk.LabelFrame(self.root, text="扫描设置", padding=12)
        controls.pack(fill=tk.X, padx=18, pady=(14, 8))
        ttk.Label(controls, text="扫描范围").grid(row=0, column=0, padx=(0, 6), sticky=tk.W)
        box = ttk.Combobox(controls, textvariable=self.scope, values=("全部股票和ETF", "仅股票", "仅ETF"), state="readonly", width=18)
        box.grid(row=0, column=1, padx=(0, 20), sticky=tk.W)
        ttk.Label(controls, text="指定代码").grid(row=0, column=2, padx=(0, 6), sticky=tk.W)
        ttk.Entry(controls, textvariable=self.tickers, width=38).grid(row=0, column=3, padx=(0, 8), sticky=tk.W)
        ttk.Label(controls, text="例：588000.SH,000001.SZ", foreground="#708399").grid(row=0, column=4, sticky=tk.W)
        self.source_box = ttk.Combobox(controls, textvariable=self.data_source, values=("eastmoney", "sina", "tencent"), state="readonly", width=12)
        self.source_box.grid(row=0, column=5, padx=(12, 4), sticky=tk.W)
        self.source_box.bind("<<ComboboxSelected>>", self._data_source_changed)
        ttk.Label(controls, textvariable=self.data_source_label, foreground="#55708a").grid(row=0, column=6, padx=(4, 0), sticky=tk.W)
        ttk.Checkbutton(controls, text="不使用断点", variable=self.no_resume).grid(row=1, column=0, columnspan=2, pady=(12, 0), sticky=tk.W)
        ttk.Checkbutton(controls, text="强制重新下载", variable=self.force_download).grid(row=1, column=2, columnspan=2, pady=(12, 0), sticky=tk.W)
        self.start_button = ttk.Button(controls, text="▶ 开始扫描", style="Accent.TButton", command=self.start_scan)
        self.start_button.grid(row=1, column=4, pady=(10, 0), sticky=tk.E)

        toolbar = ttk.Frame(self.root, padding=(18, 2))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="查看Top50", command=self._load_top50).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="查看全部结果", command=lambda: self.load_csv("AllResults.csv")).pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="打开结果目录", command=self.open_output).pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="运行回测", command=self.start_backtest).pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="查看回测", command=self.show_backtest).pack(side=tk.LEFT, padx=6)
        ttk.Label(toolbar, text="板块", padding=(16, 0, 4, 0)).pack(side=tk.LEFT)
        self.sector_box = ttk.Combobox(toolbar, textvariable=self.sector_filter, state="readonly", width=12)
        self.sector_box.pack(side=tk.LEFT)
        self.sector_box.bind("<<ComboboxSelected>>", self._sector_changed)
        ttk.Label(toolbar, text="行业", padding=(8, 0, 4, 0)).pack(side=tk.LEFT)
        self.industry_box = ttk.Combobox(toolbar, textvariable=self.industry_filter, state="readonly", width=14)
        self.industry_box.pack(side=tk.LEFT)
        ttk.Label(toolbar, text="质量", padding=(8, 0, 4, 0)).pack(side=tk.LEFT)
        ttk.Combobox(toolbar, textvariable=self.quality_filter, values=("全部质量", "强候选", "候选", "观察", "普通"), state="readonly", width=9).pack(side=tk.LEFT)
        ttk.Label(toolbar, text="搜索", padding=(12, 0, 4, 0)).pack(side=tk.LEFT)
        ttk.Entry(toolbar, textvariable=self.search, width=20).pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(toolbar, mode="indeterminate", length=180)
        self.progress.pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Label(toolbar, textvariable=self.status, style="Status.TLabel").pack(side=tk.RIGHT)

        body = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(6, 16))
        table_frame = ttk.Frame(body)
        self.table = ttk.Treeview(table_frame, show="headings", selectmode="browse")
        ybar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
        xbar = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.table.xview)
        self.table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.table.bind("<Double-1>", self.show_selected_detail)
        self.table.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        body.add(table_frame, weight=5)
        log_frame = ttk.LabelFrame(body, text="运行日志", padding=6)
        self.log_text = tk.Text(log_frame, height=8, wrap=tk.NONE, state=tk.DISABLED, bg="#17212b", fg="#d5e4f2", insertbackground="white", font=("Consolas", 9))
        logbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=logbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        logbar.pack(side=tk.RIGHT, fill=tk.Y)
        body.add(log_frame, weight=2)

    def build_command(self) -> list[str]:
        command = [sys.executable, str(MAIN_FILE), "scan"]
        if self.tickers.get().strip():
            command += ["--tickers", self.tickers.get().strip()]
        elif self.scope.get() == "仅股票":
            command.append("--stocks-only")
        elif self.scope.get() == "仅ETF":
            command.append("--etfs-only")
        if self.no_resume.get(): command.append("--no-resume")
        if self.force_download.get(): command.append("--force-download")
        command += ["--data-source", self.data_source.get()]
        return command

    def _top50_tickers(self) -> list[str]:
        path = OUTPUT_DIR / "Top50.csv"
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                return [
                    ticker.strip().upper()
                    for row in list(reader)[:50]
                    if (ticker := str(row.get("Ticker", ""))).strip()
                ]
        except (OSError, UnicodeError, csv.Error):
            return []

    def _atomic_write_text(self, path: Path, content: str, encoding: str = "utf-8") -> None:
        temporary_path = path.with_name(f".{path.name}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary_path.write_text(content, encoding=encoding)
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _write_top50_csv(self, tickers: list[str]) -> Path:
        path = OUTPUT_DIR / "Top50.csv"
        if "Ticker" not in self._csv_headers:
            raise ValueError("当前结果缺少 Ticker 列，无法生成 Top50.csv")
        ordered_tickers = list(dict.fromkeys(ticker.strip().upper() for ticker in tickers if ticker.strip()))[:50]
        ticker_index = self._csv_headers.index("Ticker")
        rows_by_ticker = {
            row[ticker_index].strip().upper(): row
            for row in self._csv_rows
            if len(row) > ticker_index and row[ticker_index].strip()
        }
        selected = [rows_by_ticker[ticker] for ticker in ordered_tickers if ticker in rows_by_ticker]
        if len(selected) != len(ordered_tickers):
            raise ValueError("当前筛选结果与表格数据不一致，无法生成 Top50.csv")
        temporary_path = path.with_name(f".{path.name}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with temporary_path.open("w", encoding="utf-8-sig", newline="") as file:
                csv.writer(file).writerows([self._csv_headers, *selected])
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        self._csv_path = None
        self._csv_mtime = None
        return path

    def _load_top50(self) -> None:
        if not self._csv_headers or not self.filtered_tickers:
            messagebox.showinfo("提示", "当前筛选结果为空，请先完成扫描或调整筛选条件。")
            return
        tickers = list(dict.fromkeys(self.filtered_tickers))[:50]
        try:
            self._write_top50_csv(tickers)
            if not self.load_csv("Top50.csv"):
                raise ValueError("Top50.csv 已生成，但未包含有效结果")
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            messagebox.showerror("生成 Top50 失败", str(exc))
            return
        self.append_log(f"已从当前筛选结果生成 Top50.csv：{len(tickers)} 只\n")

    def start_backtest(self) -> None:
        if self.scan_running:
            messagebox.showinfo("提示", "当前任务正在运行中")
            return
        backtest_tickers = list(dict.fromkeys(self.filtered_tickers))
        if len(backtest_tickers) < 50:
            messagebox.showerror("无法运行回测", f"回测至少需要 50 个标的，当前筛选结果为 {len(backtest_tickers)} 个。")
            return
        backtest_tickers = backtest_tickers[:50]
        ticker_file = OUTPUT_DIR / "BacktestTop50.txt"
        try:
            self._write_top50_csv(backtest_tickers)
            self._atomic_write_text(ticker_file, "\n".join(backtest_tickers) + "\n")
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            messagebox.showerror("准备回测失败", str(exc))
            return
        self.scan_running = True
        self.backtest_running = True
        self.start_button.configure(state=tk.DISABLED)
        self.progress.start(12)
        command = [sys.executable, str(MAIN_FILE), "backtest", "--data-source", self.data_source.get(), "--tickers-file", str(ticker_file)]
        self.append_log("回测当前筛选结果：严格 50 个标的\n")
        self.append_log(f"执行回测命令：{MAIN_FILE.name} backtest --data-source {self.data_source.get()} --tickers-file BacktestTop50.txt\n")
        threading.Thread(target=self.run_process, args=(command,), daemon=True).start()

    def show_backtest(self) -> None:
        path = OUTPUT_DIR / "BacktestSummary.json"
        if not path.exists():
            messagebox.showinfo("回测结果", "尚未生成回测结果")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            dialog = tk.Toplevel(self.root)
            dialog.title("历史回测结果")
            dialog.geometry("760x680")
            dialog.minsize(620, 480)
            dialog.configure(background="#f4f7fb")
            ttk.Label(dialog, text="历史回测结果", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor=tk.W, padx=22, pady=(20, 4))
            ttk.Label(dialog, text="仅统计本次回测传入的股票集合，不代表全市场表现", foreground="#55708a").pack(anchor=tk.W, padx=22, pady=(0, 12))
            frame = ttk.Frame(dialog, padding=(20, 4))
            frame.pack(fill=tk.BOTH, expand=True)
            text = tk.Text(frame, wrap=tk.WORD, state=tk.DISABLED, bg="white", fg="#243b53", relief=tk.FLAT, padx=14, pady=14, font=("Microsoft YaHei UI", 10))
            scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            ticker_count = data.get("ticker_count", len(data.get("by_ticker", [])))
            lines = [
                f"样本数：{data.get('samples', 0)}",
                f"股票数：{ticker_count}",
                f"20日胜率：{float(data.get('win_rate_20d', 0)) * 100:.2f}%",
                f"20日平均收益：{float(data.get('average_return_20d', 0)):.2f}%",
                f"20日中位数收益：{float(data.get('median_return_20d', 0)):.2f}%",
                f"60日胜率：{float(data.get('win_rate_60d', 0)) * 100:.2f}%",
                f"60日平均收益：{float(data.get('average_return_60d', 0)):.2f}%",
                f"60日中位数收益：{float(data.get('median_return_60d', 0)):.2f}%",
                f"回测目标值：{float(data.get('objective_value', 0)):.4f}",
                f"股票池类型：{data.get('universe_type', 'current_survivor_pool')}",
                f"幸存者偏差警告：{data.get('survivorship_bias_warning', True)}",
                "",
                "说明：胜率为未来收益大于 0 的样本占比，收益率单位为百分比。",
            ]
            text.configure(state=tk.NORMAL)
            text.insert("1.0", "\n".join(lines))
            text.configure(state=tk.DISABLED)
        except Exception as exc:
            messagebox.showerror("读取回测结果失败", str(exc))

    def start_scan(self) -> None:
        if self.scan_running:
            messagebox.showinfo("提示", "扫描正在运行中")
            return
        self.clear_log()
        self.scan_running = True
        self.scan_output_mtime = self._results_mtime()
        self.start_button.configure(state=tk.DISABLED)
        self.progress.start(12)
        command = self.build_command()
        self.append_log("执行：" + " ".join(command) + "\n")
        threading.Thread(target=self.run_process, args=(command,), daemon=True).start()

    def run_process(self, command: list[str]) -> None:
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            self.process = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self._log_queue.put(line)
            code = self.process.wait()
            self.process = None
            self.root.after(0, self.scan_finished, code)
        except Exception as exc:
            self.root.after(0, self.scan_failed, str(exc))

    def _flush_log_queue(self) -> None:
        lines: list[str] = []
        while len(lines) < 200:
            try:
                lines.append(self._log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.append_log("".join(lines))
        self._log_job = self.root.after(150, self._flush_log_queue)

    def scan_finished(self, code: int) -> None:
        self.progress.stop()
        was_backtest = self.backtest_running
        self.scan_running = False
        self.start_button.configure(state=tk.NORMAL)
        self.backtest_running = False
        self.status.set("扫描完成" if code == 0 else f"任务结束，退出码：{code}")
        if code == 0 and was_backtest and (OUTPUT_DIR / "BacktestSummary.json").exists():
            self.show_backtest()
            self._load_best_available_results()
        elif code == 0:
            if not self._load_best_available_results():
                if self._results_mtime() == getattr(self, "scan_output_mtime", ()):
                    self.status.set("扫描完成，但结果文件未更新")
                    self.append_log("扫描进程已完成，但没有找到有效结果文件，请检查运行日志。\n")
                else:
                    self.status.set("扫描完成，但结果文件为空")
                    self.append_log("扫描完成，但结果文件没有有效数据。\n")
        else:
            self.append_log("本次扫描失败，结果文件未刷新。\n")

    def _csv_has_results(self, filename: str) -> bool:
        path = OUTPUT_DIR / filename
        if not path.exists() or path.stat().st_size <= 3:
            return False
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.reader(file)
                headers = next(reader, [])
                if "Ticker" not in headers:
                    return False
                ticker_index = headers.index("Ticker")
                return any(len(row) > ticker_index and row[ticker_index].strip() for row in reader)
        except (OSError, UnicodeError, csv.Error):
            return False

    def _load_best_available_results(self) -> bool:
        for filename in ("Top50.csv", "AllResults.csv"):
            if self._csv_has_results(filename):
                return self.load_csv(filename)
        return False


    def scan_failed(self, error: str) -> None:
        self.progress.stop()
        self.start_button.configure(state=tk.NORMAL)
        self.backtest_running = False
        self.status.set("扫描启动失败")
        self.append_log(error + "\n")
        messagebox.showerror("运行失败", error)

    def append_log(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        if "扫描" in text and "完成" in text:
            self.status.set("扫描完成")
        elif text.strip():
            self.status.set("扫描运行中")

    def show_selected_detail(self, _event=None) -> None:
        selection = self.table.selection()
        if not selection:
            return
        values = self.table.item(selection[0], "values")
        data = self._row_details.get(selection[0], {})
        if not data:
            headers = list(self.table["columns"])
            data = dict(zip(headers, values))
        dialog = tk.Toplevel(self.root)
        dialog.title(f"标的详情 · {data.get('Ticker', '')}")
        dialog.geometry("620x620")
        dialog.minsize(520, 420)
        dialog.configure(background="#f4f7fb")
        ttk.Label(dialog, text=f"{data.get('Ticker', '')}  {data.get('Name', '')}", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor=tk.W, padx=22, pady=(20, 4))
        ttk.Label(dialog, text=f"阶段：{data.get('Stage', '未知')}  ·  市场环境：{data.get('MarketRegime', '未知')}", foreground="#55708a").pack(anchor=tk.W, padx=22, pady=(0, 12))
        frame = ttk.Frame(dialog, padding=(20, 4))
        frame.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(frame, wrap=tk.WORD, state=tk.DISABLED, bg="white", fg="#243b53", relief=tk.FLAT, padx=14, pady=14, font=("Microsoft YaHei UI", 10))
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        detail_keys = [
            "Quality", "Score", "ScoreConfidence", "ScoreMissingIndicators", "BacktestScore", "CompositeScore", "BacktestObjectiveValue", "BacktestSamples", "BacktestWinRate20D", "BacktestWinRate60D", "BacktestAverageReturn20D", "BacktestAverageReturn60D", "UniverseType", "SurvivorshipBiasWarning", "TrendScore", "VolumeScore", "AccumulationScore", "CompressionScore", "StructureScore",
            "WyckoffPhase", "IndustryRelativeStrength", "DataSource", "DataAsOf", "DataAgeDays", "DataCoverage",
            "SignalCount", "FilterCount", "PassedFilters", "OBV_Div", "CMF_Pos", "AD_SlopePos", "BearMarket", "Consolidation", "VolAccum", "VolContract", "Error",
        ]
        lines = [f"{COLUMN_NAMES.get(key, key)}：{data.get(key, '')}" for key in detail_keys if data.get(key, '') not in ("", None)]
        text.configure(state=tk.NORMAL)
        text.insert("1.0", "\n".join(lines))
        text.configure(state=tk.DISABLED)

    def _results_mtime(self) -> tuple[tuple[str, int], ...]:
        files = ("Top50.csv", "AllResults.csv", "Top200.parquet", "AllResults.parquet")
        return tuple(
            (name, (OUTPUT_DIR / name).stat().st_mtime_ns)
            for name in files
            if (OUTPUT_DIR / name).exists()
        )

    def clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _data_source_changed(self, _event=None) -> None:
        labels = {"eastmoney": "东方财富", "sina": "新浪", "tencent": "腾讯"}
        self.data_source_label.set(f"当前：{labels[self.data_source.get()]}")
        self.status.set(f"已切换数据源：{labels[self.data_source.get()]}")

    def _sector_changed(self, _event=None) -> None:
        self.industry_filter.set("全部行业")
        self.load_csv(self.current_file)

    def _update_filter_values(self, headers: list[str], rows: list[list[str]]) -> None:
        def values_for(column: str) -> list[str]:
            if column not in headers:
                return []
            index = headers.index(column)
            return sorted({row[index] for row in rows if len(row) > index and row[index].strip()})

        sectors = values_for("Sector")
        self.sector_box["values"] = ["全部板块", *sectors]
        if self.sector_filter.get() not in self.sector_box["values"]:
            self.sector_filter.set("全部板块")

        industries = values_for("Industry")
        if self.sector_filter.get() != "全部板块" and "Sector" in headers:
            sector_index = headers.index("Sector")
            industry_index = headers.index("Industry") if "Industry" in headers else -1
            industries = sorted({
                row[industry_index] for row in rows
                if industry_index >= 0 and len(row) > max(sector_index, industry_index)
                and row[sector_index] == self.sector_filter.get() and row[industry_index].strip()
            })
        self.industry_box["values"] = ["全部行业", *industries]
        if self.industry_filter.get() not in self.industry_box["values"]:
            self.industry_filter.set("全部行业")

    def _schedule_filter_refresh(self, *_args) -> None:
        if self._filter_job is not None:
            self.root.after_cancel(self._filter_job)
        self._filter_job = self.root.after(180, self._render_cached_rows)

    def _row_matches_filters(self, indexes: dict[str, int], row: list[str], query: str) -> bool:
        values = row + [""] * max(0, len(self._csv_headers) - len(row))
        def value_for(column: str) -> str:
            index = indexes.get(column)
            return values[index] if index is not None and index < len(values) else ""
        return (
            (not query or query in " ".join(values).casefold())
            and (self.sector_filter.get() == "全部板块" or value_for("Sector") == self.sector_filter.get())
            and (self.industry_filter.get() == "全部行业" or value_for("Industry") == self.industry_filter.get())
            and (self.quality_filter.get() == "全部质量" or value_for("Quality") == self.quality_filter.get())
        )

    def _render_cached_rows(self) -> bool:
        self._filter_job = None
        headers = self._csv_headers
        data_rows = self._csv_rows
        if not headers:
            return False
        indexes = {header: index for index, header in enumerate(headers)}
        query = self.search.get().strip().casefold()
        filtered = [row for row in data_rows if self._row_matches_filters(indexes, row, query)]
        ticker_index = indexes.get("Ticker", -1)
        self.filtered_tickers = [row[ticker_index].strip().upper() for row in filtered if ticker_index >= 0 and len(row) > ticker_index and row[ticker_index].strip()]
        display_headers = [column for column in DISPLAY_COLUMNS if column in headers]
        self.table.delete(*self.table.get_children())
        self._row_details.clear()
        self.table["columns"] = display_headers
        for header in display_headers:
            anchor = tk.E if header in NUMBER_COLUMNS else tk.W if header in TEXT_COLUMNS else tk.CENTER
            self.table.heading(header, text=COLUMN_NAMES.get(header, header))
            self.table.column(header, width=COLUMN_WIDTHS.get(header, 90), anchor=anchor, stretch=False)
        header_indexes = [indexes[column] for column in display_headers]
        rendered_count = min(len(filtered), MAX_RENDERED_ROWS)
        for row in filtered[:rendered_count]:
            values = row + [""] * max(0, len(headers) - len(row))
            display_values = [values[index] for index in header_indexes]
            if "AssetType" in display_headers:
                type_index = display_headers.index("AssetType")
                display_values[type_index] = "ETF" if str(display_values[type_index]).strip().lower() == "etf" else "股票"
            if "PassedFilters" in display_headers:
                passed_index = display_headers.index("PassedFilters")
                display_values[passed_index] = "通过" if str(display_values[passed_index]).strip().lower() in {"true", "1", "yes", "是"} else "未通过"
            item_id = self.table.insert("", tk.END, values=display_values)
            self._row_details[item_id] = dict(zip(headers, values))
        self.status.set(f"{self.current_file} · 命中 {len(filtered)} / {len(data_rows)} 条 · 实际渲染 {rendered_count} 条 · 双击查看详情")
        return True

    def load_csv(self, filename: str) -> bool:
        path = OUTPUT_DIR / filename
        self.current_file = filename
        if not path.exists():
            self.status.set(f"未找到 {filename}")
            return False
        try:
            if self._csv_path != path:
                with path.open("r", encoding="utf-8-sig", newline="") as file:
                    rows = list(csv.reader(file))
                if not rows:
                    self.status.set(f"{filename} 没有结果")
                    return False
                self._csv_headers = rows[0]
                self._csv_rows = rows[1:]
                self._csv_path = path
                self._update_filter_values(self._csv_headers, self._csv_rows)
            return self._render_cached_rows()
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc))
            return False

    def open_output(self) -> None:
        if OUTPUT_DIR.exists(): subprocess.Popen(["explorer", str(OUTPUT_DIR)])


def main() -> None:
    root = tk.Tk()
    ScannerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
