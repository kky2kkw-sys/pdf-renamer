"""
PDF Renamer v2 — DOI 기반 논문 파일 자동 정리 도구
Features: Undo, Duplicate Detection, Manual DOI, Drag&Drop, Config
"""

import os
import re
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import threading
import requests
import fitz  # PyMuPDF
from datetime import datetime

# ─── Try optional drag-and-drop support ──────────────────────────────────────
try:
    import windnd
    HAS_DND = True
except ImportError:
    HAS_DND = False

# ─── Configuration ───────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "pattern": "{year}_{author}_{journal}_{title}",
    "title_words": 5,
    "max_filename_length": 100,
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_renamer_config.json")
HISTORY_FILENAME = ".pdf_renamer_history.json"

CROSSREF_API = "https://api.crossref.org/works/{doi}"
CROSSREF_HEADERS = {
    "User-Agent": "PDFRenamer/2.0 (mailto:pdf-renamer@example.com)"
}

STOP_WORDS = {
    "a", "an", "the", "of", "in", "for", "and", "or", "to", "with",
    "on", "at", "by", "from", "is", "are", "was", "were", "be", "been",
    "its", "their", "this", "that", "these", "those", "as", "into",
    "using", "via", "between", "among", "through", "during", "after",
    "before", "about", "than", "but", "not", "no", "vs", "versus",
}


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ─── History (Undo) ──────────────────────────────────────────────────────────

def load_history(folder):
    path = os.path.join(folder, HISTORY_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_history(folder, history):
    path = os.path.join(folder, HISTORY_FILENAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ─── Metadata Extraction ────────────────────────────────────────────────────

def extract_text_from_pdf(filepath, max_pages=2):
    try:
        doc = fitz.open(filepath)
        text = ""
        for i in range(min(max_pages, len(doc))):
            text += doc[i].get_text()
        doc.close()
        return text
    except Exception:
        return ""


def find_doi(text):
    patterns = [
        r'(?:doi[:\s]*|https?://doi\.org/|https?://dx\.doi\.org/)(10\.\d{4,9}/[^\s,;}\]\"\']+)',
        r'(10\.\d{4,9}/[^\s,;}\]\"\']+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            doi = match.group(1).rstrip('.')
            # PDF text extraction artifact cleanup (e.g. "10.1111/xxx.70253WILEYlogo")
            doi = re.sub(r'[A-Za-z]{3,}$', '', doi).rstrip('.')
            if doi:
                return doi
    return None


def query_crossref(doi):
    url = CROSSREF_API.format(doi=doi)
    try:
        resp = requests.get(url, headers=CROSSREF_HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("message", {})
    except Exception:
        pass
    return None


def abbreviate_journal(full_name, short_name=None):
    if short_name:
        return re.sub(r'\.', '', short_name).strip()
    if not full_name:
        return "Unknown"
    return full_name


def make_short_title(title, max_words=5):
    if not title:
        return "Untitled"
    title = re.sub(r'<[^>]+>', '', title)
    title = re.sub(r'[^\w\s-]', '', title)
    words = title.split()
    keywords = [w for w in words if w.lower() not in STOP_WORDS]
    if not keywords:
        keywords = words[:max_words]
    else:
        keywords = keywords[:max_words]
    return " ".join(w.capitalize() for w in keywords)


def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'_+', '_', name)
    name = re.sub(r' +', ' ', name)
    name = name.strip('_ ')
    return name


def build_filename(metadata, config):
    # Year
    year = "Unknown"
    pub_date = metadata.get("published-print") or metadata.get("published-online") or metadata.get("published")
    if pub_date and "date-parts" in pub_date:
        parts = pub_date["date-parts"]
        if parts and parts[0] and parts[0][0]:
            year = str(parts[0][0])

    # Journal
    short_titles = metadata.get("short-container-title", [])
    full_titles = metadata.get("container-title", [])
    short_name = short_titles[0] if short_titles else None
    full_name = full_titles[0] if full_titles else None
    journal = abbreviate_journal(full_name, short_name)

    # Author
    authors = metadata.get("author", [])
    author = authors[0].get("family", "Unknown") if authors else "Unknown"

    # Title
    titles = metadata.get("title", [])
    raw_title = titles[0] if titles else ""
    title = make_short_title(raw_title, config.get("title_words", 5))

    # Build from pattern
    pattern = config.get("pattern", DEFAULT_CONFIG["pattern"])
    filename = pattern.format(year=year, author=author, journal=journal, title=title)
    filename = sanitize_filename(filename) + ".pdf"

    # Length limit
    max_len = config.get("max_filename_length", 100)
    if len(filename) > max_len:
        ext = ".pdf"
        filename = filename[:max_len - len(ext)].rstrip('_ ') + ext

    return filename


# ─── Processing ──────────────────────────────────────────────────────────────

class PDFEntry:
    def __init__(self, filepath):
        self.filepath = filepath
        self.original_name = os.path.basename(filepath)
        self.doi = None
        self.metadata = None
        self.new_name = None
        self.status = "대기"

    def process(self, config):
        self.status = "처리중"
        text = extract_text_from_pdf(self.filepath)
        if not text:
            self.status = "텍스트 추출 실패"
            return

        self.doi = find_doi(text)
        if not self.doi:
            self.status = "DOI 없음"
            return

        self._fetch_and_build(config)

    def process_with_doi(self, doi, config):
        self.doi = doi
        self._fetch_and_build(config)

    def _fetch_and_build(self, config):
        self.metadata = query_crossref(self.doi)
        if not self.metadata:
            self.status = "CrossRef 조회 실패"
            return
        self.new_name = build_filename(self.metadata, config)
        self.status = "완료"


# ─── GUI ─────────────────────────────────────────────────────────────────────

class PDFRenamerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF Renamer v2")
        self.root.geometry("1100x650")
        self.root.minsize(900, 450)

        self.config = load_config()
        self.entries = []
        self.folder_path = tk.StringVar()

        self._build_ui()
        self._setup_dnd()

    # ── UI Construction ──

    def _build_ui(self):
        # Menu bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="폴더 열기", command=self._select_folder)
        file_menu.add_separator()
        file_menu.add_command(label="되돌리기 (Undo)", command=self._undo)
        menubar.add_cascade(label="파일", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="파일명 패턴 설정", command=self._open_settings)
        menubar.add_cascade(label="설정", menu=settings_menu)

        # Top bar
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="폴더:").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.folder_path, width=60).pack(side=tk.LEFT, padx=(5, 5))
        ttk.Button(top, text="찾아보기", command=self._select_folder).pack(side=tk.LEFT)
        ttk.Button(top, text="스캔", command=self._scan).pack(side=tk.LEFT, padx=(10, 0))

        # Table
        cols = ("original", "doi", "status", "new_name")
        self.tree = ttk.Treeview(self.root, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("original", text="원본 파일명")
        self.tree.heading("doi", text="DOI")
        self.tree.heading("status", text="상태")
        self.tree.heading("new_name", text="새 파일명")

        self.tree.column("original", width=280, minwidth=150)
        self.tree.column("doi", width=200, minwidth=100)
        self.tree.column("status", width=110, minwidth=70, anchor=tk.CENTER)
        self.tree.column("new_name", width=400, minwidth=200)

        scrollbar_y = ttk.Scrollbar(self.root, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar_x = ttk.Scrollbar(self.root, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)

        self.tree.pack(fill=tk.BOTH, expand=True, padx=10)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)

        # Context menu (right-click)
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="DOI 수동 입력", command=self._manual_doi)
        self.context_menu.add_command(label="새 파일명 편집", command=self._edit_name)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Double-1>", self._on_double_click)

        # Bottom bar
        bottom = ttk.Frame(self.root, padding=10)
        bottom.pack(fill=tk.X)

        self.status_label = ttk.Label(bottom, text="폴더를 선택하거나 파일을 드래그하세요.")
        self.status_label.pack(side=tk.LEFT)

        ttk.Button(bottom, text="이름 변경 실행", command=self._rename).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="되돌리기 (Undo)", command=self._undo).pack(side=tk.RIGHT, padx=(0, 5))
        ttk.Button(bottom, text="메타데이터 조회", command=self._process).pack(side=tk.RIGHT, padx=(0, 5))

    def _setup_dnd(self):
        if HAS_DND:
            windnd.hook_dropfiles(self.root, func=self._on_drop)
            self.status_label.config(text="폴더를 선택하거나 파일/폴더를 드래그하세요.")

    # ── Drag & Drop ──

    def _on_drop(self, paths):
        if not paths:
            return
        # windnd returns list of bytes on Windows
        decoded = []
        for p in paths:
            if isinstance(p, bytes):
                try:
                    decoded.append(p.decode('utf-8'))
                except UnicodeDecodeError:
                    decoded.append(p.decode('gbk', errors='replace'))
            else:
                decoded.append(str(p))

        first = decoded[0]
        if os.path.isdir(first):
            self.folder_path.set(first)
            self._scan()
        elif first.lower().endswith('.pdf'):
            folder = os.path.dirname(first)
            self.folder_path.set(folder)
            self._scan_files([os.path.join(folder, os.path.basename(p)) for p in decoded if str(p).lower().endswith('.pdf')])

    # ── Folder & Scan ──

    def _select_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.folder_path.set(path)
            self._scan()

    def _scan(self):
        folder = self.folder_path.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("경고", "유효한 폴더를 선택하세요.")
            return

        pdf_files = sorted([
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith('.pdf')
        ])
        self._scan_files(pdf_files)

    def _scan_files(self, file_list):
        self.entries.clear()
        self.tree.delete(*self.tree.get_children())

        for fp in file_list:
            if os.path.isfile(fp) and fp.lower().endswith('.pdf'):
                entry = PDFEntry(fp)
                self.entries.append(entry)
                self.tree.insert("", tk.END, values=(entry.original_name, "", entry.status, ""))

        self.status_label.config(text=f"{len(self.entries)}개 PDF 파일 발견.")

    # ── Metadata Processing ──

    def _process(self):
        if not self.entries:
            messagebox.showwarning("경고", "먼저 폴더를 스캔하세요.")
            return

        self.status_label.config(text="메타데이터 조회 중...")

        def worker():
            for i, entry in enumerate(self.entries):
                if entry.status not in ("완료", "이름변경완료"):
                    entry.process(self.config)
                self.root.after(0, self._update_row, i)
            self.root.after(0, self._process_done)

        threading.Thread(target=worker, daemon=True).start()

    def _process_done(self):
        success = sum(1 for e in self.entries if e.status == "완료")
        fail = len(self.entries) - success

        # Duplicate DOI detection
        doi_map = {}
        dup_count = 0
        for i, entry in enumerate(self.entries):
            if entry.doi:
                if entry.doi in doi_map:
                    dup_count += 1
                    entry.status = f"중복 DOI (#{doi_map[entry.doi]+1}과 동일)"
                    self._update_row(i)
                else:
                    doi_map[entry.doi] = i

        msg = f"완료: {success}개 성공, {fail}개 실패/수동 필요."
        if dup_count:
            msg += f" (중복 DOI {dup_count}건 감지)"
        self.status_label.config(text=msg)

    def _update_row(self, index):
        entry = self.entries[index]
        item = self.tree.get_children()[index]
        self.tree.item(item, values=(
            entry.original_name,
            entry.doi or "",
            entry.status,
            entry.new_name or ""
        ))

    # ── Manual DOI Input ──

    def _manual_doi(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("안내", "파일을 선택하세요.")
            return

        idx = self.tree.index(selected[0])
        entry = self.entries[idx]

        doi = simpledialog.askstring(
            "DOI 수동 입력",
            f"파일: {entry.original_name}\n\nDOI를 입력하세요 (예: 10.1016/j.jhep.2025.01.023):",
            parent=self.root
        )

        if doi and doi.strip():
            doi = doi.strip()
            if not doi.startswith("10."):
                # Try to extract DOI from pasted URL
                m = re.search(r'(10\.\d{4,9}/[^\s]+)', doi)
                if m:
                    doi = m.group(1)
                else:
                    messagebox.showwarning("경고", "유효한 DOI 형식이 아닙니다.")
                    return

            self.status_label.config(text=f"DOI {doi} 조회 중...")

            def worker():
                entry.process_with_doi(doi, self.config)
                self.root.after(0, self._update_row, idx)
                self.root.after(0, lambda: self.status_label.config(
                    text=f"DOI 조회 완료: {entry.status}"))

            threading.Thread(target=worker, daemon=True).start()

    # ── Context Menu & Editing ──

    def _show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def _edit_name(self):
        selected = self.tree.selection()
        if not selected:
            return
        idx = self.tree.index(selected[0])
        entry = self.entries[idx]
        current = entry.new_name or ""

        new_val = simpledialog.askstring(
            "파일명 편집",
            f"원본: {entry.original_name}\n\n새 파일명:",
            initialvalue=current,
            parent=self.root
        )

        if new_val and new_val.strip():
            new_val = new_val.strip()
            if not new_val.endswith('.pdf'):
                new_val += '.pdf'
            entry.new_name = new_val
            entry.status = "완료"
            self._update_row(idx)

    def _on_double_click(self, event):
        col = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item:
            return

        idx = self.tree.index(item)

        if col == "#4":  # new_name column
            self._inline_edit(item, idx, col)
        elif col == "#2":  # DOI column — trigger manual DOI
            self.tree.selection_set(item)
            self._manual_doi()

    def _inline_edit(self, item, idx, col):
        bbox = self.tree.bbox(item, col)
        if not bbox:
            return

        current = self.entries[idx].new_name or ""
        entry_widget = ttk.Entry(self.tree)
        entry_widget.insert(0, current)
        entry_widget.select_range(0, tk.END)
        entry_widget.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
        entry_widget.focus_set()

        def save_edit(event=None):
            new_val = entry_widget.get().strip()
            if new_val:
                if not new_val.endswith('.pdf'):
                    new_val += '.pdf'
                self.entries[idx].new_name = new_val
                self.entries[idx].status = "완료"
                self._update_row(idx)
            entry_widget.destroy()

        entry_widget.bind("<Return>", save_edit)
        entry_widget.bind("<Escape>", lambda e: entry_widget.destroy())
        entry_widget.bind("<FocusOut>", save_edit)

    # ── Rename ──

    def _rename(self):
        to_rename = [e for e in self.entries if e.new_name and e.status == "완료"]

        if not to_rename:
            messagebox.showinfo("안내", "이름을 변경할 파일이 없습니다.")
            return

        msg = f"{len(to_rename)}개 파일의 이름을 변경합니다. 계속할까요?"
        if not messagebox.askyesno("확인", msg):
            return

        folder = self.folder_path.get()
        history = load_history(folder)
        batch = {
            "timestamp": datetime.now().isoformat(),
            "renames": []
        }

        renamed = 0
        errors = []

        for entry in to_rename:
            new_path = os.path.join(os.path.dirname(entry.filepath), entry.new_name)

            # Duplicate filename handling
            if os.path.exists(new_path) and new_path != entry.filepath:
                base, ext = os.path.splitext(entry.new_name)
                counter = 2
                while os.path.exists(new_path):
                    new_path = os.path.join(
                        os.path.dirname(entry.filepath),
                        f"{base}_{counter}{ext}"
                    )
                    counter += 1
                entry.new_name = os.path.basename(new_path)

            try:
                old_path = entry.filepath
                os.rename(old_path, new_path)
                batch["renames"].append({
                    "old": os.path.basename(old_path),
                    "new": entry.new_name
                })
                entry.filepath = new_path
                entry.status = "이름변경완료"
                renamed += 1
            except Exception as e:
                entry.status = f"오류: {e}"
                errors.append(entry.original_name)

        if batch["renames"]:
            history.append(batch)
            save_history(folder, history)

        for i in range(len(self.entries)):
            self._update_row(i)

        if errors:
            messagebox.showwarning("경고", f"{renamed}개 변경 완료, {len(errors)}개 오류 발생.")
        else:
            self.status_label.config(text=f"{renamed}개 파일 이름 변경 완료. (Undo 가능)")

    # ── Undo ──

    def _undo(self):
        folder = self.folder_path.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("경고", "먼저 폴더를 선택하세요.")
            return

        history = load_history(folder)
        if not history:
            messagebox.showinfo("안내", "되돌릴 변경 내역이 없습니다.")
            return

        last_batch = history[-1]
        renames = last_batch.get("renames", [])
        ts = last_batch.get("timestamp", "")

        msg = f"최근 변경 ({ts}):\n{len(renames)}개 파일을 원래 이름으로 복원할까요?"
        if not messagebox.askyesno("Undo 확인", msg):
            return

        restored = 0
        errors = []

        for item in renames:
            old_name = item["old"]
            new_name = item["new"]
            current_path = os.path.join(folder, new_name)
            restore_path = os.path.join(folder, old_name)

            if not os.path.exists(current_path):
                errors.append(f"{new_name} (파일 없음)")
                continue

            try:
                os.rename(current_path, restore_path)
                restored += 1
            except Exception as e:
                errors.append(f"{new_name}: {e}")

        # Remove last batch from history
        history.pop()
        save_history(folder, history)

        if errors:
            messagebox.showwarning("Undo 결과",
                f"{restored}개 복원 완료.\n오류:\n" + "\n".join(errors))
        else:
            messagebox.showinfo("Undo 완료", f"{restored}개 파일을 원래 이름으로 복원했습니다.")

        # Refresh
        self._scan()

    # ── Settings Dialog ──

    def _open_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("파일명 패턴 설정")
        dlg.geometry("520x350")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        frame = ttk.Frame(dlg, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        # Pattern
        ttk.Label(frame, text="파일명 패턴:").grid(row=0, column=0, sticky=tk.W, pady=5)
        pattern_var = tk.StringVar(value=self.config.get("pattern", DEFAULT_CONFIG["pattern"]))
        ttk.Entry(frame, textvariable=pattern_var, width=45).grid(row=0, column=1, pady=5, padx=(10,0))

        ttk.Label(frame, text="사용 가능한 변수: {year}, {author}, {journal}, {title}",
                  foreground="gray").grid(row=1, column=0, columnspan=2, sticky=tk.W)

        # Examples
        examples = [
            "{year}_{author}_{journal}_{title}",
            "{year}_{journal}_{author}_{title}",
            "{author}_{year}_{journal}_{title}",
            "{author}_{year}_{title}",
        ]
        ttk.Label(frame, text="예시 패턴:").grid(row=2, column=0, sticky=tk.W, pady=(15,5))
        for i, ex in enumerate(examples):
            btn = ttk.Button(frame, text=ex,
                           command=lambda e=ex: pattern_var.set(e))
            btn.grid(row=3+i, column=0, columnspan=2, sticky=tk.W, padx=20)

        # Title words
        ttk.Label(frame, text="제목 단어 수:").grid(row=7, column=0, sticky=tk.W, pady=(15,5))
        words_var = tk.IntVar(value=self.config.get("title_words", 5))
        words_spin = ttk.Spinbox(frame, from_=2, to=10, textvariable=words_var, width=5)
        words_spin.grid(row=7, column=1, sticky=tk.W, padx=(10,0), pady=(15,5))

        # Max length
        ttk.Label(frame, text="최대 파일명 길이:").grid(row=8, column=0, sticky=tk.W, pady=5)
        len_var = tk.IntVar(value=self.config.get("max_filename_length", 100))
        len_spin = ttk.Spinbox(frame, from_=60, to=200, textvariable=len_var, width=5)
        len_spin.grid(row=8, column=1, sticky=tk.W, padx=(10,0), pady=5)

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=9, column=0, columnspan=2, pady=(20,0))

        def save_and_close():
            self.config["pattern"] = pattern_var.get()
            self.config["title_words"] = words_var.get()
            self.config["max_filename_length"] = len_var.get()
            save_config(self.config)
            dlg.destroy()
            self.status_label.config(text="설정이 저장되었습니다.")

        ttk.Button(btn_frame, text="저장", command=save_and_close).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="취소", command=dlg.destroy).pack(side=tk.LEFT, padx=5)


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app = PDFRenamerApp(root)
    root.mainloop()
