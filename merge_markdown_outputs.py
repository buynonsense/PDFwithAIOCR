import os
import re
import glob
import argparse
import logging
from pathlib import Path
from typing import List, Optional
import sys

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

def natural_sort_key(s):
    """用于自然排序的辅助函数，将数字部分作为整数处理"""
    return [int(text) if text.isdigit() else text.lower() 
            for text in re.split('([0-9]+)', os.path.basename(s))]

def merge_markdown_files(input_folder: str, output_file: str, file_pattern: str = "*.md",
                        add_headers: bool = True, add_separator: bool = True,
                        title: str = None):
    """
    合并文件夹中的Markdown文件
    
    Args:
        input_folder: 包含Markdown文件的文件夹路径
        output_file: 合并后输出的文件路径
        file_pattern: 文件匹配模式，默认为 "*.md"
        add_headers: 是否为每个文件添加标题，默认为True
        add_separator: 是否在文件之间添加分隔线，默认为True
        title: 合并文档的标题，默认使用自动生成的标题
    """
    # 验证输入文件夹是否存在
    if not os.path.exists(input_folder):
        logging.error(f"输入文件夹 '{input_folder}' 不存在!")
        return False

    # 获取所有匹配的文件
    files = glob.glob(os.path.join(input_folder, file_pattern))
    
    # 忽略 .recovery 目录下的文件
    files = [f for f in files if '.recovery' not in f]
    
    if not files:
        logging.error(f"在 '{input_folder}' 中未找到任何匹配的Markdown文件 ({file_pattern})")
        return False
    
    # 按自然顺序排序文件
    files = sorted(files, key=natural_sort_key)
    
    logging.info(f"找到 {len(files)} 个Markdown文件，准备合并...")
    
    # 创建输出目录（如果不存在）
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 设置标题
    if not title:
        title = "PDF文本提取合并结果"
    
    # 合并内容
    with open(output_file, 'w', encoding='utf-8') as outfile:
        # 写入标题
        outfile.write(f"# {title}\n\n")
        outfile.write(f"*本文件由 {len(files)} 个PDF提取结果自动合并生成*\n\n")
        
        # 写入目录
        outfile.write("## 目录\n\n")
        for i, file_path in enumerate(files):
            file_name = os.path.splitext(os.path.basename(file_path))[0]
            outfile.write(f"{i+1}. [{file_name}](#{file_name.lower().replace(' ', '-').replace('(', '').replace(')', '')})\n")
        outfile.write("\n---\n\n")
        
        # 合并文件内容
        for i, file_path in enumerate(files):
            file_name = os.path.splitext(os.path.basename(file_path))[0]
            logging.info(f"合并文件 [{i+1}/{len(files)}]: {file_name}")
            
            try:
                with open(file_path, 'r', encoding='utf-8') as infile:
                    content = infile.read()
                    
                    if add_headers:
                        outfile.write(f"## {file_name}\n\n")
                    
                    outfile.write(content)
                    
                    if add_separator and i < len(files) - 1:
                        outfile.write("\n\n---\n\n")
                    else:
                        outfile.write("\n\n")
            except Exception as e:
                logging.error(f"处理文件 {file_name} 时出错: {str(e)}")
                # 继续处理其他文件
    
    logging.info(f"合并完成! 已保存到: {output_file}")
    file_size_mb = round(os.path.getsize(output_file) / (1024 * 1024), 2)
    logging.info(f"合并文件大小: {file_size_mb} MB")
    return True

def get_user_input(prompt_message, default_value=None):
    """获取用户输入，支持默认值"""
    if default_value:
        user_input = input(f"{prompt_message} [默认: {default_value}]: ").strip()
        if not user_input:
            return default_value
        return user_input
    else:
        while True:
            user_input = input(f"{prompt_message}: ").strip()
            if user_input:
                return user_input
            print("此项为必填项，请输入有效值。")

def interactive_mode():
    """交互模式，引导用户输入参数"""
    print("\n=== PDF提取结果合并工具 - 交互模式 ===")
    print("此工具将合并由Gemini PDF提取工具生成的多个Markdown文件为一个文件\n")
    
    # 获取输入文件夹
    default_input = os.path.join(os.getcwd(), "output")
    if os.path.exists(default_input):
        input_folder = get_user_input("请输入包含Markdown文件的文件夹路径", default_input)
    else:
        input_folder = get_user_input("请输入包含Markdown文件的文件夹路径")
    
    # 检查输入文件夹是否存在
    if not os.path.exists(input_folder):
        print(f"错误: 文件夹 '{input_folder}' 不存在!")
        return 1
    
    # 检查是否有md文件
    md_files = glob.glob(os.path.join(input_folder, "*.md"))
    if not md_files:
        print(f"警告: 在 '{input_folder}' 中未找到任何Markdown文件!")
        if not input("是否继续? (y/n): ").lower().startswith('y'):
            return 1
    else:
        print(f"找到 {len(md_files)} 个Markdown文件")
    
    # 获取输出文件路径
    default_output = os.path.join(os.getcwd(), "merged_output.md")
    output_file = get_user_input("请输入合并后的输出文件路径", default_output)
    
    # 获取文档标题
    title = get_user_input("请输入合并文档的标题", "PDF文本提取合并结果")
    
    # 其他选项
    add_headers = input("是否为每个文件添加标题? (y/n) [默认: y]: ").lower() != 'n'
    add_separator = input("是否在文件之间添加分隔线? (y/n) [默认: y]: ").lower() != 'n'
    
    print("\n=== 合并配置摘要 ===")
    print(f"输入文件夹: {input_folder}")
    print(f"输出文件: {output_file}")
    print(f"文档标题: {title}")
    print(f"添加标题: {'是' if add_headers else '否'}")
    print(f"添加分隔线: {'是' if add_separator else '否'}")
    
    if input("\n确认开始合并? (y/n) [默认: y]: ").lower() != 'n':
        print("\n开始合并文件...")
        success = merge_markdown_files(
            input_folder=input_folder,
            output_file=output_file,
            add_headers=add_headers,
            add_separator=add_separator,
            title=title
        )
        
        if success:
            print(f"\n合并成功完成! 输出文件: {output_file}")
            return 0
        else:
            print("\n合并过程中出现错误，详情请查看日志")
            return 1
    else:
        print("\n操作已取消")
        return 0

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="合并Markdown文件工具")
    parser.add_argument("--input-folder", help="包含Markdown文件的文件夹路径")
    parser.add_argument("--output-file", help="合并后输出的文件路径")
    parser.add_argument("--pattern", default="*.md", help="文件匹配模式，默认为 '*.md'")
    parser.add_argument("--no-headers", action="store_false", dest="add_headers", 
                        help="不为每个文件添加标题")
    parser.add_argument("--no-separator", action="store_false", dest="add_separator", 
                        help="不在文件之间添加分隔线")
    parser.add_argument("--title", help="合并文档的标题")
    parser.add_argument("--interactive", "-i", action="store_true", 
                        help="以交互模式运行，引导用户输入参数")
    
    args = parser.parse_args()
    
    # 如果没有提供参数或使用交互模式标志，则启动交互模式
    if args.interactive or (not args.input_folder and not args.output_file and len(sys.argv) == 1):
        return interactive_mode()
    
    # 检查必要参数
    if not args.input_folder or not args.output_file:
        logging.error("缺少必要参数: --input-folder 和 --output-file")
        logging.info("使用 --interactive 或 -i 参数可以启动交互模式")
        logging.info("使用 --help 查看帮助信息")
        return 1
    
    try:
        success = merge_markdown_files(
            input_folder=args.input_folder,
            output_file=args.output_file,
            file_pattern=args.pattern,
            add_headers=args.add_headers,
            add_separator=args.add_separator,
            title=args.title
        )
        return 0 if success else 1
    except Exception as e:
        logging.critical(f"合并过程中出现严重错误: {str(e)}")
        return 1

if __name__ == "__main__":
    exit(main())