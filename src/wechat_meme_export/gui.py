import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import os
import sys
import time
import csv
from pathlib import Path


def enable_windows_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


UI_FONT = ("Microsoft YaHei UI", 10)
UI_SMALL_FONT = ("Microsoft YaHei UI", 8)
UI_TITLE_FONT = ("Microsoft YaHei UI", 15, "bold")
UI_SUCCESS_FONT = ("Microsoft YaHei UI", 12, "bold")
MONOSPACE_FONT = ("Consolas", 10)

from .decrypt import decrypt_database
from .discovery import discover_emoticon_databases
from .exporter import export_custom_stickers, write_index
from .memory import scan_database_keys
from .stickers import load_stickers

# 检测 Pillow 是否安装
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def load_thumbnail(image_path, size=(85, 85)):
    """加载图片并转换为缩略图格式用于 Tkinter 预览"""
    if not HAS_PIL:
        return None
    try:
        img = Image.open(image_path)
        img.thumbnail(size)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


def export_worker(db_path, output_root, key_hex, progress_cb, status_cb):
    status_cb("正在准备解密数据库...")
    decrypted_db = output_root / "emoticon.decrypted.db"
    try:
        decrypt_database(db_path, decrypted_db, key_hex)
    except Exception as e:
        status_cb(f"数据库解密失败: {e}")
        return False, f"解密失败: {e}"

    try:
        status_cb("正在解析表情数据列表...")

        def on_progress(index, total, md5):
            status_cb(f"正在下载并导出 ({index}/{total}): {md5[:8]}...")
            progress_cb(int(index / total * 100) if total else 0)

        rows = export_custom_stickers(decrypted_db, output_root, delay=0.01, progress_callback=on_progress)
        write_index(output_root, rows)
        exported_count = len([r for r in rows if r["status"] == "exported"])
        if not rows:
            return True, "数据库内未发现自定义表情数据。"
        return True, f"成功导出 {exported_count} 个表情。"
    except Exception as e:
        return False, f"导出失败: {e}"


class ScrollableFrame(ttk.Frame):
    """带滚轮绑定、支持响应式布局的滚动容器"""
    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        # 视口宽度自动拉伸
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        # 全局滚轮绑定
        self.scrollable_frame.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def unbind_scroll(self):
        self.scrollable_frame.unbind_all("<MouseWheel>")

class StickerSelectionWindow(tk.Toplevel):
    """导出结果筛选界面（支持流式响应式拉伸布局）"""
    def __init__(self, parent, output_dir, csv_index_path):
        super().__init__(parent)
        self.parent = parent
        self.title("选择要保存的微信表情")
        
        # 使用合理的基础尺寸，由系统 DPI 引擎自动缩放，避免双重缩放
        self.geometry("920x680")
        self.minsize(700, 500)
        
        self.output_dir = Path(output_dir)
        self.images_dir = self.output_dir / "images"
        self.csv_index_path = Path(csv_index_path)
        
        self.items = load_stickers(self.images_dir, self.csv_index_path)
        self.checkbox_vars = {item.number: tk.BooleanVar(value=True) for item in self.items}
        for var in self.checkbox_vars.values():
            var.trace_add("write", lambda *args: self.update_info())
        
        self.thumbnail_refs = {}
        self.btn_apply_filter = None
        self.btn_keep_all = None
        self.page_size = 60
        self.current_page = 0
        self.page_label = None
        self.prev_page_btn = None
        self.next_page_btn = None
        
        # 默认初始化列数
        self.current_cols = 5

        if not self.items:
            messagebox.showwarning("提示", "未找到表情图片。")
            self.destroy()
            return

        self.build_ui()
        
        # 绑定窗口大小改变事件，用于实现流式响应式网格
        self.bind("<Configure>", self.on_window_resize)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self):
        # 顶部操作面板
        top_frame = ttk.Frame(self, padding=10)
        top_frame.pack(side="top", fill="x")

        left_bar = ttk.Frame(top_frame)
        left_bar.pack(side="left", fill="x", expand=True)

        right_bar = ttk.Frame(top_frame)
        right_bar.pack(side="right")

        ttk.Button(left_bar, text="全选", command=self.select_all).pack(side="left", padx=5)
        ttk.Button(left_bar, text="全不选", command=self.deselect_all).pack(side="left", padx=5)
        ttk.Button(left_bar, text="反选", command=self.invert_selection).pack(side="left", padx=5)

        self.info_lbl = ttk.Label(left_bar, text="已选择: 0 / 0", font=UI_FONT)
        self.info_lbl.pack(side="left", padx=15)

        self.btn_apply_filter = ttk.Button(right_bar, text="仅保存勾选", command=self.apply_filter)
        self.btn_apply_filter.pack(side="left", padx=5)

        self.btn_keep_all = ttk.Button(right_bar, text="全部保留", command=self.keep_all)
        self.btn_keep_all.pack(side="left", padx=5)

        # 提示区域
        hint_frame = ttk.Frame(self, padding=(10, 0, 10, 6))
        hint_frame.pack(side="top", fill="x")
        ttk.Label(
            hint_frame,
            text="默认全部勾选，你可以通过取消勾选来筛选表情，未勾选的将不会被保存。",
            foreground="gray",
            font=UI_SMALL_FONT
        ).pack(side="left")

        # 分页区域
        page_frame = ttk.Frame(self, padding=(10, 0, 10, 4))
        page_frame.pack(side="top", fill="x")
        self.prev_page_btn = ttk.Button(page_frame, text="上一页", command=self.prev_page)
        self.prev_page_btn.pack(side="left", padx=(0, 6))
        self.next_page_btn = ttk.Button(page_frame, text="下一页", command=self.next_page)
        self.next_page_btn.pack(side="left")
        self.page_label = ttk.Label(page_frame, text="", foreground="gray", font=UI_FONT)
        self.page_label.pack(side="left", padx=12)

        if not HAS_PIL:
            warn_lbl = ttk.Label(self, text="⚠️ 未安装 Pillow 模块，无法预览图片内容。显示列表为占位信息。", foreground="red", font=UI_FONT)
            warn_lbl.pack(side="top", fill="x", padx=15, pady=2)

        # 滚动区域
        self.scroll_frame = ScrollableFrame(self)
        self.scroll_frame.pack(side="top", fill="both", expand=True, padx=10, pady=5)
        
        self.populate_grid()
        self.update_info()

    def on_window_resize(self, event):
        """窗口大小改变时，动态计算并调整表情网格列数"""
        # 仅响应主窗口的尺寸改变，忽略内部子组件触发的事件
        if event.widget == self:
            # 直接获取内部 Canvas 视口的实际渲染宽度，计算更精准
            canvas_width = self.scroll_frame.canvas.winfo_width()
            
            # 若画布尚未完成首次渲染（winfo_width() 返回 1），则使用窗口宽度扣除边隙进行估算
            if canvas_width <= 1:
                canvas_width = event.width - 60

            # 获取系统当前缩放倍率
            try:
                scaling = self.tk.call('tk', 'scaling')
            except Exception:
                scaling = 1.33333
            scale_factor = scaling / 1.33333

            # 采用安全、保守的卡片物理宽度估算基准（预留了高 DPI 下边隙放大的安全缓冲空间）
            card_width = int(122 + 6 * scale_factor)
            
            # 动态计算当前宽度下最适合容纳的列数
            new_cols = max(3, canvas_width // card_width)
            
            # 只有当列数确实发生改变时才触发重绘，防止拖拽窗口时高频重绘卡顿
            if not hasattr(self, 'current_cols') or self.current_cols != new_cols:
                self.current_cols = new_cols
                self.populate_grid()

    def populate_grid(self):
        frame = self.scroll_frame.scrollable_frame
        for child in frame.winfo_children():
            child.destroy()
        self.thumbnail_refs = {}

        # 使用动态计算得到的列数
        cols = getattr(self, 'current_cols', 5)
        
        # 1. 清空先前的网格权重配置
        for col_idx in range(15):
            frame.grid_columnconfigure(col_idx, weight=0)
            
        # 2. 重新配置当前所有列的权重为 1。
        for col_idx in range(cols):
            frame.grid_columnconfigure(col_idx, weight=1)

        start = self.current_page * self.page_size
        end = min(start + self.page_size, len(self.items))
        page_items = self.items[start:end]

        for i, item in enumerate(page_items):
            row = i // cols
            col = i % cols

            # 单个卡片容器（使用 sticky="nsew" 让卡片能跟随列宽拉伸填满栅格）
            card = ttk.Frame(frame, relief="solid", borderwidth=1, padding=5)
            card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

            chk = ttk.Checkbutton(card, text=f"#{item.number:03d}", variable=self.checkbox_vars[item.number])
            chk.pack(anchor="nw", pady=2)

            # 图片预览区（缩略图在卡片拉伸时依然在其中央保持完美居中）
            if HAS_PIL:
                thumb = load_thumbnail(item.path)
                if thumb:
                    self.thumbnail_refs[item.number] = thumb
                    img_lbl = ttk.Label(card, image=thumb)
                    img_lbl.pack(pady=2)
                else:
                    ttk.Label(card, text="[无法加载]", foreground="gray", font=UI_SMALL_FONT).pack(pady=20)
            else:
                ttk.Label(card, text="[无预览]", foreground="gray", font=UI_SMALL_FONT).pack(pady=20)

            # 表情描述
            desc_text = item.caption if item.caption else (item.path.stem[:8] + "...")
            desc_lbl = ttk.Label(card, text=desc_text, font=UI_SMALL_FONT, width=12, anchor="center")
            desc_lbl.pack(fill="x", pady=2)

        self.update_page_controls()

    def update_page_controls(self):
        total_pages = max(1, (len(self.items) + self.page_size - 1) // self.page_size)
        start = self.current_page * self.page_size + 1
        end = min((self.current_page + 1) * self.page_size, len(self.items))
        if self.page_label:
            self.page_label.config(text=f"第 {self.current_page + 1} / {total_pages} 页，当前显示 {start}-{end}")
        if self.prev_page_btn:
            self.prev_page_btn.config(state="normal" if self.current_page > 0 else "disabled")
        if self.next_page_btn:
            self.next_page_btn.config(state="normal" if self.current_page < total_pages - 1 else "disabled")

    def prev_page(self):
        if self.current_page <= 0:
            return
        self.current_page -= 1
        self.populate_grid()
        self.scroll_frame.canvas.yview_moveto(0)

    def next_page(self):
        total_pages = max(1, (len(self.items) + self.page_size - 1) // self.page_size)
        if self.current_page >= total_pages - 1:
            return
        self.current_page += 1
        self.populate_grid()
        self.scroll_frame.canvas.yview_moveto(0)

    def select_all(self):
        for var in self.checkbox_vars.values():
            var.set(True)

    def deselect_all(self):
        for var in self.checkbox_vars.values():
            var.set(False)

    def invert_selection(self):
        for var in self.checkbox_vars.values():
            var.set(not var.get())

    def update_info(self):
        selected = sum(1 for var in self.checkbox_vars.values() if var.get())
        self.info_lbl.config(text=f"已选择: {selected} / {len(self.items)}")

    def selected_numbers(self):
        return {num for num, var in self.checkbox_vars.items() if var.get()}

    def keep_all(self):
        messagebox.showinfo("提示", "已全部保留")
        self.finish_and_exit()

    def apply_filter(self):
        selected = self.selected_numbers()
        if not selected:
            messagebox.showwarning("警告", "请至少选择一个表情。")
            return

        if len(selected) == len(self.items):
            messagebox.showinfo("提示","已全部保留")
            self.finish_and_exit()
            return

        remove_items = [item for item in self.items if item.number not in selected]
        msg = (
            f"将保留选中的：{len(selected)} 个表情\n"
            "确认继续吗？"
        )
        if not messagebox.askyesno("确认筛选导出结果", msg):
            return

        try:
            deleted, failed = self.delete_unselected(remove_items)
            self.mark_filtered_out(remove_items)
            info = f"已保留 {len(selected)} 个表情。"
            if failed:
                info += "\n\n以下文件删除失败：\n" + "\n".join(failed[:8])
                if len(failed) > 8:
                    info += f"\n... 还有 {len(failed) - 8} 个"
            messagebox.showinfo("筛选完成", info)
            self.finish_and_exit()
        except Exception as e:
            messagebox.showerror("筛选失败", f"处理过程中发生异常: {e}")

    def delete_unselected(self, items):
        deleted = 0
        failed = []
        for item in items:
            try:
                if item.path.exists():
                    item.path.unlink()
                    deleted += 1
            except Exception as exc:
                failed.append(f"{item.path.name}: {exc}")
        return deleted, failed

    def mark_filtered_out(self, items):
        if not self.csv_index_path.exists():
            return

        removed = {str(item.path.resolve()).lower() for item in items}
        with self.csv_index_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = reader.fieldnames or []
            rows = list(reader)

        if not fieldnames:
            return
        if "error" not in fieldnames:
            fieldnames.append("error")

        for row in rows:
            output = row.get("output") or ""
            if not output:
                continue
            try:
                output_key = str(Path(output).resolve()).lower()
            except Exception:
                output_key = output.lower()
            if output_key in removed:
                row["status"] = "filtered_out"
                row["error"] = "removed_by_user_filter"

        with self.csv_index_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def on_close(self):
        self.scroll_frame.unbind_scroll()
        self.destroy()

    def finish_and_exit(self):
        images_dir = self.images_dir
        if images_dir.exists():
            try:
                os.startfile(images_dir) if sys.platform == "win32" else os.system(f'xdg-open "{images_dir}"')
            except Exception:
                pass
        self.on_close()
        self.parent.after(100, self.parent.destroy)

class SuccessWindow(tk.Toplevel):
    """导出成功后的模态弹窗选项"""
    def __init__(self, parent, output_dir, csv_index_path):
        super().__init__(parent)
        self.parent = parent
        self.output_dir = Path(output_dir)
        self.csv_index_path = Path(csv_index_path)

        self.title("导出成功")
        self.geometry("360x160")
        self.resizable(False, False)
        self.grab_set()

        # 居中对齐父窗口
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

        ttk.Label(self, text="微信自定义表情导出完成！", font=UI_SUCCESS_FONT).pack(pady=15)
        
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", pady=10)

        ttk.Button(btn_frame, text="打开图片文件夹", command=self.open_images).pack(side="left", expand=True, padx=5)
        ttk.Button(btn_frame, text="筛选表情", command=self.filter_stickers).pack(side="left", expand=True, padx=5)
        ttk.Button(btn_frame, text="关闭", command=self.destroy).pack(side="left", expand=True, padx=5)

    def open_images(self):
        img_dir = self.output_dir / "images"
        if img_dir.exists():
            os.startfile(img_dir) if sys.platform == "win32" else os.system(f'xdg-open "{img_dir}"')
        else:
            messagebox.showerror("错误", "无法找到图片导出路径")

    def filter_stickers(self):
        self.destroy()
        StickerSelectionWindow(self.parent, self.output_dir, self.csv_index_path)


class MainApp(tk.Tk):
    """主程序框架"""
    def __init__(self):
        super().__init__()
        self.title("微信表情导出工具")

        # 样式美化
        style = ttk.Style(self)
        if sys.platform == "win32":
            style.theme_use("vista")
        else:
            style.theme_use("clam")

        self.db_candidates = []
        self.selected_db_path = None

        self.build_ui()
        self.refresh_db_list()

        # 动态适应高 DPI 缩放
        # update_idletasks 能够确保 Tkinter 提前完成内部组件的宽高测量
        self.update_idletasks()
        req_w = max(self.winfo_reqwidth(), 680)  # 设置 680 像素的合理最小宽度，保障文本框空间
        req_h = max(self.winfo_reqheight(), 470) # 动态计算所需高度，彻底避免底部组件被截断
        self.geometry(f"{req_w}x{req_h}")
        self.resizable(False, False)

    def build_ui(self):
        main_frame = ttk.Frame(self, padding=(20, 15, 20, 15))
        main_frame.pack(fill="both", expand=True)

        # 标题
        ttk.Label(main_frame, text="微信自定义表情导出工具", font=UI_TITLE_FONT).pack(anchor="w", pady=(0, 10))

        # 1. 数据库选择区域
        db_group = ttk.LabelFrame(main_frame, text=" 1. 表情数据库定位 (emoticon.db) ", padding=10)
        db_group.pack(fill="x", pady=6)
        
        db_select_frame = ttk.Frame(db_group)
        db_select_frame.pack(fill="x")
        
        self.db_combo = ttk.Combobox(db_select_frame, state="readonly")
        self.db_combo.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.db_combo.bind("<<ComboboxSelected>>", self.on_db_selected)
        
        ttk.Button(db_select_frame, text="刷新列表", command=self.refresh_db_list).pack(side="right")
        
        self.db_info_lbl = ttk.Label(db_group, text="正在搜索本地微信表情数据库...", foreground="gray")
        self.db_info_lbl.pack(anchor="w", pady=(5, 0))

        # 2. 密钥定位
        key_group = ttk.LabelFrame(main_frame, text=" 2. 数据库解密密钥 (Key) ", padding=10)
        key_group.pack(fill="x", pady=6)
        
        key_select_frame = ttk.Frame(key_group)
        key_select_frame.pack(fill="x")
        
        self.key_entry = ttk.Entry(key_select_frame, font=MONOSPACE_FONT)
        self.key_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        self.btn_scan = ttk.Button(key_select_frame, text="扫描运行中微信获取密钥", command=self.start_scan_key)
        self.btn_scan.pack(side="right")

        # 3. 导出选项
        out_group = ttk.LabelFrame(main_frame, text=" 3. 导出路径配置 ", padding=10)
        out_group.pack(fill="x", pady=6)
        
        out_select_frame = ttk.Frame(out_group)
        out_select_frame.pack(fill="x")
        
        self.out_entry = ttk.Entry(out_select_frame)
        self.out_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.out_entry.insert(0, str(Path("./exports").resolve()))
        
        ttk.Button(out_select_frame, text="浏览...", command=self.browse_output).pack(side="right")

        # 4. 底部进度条与按钮 (采用 side="top" 流式布局，确保在不同系统下顺次排列不重叠)
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill="x", side="top", pady=(12, 0))

        self.btn_run = ttk.Button(bottom_frame, text="开始导出表情", command=self.start_export)
        self.btn_run.pack(side="right", padx=(10, 0), pady=2)

        status_frame = ttk.Frame(bottom_frame)
        status_frame.pack(side="left", fill="x", expand=True)

        self.status_lbl = ttk.Label(status_frame, text="就绪")
        self.status_lbl.pack(anchor="w")

        self.progress = ttk.Progressbar(status_frame, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", pady=(3, 0))

    def refresh_db_list(self):
        try:
            self.db_candidates = discover_emoticon_databases()
        except Exception:
            self.db_candidates = []

        if self.db_candidates:
            vals = [f"{i+1}. {item.path.parent.parent.name} ({item.size // 1024} KB)" for i, item in enumerate(self.db_candidates)]
            self.db_combo.config(values=vals)
            self.db_combo.current(0)
            self.on_db_selected(None)
        else:
            self.db_combo.config(values=["未检测到数据库"])
            self.db_combo.set("未检测到数据库")
            self.db_info_lbl.config(text="无法定位默认微信目录，请手动运行微信 4.x 并保持其处于活跃状态。")
            self.selected_db_path = None

    def on_db_selected(self, event):
        idx = self.db_combo.current()
        if idx >= 0 and idx < len(self.db_candidates):
            db = self.db_candidates[idx]
            self.selected_db_path = db.path
            mtime_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(db.modified_at))
            self.db_info_lbl.config(text=f"路径: {db.path.name} | 修改时间: {mtime_str} | 大小: {db.size // 1024} KB")
        else:
            self.selected_db_path = None

    def browse_output(self):
        dir_path = filedialog.askdirectory(initialdir=self.out_entry.get())
        if dir_path:
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, os.path.abspath(dir_path))

    def start_scan_key(self):
        if not self.selected_db_path:
            messagebox.showwarning("提示", "未找到可解密的数据库。")
            return
        
        self.btn_scan.config(state="disabled")
        self.status_lbl.config(text="正在从微信进程内存搜寻原始密钥...")
        self.progress.config(mode="indeterminate")
        self.progress.start(10)

        threading.Thread(target=self.scan_key_thread, daemon=True).start()

    def scan_key_thread(self):
        try:
            results = scan_database_keys(self.selected_db_path)
            all_keys = set()
            for pid, scan in results.items():
                all_keys.update(scan.keys)
            
            if all_keys:
                key = sorted(all_keys)[0]
                self.after(0, lambda: self.on_scan_success(key))
            else:
                self.after(0, lambda: self.on_scan_fail(results))
        except Exception as e:
            self.after(0, lambda: self.on_scan_error(str(e)))

    def on_scan_success(self, key):
        self.key_entry.delete(0, tk.END)
        self.key_entry.insert(0, key)
        self.progress.stop()
        self.progress.config(mode="determinate", value=0)
        self.btn_scan.config(state="normal")
        self.status_lbl.config(text="密钥获取成功")
        messagebox.showinfo("成功", "已成功搜寻并填入进程对应的解密 Key！")

    def on_scan_fail(self, results):
        self.progress.stop()
        self.progress.config(mode="determinate", value=0)
        self.btn_scan.config(state="normal")
        self.status_lbl.config(text="未能找到密钥。")
        if not results:
            detail = "未检测到 Weixin.exe 进程，请先启动微信并登录。"
        elif all(scan.open_error is not None for scan in results.values()):
            detail = "无法打开微信进程。请确认本工具与微信以相同权限级别运行；如果微信以管理员身份启动，本工具也需要管理员权限。"
        elif not any(scan.bytes_read for scan in results.values()):
            detail = "没有读取到进程内存。请确认微信仍在运行，并检查 Python 与微信的位数是否一致（通常都应为 64 位）。"
        elif any(scan.config_adapter for scan in results.values()):
            if any(scan.config_salt_matches for scan in results.values()):
                detail = "已识别微信版本专用的 Config.Cipher 配置，并找到与所选数据库 salt 相同的记录，但第一页 HMAC 校验未通过。可能是当前数据库页格式与适配参数不同。"
            else:
                detail = "已识别微信版本专用的 Config.Cipher 适配器，但没有取得与所选数据库 salt 匹配且通过校验的记录。请确认数据库属于当前登录账号，并在微信中打开表情数据后重新扫描。"
        elif not any(scan.candidates_tested for scan in results.values()):
            detail = "已读取微信进程内存，但没有找到十六进制密钥候选。此微信版本可能以其他格式保存密钥。"
        else:
            detail = "找到了密钥候选，但都无法解密所选数据库。请确认数据库属于当前登录账号，并重新选择数据库后扫描。"
        messagebox.showwarning("提示", detail)

    def on_scan_error(self, err):
        self.progress.stop()
        self.progress.config(mode="determinate", value=0)
        self.btn_scan.config(state="normal")
        self.status_lbl.config(text="扫描出错")
        messagebox.showerror("扫描失败", f"内存扫描出现系统异常:\n{err}")

    def start_export(self):
        if not self.selected_db_path:
            messagebox.showwarning("提示", "请先选择需要操作的微信表情数据库。")
            return
        
        key = self.key_entry.get().strip()
        if not key:
            messagebox.showwarning("提示", "数据库解密密钥 (Key) 不能为空。请先进行扫描或手动输入。")
            return
            
        output_dir = Path(self.out_entry.get().strip())
        if not output_dir:
            messagebox.showwarning("提示", "请选择有效的导出路径。")
            return

        self.btn_run.config(state="disabled")
        self.btn_scan.config(state="disabled")
        self.db_combo.config(state="disabled")
        self.progress.config(mode="determinate", value=0)

        threading.Thread(target=self.export_thread, args=(self.selected_db_path, output_dir, key), daemon=True).start()

    def export_thread(self, db_path, output_dir, key_hex):
        def progress_cb(val):
            self.after(0, lambda: self.progress.config(value=val))
            
        def status_cb(text):
            self.after(0, lambda: self.status_lbl.config(text=text))

        success, msg = export_worker(db_path, output_dir, key_hex, progress_cb, status_cb)
        self.after(0, lambda: self.on_export_finished(success, msg, output_dir))

    def on_export_finished(self, success, msg, output_dir):
        self.btn_run.config(state="normal")
        self.btn_scan.config(state="normal")
        self.db_combo.config(state="readonly")
        
        if success:
            self.status_lbl.config(text="导出完成")
            self.progress.config(value=100)
            csv_index_path = output_dir / "added_stickers_index.csv"
            SuccessWindow(self, output_dir, csv_index_path)
        else:
            self.status_lbl.config(text="导出出错")
            self.progress.config(value=0)
            messagebox.showerror("导出失败", msg)


def main() -> None:
    enable_windows_dpi_awareness()
    app = MainApp()
    app.mainloop()


if __name__ == "__main__":
    main()
