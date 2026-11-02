"""從電子 PDF 擷取文字並以 tkinter.ttk 提供 Windows 桌面介面。"""

from __future__ import annotations

import hashlib
import os
import queue
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

try:
    import pymupdf
except ImportError:
    pymupdf = None


ENCODINGS = {
    "自動（優先 CP950，必要時 UTF-8）": "auto",
    "ANSI（繁中 Windows／CP950）": "cp950",
    "UTF-8": "utf-8",
    "UTF-8 BOM": "utf-8-sig",
    "Big5": "big5",
    "UTF-16 LE BOM": "utf-16-le",
}
BOMS = {"utf-8-sig": b"\xef\xbb\xbf", "utf-16-le": b"\xff\xfe"}


class ConversionError(Exception):
    """可直接顯示給使用者的轉換錯誤。"""


@dataclass(frozen=True)
class DestinationSnapshot:
    size: int
    modified_ns: int
    digest: str


@dataclass(frozen=True)
class ConversionResult:
    total_pages: int
    empty_pages: tuple[int, ...]
    encoding: str


def capture_destination(path: Path) -> DestinationSnapshot | None:
    """記錄既有目的檔狀態；不存在時傳回 None。"""
    try:
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return DestinationSnapshot(stat.st_size, stat.st_mtime_ns, digest.hexdigest())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConversionError(f"無法檢查目的檔：{exc}") from exc


def _validate_paths(source: Path, destination: Path, encoding: str) -> None:
    if encoding not in ENCODINGS.values():
        raise ConversionError("請選擇有效的輸出編碼。")
    if source.suffix.lower() != ".pdf" or not source.is_file():
        raise ConversionError("來源必須是存在的 PDF 檔案。")
    if destination.suffix.lower() != ".txt":
        raise ConversionError("目的檔副檔名必須是 .txt，請修正路徑。")
    if not destination.parent.is_dir():
        raise ConversionError("目的資料夾不存在，請選擇已存在的資料夾。")
    if source.resolve() == destination.resolve():
        raise ConversionError("來源與目的不可指向同一個檔案。")
    if destination.exists() and not destination.is_file():
        raise ConversionError("目的路徑不是一般檔案。")


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").replace("\n", "\r\n")


def convert_pdf(
    source: Path | str,
    destination: Path | str,
    encoding: str,
    progress: Callable[[int, int], None],
    expected_destination: DestinationSnapshot | None = None,
) -> ConversionResult:
    """安全轉換單一 PDF；既有目的檔須提供經確認的狀態。"""
    source, destination = Path(source), Path(destination)
    _validate_paths(source, destination, encoding)
    if pymupdf is None:
        raise ConversionError("缺少 PyMuPDF，請先執行 python -m pip install -r requirements.txt。")
    if capture_destination(destination) != expected_destination:
        raise ConversionError("目的檔已變更或尚未確認覆寫，請重新確認。")

    temporary: Path | None = None
    alternate: Path | None = None
    try:
        try:
            document = pymupdf.open(str(source))
        except Exception as exc:
            raise ConversionError(f"無法開啟 PDF，檔案可能已損毀：{exc}") from exc
        with document:
            if not document.is_pdf:
                raise ConversionError("來源不是有效的 PDF。")
            if document.needs_pass:
                raise ConversionError("此 PDF 需要密碼，請先提供已解密副本。")
            total = document.page_count
            if total < 1:
                raise ConversionError("PDF 沒有可處理的頁面。")

            with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent, suffix=".tmp", prefix="pdf-to-txt-", delete=False) as output:
                temporary = Path(output.name)
                output.write(BOMS.get(encoding, b""))
                empty_pages = []
                has_text = False
                cp950_compatible = True
                write_encoding = "utf-8" if encoding in ("auto", "utf-8-sig") else encoding
                for index in range(total):
                    page_number = index + 1
                    page_text = document.load_page(index).get_text("text", sort=True)
                    if not page_text.strip():
                        empty_pages.append(page_number)
                    else:
                        has_text = True
                    normalized = _normalize_text(page_text)
                    if encoding == "auto" and cp950_compatible:
                        try:
                            normalized.encode("cp950", errors="strict")
                        except UnicodeEncodeError:
                            cp950_compatible = False
                    try:
                        encoded = normalized.encode(write_encoding, errors="strict")
                    except UnicodeEncodeError as exc:
                        character = normalized[exc.start]
                        raise ConversionError(
                            f"第 {page_number} 頁的字元 {character!r}（U+{ord(character):04X}）無法以所選編碼儲存。"
                            "請改選 UTF-8 或 UTF-8 BOM 後重試。"
                        ) from exc
                    if index:
                        output.write("\r\n\r\n".encode(write_encoding))
                    output.write(encoded)
                    progress(page_number, total)
                if not has_text:
                    raise ConversionError("整份 PDF 都未擷取到文字，可能需要 OCR。")
                output.flush()
                os.fsync(output.fileno())

        selected_encoding = "utf-8" if encoding == "auto" else encoding
        if encoding == "auto" and cp950_compatible:
            with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent, suffix=".tmp", prefix="pdf-to-txt-", delete=False) as output:
                alternate = Path(output.name)
                with temporary.open("r", encoding="utf-8", newline="") as extracted:
                    while chunk := extracted.read(64 * 1024):
                        output.write(chunk.encode("cp950", errors="strict"))
                output.flush()
                os.fsync(output.fileno())
            selected_encoding = "cp950"

        if capture_destination(destination) != expected_destination:
            raise ConversionError("目的檔在確認後已有變更，請重新確認覆寫。")
        os.replace(alternate if alternate is not None else temporary, destination)
        return ConversionResult(total, tuple(empty_pages), selected_encoding)
    except ConversionError:
        raise
    except OSError as exc:
        raise ConversionError(f"無法完成寫入，請檢查權限、磁碟空間或檔案是否被占用：{exc}") from exc
    except Exception as exc:
        raise ConversionError(f"轉換失敗，請檢查 PDF 是否損毀：{exc}") from exc
    finally:
        for path in (temporary, alternate):
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


class PdfToTxtApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("中文 PDF 轉 TXT")
        self.root.minsize(600, 230)
        self.source = tk.StringVar()
        self.destination = tk.StringVar()
        self.encoding = tk.StringVar(value=next(iter(ENCODINGS)))
        self.status = tk.StringVar(value="待命")
        self.events: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.controls: list[tk.Widget] = []
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="來源 PDF").grid(row=0, column=0, sticky="w", pady=5)
        source_entry = ttk.Entry(frame, textvariable=self.source)
        source_entry.grid(row=0, column=1, sticky="ew", padx=8)
        source_button = ttk.Button(frame, text="瀏覽…", command=self._browse_source)
        source_button.grid(row=0, column=2)
        ttk.Label(frame, text="目的 TXT").grid(row=1, column=0, sticky="w", pady=5)
        destination_entry = ttk.Entry(frame, textvariable=self.destination)
        destination_entry.grid(row=1, column=1, sticky="ew", padx=8)
        destination_button = ttk.Button(frame, text="另存為…", command=self._browse_destination)
        destination_button.grid(row=1, column=2)
        ttk.Label(frame, text="輸出編碼").grid(row=2, column=0, sticky="w", pady=5)
        encoding_box = ttk.Combobox(frame, textvariable=self.encoding, values=list(ENCODINGS), state="readonly")
        encoding_box.grid(row=2, column=1, sticky="w", padx=8)
        start_button = ttk.Button(frame, text="開始轉換", command=self._start)
        start_button.grid(row=3, column=1, sticky="w", padx=8, pady=9)
        self.progressbar = ttk.Progressbar(frame, mode="determinate")
        self.progressbar.grid(row=4, column=0, columnspan=3, sticky="ew", pady=5)
        ttk.Label(frame, textvariable=self.status, wraplength=550).grid(row=5, column=0, columnspan=3, sticky="w")
        self.controls = [source_entry, source_button, destination_entry, destination_button, encoding_box, start_button]

    def _browse_source(self) -> None:
        selected = filedialog.askopenfilename(parent=self.root, title="選取來源 PDF", filetypes=[("PDF 檔案", "*.pdf"), ("所有檔案", "*.*")])
        if selected:
            self.source.set(selected)
            if not self.destination.get().strip():
                self.destination.set(str(Path(selected).with_suffix(".txt")))

    def _browse_destination(self) -> None:
        selected = filedialog.asksaveasfilename(parent=self.root, title="指定目的 TXT", defaultextension=".txt", filetypes=[("文字檔", "*.txt")])
        if selected:
            self.destination.set(selected)

    def _start(self) -> None:
        source = Path(self.source.get().strip())
        destination = Path(self.destination.get().strip())
        encoding = ENCODINGS[self.encoding.get()]
        try:
            _validate_paths(source, destination, encoding)
            snapshot = capture_destination(destination)
        except ConversionError as exc:
            messagebox.showerror("無法轉換", str(exc), parent=self.root)
            return
        if snapshot is not None and not messagebox.askyesno("確認覆寫", f"目的檔已存在：\n{destination}\n\n確定覆寫嗎？", parent=self.root):
            self.status.set("已取消轉換，原檔未變更。")
            return
        self.busy = True
        self.progressbar.configure(value=0, maximum=1)
        self.status.set("準備轉換…")
        for control in self.controls:
            control.configure(state="disabled")
        thread = threading.Thread(target=self._worker, args=(source, destination, encoding, snapshot), daemon=False)
        thread.start()
        self.root.after(50, self._poll)

    def _worker(self, source: Path, destination: Path, encoding: str, snapshot: DestinationSnapshot | None) -> None:
        try:
            result = convert_pdf(source, destination, encoding, lambda done, total: self.events.put(("progress", done, total)), snapshot)
            self.events.put(("success", result, destination))
        except ConversionError as exc:
            self.events.put(("error", str(exc)))

    def _poll(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "progress":
                _, done, total = event
                self.progressbar.configure(maximum=total, value=done)
                self.status.set(f"處理第 {done} / {total} 頁…")
            elif event[0] == "success":
                _, result, destination = event
                warning = f"\n未擷取到文字的頁碼：{', '.join(map(str, result.empty_pages))}" if result.empty_pages else ""
                encoding_name = next(name for name, codec in ENCODINGS.items() if codec == result.encoding)
                text = f"轉換完成，共 {result.total_pages} 頁。\n輸出：{destination}\n輸出編碼：{encoding_name}{warning}"
                self._finish(text)
                messagebox.showinfo("轉換完成", text, parent=self.root)
            else:
                self._finish("轉換失敗。")
                messagebox.showerror("轉換失敗", event[1], parent=self.root)
        if self.busy:
            self.root.after(50, self._poll)

    def _finish(self, status: str) -> None:
        self.busy = False
        self.status.set(status)
        for control in self.controls:
            control.configure(state="readonly" if isinstance(control, ttk.Combobox) else "normal")

    def _on_close(self) -> None:
        if self.busy:
            messagebox.showinfo("轉換進行中", "請等待轉換完成後再關閉視窗。", parent=self.root)
        else:
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    icon_path = Path(__file__).resolve().parent / "assets" / "shortcut_icon.ico"
    if icon_path.is_file():
        try:
            root.iconbitmap(str(icon_path))
        except tk.TclError:
            pass
    PdfToTxtApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
