import io
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pymupdf
import tkinter as tk

from pdf_to_txt import ConversionError, PdfToTxtApp, capture_destination, cli_main, convert_pdf


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "繁體 文件.pdf"
        self.destination = self.folder / "輸出 文件.txt"

    def make_pdf(self, pages):
        document = pymupdf.open()
        for text in pages:
            page = document.new_page()
            if text:
                page.insert_htmlbox(page.rect, f"<p>{text}</p>")
        document.save(self.source)
        document.close()

    def test_cli_converts_pdf_and_reports_result(self):
        self.make_pdf(["第一頁", "第二頁"])
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = cli_main([str(self.source), str(self.destination)])

        self.assertEqual(exit_code, 0)
        self.assertIn("轉換完成，共 2 頁", output.getvalue())
        self.assertIn(str(self.destination), output.getvalue())
        self.assertIn("第一頁", self.destination.read_bytes().decode("cp950"))

    def test_cli_rejects_invalid_argument_count(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli_main([str(self.source)])

        self.assertEqual(raised.exception.code, 2)

    def test_cli_requires_confirmation_before_overwriting(self):
        self.make_pdf(["新內容"])
        self.destination.write_bytes(b"old")
        output = io.StringIO()
        errors = io.StringIO()

        with patch("builtins.input", return_value="n"), redirect_stdout(output), redirect_stderr(errors):
            exit_code = cli_main([str(self.source), str(self.destination)])

        self.assertEqual(exit_code, 1)
        self.assertEqual(self.destination.read_bytes(), b"old")
        self.assertIn("已取消", errors.getvalue())

    def test_multipage_chinese_crlf_and_blank_page_warning(self):
        self.make_pdf(["繁體中文 123", "", "第二頁 英文 ABC"])
        progress = []
        result = convert_pdf(self.source, self.destination, "cp950", lambda done, total: progress.append((done, total)))
        content = self.destination.read_bytes().decode("cp950")
        self.assertEqual(result.total_pages, 3)
        self.assertEqual(result.empty_pages, (2,))
        self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])
        self.assertIn("繁體中文 123", content)
        self.assertIn("第二頁 英文 ABC", content)
        self.assertIn("\r\n\r\n", content)
        self.assertNotIn("\r\r\n", content)

    def test_all_encoding_bytes_and_bom(self):
        self.make_pdf(["繁體中文 ABC", "第二頁"])
        cases = {
            "cp950": (b"", "cp950"),
            "utf-8": (b"", "utf-8"),
            "utf-8-sig": (b"\xef\xbb\xbf", "utf-8-sig"),
            "big5": (b"", "big5"),
            "utf-16-le": (b"\xff\xfe", "utf-16"),
        }
        for codec, (bom, reader) in cases.items():
            with self.subTest(codec=codec):
                target = self.folder / f"{codec}.txt"
                convert_pdf(self.source, target, codec, lambda *_: None)
                data = target.read_bytes()
                self.assertTrue(data.startswith(bom))
                self.assertIn("繁體中文 ABC", data.decode(reader))
                self.assertIn("第二頁", data.decode(reader))
                if bom:
                    self.assertEqual(data.count(bom), 1)
                if codec in ("cp950", "utf-8", "big5"):
                    self.assertFalse(data.startswith((b"\xef\xbb\xbf", b"\xff\xfe")))

    def test_auto_uses_cp950_when_all_text_is_representable(self):
        self.make_pdf(["繁體中文 ABC", "第二頁 123"])
        result = convert_pdf(self.source, self.destination, "auto", lambda *_: None)
        self.assertEqual(result.encoding, "cp950")
        data = self.destination.read_bytes()
        self.assertIn("繁體中文 ABC", data.decode("cp950"))
        self.assertIn("第二頁 123", data.decode("cp950"))
        self.assertIn(b"\r\n\r\n", data)
        self.assertNotIn(b"\xef\xbb\xbf", data)

    def test_auto_falls_back_to_utf8_for_unrepresentable_later_page(self):
        self.make_pdf(["繁體中文", "第二頁"])
        original = pymupdf.Page.get_text

        def substitute(page, *args, **kwargs):
            if page.number == 1:
                return "表情 😀"
            return original(page, *args, **kwargs)

        with patch.object(pymupdf.Page, "get_text", substitute):
            result = convert_pdf(self.source, self.destination, "auto", lambda *_: None)
        self.assertEqual(result.encoding, "utf-8")
        data = self.destination.read_bytes()
        self.assertIn("繁體中文", data.decode("utf-8"))
        self.assertIn("表情 😀", data.decode("utf-8"))
        self.assertIn(b"\r\n\r\n", data)
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))

    def test_unencodable_character_keeps_existing_file(self):
        self.make_pdf(["安全內容", "第二頁"])
        self.destination.write_bytes(b"old")
        old = capture_destination(self.destination)
        original = pymupdf.Page.get_text

        def substitute(page, *args, **kwargs):
            if page.number == 1:
                return "無法編碼 😀"
            return original(page, *args, **kwargs)

        with patch.object(pymupdf.Page, "get_text", substitute):
            with self.assertRaises(ConversionError) as raised:
                convert_pdf(self.source, self.destination, "cp950", lambda *_: None, old)
        self.assertIn("😀", str(raised.exception))
        self.assertIn("U+1F600", str(raised.exception))
        self.assertIn("第 2 頁", str(raised.exception))
        self.assertEqual(self.destination.read_bytes(), b"old")
        self.assertEqual(list(self.folder.glob("*.tmp")), [])

    def test_all_blank_pages_do_not_replace_existing_file(self):
        self.make_pdf(["", ""])
        self.destination.write_bytes(b"old")
        with self.assertRaisesRegex(ConversionError, "OCR"):
            convert_pdf(self.source, self.destination, "utf-8", lambda *_: None, capture_destination(self.destination))
        self.assertEqual(self.destination.read_bytes(), b"old")

    def test_existing_file_requires_confirmed_snapshot(self):
        self.make_pdf(["內容"])
        self.destination.write_bytes(b"old")
        with self.assertRaises(ConversionError):
            convert_pdf(self.source, self.destination, "utf-8", lambda *_: None)
        self.assertEqual(self.destination.read_bytes(), b"old")
        convert_pdf(self.source, self.destination, "utf-8", lambda *_: None, capture_destination(self.destination))
        self.assertIn("內容", self.destination.read_text(encoding="utf-8"))

    def test_destination_change_during_conversion_aborts(self):
        self.make_pdf(["內容"])
        self.destination.write_bytes(b"old")
        old = capture_destination(self.destination)

        def change_destination(*_):
            self.destination.write_bytes(b"changed")

        with self.assertRaisesRegex(ConversionError, "變更"):
            convert_pdf(self.source, self.destination, "utf-8", change_destination, old)
        self.assertEqual(self.destination.read_bytes(), b"changed")

    def test_invalid_paths_and_bad_pdf_are_reported(self):
        with self.assertRaises(ConversionError):
            convert_pdf(self.source, self.destination, "utf-8", lambda *_: None)
        self.source.write_bytes(b"not a pdf")
        with self.assertRaises(ConversionError):
            convert_pdf(self.source, self.destination, "utf-8", lambda *_: None)
        self.make_pdf(["內容"])
        with self.assertRaises(ConversionError):
            convert_pdf(self.source, self.folder / "missing" / "out.txt", "utf-8", lambda *_: None)
        with self.assertRaises(ConversionError):
            convert_pdf(self.source, self.folder / "out.doc", "utf-8", lambda *_: None)

    def test_password_protected_pdf_reports_decrypted_copy(self):
        document = pymupdf.open()
        document.new_page().insert_text((50, 50), "Secret")
        document.save(self.source, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
        document.close()
        with self.assertRaisesRegex(ConversionError, "已解密副本"):
            convert_pdf(self.source, self.destination, "utf-8", lambda *_: None)
        self.assertFalse(self.destination.exists())

    def test_replace_failure_preserves_original_and_cleans_temp(self):
        self.make_pdf(["內容"])
        self.destination.write_bytes(b"old")
        with patch("pdf_to_txt.os.replace", side_effect=PermissionError("file locked")):
            with self.assertRaisesRegex(ConversionError, "占用"):
                convert_pdf(self.source, self.destination, "utf-8", lambda *_: None, capture_destination(self.destination))
        self.assertEqual(self.destination.read_bytes(), b"old")
        self.assertEqual(list(self.folder.glob("*.tmp")), [])


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = PdfToTxtApp(self.root)
        self.source = Path(self.temp.name) / "中文.pdf"
        self.destination = self.source.with_suffix(".txt")

    def test_defaults_browse_and_background_progress(self):
        self.assertEqual(self.app.encoding.get(), "自動（優先 CP950，必要時 UTF-8）")
        document = pymupdf.open()
        for text in ("繁體中文", "第二頁"):
            page = document.new_page()
            page.insert_htmlbox(page.rect, f"<p>{text}</p>")
        document.save(self.source)
        document.close()
        with patch("pdf_to_txt.filedialog.askopenfilename", return_value=str(self.source)):
            self.app._browse_source()
        self.assertEqual(self.app.destination.get(), str(self.destination))
        with patch("pdf_to_txt.messagebox.showinfo") as info:
            self.app._start()
            self.assertTrue(self.app.busy)
            self.assertEqual(str(self.app.controls[0]["state"]), "disabled")
            self.app._on_close()
            self.assertTrue(info.called)
            deadline = time.monotonic() + 5
            while self.app.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.02)
            self.assertFalse(self.app.busy)
            self.assertEqual(self.app.progressbar["value"], 2)
            self.assertIn("共 2 頁", self.app.status.get())
            self.assertIn("CP950", self.app.status.get())
            self.assertIn("繁體中文", self.destination.read_bytes().decode("cp950"))
            self.assertEqual(str(self.app.controls[0]["state"]), "normal")

    def test_reject_overwrite_leaves_existing_file_untouched(self):
        document = pymupdf.open()
        document.new_page().insert_text((50, 50), "new")
        document.save(self.source)
        document.close()
        self.destination.write_bytes(b"old")
        self.app.source.set(str(self.source))
        self.app.destination.set(str(self.destination))
        with patch("pdf_to_txt.messagebox.askyesno", return_value=False) as confirmation:
            self.app._start()
        confirmation.assert_called_once()
        self.assertFalse(self.app.busy)
        self.assertEqual(self.destination.read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
