import sys
import os
import re
import time
import threading
import pickle
import requests
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urljoin, urlparse
from Crypto.Cipher import AES

class VideoDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("视频下载器 (HLS/m3u8) - 清晰度选择版")
        self.root.geometry("620x600")
        self.driver = None
        self.cookie_str = ""
        self.cookie_file = "cookies.pkl"

        # ---------- 界面元素 ----------
        row = 0
        # 视频页面 URL
        ttk.Label(root, text="视频页面地址:").grid(row=row, column=0, padx=5, pady=5, sticky="w")
        self.url_entry = ttk.Entry(root, width=65)
        self.url_entry.grid(row=row, column=1, columnspan=2, padx=5, pady=5)
        row += 1

        # 保存路径
        ttk.Label(root, text="保存到:").grid(row=row, column=0, padx=5, pady=5, sticky="w")
        self.path_var = tk.StringVar(value=os.path.join(os.getcwd(), "downloads"))
        self.path_entry = ttk.Entry(root, textvariable=self.path_var, width=55)
        self.path_entry.grid(row=row, column=1, padx=5, pady=5)
        ttk.Button(root, text="浏览...", command=self.browse_folder).grid(row=row, column=2, padx=5)
        row += 1

        # 清晰度选择（新增）
        ttk.Label(root, text="清晰度:").grid(row=row, column=0, padx=5, pady=5, sticky="w")
        self.quality_var = tk.StringVar(value="720p")
        self.quality_combo = ttk.Combobox(
            root,
            textvariable=self.quality_var,
            values=["最高画质", "1080p", "720p", "480p", "360p", "240p", "最低画质"],
            state="readonly",
            width=12
        )
        self.quality_combo.grid(row=row, column=1, padx=5, pady=5, sticky="w")
        self.quality_combo.current(2)  # 默认 720p
        row += 1

        # Cookie 来源选项
        ttk.Label(root, text="Cookie 来源:").grid(row=row, column=0, padx=5, pady=5, sticky="w")
        self.cookie_source = tk.StringVar(value="file")
        ttk.Radiobutton(root, text="使用本地保存的 Cookie（首次需登录一次）",
                        variable=self.cookie_source, value="file").grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1
        ttk.Radiobutton(root, text="手动粘贴 Cookie 字符串",
                        variable=self.cookie_source, value="paste").grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1

        # Cookie 文本框
        ttk.Label(root, text="Cookie 字符串:").grid(row=row, column=0, padx=5, pady=5, sticky="w")
        self.cookie_text = tk.Text(root, height=4, width=60)
        self.cookie_text.grid(row=row, column=1, columnspan=2, padx=5, pady=5)
        row += 1

        # 重置 Cookie 按钮
        self.reset_cookie_btn = ttk.Button(root, text="重置本地 Cookie", command=self.reset_cookie)
        self.reset_cookie_btn.grid(row=row, column=0, columnspan=3, pady=5)
        row += 1

        # 进度条与状态
        self.progress = ttk.Progressbar(root, orient="horizontal", length=500, mode="determinate")
        self.progress.grid(row=row, column=0, columnspan=3, padx=5, pady=10)
        row += 1
        self.status_label = ttk.Label(root, text="就绪")
        self.status_label.grid(row=row, column=0, columnspan=3, padx=5)
        row += 1

        # 下载按钮
        self.download_btn = ttk.Button(root, text="开始下载", command=self.start_download)
        self.download_btn.grid(row=row, column=0, columnspan=3, pady=10)

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.path_var.set(folder)

    def update_status(self, msg, progress_val=None):
        self.status_label.config(text=msg)
        if progress_val is not None:
            self.progress["value"] = progress_val
        self.root.update_idletasks()

    def reset_cookie(self):
        if os.path.exists(self.cookie_file):
            os.remove(self.cookie_file)
            messagebox.showinfo("提示", "本地 Cookie 已删除，下次下载需要重新登录。")
        else:
            messagebox.showinfo("提示", "没有找到本地 Cookie 文件。")

    def start_download(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("错误", "请输入视频页面地址")
            return
        self.download_btn.config(state="disabled")
        threading.Thread(target=self._download_thread, args=(url,), daemon=True).start()

    def _download_thread(self, url):
        try:
            self._do_download(url)
        except Exception as e:
            self.root.after(0, lambda err=e: messagebox.showerror("下载失败", str(err)))
        finally:
            self.root.after(0, lambda: self.download_btn.config(state="normal"))
            self.root.after(0, lambda: self.update_status("就绪", 0))

    def _inject_cookies_from_file(self, driver, domain):
        """从本地文件加载 Cookie 并注入"""
        if not os.path.exists(self.cookie_file):
            return False
        with open(self.cookie_file, "rb") as f:
            cookies = pickle.load(f)
        driver.get(domain)
        try:
            for c in cookies:
                driver.add_cookie(c)
        except Exception:
            pass
        return True

    def _save_cookies_to_file(self):
        if self.driver:
            cookies = self.driver.get_cookies()
            with open(self.cookie_file, "wb") as f:
                pickle.dump(cookies, f)

    def _get_base_domain(self, url):
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _wait_for_video_ready(self, timeout=20):
        """等待页面中 source842 等变量出现，或至少 video 标签存在"""
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script(
                    "return window.source842 || window.source1280 || window.f;"
                )
            )
        except:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "video"))
            )

    def _do_download(self, url):
        save_dir = self.path_var.get()
        os.makedirs(save_dir, exist_ok=True)

        self.update_status("正在启动浏览器...", 5)
        options = webdriver.ChromeOptions()
        options.page_load_strategy = 'eager'
        # options.add_argument('--headless=new')  # 如需无头模式可取消注释
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(30)

        try:
            source = self.cookie_source.get()
            base_domain = self._get_base_domain(url)

            if source == "paste":
                raw_cookie = self.cookie_text.get("1.0", tk.END).strip()
                if not raw_cookie:
                    raise Exception("请粘贴有效的 Cookie 字符串")
                self.cookie_str = raw_cookie
                self.driver.get(base_domain)
                for pair in raw_cookie.split("; "):
                    if "=" in pair:
                        name, value = pair.split("=", 1)
                        self.driver.add_cookie({"name": name, "value": value})
                self.driver.get(url)
                time.sleep(1)
            else:
                if os.path.exists(self.cookie_file):
                    self.update_status("加载本地 Cookie...", 10)
                    if not self._inject_cookies_from_file(self.driver, base_domain):
                        raise Exception("本地 Cookie 文件损坏")
                    self.driver.get(url)
                else:
                    self.update_status("首次使用，请手动登录...", 10)
                    self.root.after(0, lambda: messagebox.showinfo(
                        "手动登录",
                        "请在打开的浏览器窗口中手动登录。\n登录成功后，回到本程序点击“确定”。"
                    ))
                    self.driver.get(url)
                    login_done = threading.Event()
                    self.root.after(0, lambda: (messagebox.showinfo(
                        "继续", "登录完成后请点击确定"
                    ), login_done.set()))
                    login_done.wait()
                    self._save_cookies_to_file()
                    self.update_status("Cookie 已保存，下次自动登录", 15)

            # 关闭可能的弹窗
            try:
                btns = self.driver.find_elements(By.XPATH,
                    "//*[contains(text(), 'Accept') or contains(text(), '接受') or contains(text(), '同意')]")
                if btns:
                    btns[0].click()
                    time.sleep(1)
            except:
                pass

            # 等待视频链接就绪（显式等待，不再固定 sleep）
            self._wait_for_video_ready(timeout=20)

            self.update_status("正在提取视频链接...", 30)
            video_url = self._extract_video_url()
            if not video_url:
                raise Exception("未能从页面提取到视频地址，请确认页面结构或登录状态。")
            self.update_status(f"视频链接已抓到: {video_url[:80]}...", 40)

            # 构造请求头
            cookies = self.driver.get_cookies()
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
            if source == "paste" and self.cookie_str:
                cookie_str = self.cookie_str
            headers = {
                "User-Agent": self.driver.execute_script("return navigator.userAgent;"),
                "Referer": url,
                "Cookie": cookie_str
            }

            output_file = os.path.join(save_dir, "video_output.mp4")
            self._download_m3u8(video_url, headers, output_file)

            self.update_status(f"下载完成！文件保存在: {output_file}", 100)
            self.root.after(0, lambda: messagebox.showinfo("成功", f"视频已保存到:\n{output_file}"))

        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None

    def _extract_video_url(self):
        """提取 m3u8 地址，优先读取全局变量"""
        # 策略1：直接读取已知的全局变量（页面 eval 执行后会出现）
        for var in ['source842', 'source1280', 'f']:
            try:
                url = self.driver.execute_script(f"return window.{var};")
                if url and 'm3u8' in str(url):
                    return str(url)
            except:
                pass

        # 策略2：尝试执行页面中的 eval 代码块
        page_source = self.driver.page_source
        eval_blocks = re.findall(
            r'(eval\s*\(function\(p,a,c,k,e,d\)\{.*?\}\(.*?\)\))',
            page_source, re.DOTALL
        )
        for block in eval_blocks:
            try:
                self.driver.execute_script(block)
                for var in ['source842', 'source1280', 'f']:
                    url = self.driver.execute_script(f"return window.{var};")
                    if url and 'm3u8' in str(url):
                        return str(url)
            except:
                pass

        # 策略3：flashvars_ 遍历
        try:
            video_url = self.driver.execute_script("""
                for (let key in window) {
                    if (key.startsWith('flashvars_')) {
                        let obj = window[key];
                        let queue = [obj];
                        let visited = new WeakSet();
                        while (queue.length) {
                            let cur = queue.pop();
                            if (cur && typeof cur === 'object') {
                                if (visited.has(cur)) continue;
                                visited.add(cur);
                                if (cur.videoUrl) return cur.videoUrl;
                                if (cur.mediaDefinitions) {
                                    for (let d of cur.mediaDefinitions) {
                                        if (d.videoUrl) return d.videoUrl;
                                    }
                                }
                                for (let prop in cur) {
                                    try {
                                        let val = cur[prop];
                                        if (val && typeof val === 'object') queue.push(val);
                                    } catch(e) {}
                                }
                            }
                        }
                    }
                }
                let v = document.querySelector('video');
                if (v && v.src) return v.src;
                let s = document.querySelector('video source');
                if (s && s.src) return s.src;
                return null;
            """)
            if video_url:
                return video_url.replace('\\/', '/')
        except:
            pass

        # 策略4：正则匹配源码中的 videoUrl
        match = re.search(r'videoUrl["\']?\s*:\s*["\'](https?:\\?/\\?/[^"\']+)', page_source)
        if match:
            return match.group(1).replace("\\/", "/")
        return None

    def _parse_master_playlist(self, m3u8_url, headers, content):
        """解析 master playlist，返回所有 variant 的列表"""
        lines = content.splitlines()
        variants = []
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                info = line
                # 分辨率
                res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', info)
                height = int(res_match.group(2)) if res_match else 0
                # 带宽
                bw_match = re.search(r'BANDWIDTH=(\d+)', info)
                bandwidth = int(bw_match.group(1)) if bw_match else 0
                # 子列表 URL 在下一行
                if i+1 < len(lines) and not lines[i+1].startswith("#"):
                    variants.append({
                        'url': urljoin(m3u8_url, lines[i+1].strip()),
                        'height': height,
                        'bandwidth': bandwidth,
                        'raw': info
                    })
        return variants

    def _select_variant(self, variants):
        """根据用户选择的清晰度，从 variant 列表中选出最合适的那个"""
        if not variants:
            return None

        choice = self.quality_var.get()
        if choice == "最高画质":
            # 按高度降序，选第一个
            variants.sort(key=lambda v: v['height'], reverse=True)
            return variants[0]
        elif choice == "最低画质":
            variants.sort(key=lambda v: v['height'])
            return variants[0]
        else:
            # 具体分辨率如 720p -> target_h = 720
            try:
                target_h = int(choice[:-1])
            except:
                target_h = 720
            # 寻找高度 ≤ target_h 的最大高度
            candidates = [v for v in variants if v['height'] <= target_h]
            if candidates:
                candidates.sort(key=lambda v: v['height'], reverse=True)
                return candidates[0]
            else:
                # 没有符合条件的，返回最低的
                variants.sort(key=lambda v: v['height'])
                return variants[0]

    def _download_m3u8(self, m3u8_url, headers, save_path):
        """递归解析 m3u8，处理加密流并下载合并"""
        self.update_status("分析 m3u8 播放列表...", 50)
        resp = requests.get(m3u8_url, headers=headers)
        if resp.status_code == 410:
            raise Exception("链接已失效(410)，请重新运行并尽快下载。")
        resp.raise_for_status()
        content = resp.text

        # 如果是 master playlist，根据清晰度选择子列表
        if "#EXT-X-STREAM-INF" in content:
            variants = self._parse_master_playlist(m3u8_url, headers, content)
            if not variants:
                raise Exception("未找到任何子播放列表")
            target = self._select_variant(variants)
            self.update_status(f"已选择清晰度: {target['height']}p (带宽: {target['bandwidth']})", 55)
            return self._download_m3u8(target['url'], headers, save_path)

        # ---------- 解析加密信息 ----------
        key = None
        key_iv = None
        key_method = None
        for line in content.splitlines():
            if line.startswith('#EXT-X-KEY:'):
                method_match = re.search(r'METHOD=([^,]+)', line)
                uri_match = re.search(r'URI="([^"]+)"', line)
                iv_match = re.search(r'IV=0x([0-9a-fA-F]+)', line)
                if method_match:
                    key_method = method_match.group(1)
                if uri_match:
                    key_uri = uri_match.group(1)
                    key_url = urljoin(m3u8_url, key_uri)  # 补全相对路径
                    key_resp = requests.get(key_url, headers=headers)
                    key_resp.raise_for_status()
                    key = key_resp.content
                if iv_match:
                    key_iv = bytes.fromhex(iv_match.group(1))
                break

        # 提取所有 .ts 链接
        base_url = m3u8_url.rsplit("/", 1)[0] + "/"
        ts_urls = []
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                if not line.startswith("http"):
                    line = urljoin(base_url, line)
                ts_urls.append(line)

        total = len(ts_urls)
        if total == 0:
            raise Exception("m3u8 内未找到任何视频片段")
        self.update_status(f"共 {total} 个片段，开始下载...", 60)

        temp_dir = os.path.join(os.path.dirname(save_path), "temp_ts")
        os.makedirs(temp_dir, exist_ok=True)

        # 逐片下载并解密
        for i, ts_url in enumerate(ts_urls):
            success = False
            for attempt in range(4):
                try:
                    r = requests.get(ts_url, headers=headers, timeout=15)
                    if r.status_code == 429:
                        wait = 2 ** attempt
                        time.sleep(wait)
                        continue
                    r.raise_for_status()
                    data = r.content

                    # AES-128-CBC 解密（无填充）
                    if key and key_method == 'AES-128':
                        iv = key_iv if key_iv else i.to_bytes(16, byteorder='big')
                        cipher = AES.new(key, AES.MODE_CBC, iv=iv)
                        data = cipher.decrypt(data)

                    with open(os.path.join(temp_dir, f"{i:05d}.ts"), "wb") as f:
                        f.write(data)
                    success = True
                    break
                except Exception as e:
                    if attempt == 3:
                        raise Exception(f"下载片段 {i} 失败: {e}")
                    time.sleep(1)
            if not success:
                raise Exception(f"片段 {i} 下载失败")
            time.sleep(0.2)   # 节流，避免请求过快
            pct = 60 + int((i+1) / total * 30)
            self.update_status(f"下载片段 {i+1}/{total}", pct)

        # 合并 ts 文件
        self.update_status("合并视频文件中...", 92)
        with open(save_path, "wb") as out:
            for i in range(total):
                part_path = os.path.join(temp_dir, f"{i:05d}.ts")
                with open(part_path, "rb") as inf:
                    out.write(inf.read())
                os.remove(part_path)
        os.rmdir(temp_dir)
        self.update_status("合并完成", 100)

    def on_close(self):
        if self.driver:
            self.driver.quit()
        self.root.destroy()

if __name__ == "__main__":
    if getattr(sys, 'frozen', False):
        os.chdir(os.path.dirname(sys.executable))
    root = tk.Tk()
    app = VideoDownloaderApp(root)
    root.mainloop()