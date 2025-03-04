"""
Gemini API PDF 文字提取工具 (AI Studio 版本 - 本地代理优化)
使用 Google AI Studio API 密钥自动处理 PDF 文件并提取文本
支持多 API 密钥自动轮换
"""
import re
import os
import glob
import time
import base64
import logging
import argparse
from pathlib import Path
from typing import List, Optional
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from pypdf import PdfReader
from PIL import Image
import fitz  # PyMuPDF
import io
import sys
import requests
import socket
import urllib3

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("gemini_pdf_extraction.log"),
        logging.StreamHandler()
    ]
)

# 配置请求的超时时间和重试
socket.setdefaulttimeout(180)  # 增加套接字超时时间

# 设置默认代理
DEFAULT_PROXY = "http://localhost:7890"
DEFAULT_MODEL = "gemini-2.0-pro-exp-02-05"  # 改为确定支持的模型

# 定义提示词
PROMPT_TEMPLATE = """请精确识别以下图片中的内容，包含中文、日语和英语：
     1. 保持原始排版格式
     2. 中日混合内容保留原文
     3. 输出全部用Markdown格式
     4. 每页结束后用「=== Page ===」分隔"""

def natural_sort_key(s):
    """用于自然排序的辅助函数，将数字部分作为整数处理"""
    return [int(text) if text.isdigit() else text.lower() 
            for text in re.split('([0-9]+)', os.path.basename(s))]

def load_api_keys(key_file):
    """从文件加载API密钥列表"""
    try:
        with open(key_file, 'r', encoding='utf-8') as f:
            keys = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            logging.info(f"从 {key_file} 加载了 {len(keys)} 个API密钥")
            return keys
    except Exception as e:
        logging.error(f"加载API密钥文件失败: {str(e)}")
        return []

class GeminiPDFProcessor:
    def __init__(self, api_keys: List[str], model_name: str = DEFAULT_MODEL, proxy: str = DEFAULT_PROXY):
        """
        初始化 Gemini API 处理器
        
        Args:
            api_keys: Google API 密钥列表
            model_name: 使用的模型名称
            proxy: 代理服务器地址
        """
        self.api_keys = api_keys if isinstance(api_keys, list) else [api_keys]
        self.current_key_index = 0
        self.model_name = model_name
        self.proxy = proxy
        self.model = None
        self.setup_api()
        
    def switch_to_next_key(self):
        """切换到下一个可用的API密钥"""
        old_key = self.api_keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        new_key = self.api_keys[self.current_key_index]
        
        # 如果一轮循环回到相同的密钥，则所有密钥都已耗尽
        if old_key == new_key:
            logging.error("所有API密钥都已达到配额限制!")
            return False
            
        logging.info(f"切换到新的API密钥 (索引: {self.current_key_index})")
        
        # 重新设置API
        try:
            genai.configure(api_key=new_key)
            
            # 安全设置
            self.safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE
            }
            
            # 初始化模型
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                safety_settings=self.safety_settings,
                generation_config={
                    "temperature": 0.0,
                    "top_p": 0.95,
                    "top_k": 32,
                    "max_output_tokens": 4096,
                }
            )
            
            # 简单测试
            try:
                test_result = self.model.generate_content("Hello")
                logging.info(f"新API密钥测试成功: {test_result.text[:20]}...")
                return True
            except Exception as e:
                logging.error(f"新API密钥测试失败: {str(e)}")
                # 如果测试失败，尝试下一个密钥
                return self.switch_to_next_key()
                
        except Exception as e:
            logging.error(f"切换API密钥失败: {str(e)}")
            # 如果切换失败，尝试下一个密钥
            return self.switch_to_next_key()
        
    def setup_api(self):
        """配置 Gemini API (AI Studio版本)"""
        # 设置环境变量代理
        if self.proxy:
            os.environ['HTTP_PROXY'] = self.proxy
            os.environ['HTTPS_PROXY'] = self.proxy
            logging.info(f"已设置代理: {self.proxy}")
        
        # 使用第一个API密钥配置
        if not self.api_keys:
            raise ValueError("未提供任何API密钥")
            
        current_key = self.api_keys[self.current_key_index]
        genai.configure(api_key=current_key)
        
        # 测试网络连接
        try:
            session = requests.Session()
            proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None
            
            logging.info("测试到 Google API 的网络连接...")
            response = session.get("https://generativelanguage.googleapis.com/", 
                                  timeout=30, 
                                  proxies=proxies)
            logging.info(f"网络连接测试结果: {response.status_code}")
        except Exception as e:
            logging.warning(f"网络连接测试失败: {str(e)}")
            logging.warning("继续尝试初始化 API...")
        
        # 尝试设置模型
        try:
            # 获取可用模型列表并记录，但不要依赖它们
            try:
                models = genai.list_models()
                model_names = [m.name for m in models]
                logging.info(f"可用的模型: {model_names}")
                
                # 如果指定模型不在列表中，使用默认可用视觉模型
                if self.model_name not in model_names and "gemini-2.0-pro-exp-02-05" in model_names:
                    logging.warning(f"指定的模型 {self.model_name} 可能不可用，将使用 gemini-2.0-pro-exp-02-05")
                    self.model_name = "gemini-2.0-pro-exp-02-05"
            except Exception as e:
                logging.warning(f"获取模型列表失败: {str(e)}，使用指定的模型 {self.model_name}")
            
            # 安全设置
            self.safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE
            }
            
            # 初始化模型
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                safety_settings=self.safety_settings,
                generation_config={
                    "temperature": 0.0,
                    "top_p": 0.95,
                    "top_k": 32,
                    "max_output_tokens": 4096,
                }
            )
            
            logging.info(f"Gemini API 已初始化，使用模型: {self.model_name}")
            
            # 简单测试 - 不传递timeout参数
            test_result = self.model.generate_content("Hello")
            logging.info(f"模型测试成功: {test_result.text[:20]}...")
            
        except Exception as e:
            logging.error(f"初始化模型失败: {str(e)}")
            # 提供错误指导
            if "invalid_api_key" in str(e).lower():
                logging.error("AI Studio API密钥无效，请检查是否正确复制了完整密钥")
            elif "permission_denied" in str(e).lower():
                logging.error("权限被拒绝，请确认AI Studio密钥已启用并具有适当权限")
            elif "connection" in str(e).lower() or "timeout" in str(e).lower():
                logging.error("连接错误，请检查代理设置或网络连接")
            raise

    def process_pdf_folder(self, pdf_folder: str, output_folder: str, start_index: int = 0, 
                        end_index: Optional[int] = None, retry_count: int = 3):
        """
        处理文件夹中的所有PDF文件
        
        Args:
            pdf_folder: PDF文件所在文件夹
            output_folder: 输出结果保存文件夹
            start_index: 开始处理的文件索引
            end_index: 结束处理的文件索引
            retry_count: 失败重试次数
        """
        # 创建输出文件夹和恢复文件夹
        os.makedirs(output_folder, exist_ok=True)
        recovery_dir = os.path.join(output_folder, ".recovery")
        os.makedirs(recovery_dir, exist_ok=True)
        
        # 恢复文件路径
        recovery_file = os.path.join(recovery_dir, "processed_files.txt")
        progress_file = os.path.join(recovery_dir, "progress.json")
        
        # 获取已处理文件列表
        processed_files = set()
        if os.path.exists(recovery_file):
            with open(recovery_file, "r", encoding="utf-8") as f:
                processed_files = set(line.strip() for line in f if line.strip())
            logging.info(f"从记录中找到 {len(processed_files)} 个已处理的文件")
        
        # 添加：扫描输出目录中的现有文件
        existing_outputs = glob.glob(os.path.join(output_folder, "*.md"))
        existing_file_bases = {os.path.splitext(os.path.basename(f))[                                                                                                                                       0] for f in existing_outputs}
        existing_count = 0
        
        # 获取所有PDF文件并按自然顺序排序
        pdf_files = glob.glob(os.path.join(pdf_folder, "*.pdf"))
        pdf_files = sorted(pdf_files, key=natural_sort_key)  # 使用自然排序
        
        # 将已有输出的文件路径添加到processed_files
        for pdf_path in pdf_files:
            pdf_basename = os.path.splitext(os.path.basename(pdf_path))[0]
            if pdf_basename in existing_file_bases:
                processed_files.add(pdf_path)
                existing_count += 1
        
        if existing_count > 0:
            logging.info(f"从输出目录找到 {existing_count} 个已处理文件")
        
        # 确定处理范围
        end_index = end_index or len(pdf_files)
        pdf_files = pdf_files[start_index:end_index]
        
        # 估计总处理时间
        est_time_per_file = 3  # 估计每个文件处理时间(分钟)
        est_total_time = len(pdf_files) * est_time_per_file
        logging.info(f"找到 {len(pdf_files)} 个PDF文件待处理，估计需要约 {est_total_time} 分钟")
        
        # 处理每个PDF文件
        for i, pdf_path in enumerate(pdf_files):
            pdf_name = os.path.basename(pdf_path)
            output_path = os.path.join(output_folder, f"{os.path.splitext(pdf_name)[0]}.md")
            
            # 保存当前进度
            import json
            with open(progress_file, "w", encoding="utf-8") as f:
                json.dump({
                    "current_index": i + start_index,
                    "current_file": pdf_path,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "current_key_index": self.current_key_index
                }, f, ensure_ascii=False, indent=2)
            
            # 如果文件已被处理（检查记录和输出文件）
            if pdf_path in processed_files and os.path.exists(output_path):
                logging.info(f"[{i+1}/{len(pdf_files)}] 跳过已处理文件: {pdf_name}")
                continue
            
            logging.info(f"[{i+1}/{len(pdf_files)}] 正在处理: {pdf_name}")
            start_time = time.time()
            
            # 处理PDF文件
            success = False
            attempts = 0
            
            while not success and attempts < retry_count:
                attempts += 1
                try:
                    # 处理单个PDF文件
                    text_content = self.process_single_pdf(pdf_path)
                    
                    # 保存提取的文本
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(text_content)
                    
                    # 记录成功处理的文件
                    with open(recovery_file, "a", encoding="utf-8") as f:
                        f.write(f"{pdf_path}\n")
                    
                    elapsed = (time.time() - start_time) / 60.0
                    logging.info(f"成功处理 {pdf_name} 并保存到 {output_path}，耗时: {elapsed:.2f}分钟")
                    success = True
                    
                except Exception as e:
                    logging.error(f"处理 {pdf_name} 时出错 (尝试 {attempts}/{retry_count}): {str(e)}")
                    if attempts < retry_count:
                        wait_time = 10 * attempts  # 指数退避
                        logging.info(f"等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
            
            # 在处理文件之间添加延迟，避免超出API速率限制
            if i < len(pdf_files) - 1:  # 如果不是最后一个文件
                wait_time = 30  # 增加到30秒，减少速率限制错误
                logging.info(f"等待 {wait_time} 秒后处理下一个文件...")
                time.sleep(wait_time)
            
            # 显示处理进度和剩余时间估计
            files_completed = i + 1
            files_remaining = len(pdf_files) - files_completed
            time_per_file = (time.time() - start_time) / 60.0
            est_remaining = files_remaining * time_per_file
            
            logging.info(f"进度: {files_completed}/{len(pdf_files)} 完成，"
                        f"估计剩余时间: {est_remaining:.2f}分钟")
        
        # 处理完成，清理进度文件
        if os.path.exists(progress_file):
            os.remove(progress_file)
        logging.info("所有文件处理完成!")
    
    def process_single_pdf(self, pdf_path: str) -> str:
        """
        处理单个PDF文件
        
        Args:
            pdf_path: PDF文件路径
            
        Returns:
            提取的文本内容
        """
        # 从PDF提取图像
        page_images = self.extract_images_from_pdf(pdf_path)
        
        if not page_images:
            logging.warning(f"无法从 {pdf_path} 提取图像")
            return "无法处理此PDF文件，未能提取图像"
        
        all_page_texts = []
        
        # 如果页面太多，分批处理
        batch_size = 1  # 每批处理的页面数
        for i in range(0, len(page_images), batch_size):
            batch_images = page_images[i:i+batch_size]
            logging.info(f"处理页面批次 {i//batch_size + 1}/{(len(page_images) + batch_size - 1)//batch_size}, "
                        f"页面 {i+1}-{min(i+batch_size, len(page_images))}")
            
            # 为每个批次构建提示词
            content = [
                PROMPT_TEMPLATE,
            ]
            
            # 添加批次中的图像
            for j, img in enumerate(batch_images):
                # 调整图像大小以适应API限制
                img = self.resize_image_if_needed(img)
                
                # 添加图像
                content.append(img)
                
                # 添加页码提示
                page_num = i + j + 1
                content.append(f"这是第 {page_num} 页")
            
            # 调用Gemini API
            try:
                response = self.generate_content_with_retry(content)
                
                if response and response.text:
                    page_text = response.text
                    # 添加页码标记
                    if "=== Page" not in page_text:
                        page_text = f"=== Page {i+1} ===\n\n{page_text}"
                    all_page_texts.append(page_text)
                else:
                    all_page_texts.append(f"无法处理页面 {i+1}-{min(i+batch_size, len(page_images))}")
                
            except Exception as e:
                logging.error(f"处理页面 {i+1} 时出错: {str(e)}")
                all_page_texts.append(f"处理页面 {i+1} 时出错: {str(e)}")
            
            # 增加页面处理之间的等待时间
            if i + batch_size < len(page_images):
                wait_time = 10  # 10秒
                logging.info(f"等待 {wait_time} 秒后处理下一页...")
                time.sleep(wait_time)
        
        # 合并所有批次的结果
        return "\n\n".join(all_page_texts)
    
    def resize_image_if_needed(self, img):
        """调整图像大小，避免超出API限制"""
        max_size = 1024  # 最大尺寸
        
        width, height = img.size
        if width > max_size or height > max_size:
            # 保持宽高比的情况下调整大小
            if width > height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_height = max_size
                new_width = int(width * (max_size / height))
                
            img = img.resize((new_width, new_height), Image.LANCZOS)
            logging.info(f"调整图像大小从 {width}x{height} 到 {new_width}x{new_height}")
            
        return img
    
    def generate_content_with_retry(self, content, max_retries=5):
        """带重试的API调用 - 带API密钥轮换功能"""
        retry_count = 0
        key_switched = False
        last_error = None
        
        while retry_count < max_retries:
            try:
                # 设置超时
                start_time = time.time()
                logging.info("开始API调用...")
                
                # 实际的API调用
                response = self.model.generate_content(content)
                
                elapsed = time.time() - start_time
                logging.info(f"API调用成功，耗时: {elapsed:.2f}秒")
                return response
                
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                logging.warning(f"API调用失败: {str(e)}")
                
                # 检测是否是配额限制错误
                if ("rate limit" in error_str or "quota" in error_str or 
                    "resource" in error_str or "429" in error_str or 
                    "exhausted" in error_str):
                    
                    logging.warning("检测到API配额限制，尝试切换API密钥...")
                    
                    # 尝试切换到下一个API密钥
                    if self.switch_to_next_key():
                        logging.info("成功切换到新的API密钥，重试请求...")
                        key_switched = True
                        # 不增加重试计数，因为使用了新密钥
                        continue
                    else:
                        # 所有密钥都耗尽
                        logging.error("所有API密钥都已达到配额限制!")
                        # 仍然可以尝试等待并重试当前密钥
                        wait_time = 120  # 长等待
                        logging.info(f"所有密钥都已用尽，等待{wait_time}秒后重试当前密钥...")
                        time.sleep(wait_time)
                
                # 其他错误处理逻辑
                retry_count += 1
                if "timeout" in error_str or "connection" in error_str or "503" in error_str:
                    wait_time = 30 * retry_count  # 网络错误等待更长时间
                    logging.info(f"检测到网络连接问题，等待{wait_time}秒后重试...")
                elif "image too large" in error_str:
                    logging.error("图像太大，API无法处理")
                    raise ValueError("图像太大，请减小图像大小后重试")
                else:
                    wait_time = 15 * retry_count
                
                if retry_count < max_retries:
                    logging.info(f"等待 {wait_time} 秒后重试 (尝试 {retry_count}/{max_retries})...")
                    time.sleep(wait_time)
        
        # 所有重试都失败
        msg = "达到最大重试次数" if not key_switched else "所有API密钥尝试后"
        logging.error(f"API调用失败，{msg}: {str(last_error)}")
        raise last_error
    
    def extract_images_from_pdf(self, pdf_path: str):
        """
        从PDF文件提取图像
        
        Args:
            pdf_path: PDF文件路径
            
        Returns:
            图像列表
        """
        images = []
        
        try:
            # 使用PyMuPDF提取高质量图像
            doc = fitz.open(pdf_path)
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                # 将页面渲染为图像 (为API优化DPI)
                pix = page.get_pixmap(matrix=fitz.Matrix(150/72, 150/72))
                
                # 转换为PIL图像
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))
                
                images.append(img)
            
            logging.info(f"从 {pdf_path} 中提取了 {len(images)} 页图像")
            return images
            
        except Exception as e:
            logging.error(f"从PDF提取图像时出错: {str(e)}")
            
            # 尝试备用方法
            try:
                logging.info("尝试备用图像提取方法...")
                doc = fitz.open(pdf_path)
                
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    
                    # 使用不同参数渲染
                    pix = page.get_pixmap(alpha=False)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    
                    images.append(img)
                
                logging.info(f"备用方法成功，提取了 {len(images)} 页图像")
                return images
                
            except Exception as e2:
                logging.error(f"备用图像提取也失败: {str(e2)}")
                return []

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="使用Gemini API从PDF文件提取文本（多密钥版）")
    parser.add_argument("--api-key", help="Google API密钥（单个）")
    parser.add_argument("--key-file", help="包含多个API密钥的文件，每行一个")
    parser.add_argument("--pdf-folder", required=True, help="PDF文件所在文件夹")
    parser.add_argument("--output-folder", required=True, help="输出结果保存文件夹")
    parser.add_argument("--start", type=int, default=0, help="开始处理的文件索引")
    parser.add_argument("--end", type=int, help="结束处理的文件索引")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, 
                       help=f"使用的模型名称，默认为{DEFAULT_MODEL}")
    parser.add_argument("--proxy", type=str, default=DEFAULT_PROXY, 
                       help=f"代理服务器地址，默认为{DEFAULT_PROXY}")
    parser.add_argument("--no-proxy", action="store_true", 
                       help="不使用任何代理")
    parser.add_argument("--resume", action="store_true", 
                       help="从上次中断的位置恢复处理")
    
    args = parser.parse_args()
    
    # 处理API密钥
    api_keys = []
    if args.api_key:
        api_keys = [args.api_key]
    elif args.key_file and os.path.exists(args.key_file):
        api_keys = load_api_keys(args.key_file)
    else:
        # 如果没有通过参数提供密钥，尝试默认位置
        default_key_file = "key.txt"
        if os.path.exists(default_key_file):
            api_keys = load_api_keys(default_key_file)
        else:
            logging.error("未提供API密钥，请使用 --api-key 或 --key-file 参数")
            sys.exit(1)
    
    if not api_keys:
        logging.error("未能加载任何有效的API密钥")
        sys.exit(1)
    
    # 处理代理设置
    proxy = None if args.no_proxy else args.proxy
    
    try:
        # 初始化处理器
        processor = GeminiPDFProcessor(
            api_keys=api_keys,
            model_name=args.model,
            proxy=proxy
        )
        
        # 如果要从断点恢复，加载上次的进度
        start_index = args.start
        if args.resume:
            progress_file = os.path.join(args.output_folder, ".recovery", "progress.json")
            if os.path.exists(progress_file):
                import json
                try:
                    with open(progress_file, "r", encoding="utf-8") as f:
                        progress = json.load(f)
                        start_index = progress["current_index"]
                        # 可以选择恢复上次使用的密钥索引
                        if "current_key_index" in progress:
                            processor.current_key_index = progress["current_key_index"]
                            logging.info(f"恢复使用的API密钥索引: {processor.current_key_index}")
                        logging.info(f"从断点恢复: 继续处理索引 {start_index} ({progress['current_file']})")
                except Exception as e:
                    logging.warning(f"读取断点文件失败: {str(e)}，从指定的起始索引 {args.start} 开始")
            else:
                logging.info(f"未找到断点文件，从头开始")
        
        # 处理PDF文件
        processor.process_pdf_folder(
            pdf_folder=args.pdf_folder,
            output_folder=args.output_folder,
            start_index=start_index,
            end_index=args.end
        )
    except KeyboardInterrupt:
        logging.info("用户中断，程序退出。可以使用 --resume 参数从断点恢复。")
        sys.exit(0)
    except Exception as e:
        logging.critical(f"程序执行过程中出现严重错误: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()