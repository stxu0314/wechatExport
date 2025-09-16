import argparse
import hashlib
import json
import logging
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from datetime import datetime

# 版本信息
__version__ = "1.0.0"

# 设置日志记录
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("wxdump2pdf.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("wxdump2pdf")

# 检查Python版本
if sys.version_info < (3, 6):
    logger.error("此程序需要Python 3.6或更高版本")
    sys.exit(1)

# 第三方库依赖
REQUIRED_PACKAGES = {
    "pillow": "PIL",
    "reportlab": "reportlab",
    "tqdm": "tqdm",
    "pypdf2": "PyPDF2"
}

# 可选库依赖
OPTIONAL_PACKAGES = {
    "funasr": "FunASR语音识别",
    "opencc": "繁体转简体转换"
}

# 检查必要依赖
missing_packages = []
for package, import_name in REQUIRED_PACKAGES.items():
    try:
        __import__(import_name.split('.')[0])
    except ImportError:
        missing_packages.append(package)

if missing_packages:
    logger.error(f"缺少必要的依赖库: {', '.join(missing_packages)}")
    logger.error(f"请安装缺少的依赖: pip install {' '.join(missing_packages)}")
    sys.exit(1)

# 导入依赖库
try:
    from PIL import Image
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from tqdm import tqdm
except ImportError as e:
    logger.error(f"导入依赖库时出错: {e}")
    sys.exit(1)

# 检查并记录可选依赖
for package, description in OPTIONAL_PACKAGES.items():
    try:
        __import__(package)
        logger.info(f"✅ 可选功能已启用: {description}")
    except ImportError:
        logger.warning(f"⚠️ 可选功能未启用: {description}。安装: pip install {package}")


# 配置
class Config:
    """程序配置"""
    # 媒体缓存目录
    MEDIA_CACHE_DIR = os.path.join(tempfile.gettempdir(), "wechat_media_cache")
    # 语音转写缓存目录
    SPEECH_CACHE_DIR = os.path.join(MEDIA_CACHE_DIR, "speech_transcripts")
    # PDF压缩级别 (0-9)
    PDF_COMPRESSION_LEVEL = 9
    # 图像质量 (1-100)
    IMAGE_QUALITY = 60
    # 最大图像尺寸(像素)
    MAX_IMAGE_DIMENSION = 1000
    # 最大缓存大小(MB)
    MAX_CACHE_SIZE_MB = 500
    # 调试模式
    DEBUG_MODE = False
    # 最大头像下载线程数
    MAX_AVATAR_THREADS = 4
    # 头像大小(毫米)
    AVATAR_SIZE_MM = 10
    # 最大音频转写尝试次数
    MAX_TRANSCRIBE_RETRIES = 3
    # 字体路径
    FONT_PATHS = [
        # Windows字体
        os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'simsun.ttc'),
        # Linux字体
        '/usr/share/fonts/truetype/arphic/uming.ttc',
        # macOS字体
        '/System/Library/Fonts/STHeiti Light.ttc',
        # 当前目录下的字体
        'simsun.ttc'
    ]


# 创建缓存目录
os.makedirs(Config.MEDIA_CACHE_DIR, exist_ok=True)
os.makedirs(Config.SPEECH_CACHE_DIR, exist_ok=True)


# 检测FFmpeg路径
def find_ffmpeg():
    """自动查找FFmpeg可执行文件路径"""
    # 系统默认路径
    if platform.system() == "Windows":
        paths = [
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
        ]
    else:  # Linux 或 macOS
        paths = ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"]

    # 检查环境变量PATH中的ffmpeg
    if shutil.which("ffmpeg"):
        paths.insert(0, shutil.which("ffmpeg"))

    for path in paths:
        if os.path.exists(path) and os.path.isfile(path):
            return path
    return None


# 全局变量
FFMPEG_PATH = find_ffmpeg()
FFMPEG_AVAILABLE = False
FUNASR_AVAILABLE = False
funasr_model = None

# 缓存
timestamp_cache = {}
string_width_cache = {}
avatar_cache = {}
path_cache = {}

# Media cache directory
MEDIA_CACHE_DIR = os.path.join(tempfile.gettempdir(), "wechat_media_cache")
os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)

# PDF Compression level (0-9), 0 means no compression, 9 means maximum compression
PDF_COMPRESSION_LEVEL = 9
# Image quality for JPEG compression (1-100), lower values mean smaller file size but lower quality
IMAGE_QUALITY = 60
# Maximum image dimension (width or height) in pixels
MAX_IMAGE_DIMENSION = 1000

# Speech transcript cache directory
SPEECH_CACHE_DIR = os.path.join(MEDIA_CACHE_DIR, "speech_transcripts")
os.makedirs(SPEECH_CACHE_DIR, exist_ok=True)

# FunASR 模型设置
FUNASR_AVAILABLE = False
funasr_model = None

# FFmpeg path
FFMPEG_PATH = r"C:\Users\STXU\Downloads\ffmpeg-master-latest-win64-gpl\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"

# Emoji mapping
EMOJI_MAP = {
    # 基础表情
    "[微笑]": "😊",
    "[笑]": "😄",
    "[大笑]": "😂",
    "[呲牙]": "😁",
    "[嘻嘻]": "😆",
    "[偷笑]": "😏",
    "[害羞]": "😳",
    "[可爱]": "🥰",
    "[调皮]": "😜",
    "[得意]": "😎",
    "[龇牙]": "😬",
    "[鼓掌]": "👏",
    "[呲牙笑]": "😁",
    "[憨笑]": "🤪",

    # 情绪表情
    "[难过]": "😔",
    "[流泪]": "😢",
    "[大哭]": "😭",
    "[伤心]": "💔",
    "[失望]": "😞",
    "[恐惧]": "😱",
    "[尴尬]": "😓",
    "[汗]": "💦",
    "[抓狂]": "😫",
    "[怒]": "😡",
    "[发怒]": "😠",
    "[生气]": "🤬",
    "[委屈]": "🥺",
    "[惊讶]": "😲",
    "[惊恐]": "😨",
    "[惊吓]": "😱",
    "[惊喜]": "🤩",
    "[疑问]": "❓",
    "[思考]": "🤔",
    "[紧张]": "😰",
    "[捂脸]": "🤦",
    "[晕]": "😵",
    "[衰]": "😣",
    "[悠闲]": "😌",
    "[奋斗]": "💪",

    # 状态表情
    "[发呆]": "😐",
    "[睡]": "😴",
    "[睡觉]": "😴",
    "[疲惫]": "😩",
    "[困]": "😫",
    "[口罩]": "😷",
    "[感冒]": "🤒",
    "[生病]": "🤢",
    "[吐]": "🤮",
    "[瞌睡]": "😪",
    "[闭嘴]": "🤐",
    "[傻眼]": "😳",
    "[色]": "😍",
    "[嘴唇]": "👄",
    "[亲亲]": "😘",
    "[互相亲]": "💏",
    "[吓]": "😨",
    "[冷汗]": "😅",
    "[阴险]": "😏",
    "[嘘]": "🤫",
    "[右哼哼]": "😤",
    "[左哼哼]": "😒",

    # 动作表情
    "[给力]": "👍",
    "[差劲]": "👎",
    "[举手]": "🙋",
    "[拜拜]": "👋",
    "[加油]": "💪",
    "[合十]": "🙏",

    # 动物表情
    "[猪头]": "🐷",
    "[猪]": "🐖",
    "[熊猫]": "🐼",
    "[兔子]": "🐰",
    "[小狗]": "🐶",
    "[狗]": "🐕",
    "[猫咪]": "🐱",
    "[猫]": "🐈",
    "[猴子]": "🐒",
    "[羊]": "🐑",
    "[老虎]": "🐯",
    "[蛇]": "🐍",
    "[鸡]": "🐔",
    "[公鸡]": "🐓",
    "[青蛙]": "🐸",

    # 食物表情
    "[西瓜]": "🍉",
    "[啤酒]": "🍺",
    "[咖啡]": "☕",
    "[蛋糕]": "🍰",
    "[吃瓜]": "🍉",
    "[饭]": "🍚",
    "[苹果]": "🍎",
    "[甜品]": "🧁",
    "[红酒]": "🍷",
    "[面条]": "🍜",

    # 物品表情
    "[礼物]": "🎁",
    "[红包]": "🧧",
    "[花]": "🌸",
    "[玫瑰]": "🌹",
    "[枯萎]": "🥀",
    "[爱心]": "❤️",
    "[心碎]": "💔",
    "[拥抱]": "🤗",
    "[强]": "💪",
    "[弱]": "👎",
    "[拍照]": "📷",
    "[火]": "🔥",
    "[溜]": "🏃",
    "[炸弹]": "💣",
    "[刀]": "🔪",
    "[足球]": "⚽",
    "[篮球]": "🏀",
    "[毛线]": "🧶",

    # 天气表情
    "[太阳]": "☀️",
    "[月亮]": "🌙",
    "[雨]": "🌧️",
    "[雪]": "❄️",
    "[闪电]": "⚡",
    "[阴天]": "☁️",

    # 其他表情
    "[赞]": "👍",
    "[嗯]": "😐",
    "[抠鼻]": "👃",
    "[吐舌]": "😝",
    "[可怜]": "🥺",
    "[白眼]": "🙄",
    "[右太极]": "☯️",
    "[左太极]": "☯️",
    "[骷髅]": "💀",
    "[嘿哈]": "✌️",
    "[奸笑]": "😏",
    "[机智]": "😎",
    "[耶]": "✌️",
    "[面对疗伤]": "🤒",
    "[摊手]": "🤷",

    # 交通表情
    "[车]": "🚗",
    "[车厢]": "🚃",
    "[飞机]": "✈️",
    "[火车]": "🚄",
    "[自行车]": "🚲",

    # 节日表情
    "[圣诞树]": "🎄",
    "[圣诞老人]": "🎅",
    "[灯笼]": "🏮",
    "[鞭炮]": "🧨",
    "[烟花]": "🎆",

    # 手势表情
    "[NO]": "🙅",
    "[点赞]": "👍",
    "[握手]": "🤝",
    "[胜利]": "✌️",
    "[抱拳]": "🙏",
    "[勾引]": "💋",
    "[拳头]": "👊",
    "[OK]": "👌",
    "[跳跳]": "💃",
    "[发抖]": "😰",
    "[转圈]": "😵‍💫",

    # 新增微信特色表情
    "[打脸]": "😣👋",
    "[破涕为笑]": "😂",
    "[脸红]": "😊",
    "[嫌弃]": "😒",
    "[皱眉]": "😞",
    "[擦汗]": "😅",
    "[撇嘴]": "😏",
    "[偷看]": "👀",
    "[托腮]": "🤔",
    "[眨眼]": "😉",
    "[泪奔]": "😭",
    "[石化]": "😶",
    "[喷血]": "🥵",
    "[笑哭]": "😂",
    "[doge]": "🐶",
    "[滑稽]": "🤡",
    "[疼]": "🤕",
    "[再见]": "👋",
    "[鄙视]": "😠",
    "[财迷]": "🤑",
    "[吃惊]": "😲",
    "[悲催]": "😭",
    "[激动]": "🤩",
    "[酷]": "😎",
    "[抱抱]": "🤗",
    "[坏笑]": "😏",
    "[飙泪]": "😭",
    "[打call]": "👏",

    # 微信独特符号表情
    "[666]": "666",
    "[233]": "233",
    "[服]": "🙇",
    "[作揖]": "🙇",
    "[发财]": "🤑",
    "[来看我]": "👀",
    "[别想歪]": "🙄",
    "[加我]": "🙋",
    "[叹气]": "😮‍💨",
    "[裂开]": "😱",
    "[羡慕]": "🤩",
    "[求抱抱]": "🤗",
    "[我想静静]": "😶",
    "[允悲]": "😔",
    "[泪流满面]": "😭",
    "[斜眼]": "🙄",
    "[跪了]": "🧎",
    "[潜水]": "🤿",
    "[柠檬]": "🍋",
    "[冷漠]": "😐",
    "[舔屏]": "👅",
    "[二哈]": "🐶",
    "[牛年吉祥]": "🐂",
    "[春节快乐]": "🧧",
    "[福到了]": "福",
    "[黑脸]": "🌚",
    "[捞月亮]": "🌝",
    "[旺财]": "🐕"
}
# Caches
timestamp_cache = {}
string_width_cache = {}
avatar_cache = {}

# Check FFmpeg availability
FFMPEG_AVAILABLE = False

# 调试模式开关，设置为False将减少详细日志输出
DEBUG_MODE = False


def print_info(message):
    """
    打印信息日志，可以通过设置DEBUG_MODE控制详细程度
    - 如果DEBUG_MODE为True，打印所有日志
    - 如果DEBUG_MODE为False，只打印带有特殊前缀的重要日志
    """
    if DEBUG_MODE or message.startswith(("✅", "❌", "⚠️", "📑", "🔧", "📊")):
        print(message)


def check_ffmpeg_available():
    """检查FFmpeg是否可用，返回布尔值"""
    if not FFMPEG_PATH:
        logger.error("未找到FFmpeg路径")
        return False

    try:
        result = subprocess.run([FFMPEG_PATH, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        logger.info(f"已找到FFmpeg: {FFMPEG_PATH}")
        return True
    except Exception as e:
        logger.error(f"未找到FFmpeg: {e}")
        logger.error("请安装FFmpeg: https://ffmpeg.org/download.html")
        return False


FFMPEG_AVAILABLE = check_ffmpeg_available()

# Check FunASR availability
try:
    from funasr import AutoModel

    FUNASR_AVAILABLE = True
    print_info("✅ FunASR可用")
except ImportError:
    print_info("⚠️ FunASR未安装。安装命令: pip install -U funasr")


def download_emoji_font(force=False):
    """
    下载Google的Noto Color Emoji字体
    参数:
        force: 是否强制下载，即使已存在也重新下载
    返回:
        下载成功与否
    """
    # 检查是否已有带Regular后缀的字体文件
    regular_font_path = os.path.join(os.path.dirname(__file__), 'NotoColorEmoji-Regular.ttf')
    if os.path.exists(regular_font_path) and not force:
        print_info(f"✅ 已存在带Regular后缀的Emoji字体文件: {regular_font_path}")
        return True

    # 检查是否已有标准命名的字体文件
    font_url = "https://github.com/googlefonts/noto-emoji/raw/main/fonts/NotoColorEmoji.ttf"
    font_path = os.path.join(os.path.dirname(__file__), 'NotoColorEmoji.ttf')

    if os.path.exists(font_path) and not force:
        print_info(f"✅ 已存在Emoji字体文件: {font_path}")
        return True

    try:
        print_info(f"🔄 正在下载Emoji字体文件...")
        import urllib.request
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124'
        }
        req = urllib.request.Request(font_url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            with open(font_path, 'wb') as f:
                f.write(response.read())
        print_info(f"✅ Emoji字体下载成功: {font_path}")
        return True
    except Exception as e:
        print_info(f"❌ Emoji字体下载失败: {e}")
        return False


def register_fonts():
    """
    注册中文字体和emoji字体，优先级为列表中顺序
    如果找不到任何中文字体，将回退到Helvetica
    返回: 是否成功注册了中文字体
    """
    # 注册中文字体
    chinese_font_paths = [
        'simsun.ttc',
        '/usr/share/fonts/truetype/arphic/uming.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        'C:\\Windows\\Fonts\\simsun.ttc',
        'C:\\Windows\\Fonts\\msyh.ttc',  # 微软雅黑
        'C:\\Windows\\Fonts\\msyhbd.ttc',  # 微软雅黑粗体
        'C:\\Windows\\Fonts\\simhei.ttf'  # 黑体
    ]

    # 注册支持 emoji 的字体
    emoji_font_paths = [
        'seguiemj.ttf',
        'seguisym.ttf',
        'C:\\Windows\\Fonts\\seguiemj.ttf',  # Segoe UI Emoji
        'C:\\Windows\\Fonts\\seguisym.ttf',  # Segoe UI Symbol
        '/System/Library/Fonts/Apple Color Emoji.ttc',  # macOS Emoji
        '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf',  # Linux Noto Emoji
        # 当前目录的Noto Emoji
        'NotoColorEmoji.ttf',  # 添加对当前目录Noto字体的支持
        'NotoColorEmoji-Regular.ttf',  # 添加对带Regular后缀的Noto字体的支持
        os.path.join(os.path.dirname(__file__), 'NotoColorEmoji.ttf'),  # 相对于脚本的路径
        os.path.join(os.path.dirname(__file__), 'NotoColorEmoji-Regular.ttf'),  # 带Regular后缀的相对路径
        # 添加更多常见Emoji字体路径
        'C:\\Windows\\Fonts\\coloemoj.ttf',  # Windows Color Emoji
        'C:\\Windows\\Fonts\\ColorEmoji.ttf',  # Windows Color Emoji (另一个可能的名称)
        'C:\\Users\\{}\\AppData\\Local\\Microsoft\\Windows\\Fonts\\NotoColorEmoji.ttf'.format(os.getenv('USERNAME')),
        # 用户安装的Noto Emoji
        'C:\\Users\\{}\\AppData\\Local\\Microsoft\\Windows\\Fonts\\NotoColorEmoji-Regular.ttf'.format(
            os.getenv('USERNAME')),  # 带Regular后缀
    ]

    chinese_font_registered = False
    emoji_font_registered = False

    # 注册中文字体
    for path in chinese_font_paths:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont('SimSun', path))
            print_info(f"✅ 已注册宋体字体: {path}")
            chinese_font_registered = True
            break

    # 注册Emoji字体
    for path in emoji_font_paths:
        if os.path.exists(path):
            try:
                # 检查是否为彩色表情字体
                is_color_emoji = ('Color' in path or 'color' in path or 'emoji' in path.lower())
                pdfmetrics.registerFont(TTFont('EmojiFont', path))
                if is_color_emoji:
                    print_info(f"✅ 已注册彩色Emoji字体: {path}")
                else:
                    print_info(f"✅ 已注册Emoji字体: {path}")
                emoji_font_registered = True
                break
            except Exception as e:
                print_info(f"⚠️ 注册Emoji字体失败: {path}, 错误: {e}")

    if not chinese_font_registered:
        print_info("⚠️ 未找到中文字体，将使用Helvetica替代")

    if not emoji_font_registered:
        print_info("⚠️ 未找到Emoji字体，表情符号可能无法正确显示")
        print_info("📝 提示：您可以下载NotoColorEmoji.ttf字体并放在脚本同目录下以支持彩色表情")
        # 提示是否要下载表情字体
        try:
            import sys
            if sys.stdout.isatty():  # 确认是在交互式终端中运行
                response = input("是否要自动下载Noto Color Emoji字体? (y/n): ")
                if response.lower() in ['y', 'yes']:
                    if download_emoji_font():
                        # 尝试再次注册下载的字体
                        try:
                            # 优先尝试带Regular后缀的字体文件
                            regular_font_path = os.path.join(os.path.dirname(__file__), 'NotoColorEmoji-Regular.ttf')
                            if os.path.exists(regular_font_path):
                                pdfmetrics.registerFont(TTFont('EmojiFont', regular_font_path))
                                print_info(f"✅ 已注册下载的彩色Emoji字体(Regular): {regular_font_path}")
                                emoji_font_registered = True
                            else:
                                # 尝试标准命名的字体文件
                                font_path = os.path.join(os.path.dirname(__file__), 'NotoColorEmoji.ttf')
                                pdfmetrics.registerFont(TTFont('EmojiFont', font_path))
                                print_info(f"✅ 已注册下载的彩色Emoji字体: {font_path}")
                                emoji_font_registered = True
                        except Exception as e:
                            print_info(f"⚠️ 注册下载的Emoji字体失败: {e}")
        except:
            pass

    return chinese_font_registered


def load_json_file(file_path):
    """
    加载JSON文件
    参数:
        file_path: JSON文件路径
    返回:
        JSON数据(字典)，加载失败则返回空字典
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print_info(f"❌ 加载JSON文件失败: {file_path}, 错误: {e}")
        return {}


def parse_timestamp(timestamp):
    """
    解析各种格式的时间戳为datetime对象
    参数:
        timestamp: 时间戳，可以是整数(Unix时间戳)或字符串(多种格式)
    返回:
        datetime对象，无法解析则返回None
    """
    if not timestamp:
        return None
    # 使用缓存提高性能
    if timestamp in timestamp_cache:
        return timestamp_cache[timestamp]
    try:
        # 尝试作为Unix时间戳解析
        ts = int(timestamp)
        result = datetime.fromtimestamp(ts)
    except ValueError:
        # 尝试各种日期格式
        formats = ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"]
        result = None
        for fmt in formats:
            try:
                result = datetime.strptime(timestamp, fmt)
                break
            except ValueError:
                continue
        if not result:
            print_info(f"⚠️ 无法解析时间戳: {timestamp}")
    # 缓存结果以提高性能
    timestamp_cache[timestamp] = result
    return result


def format_timestamp(timestamp):
    """
    格式化时间戳为易读的字符串
    参数:
        timestamp: 时间戳(任何parse_timestamp支持的格式)
    返回:
        格式化的时间字符串，如"2023-01-01 12:00:00"
    """
    dt = parse_timestamp(timestamp)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else str(timestamp)


def get_date_from_timestamp(timestamp):
    """
    从时间戳中提取日期部分
    参数:
        timestamp: 时间戳
    返回:
        日期字符串，格式为"YYYY-MM-DD"，无法解析则返回None
    """
    dt = parse_timestamp(timestamp)
    return dt.strftime("%Y-%m-%d") if dt else None


def download_avatars_thread(avatar_queue, avatar_cache, temp_dir):
    """
    头像下载线程函数
    参数:
        avatar_queue: 包含待下载头像URL的队列
        avatar_cache: 存储下载的头像的缓存字典
        temp_dir: 临时文件目录
    """
    while True:
        try:
            url = avatar_queue.get(timeout=1)
            # None信号用于停止线程
            if url is None:
                break
            # 使用URL的哈希值作为文件名，避免文件名冲突
            file_hash = hashlib.md5(url.encode()).hexdigest()
            temp_file = os.path.join(temp_dir, f"avatar_{file_hash}.jpg")
            # 如果已经缓存，直接使用缓存文件
            if os.path.exists(temp_file):
                avatar_cache[url] = ImageReader(temp_file)
                avatar_queue.task_done()
                continue
            # 下载头像
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124'
            }
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                with open(temp_file, 'wb') as f:
                    f.write(response.read())
                avatar_cache[url] = ImageReader(temp_file)
            avatar_queue.task_done()
        except queue.Empty:
            # 队列为空，线程退出
            break
        except Exception:
            # 发生错误，标记任务完成并继续
            avatar_queue.task_done()


def prepare_avatars(users, download_avatars=True, max_workers=4):
    """
    预下载所有用户头像，使用多线程提高效率
    参数:
        users: 用户信息字典
        download_avatars: 是否下载头像
        max_workers: 最大工作线程数
    返回:
        头像缓存字典
    """
    if not download_avatars:
        return {}
    # 收集所有头像URL
    avatar_urls = {user_info.get("headImgUrl", "") for user_info in users.values() if user_info.get("headImgUrl")}
    if not avatar_urls:
        return {}
    print_info(f"🔄 预下载 {len(avatar_urls)} 个头像...")
    # 创建下载队列
    avatar_queue = queue.Queue()
    for url in avatar_urls:
        avatar_queue.put(url)
    # 创建多个工作线程
    threads = []
    temp_dir = tempfile.gettempdir()
    for _ in range(min(max_workers, len(avatar_urls))):
        t = threading.Thread(target=download_avatars_thread, args=(avatar_queue, avatar_cache, temp_dir))
        t.daemon = True
        t.start()
        threads.append(t)
    # 等待所有任务完成
    avatar_queue.join()
    # 发送停止信号给所有线程
    for _ in threads:
        avatar_queue.put(None)
    # 等待所有线程退出
    for t in threads:
        t.join(timeout=0.5)
    print_info(f"✅ 已下载 {len(avatar_cache)}/{len(avatar_urls)} 个头像")
    return avatar_cache


def get_avatar_image(url):
    """
    获取头像图像
    参数:
        url: 头像URL
    返回:
        ImageReader对象或None
    """
    return avatar_cache.get(url)


def get_string_width(c, text, font_name, font_size):
    """
    获取字符串在PDF中的宽度，并缓存结果提高性能
    支持emoji表情符号的宽度计算
    参数:
        c: Canvas对象
        text: 要测量的文本
        font_name: 字体名称
        font_size: 字体大小
    返回:
        文本宽度(点)
    """
    # 使用缓存提高性能
    cache_key = (text, font_name, font_size)
    if cache_key in string_width_cache:
        return string_width_cache[cache_key]

    width = 0

    # 检查是否包含emoji表情
    contains_emoji = False
    for char in text:
        if (
                (0x1F600 <= ord(char) <= 0x1F64F) or  # 表情符号
                (0x1F300 <= ord(char) <= 0x1F5FF) or  # 杂项符号和象形文字
                (0x1F680 <= ord(char) <= 0x1F6FF) or  # 交通和地图符号
                (0x2600 <= ord(char) <= 0x26FF) or  # 杂项符号
                (0x2700 <= ord(char) <= 0x27BF) or  # 装饰符号
                (0x1F900 <= ord(char) <= 0x1F9FF)  # 补充符号和象形文字
        ):
            contains_emoji = True
            break

    if contains_emoji:
        # 如果包含emoji，逐字符处理
        for i in range(len(text)):
            char = text[i]
            is_emoji = (
                    (0x1F600 <= ord(char) <= 0x1F64F) or
                    (0x1F300 <= ord(char) <= 0x1F5FF) or
                    (0x1F680 <= ord(char) <= 0x1F6FF) or
                    (0x2600 <= ord(char) <= 0x26FF) or
                    (0x2700 <= ord(char) <= 0x27BF) or
                    (0x1F900 <= ord(char) <= 0x1F9FF)
            )

            # 变体选择器、肤色修饰符等不单独计算宽度
            is_modifier = (
                    (0xFE00 <= ord(char) <= 0xFE0F) or  # 变体选择器
                    (0x1F3FB <= ord(char) <= 0x1F3FF) or  # 肤色修饰符
                    ord(char) == 0x200D  # 零宽连接符
            )

            if is_modifier:
                continue

            if is_emoji:
                # 尝试使用emoji字体测量
                try:
                    c.setFont("EmojiFont", font_size)
                    char_width = c.stringWidth(char, "EmojiFont", font_size)
                except:
                    # 回退到普通字体
                    try:
                        c.setFont(font_name, font_size)
                        char_width = c.stringWidth(char, font_name, font_size)
                    except:
                        c.setFont("Helvetica", font_size)
                        char_width = c.stringWidth(char, "Helvetica", font_size)
            else:
                # 普通字符使用指定字体
                try:
                    c.setFont(font_name, font_size)
                    char_width = c.stringWidth(char, font_name, font_size)
                except:
                    c.setFont("Helvetica", font_size)
                    char_width = c.stringWidth(char, "Helvetica", font_size)

            width += char_width
    else:
        # 如果不包含emoji，直接使用Canvas的stringWidth方法
        try:
            width = c.stringWidth(text, font_name, font_size)
        except:
            print_info(f"⚠️ 字体 {font_name} 失败，回退到Helvetica")
            try:
                c.setFont("Helvetica", font_size)
                width = c.stringWidth(text, "Helvetica", font_size)
            except Exception as e:
                print_info(f"❌ 计算宽度失败: {e}")
                # 使用估算宽度
                width = len(text) * font_size * 0.6

    # 还原之前的字体设置
    try:
        c.setFont(font_name, font_size)
    except:
        c.setFont("Helvetica", font_size)

    # 缓存结果
    string_width_cache[cache_key] = width
    return width


def wrap_text(c, text, font_name, font_size, max_width):
    """
    将文本按指定宽度自动换行，保持表情符号完整性
    参数:
        c: Canvas对象
        text: 要换行的文本
        font_name: 字体名称
        font_size: 字体大小
        max_width: 最大宽度(点)
    返回:
        换行后的文本行列表
    """
    if not text:
        return []

    lines = []
    current_line = ""
    i = 0

    while i < len(text):
        char = text[i]
        is_emoji = False
        emoji_length = 1

        # 检测 emoji 表情符号
        if (
                (0x1F600 <= ord(char) <= 0x1F64F) or  # 表情符号
                (0x1F300 <= ord(char) <= 0x1F5FF) or  # 杂项符号和象形文字
                (0x1F680 <= ord(char) <= 0x1F6FF) or  # 交通和地图符号
                (0x2600 <= ord(char) <= 0x26FF) or  # 杂项符号
                (0x2700 <= ord(char) <= 0x27BF) or  # 装饰符号
                (0x1F900 <= ord(char) <= 0x1F9FF)  # 补充符号和象形文字
        ):
            is_emoji = True

            # 检查后面是否有表情修饰符（如肤色、变体选择器等）
            j = i + 1
            while j < len(text) and (
                    (0xFE00 <= ord(text[j]) <= 0xFE0F) or  # 变体选择器
                    (0x1F3FB <= ord(text[j]) <= 0x1F3FF) or  # 肤色修饰符
                    ord(text[j]) == 0x200D  # 零宽连接符
            ):
                emoji_length += 1
                j += 1

                # 如果是零宽连接符，检查后面的表情符号
                if j < len(text) and ord(text[j - 1]) == 0x200D:
                    while j < len(text) and (
                            (0x1F600 <= ord(text[j]) <= 0x1F64F) or
                            (0x1F300 <= ord(text[j]) <= 0x1F5FF) or
                            (0x1F680 <= ord(text[j]) <= 0x1F6FF) or
                            (0x2600 <= ord(text[j]) <= 0x26FF) or
                            (0x2700 <= ord(text[j]) <= 0x27BF) or
                            (0x1F900 <= ord(text[j]) <= 0x1F9FF) or
                            (0xFE00 <= ord(text[j]) <= 0xFE0F) or
                            (0x1F3FB <= ord(text[j]) <= 0x1F3FF)
                    ):
                        emoji_length += 1
                        j += 1

        # 获取当前字符或表情
        current_char = text[i:i + emoji_length]
        test_line = current_line + current_char

        # 检查添加后是否超出宽度
        if get_string_width(c, test_line, font_name, font_size) <= max_width:
            current_line = test_line
        else:
            # 超出宽度，先添加当前行
            if current_line:
                lines.append(current_line)

            # 判断单个表情符号是否超出最大宽度
            if get_string_width(c, current_char, font_name, font_size) > max_width:
                # 如果单个表情符号超出宽度，拆分为多行
                print_info(f"⚠️ 表情符号 {repr(current_char)} 宽度超出限制，单独占一行")
                lines.append(current_char)
                current_line = ""
            else:
                # 否则开始新行
                current_line = current_char

        # 移动到下一个字符或表情
        i += emoji_length

    # 添加最后一行
    if current_line:
        lines.append(current_line)

    return lines


def get_real_media_path(src_path, media_type="image", user_id=""):
    """
    查找媒体文件的实际路径，处理各种可能的路径格式和位置

    参数:
        src_path: 原始媒体路径
        media_type: 媒体类型("image", "voice", "video", "emoji", "file")
        user_id: 用户ID

    返回:
        实际媒体文件路径，未找到则返回None
    """
    if not src_path:
        print_info(f"⚠️ {media_type}的媒体路径为空")
        return None
    if src_path.startswith(("http://", "https://")):
        print_info(f"✅ 媒体路径是URL: {src_path}")
        return src_path

    # 根据媒体类型确定目录名
    type_dir = {"image": "img", "voice": "audio", "video": "video", "emoji": "emoji", "file": "file"}.get(media_type,
                                                                                                          "")
    possible_paths = []
    filename = os.path.basename(src_path)

    # 为语音文件添加详细日志
    if media_type in ("voice", "audio"):
        print_info(f"🔍 查找语音文件: 源路径={src_path}, 用户ID={user_id}, 文件名={filename}")

    # 处理聊天室带反斜杠的路径
    if "\\" in src_path and "@chatroom" in src_path:
        parts = src_path.split("\\")
        if len(parts) == 2:
            chatroom_id, filename = parts
            possible_paths.extend([
                os.path.join(user_id, type_dir, chatroom_id, filename),
                os.path.join(chatroom_id, filename),
                os.path.join(user_id, chatroom_id, filename),
                src_path.replace("\\", "/")
            ])

    # 处理带日期模式或简单反斜杠路径
    elif "\\" in src_path:
        normalized_path = src_path.replace("\\", "/")
        possible_paths.append(normalized_path)
        if re.search(r'\d{4}-\d{2}-\d{2}', normalized_path) and media_type in ("voice", "audio"):
            possible_paths.append(os.path.join(user_id, type_dir, normalized_path))
            filename = os.path.basename(normalized_path)
            possible_paths.append(os.path.join(user_id, type_dir, filename))
            if "/" in normalized_path:
                chat_id = normalized_path.split("/")[0]
                possible_paths.append(os.path.join(user_id, type_dir, chat_id, filename))

    # 处理FileStorage路径
    elif "FileStorage" in src_path:
        base_name = os.path.basename(src_path)
        if media_type == "image":
            possible_paths.extend([
                os.path.join(user_id, "img", "FileStorage", "MsgAttach", base_name),
                os.path.join(user_id, "img", "FileStorage", "Image", base_name),
                os.path.join("img", "FileStorage", "MsgAttach", base_name),
                os.path.join("img", "FileStorage", "Image", base_name)
            ])
        elif media_type == "video":
            possible_paths.extend([
                os.path.join(user_id, "video", "FileStorage", "MsgAttach", base_name),
                os.path.join(user_id, "video", "FileStorage", "Video", base_name),
                os.path.join("video", "FileStorage", "MsgAttach", base_name),
                os.path.join("video", "FileStorage", "Video", base_name)
            ])
        elif media_type in ("voice", "audio"):
            possible_paths.extend([
                os.path.join(user_id, "audio", "FileStorage", "MsgAttach", base_name),
                os.path.join(user_id, "audio", "FileStorage", "Voice", base_name),
                os.path.join(user_id, "voice", "FileStorage", "MsgAttach", base_name),
                os.path.join(user_id, "voice", "FileStorage", "Voice", base_name),
                os.path.join("audio", "FileStorage", "MsgAttach", base_name),
                os.path.join("audio", "FileStorage", "Voice", base_name),
                os.path.join("voice", "FileStorage", "MsgAttach", base_name),
                os.path.join("voice", "FileStorage", "Voice", base_name)
            ])
        elif media_type == "file":
            possible_paths.extend([
                os.path.join(user_id, "file", "FileStorage", "MsgAttach", base_name),
                os.path.join(user_id, "file", "FileStorage", "File", base_name),
                os.path.join("file", "FileStorage", "MsgAttach", base_name),
                os.path.join("file", "FileStorage", "File", base_name)
            ])

        # 添加针对FileStorage的递归glob搜索
        if media_type in ("image", "video", "voice", "audio"):
            import glob
            if os.path.exists(type_dir) and os.path.exists(os.path.join(type_dir, "FileStorage")):
                # 在MsgAttach中搜索
                pattern = os.path.join(type_dir, "FileStorage", "MsgAttach", "**", base_name)
                matching_files = glob.glob(pattern, recursive=True)
                possible_paths.extend(matching_files)
                # 在Audio/Video/Image子目录中搜索
                if media_type in ("voice", "audio"):
                    type_subdir = "Voice"
                else:
                    type_subdir = media_type.capitalize()
                pattern = os.path.join(type_dir, "FileStorage", type_subdir, "**", base_name)
                matching_files = glob.glob(pattern, recursive=True)
                possible_paths.extend(matching_files)

    # 添加基本路径
    possible_paths.extend([
        src_path,
        os.path.join(user_id, type_dir, src_path),
        os.path.join(user_id, type_dir, filename),
        os.path.join(type_dir, src_path)
    ])

    # 基于glob的图像和视频搜索
    if not any(os.path.exists(p) for p in possible_paths) and filename:
        import glob
        filename_pattern = re.sub(r'(\d+)-(\d+)-(\d+)', r'\1-\2-\3', filename)
        patterns = []
        if media_type == "image":
            patterns = [
                f"**/*{filename_pattern.replace('.dat', '')}*.jpg",
                f"**/{filename_pattern}",
                f"**/{filename_pattern}_*.jpg",
                f"**/{filename_pattern}.jpg",
                f"**/{filename_pattern}.png",
                f"**/img/**/{filename_pattern}",
                f"**/img/**/Image/**/{filename_pattern}"
            ]
        elif media_type == "video":
            patterns = [
                f"**/*{filename_pattern}*",
                f"**/video/**/{filename_pattern}",
                f"**/video/**/Video/**/{filename_pattern}",
                f"**/video/**/MsgAttach/**/{filename_pattern}"
            ]
        elif media_type in ("voice", "audio"):
            patterns = [
                f"**/*{filename_pattern}*",
                f"**/audio/**/{filename_pattern}",
                f"**/voice/**/{filename_pattern}",
                f"**/audio/**/Voice/**/{filename_pattern}",
                f"**/voice/**/Voice/**/{filename_pattern}",
                f"**/audio/**/MsgAttach/**/{filename_pattern}",
                f"**/voice/**/MsgAttach/**/{filename_pattern}",
                f"**/*.amr",  # 常见的微信语音格式
                f"**/*.mp3",
                f"**/*.silk",
                f"**/*.wav"
            ]

        for pattern in patterns:
            matching_files = glob.glob(pattern, recursive=True)
            possible_paths.extend(matching_files)

    # 检查所有可能的路径
    for path in possible_paths:
        if os.path.exists(path):
            print_info(f"✅ 找到媒体文件: {path}")
            return path

    # 针对语音文件的最后尝试
    if media_type in ("voice", "audio") and filename:
        print_info(f"⚠️ 未找到语音文件，尝试额外搜索...")
        # 在更多位置搜索
        import glob
        try:
            additional_patterns = [
                f"**/{filename}.*",
                f"**/*{filename}*",
                f"**/audio/**/{filename}.*",
                f"**/voice/**/{filename}.*",
                f"**/*.amr",  # 常见的微信语音格式
                f"**/*.silk"
            ]
            for pattern in additional_patterns:
                matching_files = glob.glob(pattern, recursive=True)
                if matching_files:
                    first_match = matching_files[0]
                    print_info(f"✅ 通过额外搜索找到语音文件: {first_match}")
                    return first_match
        except Exception as e:
            print_info(f"⚠️ 额外搜索失败: {e}")

    print_info(f"⚠️ 未找到媒体文件: {src_path}")
    if DEBUG_MODE or media_type in ("voice", "audio"):
        print_info(f"  尝试过的路径: {possible_paths}")
    return None


def extract_video_thumbnail(video_path):
    """
    从视频中提取缩略图

    参数:
        video_path: 视频文件路径

    返回:
        缩略图路径，如果提取失败则返回None
    """
    if not video_path or not os.path.exists(video_path) or not FFMPEG_AVAILABLE:
        print_info(f"⚠️ 无法提取缩略图: {video_path}")
        print_info(
            f"原因: {'文件缺失' if not os.path.exists(video_path) else ''} {'FFmpeg不可用' if not FFMPEG_AVAILABLE else ''}")
        return None

    # 使用缓存，避免重复提取
    thumbnail_path = video_path + "_thumb.jpg"
    if os.path.exists(thumbnail_path):
        print_info(f"✅ 使用缓存的缩略图: {thumbnail_path}")
        return thumbnail_path

    try:
        # 尝试在不同时间点提取缩略图，增加成功率
        for seek_time in ["00:00:00", "00:00:01"]:
            cmd = [
                FFMPEG_PATH, "-y", "-i", video_path, "-ss", seek_time,
                "-vframes", "1", "-f", "image2", "-q:v", "2", thumbnail_path
            ]
            print_info(f"🔄 执行FFmpeg命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, text=True,
                                    encoding='utf-8', errors='ignore')

            # 检查是否成功提取
            if result.returncode == 0 and os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
                print_info(f"✅ 成功提取缩略图: {thumbnail_path}")
                return thumbnail_path

            print_info(f"⚠️ 缩略图提取失败(时间点 {seek_time}):")
            print_info(f"FFmpeg 标准输出: {result.stdout}")
            print_info(f"FFmpeg 错误输出: {result.stderr}")

        print_info("⚠️ 多次尝试后，缩略图提取仍然失败")
        return None

    except subprocess.TimeoutExpired:
        print_info(f"⚠️ 缩略图提取超时: {video_path}")
        return None
    except Exception as e:
        print_info(f"⚠️ 缩略图提取错误: {e}")
        return None


def load_funasr_model():
    """
    加载FunASR语音识别模型
    返回:
        加载的模型，如果加载失败则返回None
    """
    global funasr_model, FUNASR_AVAILABLE
    if not FUNASR_AVAILABLE:
        logger.warning("FunASR不可用，跳过模型加载")
        return None

    if funasr_model is None:
        logger.info("正在加载FunASR模型(paraformer-zh)...")
        try:
            # 尝试使用GPU，如果失败则回退到CPU
            try:
                funasr_model = AutoModel(
                    model="paraformer-zh",  # 中文语音识别模型
                    vad_model="fsmn-vad",  # 语音活动检测模型
                    punc_model="ct-punc",  # 标点符号恢复模型
                    spk_model="cam++",  # 说话人识别模型
                    device="cuda",  # 使用GPU加速
                    disable_update=True  # 禁用自动更新
                )
                logger.info("已使用GPU加载FunASR模型")
            except Exception as gpu_err:
                logger.warning(f"GPU加载FunASR模型失败: {gpu_err}，尝试使用CPU")
                funasr_model = AutoModel(
                    model="paraformer-zh",
                    vad_model="fsmn-vad",
                    punc_model="ct-punc",
                    spk_model="cam++",
                    device="cpu",  # 回退到CPU
                    disable_update=True
                )
                logger.info("已使用CPU加载FunASR模型")
        except Exception as e:
            logger.error(f"FunASR模型加载失败: {e}")
            FUNASR_AVAILABLE = False
            return None
    return funasr_model


def convert_traditional_to_simplified(text):
    """
    将繁体中文转换为简体中文

    参数:
        text: 需要转换的文本
    返回:
        转换后的简体中文文本
    """
    try:
        import opencc
        converter = opencc.OpenCC('t2s')
        return converter.convert(text)
    except ImportError:
        logger.warning("未安装opencc模块，无法进行繁简转换")
        return text
    except Exception as e:
        logger.error(f"繁简转换出错: {e}")
        return text


def transcribe_with_funasr(audio_path, duration=None):
    """
    使用FunASR进行语音识别转写

    参数:
        audio_path: 音频文件路径
        duration: 音频时长(秒)，用于显示进度
    返回:
        转写结果文本
    """
    global FUNASR_AVAILABLE

    if not os.path.exists(audio_path):
        logger.error(f"音频文件不存在: {audio_path}")
        return "【语音识别失败：文件不存在】"

    if not FUNASR_AVAILABLE:
        logger.warning("FunASR不可用，无法进行语音识别")
        return "【语音未能识别：FunASR不可用】"

    model = load_funasr_model()
    if model is None:
        return "【语音未能识别：模型加载失败】"

    try:
        # 创建缓存目录
        os.makedirs(SPEECH_CACHE_DIR, exist_ok=True)

        # 计算文件哈希值作为缓存键
        file_hash = get_file_hash(audio_path)
        cache_file = os.path.join(SPEECH_CACHE_DIR, f"{file_hash}.txt")

        # 检查缓存
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                logger.info(f"从缓存读取转写结果: {audio_path}")
                return f.read().strip()

        # 未缓存，进行转写
        if duration:
            logger.info(f"正在转写音频: {audio_path} (时长 {duration:.1f}秒)")
        else:
            logger.info(f"正在转写音频: {audio_path}")

        result = model.generate(audio_path)

        # 提取并处理转写文本
        if result and len(result) > 0 and "text" in result[0]:
            transcript = result[0]["text"].strip()
            # 移除繁体转简体处理，因为FunASR输出已经是简体
            # transcript = convert_traditional_to_simplified(transcript)  # 繁转简

            # 缓存结果
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(transcript)

            logger.info(f"转写成功: '{transcript[:30]}...'")
            return transcript
        else:
            logger.warning(f"转写结果为空: {audio_path}")
            return "【语音未能识别】"

    except Exception as e:
        logger.error(f"转写音频出错: {e}")
        return f"【语音识别错误: {str(e)}】"


def get_audio_duration(audio_path):
    """
    获取音频文件的时长

    参数:
        audio_path: 音频文件路径
    返回:
        音频时长(秒)，如果出错则返回None
    """
    if not os.path.exists(audio_path):
        logger.error(f"音频文件不存在: {audio_path}")
        return None

    try:
        # 使用ffprobe获取音频时长
        ffprobe_path = os.path.join(os.path.dirname(FFMPEG_PATH), "ffprobe")
        if platform.system() == "Windows":
            ffprobe_path += ".exe"

        if not os.path.exists(ffprobe_path):
            logger.warning(f"ffprobe不存在: {ffprobe_path}")
            return None

        cmd = [ffprobe_path, "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
        result = subprocess.check_output(cmd, universal_newlines=True).strip()
        duration = float(result)
        return duration
    except Exception as e:
        logger.error(f"获取音频时长出错: {e}")
        return None


def format_voice_message(duration):
    """
    格式化语音消息的时长显示

    参数:
        duration: 语音时长(秒)
    返回:
        格式化的时长字符串，如"0:08"
    """
    if duration is None:
        return "语音"

    try:
        minutes = int(duration) // 60
        seconds = int(duration) % 60
        return f"{minutes}:{seconds:02d}"
    except Exception as e:
        logger.error(f"格式化语音时长出错: {e}")
        return "语音"


def transcribe_audio(audio_path, duration=None):
    """
    转写音频文件为文本

    参数:
        audio_path: 音频文件路径
        duration: 音频时长(秒)
    返回:
        转写结果和持续时间的元组(transcript, duration_display)
    """
    if not os.path.exists(audio_path):
        logger.error(f"音频文件不存在: {audio_path}")
        return "【语音识别失败：文件不存在】", "语音"

    if duration is None:
        duration = get_audio_duration(audio_path)

    transcript = transcribe_with_funasr(audio_path, duration)
    duration_display = format_voice_message(duration)

    return transcript, duration_display


def download_media_file(src_path, media_type="image", msg_id=None, user_id=""):
    """
    下载或复制媒体文件到缓存目录

    参数:
        src_path: 媒体文件源路径或URL
        media_type: 媒体类型("image", "video", "voice", "emoji", "file")
        msg_id: 消息ID，用于生成唯一文件名
        user_id: 用户ID

    返回:
        缓存的媒体文件路径，如果失败则返回None
    """
    if not src_path:
        print_info(f"⚠️ {media_type}的源路径为空")
        return None

    # 为语音消息添加额外的调试信息
    if media_type in ("voice", "audio"):
        print_info(f"🔍 尝试下载语音文件: src={src_path}, msg_id={msg_id}, user_id={user_id}")

    # 生成缓存文件路径
    msg_id = msg_id or hashlib.md5(src_path.encode()).hexdigest()
    ext = {"image": ".jpg", "video": ".mp4", "voice": ".wav", "emoji": ".gif", "file": ""}.get(media_type, ".dat")
    cache_file = os.path.join(MEDIA_CACHE_DIR, f"{media_type}_{msg_id}{ext}")

    # 检查缓存
    if os.path.exists(cache_file):
        if media_type in ("voice", "audio"):
            print_info(f"✅ 使用缓存的语音文件: {cache_file}")
        else:
            print_info(f"✅ 使用缓存的媒体文件: {cache_file}")
        return cache_file

    # 处理URL
    if src_path.startswith(("http://", "https://")):
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124'}
            req = urllib.request.Request(src_path, headers=headers)
            print_info(f"🔄 从URL下载媒体: {src_path}")

            with urllib.request.urlopen(req, timeout=10) as response:
                # 检查内容类型
                content_type = response.getheader('Content-Type', '')
                if media_type == "image" and not content_type.startswith('image/'):
                    print_info(f"⚠️ URL不是图片: {src_path}, Content-Type: {content_type}")
                    return None
                if media_type == "video" and not content_type.startswith('video/'):
                    print_info(f"⚠️ URL不是视频: {src_path}, Content-Type: {content_type}")
                    return None

                # 下载文件
                with open(cache_file, 'wb') as f:
                    f.write(response.read())

            # 验证下载的文件
            if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
                print_info(f"✅ 媒体下载成功: {cache_file}")
                return cache_file

            print_info(f"⚠️ 下载的文件为空或丢失: {cache_file}")
            return None

        except Exception as e:
            print_info(f"⚠️ 下载失败: {src_path}, 错误: {e}")
            return None

    # 查找本地文件
    real_path = get_real_media_path(src_path, media_type, user_id)
    if real_path and os.path.exists(real_path):
        # 复制文件到缓存
        import shutil
        shutil.copy2(real_path, cache_file)
        print_info(f"✅ 已复制媒体文件: {cache_file}")
        return cache_file

    print_info(f"⚠️ 未找到{media_type}媒体文件: {src_path}")
    return None


def draw_message(c, msg, users, y_pos, text_max_width, width, avatar_cache={}, enable_speech_to_text=True):
    try:
        sender = msg.get("talker", "Unknown")
        sender_info = users.get(sender, {})
        sender_name = sender_info.get("remark") or sender_info.get("nickname") or sender
        timestamp = format_timestamp(msg.get("CreateTime", ""))
        msg_type = msg.get("type_name", "文本")
        msg_text = msg.get("msg", "")
        msg_src = msg.get("src", "")
        msg_id = msg.get("id", "")
        user_id = sender
        is_self = msg.get("is_sender", 0) == 1

        # 减少详细日志，只显示消息类型
        if DEBUG_MODE:
            print_info(f"🔄 Processing {msg_type} message: {msg_text[:30]}{'...' if len(msg_text) > 30 else ''}")

        margin_bottom = 10 * mm
        if msg_type == "系统通知":
            if y_pos < margin_bottom + 10 * mm:
                c.showPage()
                y_pos = A4[1] - 30 * mm
            c.setFillColor(colors.grey)
            c.setFont("SimSun", 9, leading=None)
            c.drawCentredString(width / 2, y_pos, msg_text)
            return y_pos - 10 * mm

        bubble_padding = 3 * mm
        line_height = 5 * mm
        avatar_size = 10 * mm
        message_height = 0

        if msg_type == "文本":
            # 处理文本中的表情符号
            processed_text = msg_text
            emoji_count = 0
            for emoji, unicode_emoji in EMOJI_MAP.items():
                if emoji in processed_text:
                    processed_text = processed_text.replace(emoji, unicode_emoji)
                    emoji_count += 1

            if emoji_count > 0:
                print_info(f"✅ 处理了 {emoji_count} 个表情符号")

            # 检测文本中是否含有Unicode表情符号
            has_unicode_emoji = False
            for char in processed_text:
                if (
                        (0x1F600 <= ord(char) <= 0x1F64F) or  # 表情符号
                        (0x1F300 <= ord(char) <= 0x1F5FF) or  # 杂项符号和象形文字
                        (0x1F680 <= ord(char) <= 0x1F6FF) or  # 交通和地图符号
                        (0x2600 <= ord(char) <= 0x26FF) or  # 杂项符号
                        (0x2700 <= ord(char) <= 0x27BF) or  # 装饰符号
                        (0x1F900 <= ord(char) <= 0x1F9FF)  # 补充符号和象形文字
                ):
                    has_unicode_emoji = True
                    break

            if has_unicode_emoji:
                print_info("✅ 检测到Unicode表情符号，将使用特殊渲染")

            text_lines = wrap_text(c, processed_text, "SimSun", 10, text_max_width * 0.8) or ["[空消息]"]
            bubble_width = min(text_max_width * 0.8,
                               max(get_string_width(c, line, "SimSun", 10) for line in text_lines)) + 2 * bubble_padding
            bubble_height = len(text_lines) * line_height + 2 * bubble_padding
            message_height = max(bubble_height, avatar_size) + 12 * mm
        elif msg_type == "图片":
            message_height = 80 * mm + 12 * mm
        elif msg_type in ["视频", "动画表情"]:
            message_height = 60 * mm + 12 * mm
        elif msg_type == "语音":
            message_height = 30 * mm + 10 * mm
        elif "文件" in msg_type:
            message_height = 30 * mm + 10 * mm
        else:
            message_height = 30 * mm + 10 * mm

        if y_pos - message_height < margin_bottom:
            c.showPage()
            y_pos = A4[1] - 30 * mm

        c.setFillColor(colors.grey)
        try:
            c.setFont("SimSun", 8, leading=None)
        except:
            c.setFont("Helvetica", 8, leading=None)
        c.drawCentredString(width / 2, y_pos, timestamp)
        y_pos -= 5 * mm

        avatar_x = width - 20 * mm - avatar_size if is_self else 20 * mm
        c.setFillColor(colors.grey)
        try:
            c.setFont("SimSun", 8, leading=None)
        except:
            c.setFont("Helvetica", 8, leading=None)
        bubble_fill_color = colors.Color(0.8, 1, 0.6) if is_self else colors.white
        bubble_border_color = colors.Color(0.6, 0.8, 0.6) if is_self else colors.Color(0.8, 0.8, 0.8)
        if is_self:
            c.drawRightString(avatar_x, y_pos + 5 * mm, sender_name)
        else:
            c.drawString(avatar_x + avatar_size + 3 * mm, y_pos + 5 * mm, sender_name)

        avatar_y = y_pos - avatar_size
        if avatar_img := get_avatar_image(sender_info.get("headImgUrl", "")):
            c.drawImage(avatar_img, avatar_x, avatar_y, width=avatar_size, height=avatar_size, mask='auto')
        else:
            c.setFillColor(colors.white)
            c.rect(avatar_x, avatar_y, avatar_size, avatar_size, fill=1)
            c.setFillColor(colors.black)
            try:
                c.setFont("SimSun", 8, leading=None)
            except:
                c.setFont("Helvetica", 8, leading=None)
            c.drawCentredString(avatar_x + avatar_size / 2, avatar_y + avatar_size / 2 - 2 * mm,
                                sender_name[0] if sender_name else "?")

        if msg_type == "文本":
            text_lines = wrap_text(c, processed_text, "SimSun", 10, text_max_width * 0.8) or ["[空消息]"]
            bubble_width = min(text_max_width * 0.8,
                               max(get_string_width(c, line, "SimSun", 10) for line in text_lines)) + 2 * bubble_padding
            bubble_height = len(text_lines) * line_height + 2 * bubble_padding
            bubble_x = width - 20 * mm - bubble_width - avatar_size - 3 * mm if is_self else 20 * mm + avatar_size + 3 * mm

            c.setLineWidth(0.5)
            c.setStrokeColor(bubble_border_color)
            c.setFillColor(bubble_fill_color)
            c.roundRect(bubble_x, y_pos - bubble_height, bubble_width, bubble_height, 3 * mm, stroke=1, fill=1)

            c.setFillColor(colors.Color(0.2, 0.2, 0.2))

            # 绘制每一行文本
            for i, line in enumerate(text_lines):
                y_position = y_pos - bubble_padding - (i + 1) * line_height
                current_x = bubble_x + bubble_padding

                # 处理每行中的普通文本和表情符号
                skip_chars = 0
                for j, char in enumerate(line):
                    if skip_chars > 0:
                        skip_chars -= 1
                        continue

                    # 检查是否为emoji (通过Unicode范围)
                    is_emoji = False
                    emoji_width = 0

                    # 判断是否为emoji字符
                    # emoji通常在以下Unicode范围内:
                    # - 基本emoji: U+1F600-U+1F64F
                    # - 其他emoji: U+1F300-U+1F5FF, U+1F680-U+1F6FF, U+2600-U+26FF
                    if (
                            (0x1F600 <= ord(char) <= 0x1F64F) or  # 表情符号
                            (0x1F300 <= ord(char) <= 0x1F5FF) or  # 杂项符号和象形文字
                            (0x1F680 <= ord(char) <= 0x1F6FF) or  # 交通和地图符号
                            (0x2600 <= ord(char) <= 0x26FF) or  # 杂项符号
                            (0x2700 <= ord(char) <= 0x27BF) or  # 装饰符号
                            (0x1F900 <= ord(char) <= 0x1F9FF)  # 补充符号和象形文字
                    ):
                        is_emoji = True

                    # 检查是否为变体选择器
                    if j < len(line) - 1 and (0xFE00 <= ord(line[j + 1]) <= 0xFE0F):
                        emoji_width = 1  # 加上变体选择器

                    # 检查是否为肤色修饰符 (U+1F3FB - U+1F3FF)
                    if j < len(line) - 1 and (0x1F3FB <= ord(line[j + 1]) <= 0x1F3FF):
                        emoji_width = 1  # 加上肤色修饰符

                    # 检查是否为零宽连接符 (ZWJ, U+200D)
                    if j < len(line) - 2 and ord(line[j + 1]) == 0x200D:
                        # 检查后面是否还有字符可以连接
                        k = j + 2
                        while k < len(line) and (
                                (0x1F600 <= ord(line[k]) <= 0x1F64F) or
                                (0x1F300 <= ord(line[k]) <= 0x1F5FF) or
                                (0x1F680 <= ord(line[k]) <= 0x1F6FF) or
                                (0x2600 <= ord(line[k]) <= 0x26FF) or
                                (0x2700 <= ord(line[k]) <= 0x27BF) or
                                (0x1F900 <= ord(line[k]) <= 0x1F9FF) or
                                ord(line[k]) == 0x200D or
                                (0xFE00 <= ord(line[k]) <= 0xFE0F) or
                                (0x1F3FB <= ord(line[k]) <= 0x1F3FF)
                        ):
                            emoji_width += 1
                            k += 1

                    # 根据字符类型选择字体并绘制
                    if is_emoji:
                        emoji_text = line[j:j + emoji_width + 1]
                        skip_chars = emoji_width

                        # 尝试使用Emoji字体
                        try:
                            # 对于彩色表情，设置更大的字体大小以确保完整显示
                            emoji_font_size = 12  # 稍微大一点的字体大小，确保表情显示完整
                            c.setFont("EmojiFont", emoji_font_size, leading=None)

                            # 计算emoji宽度用于定位
                            try:
                                emoji_char_width = c.stringWidth(emoji_text, "EmojiFont", emoji_font_size)
                            except Exception:
                                emoji_char_width = emoji_font_size  # 默认宽度

                            # 尝试使用比文本底线略低的位置绘制表情，使其与文本更协调
                            emoji_y_offset = -1  # 微调表情的垂直位置
                            c.drawString(current_x, y_position + emoji_y_offset, emoji_text)

                            print_info(f"✅ 绘制表情: {repr(emoji_text)}")
                        except Exception as e:
                            print_info(f"⚠️ Emoji绘制错误，尝试备用方案: {e}")
                            # 如果Emoji字体失败，回退到SimSun
                            try:
                                c.setFont("SimSun", 10, leading=None)
                                c.drawString(current_x, y_position, emoji_text)
                            except Exception:
                                # 如果SimSun也失败，使用Helvetica
                                c.setFont("Helvetica", 10, leading=None)
                                c.drawString(current_x, y_position, emoji_text)

                        # 更新位置
                        current_x += max(emoji_char_width, emoji_font_size)  # 确保至少有emoji_font_size的宽度
                    else:
                        # 普通文本使用SimSun字体
                        try:
                            c.setFont("SimSun", 10, leading=None)
                        except Exception:
                            c.setFont("Helvetica", 10, leading=None)

                        c.drawString(current_x, y_position, char)
                        try:
                            char_width = c.stringWidth(char, c._fontname, 10)
                        except Exception:
                            char_width = 10  # 默认宽度
                        current_x += char_width

            return y_pos - max(bubble_height, avatar_size) - 8 * mm

        elif msg_type == "图片":
            img_path = download_media_file(msg_src, "image", msg_id, user_id)
            if img_path and os.path.exists(img_path):
                try:
                    # 压缩图像以减小PDF大小
                    compressed_img_path = compress_image(img_path)
                    img = Image.open(compressed_img_path)
                    img_width, img_height = img.size
                    max_img_width = text_max_width * 0.6
                    max_img_height = 80 * mm
                    ratio = min(max_img_width / img_width, max_img_height / img_height)
                    new_width = img_width * ratio
                    new_height = img_height * ratio
                    bubble_width = new_width + 4 * bubble_padding
                    bubble_height = new_height + 4 * bubble_padding
                    bubble_x = width - 20 * mm - bubble_width - avatar_size - 3 * mm if is_self else 20 * mm + avatar_size + 3 * mm

                    c.setLineWidth(0.5)
                    c.setStrokeColor(bubble_border_color)
                    c.setFillColor(bubble_fill_color)
                    c.roundRect(bubble_x, y_pos - bubble_height, bubble_width, bubble_height, 3 * mm, stroke=1, fill=1)

                    img_x = bubble_x + (bubble_width - new_width) / 2
                    img_y = y_pos - bubble_height + (bubble_height - new_height) / 2
                    c.drawImage(compressed_img_path, img_x, img_y, width=new_width, height=new_height, mask='auto')
                    print_info(f"✅ Image rendered: {compressed_img_path}")
                    return y_pos - max(bubble_height, avatar_size) - 8 * mm
                except Exception as e:
                    print_info(f"⚠️ Image drawing error: {e}")
                    img_path = None

            print_info("🔄 Image not found or failed; rendering [图片]")
            text_lines = wrap_text(c, "[图片]", "SimSun", 10, text_max_width * 0.8)
            bubble_width = min(text_max_width * 0.8,
                               max(get_string_width(c, line, "SimSun", 10) for line in text_lines)) + 2 * bubble_padding
            bubble_height = len(text_lines) * line_height + 2 * bubble_padding
            bubble_x = width - 20 * mm - bubble_width - avatar_size - 3 * mm if is_self else 20 * mm + avatar_size + 3 * mm

            c.setLineWidth(0.5)
            c.setStrokeColor(bubble_border_color)
            c.setFillColor(bubble_fill_color)
            c.roundRect(bubble_x, y_pos - bubble_height, bubble_width, bubble_height, 3 * mm, stroke=1, fill=1)

            c.setFillColor(colors.Color(0.2, 0.2, 0.2))
            try:
                c.setFont("SimSun", 10, leading=None)
            except:
                c.setFont("Helvetica", 10, leading=None)
            for i, line in enumerate(text_lines):
                c.drawString(bubble_x + bubble_padding, y_pos - bubble_padding - (i + 1) * line_height, line)
            return y_pos - max(bubble_height, avatar_size) - 8 * mm

        elif msg_type == "语音":
            print_info(f"🔄 处理语音消息: src={msg_src}, sender={sender}, is_self={is_self}")
            voice_path = download_media_file(msg_src, "voice", msg_id, user_id)
            transcribed_text = "📢[语音消息]"
            if voice_path and os.path.exists(voice_path) and enable_speech_to_text:
                print_info(f"✅ 找到语音文件: {voice_path}, 开始转写...")
                transcript, duration_display = transcribe_audio(voice_path)
                # 添加明显标识，表示这是语音转文本
                transcribed_text = f"📢语音转文本 ({duration_display}):\n{transcript}"
                print_info(f"✅ 语音转写结果: {transcribed_text}")
            else:
                print_info(
                    f"⚠️ 语音文件未找到或无法转写: path={voice_path}, enable_speech_to_text={enable_speech_to_text}")
            # 增强文本宽度控制，使用更小的最大宽度来确保不会超出气泡
            text_lines = wrap_text(c, transcribed_text, "SimSun", 10, text_max_width * 0.6) or ["🔊 [语音消息]"]
            bubble_width = min(text_max_width * 0.7,
                               max(get_string_width(c, line, "SimSun", 10) for line in text_lines)) + 2 * bubble_padding
            bubble_height = len(text_lines) * line_height + 2 * bubble_padding
            bubble_x = width - 20 * mm - bubble_width - avatar_size - 3 * mm if is_self else 20 * mm + avatar_size + 3 * mm

            # 使用不同的气泡背景色表示这是语音消息
            voice_bubble_fill_color = colors.Color(0.9, 0.9, 1.0) if is_self else colors.Color(0.95, 0.95, 1.0)
            voice_bubble_border_color = colors.Color(0.7, 0.7, 0.9) if is_self else colors.Color(0.8, 0.8, 0.9)

            c.setLineWidth(0.5)
            c.setStrokeColor(voice_bubble_border_color)
            c.setFillColor(voice_bubble_fill_color)
            c.roundRect(bubble_x, y_pos - bubble_height, bubble_width, bubble_height, 3 * mm, stroke=1, fill=1)

            c.setFillColor(colors.Color(0.2, 0.2, 0.2))
            try:
                c.setFont("SimSun", 10, leading=None)
            except:
                c.setFont("Helvetica", 10, leading=None)
            for i, line in enumerate(text_lines):
                c.drawString(bubble_x + bubble_padding, y_pos - bubble_padding - (i + 1) * line_height, line)
            return y_pos - max(bubble_height, avatar_size) - 8 * mm

        elif msg_type == "视频":
            video_path = download_media_file(msg_src, "video", msg_id, user_id)
            thumbnail_path = extract_video_thumbnail(video_path) if video_path and os.path.exists(video_path) else None
            if thumbnail_path and os.path.exists(thumbnail_path):
                try:
                    # 压缩视频缩略图
                    compressed_thumbnail_path = compress_image(thumbnail_path)
                    img = Image.open(compressed_thumbnail_path)
                    img_width, img_height = img.size
                    max_img_width = text_max_width * 0.6
                    max_img_height = 80 * mm
                    ratio = min(max_img_width / img_width, max_img_height / img_height)
                    new_width = img_width * ratio
                    new_height = img_height * ratio
                    bubble_width = new_width + 4 * bubble_padding
                    bubble_height = new_height + 4 * bubble_padding + 5 * mm
                    bubble_x = width - 20 * mm - bubble_width - avatar_size - 3 * mm if is_self else 20 * mm + avatar_size + 3 * mm

                    c.setLineWidth(0.5)
                    c.setStrokeColor(bubble_border_color)
                    c.setFillColor(bubble_fill_color)
                    c.roundRect(bubble_x, y_pos - bubble_height, bubble_width, bubble_height, 3 * mm, stroke=1, fill=1)

                    img_x = bubble_x + (bubble_width - new_width) / 2
                    img_y = y_pos - bubble_height + (bubble_height - new_height) / 2
                    c.drawImage(compressed_thumbnail_path, img_x, img_y, width=new_width, height=new_height,
                                mask='auto')

                    c.setFillColor(colors.white.clone(alpha=0.7))
                    c.circle(bubble_x + bubble_width / 2, img_y + new_height / 2, 8 * mm, fill=1)
                    c.setFillColor(colors.black)
                    try:
                        c.setFont("SimSun", 12, leading=None)
                    except:
                        c.setFont("Helvetica", 12, leading=None)
                    c.drawString(bubble_x + bubble_width / 2 - 2 * mm, img_y + new_height / 2 - 2 * mm, "▶")
                    try:
                        c.setFont("SimSun", 8, leading=None)
                    except:
                        c.setFont("Helvetica", 8, leading=None)
                    c.drawString(bubble_x + bubble_padding, y_pos - bubble_height + 2 * mm, "[视频消息]")
                    print_info(f"✅ Video thumbnail rendered: {compressed_thumbnail_path}")
                    return y_pos - max(bubble_height, avatar_size) - 8 * mm
                except Exception as e:
                    print_info(f"⚠️ Video thumbnail error: {e}")
                    thumbnail_path = None

            print_info("🔄 Video thumbnail not found or failed; rendering [视频消息]")
            text_lines = wrap_text(c, "[视频消息]", "SimSun", 10, text_max_width * 0.8)
            bubble_width = min(text_max_width * 0.8,
                               max(get_string_width(c, line, "SimSun", 10) for line in text_lines)) + 2 * bubble_padding
            bubble_height = len(text_lines) * line_height + 2 * bubble_padding
            bubble_x = width - 20 * mm - bubble_width - avatar_size - 3 * mm if is_self else 20 * mm + avatar_size + 3 * mm

            c.setLineWidth(0.5)
            c.setStrokeColor(bubble_border_color)
            c.setFillColor(bubble_fill_color)
            c.roundRect(bubble_x, y_pos - bubble_height, bubble_width, bubble_height, 3 * mm, stroke=1, fill=1)

            c.setFillColor(colors.Color(0.2, 0.2, 0.2))
            try:
                c.setFont("SimSun", 10, leading=None)
            except:
                c.setFont("Helvetica", 10, leading=None)
            for i, line in enumerate(text_lines):
                c.drawString(bubble_x + bubble_padding, y_pos - bubble_padding - (i + 1) * line_height, line)
            return y_pos - max(bubble_height, avatar_size) - 8 * mm

        elif msg_type == "动画表情":
            emoji_path = download_media_file(msg_src, "emoji", msg_id, user_id)
            if emoji_path and os.path.exists(emoji_path):
                try:
                    # 压缩表情图片
                    compressed_emoji_path = compress_image(emoji_path)
                    emoji_width = 40 * mm
                    emoji_height = 40 * mm
                    bubble_width = emoji_width + 4 * bubble_padding
                    bubble_height = emoji_height + 4 * bubble_padding
                    bubble_x = width - 20 * mm - bubble_width - avatar_size - 3 * mm if is_self else 20 * mm + avatar_size + 3 * mm

                    c.setLineWidth(0.5)
                    c.setStrokeColor(bubble_border_color)
                    c.setFillColor(bubble_fill_color)
                    c.roundRect(bubble_x, y_pos - bubble_height, bubble_width, bubble_height, 3 * mm, stroke=1, fill=1)

                    emoji_x = bubble_x + (bubble_width - emoji_width) / 2
                    emoji_y = y_pos - bubble_height + (bubble_height - emoji_height) / 2
                    c.drawImage(compressed_emoji_path, emoji_x, emoji_y, width=emoji_width, height=emoji_height,
                                mask='auto')
                    print_info(f"✅ Emoji rendered: {compressed_emoji_path}")
                    return y_pos - max(bubble_height, avatar_size) - 8 * mm
                except Exception as e:
                    print_info(f"⚠️ Emoji drawing error: {e}")
                    emoji_path = None

            print_info("🔄 Emoji not found or failed; rendering [表情消息]")
            text_lines = wrap_text(c, "[表情消息]", "SimSun", 10, text_max_width * 0.8)
            bubble_width = min(text_max_width * 0.8,
                               max(get_string_width(c, line, "SimSun", 10) for line in text_lines)) + 2 * bubble_padding
            bubble_height = len(text_lines) * line_height + 2 * bubble_padding
            bubble_x = width - 20 * mm - bubble_width - avatar_size - 3 * mm if is_self else 20 * mm + avatar_size + 3 * mm

            c.setLineWidth(0.5)
            c.setStrokeColor(bubble_border_color)
            c.setFillColor(bubble_fill_color)
            c.roundRect(bubble_x, y_pos - bubble_height, bubble_width, bubble_height, 3 * mm, stroke=1, fill=1)

            c.setFillColor(colors.Color(0.2, 0.2, 0.2))
            try:
                c.setFont("SimSun", 10, leading=None)
            except:
                c.setFont("Helvetica", 10, leading=None)
            for i, line in enumerate(text_lines):
                c.drawString(bubble_x + bubble_padding, y_pos - bubble_padding - (i + 1) * line_height, line)
            return y_pos - max(bubble_height, avatar_size) - 8 * mm

        elif "文件" in msg_type:
            file_path = download_media_file(msg_src, "file", msg_id, user_id)
            if msg_text and msg_text != "文件":
                file_name = msg_text
            elif msg_src:
                file_name = os.path.basename(msg_src)
            elif file_path:
                file_name = os.path.basename(file_path)
            else:
                file_name = "未知文件"

            display_text = f"[文件] {file_name}"
            print_info(f"🔄 Rendering file message: {display_text}, msg_text={msg_text}, msg_src={msg_src}")
            text_lines = wrap_text(c, display_text, "SimSun", 10, text_max_width * 0.8) or ["[文件]"]
            bubble_width = min(text_max_width * 0.8,
                               max(get_string_width(c, line, "SimSun", 10) for line in text_lines)) + 2 * bubble_padding
            bubble_height = len(text_lines) * line_height + 2 * bubble_padding
            bubble_x = width - 20 * mm - bubble_width - avatar_size - 3 * mm if is_self else 20 * mm + avatar_size + 3 * mm

            c.setLineWidth(0.5)
            c.setStrokeColor(bubble_border_color)
            c.setFillColor(bubble_fill_color)
            c.roundRect(bubble_x, y_pos - bubble_height, bubble_width, bubble_height, 3 * mm, stroke=1, fill=1)

            c.setFillColor(colors.Color(0.2, 0.2, 0.2))
            try:
                c.setFont("SimSun", 10, leading=None)
            except:
                c.setFont("Helvetica", 10, leading=None)
            for i, line in enumerate(text_lines):
                c.drawString(bubble_x + bubble_padding, y_pos - bubble_padding - (i + 1) * line_height, line)
            return y_pos - max(bubble_height, avatar_size) - 8 * mm

        else:
            display_text = f"[{msg_type}] {msg_text}"
            text_lines = wrap_text(c, display_text, "SimSun", 10, text_max_width * 0.8) or [f"[{msg_type}]"]
            bubble_width = min(text_max_width * 0.8,
                               max(get_string_width(c, line, "SimSun", 10) for line in text_lines)) + 2 * bubble_padding
            bubble_height = len(text_lines) * line_height + 2 * bubble_padding
            bubble_x = width - 20 * mm - bubble_width - avatar_size - 3 * mm if is_self else 20 * mm + avatar_size + 3 * mm

            c.setLineWidth(0.5)
            c.setStrokeColor(bubble_border_color)
            c.setFillColor(bubble_fill_color)
            c.roundRect(bubble_x, y_pos - bubble_height, bubble_width, bubble_height, 3 * mm, stroke=1, fill=1)

            c.setFillColor(colors.Color(0.2, 0.2, 0.2))
            try:
                c.setFont("SimSun", 10, leading=None)
            except:
                c.setFont("Helvetica", 10, leading=None)
            for i, line in enumerate(text_lines):
                c.drawString(bubble_x + bubble_padding, y_pos - bubble_padding - (i + 1) * line_height, line)
            return y_pos - max(bubble_height, avatar_size) - 8 * mm

    except Exception as e:
        print_info(f"⚠️ Message drawing error: {e}")
        if DEBUG_MODE:
            traceback.print_exc()
        return y_pos - 5 * mm


def generate_pdf(chat_file, user_file=None, output_file="we_chat.pdf", download_avatars=False,
                 enable_speech_to_text=True, image_quality=IMAGE_QUALITY):
    """
    生成微信聊天记录PDF文件

    参数:
        chat_file: 聊天记录JSON文件路径或数据
        user_file: 用户信息JSON文件路径
        output_file: 输出PDF文件路径
        download_avatars: 是否下载头像
        enable_speech_to_text: 是否启用语音转文字
        image_quality: 图像质量(1-100)，越小文件越小，质量越低
    """
    global IMAGE_QUALITY
    # 设置全局图像质量
    if image_quality != IMAGE_QUALITY and 1 <= image_quality <= 100:
        print_info(f"🔧 设置图像质量: {image_quality}")
        IMAGE_QUALITY = image_quality

    start_time = time.time()
    print_info(f"🔄 正在生成PDF: {output_file}")
    print_info(
        f"⚙️ 设置: {'下载头像' if download_avatars else '不下载头像'}, {'启用语音转文字' if enable_speech_to_text else '禁用语音转文字'}")

    # 如果启用语音转文字且FunASR可用，预加载模型
    if enable_speech_to_text and FUNASR_AVAILABLE:
        load_funasr_model()

    # 注册字体
    has_chinese_font = register_fonts()
    if not has_chinese_font:
        print_info("⚠️ 注意: 未能找到中文字体，文本可能显示不正确")

    # 检查是否存在Emoji字体
    has_emoji_font = "EmojiFont" in pdfmetrics.getRegisteredFontNames()
    if not has_emoji_font:
        print_info("⚠️ 注意: 未能找到Emoji字体，表情符号可能无法正确显示")
    else:
        print_info("✅ Emoji字体可用，表情符号将正确显示")

    # 加载聊天数据
    if isinstance(chat_file, str):
        if os.path.exists(chat_file):
            chats = load_json_file(chat_file)
            chat_filename = os.path.basename(chat_file)
        else:
            try:
                chats = json.loads(chat_file)
                chat_filename = "chat_data"
            except:
                print_info(f"❌ 无法解析聊天数据: {chat_file}")
                return
    elif isinstance(chat_file, list):
        chats = chat_file
        chat_filename = "chat_data"
    else:
        print_info("❌ 无效的聊天数据格式")
        return

    # 验证聊天数据
    if not isinstance(chats, list) or not chats:
        print_info("⚠️ 聊天数据为空或无效")
        return

    # 加载用户信息
    users = load_json_file(user_file) if user_file and os.path.exists(user_file) else {}
    if not users:
        print_info("🔄 从聊天记录中提取用户信息...")
        talker_set = {msg.get("talker", "") for msg in chats if msg.get("talker") and msg.get("talker") != "未知"}
        users = {talker: {"nickname": talker, "remark": "", "is_self": False, "headImgUrl": ""} for talker in
                 talker_set}

    # 标记自己的消息
    for wxid, user_info in users.items():
        if any(msg.get("talker") == wxid and msg.get("is_sender", 0) == 1 for msg in chats):
            user_info["is_self"] = True

    # 下载头像
    if download_avatars:
        prepare_avatars(users, download_avatars)

    print_info("🔄 预处理消息...")
    dates_dict = {}
    try:
        # 使用更快的方式排序（根据时间戳）
        chats.sort(key=lambda x: int(x.get("CreateTime", 0)) if x.get("CreateTime", "").isdigit() else 0)
    except Exception as e:
        print_info(f"⚠️ 排序错误: {e}")
        # 回退到原始排序方法
        try:
            chats.sort(key=lambda x: parse_timestamp(x.get("CreateTime", 0)) or datetime(1970, 1, 1))
        except Exception as e:
            print_info(f"⚠️ 备用排序方法也失败: {e}")

    # 一次性遍历，按日期分组消息，提高效率
    for msg in chats:
        timestamp = msg.get("CreateTime")
        if timestamp:
            date = get_date_from_timestamp(timestamp)
            if date:
                # 使用setdefault避免重复检查键是否存在
                dates_dict.setdefault(date, []).append(msg)

    if not dates_dict:
        print_info("⚠️ 无法按日期分组消息")
        return

    # 为了性能提升，创建自定义Canvas类跟踪页码
    class PageTracker(canvas.Canvas):
        """
        扩展Canvas类以跟踪页码和页面大小
        """

        def __init__(self, *args, **kwargs):
            canvas.Canvas.__init__(self, *args, **kwargs)
            self.pages = []
            self.current_page = 0

        def showPage(self):
            # 保存当前页面信息
            self.pages.append({"page_number": self._pageNumber, "page_size": self._pagesize})
            self.current_page += 1
            super(PageTracker, self).showPage()

        def save(self):
            # 保存最后一页信息并调用父类的save方法
            self.pages.append({"page_number": self._pageNumber, "page_size": self._pagesize})
            super(PageTracker, self).save()

        def getPageNumber(self):
            return self.current_page

    # 创建带压缩的PDF画布
    c = PageTracker(output_file, pagesize=A4, compress=PDF_COMPRESSION_LEVEL > 0, compressLevel=PDF_COMPRESSION_LEVEL)
    print_info(f"🔧 PDF压缩级别: {PDF_COMPRESSION_LEVEL}")
    width, height = A4
    margin_top = 30 * mm
    margin_bottom = 10 * mm
    text_max_width = width - 60 * mm  # 减去两侧的边距和头像宽度

    # 设置默认字体并绘制标题
    font_name = "SimSun" if has_chinese_font else "Helvetica"
    c.setFont(font_name, 16, leading=None)
    c.drawCentredString(width / 2, height - 15 * mm, "微信聊天记录")
    c.setFillColor(colors.grey)
    c.setFont(font_name, 12, leading=None)
    c.drawCentredString(width / 2, height - 25 * mm, f"文件名: {chat_filename}")
    y = height - margin_top

    # 创建目录结构并记录页码位置
    bookmarks = []
    # 封面页是第0页
    bookmarks.append(["微信聊天记录", 0])

    # 按日期顺序处理消息
    sorted_dates = sorted(dates_dict.keys())
    print_info(f"🔄 生成 {len(dates_dict)} 个日期的内容...")

    # 使用tqdm显示进度条
    total_messages = sum(len(dates_dict[date]) for date in sorted_dates)
    with tqdm(total=total_messages, desc="处理消息") as pbar:
        for date_idx, date in enumerate(sorted_dates):
            msgs_count = len(dates_dict[date])

            # 获取当前页码并添加书签
            current_page = c.getPageNumber()
            bookmark_title = f"{date} ({msgs_count}条)"
            bookmarks.append([bookmark_title, current_page])

            if DEBUG_MODE:
                print_info(f"🔖 日期 {date} 将添加到第 {current_page} 页的书签")

            # 处理该日期的所有消息
            for msg_idx, msg in enumerate(dates_dict[date]):
                pbar.update(1)  # 更新进度条

                # 绘制消息
                y = draw_message(c, msg, users, y, text_max_width, width, avatar_cache, enable_speech_to_text)

                # 检查是否需要新页面
                if y < margin_bottom:
                    c.showPage()
                    y = height - margin_top - 5 * mm

    # 保存PDF文件
    c.save()
    print_info(f"✅ PDF内容已生成: {output_file}")
    print_info(f"📊 共 {c.getPageNumber() + 1} 页，包含 {len(bookmarks)} 个书签")

    # ===== 使用PyPDF2添加书签 =====
    try:
        # 关闭文件，确保没有文件句柄占用
        c = None

        # 在操作前等待一小段时间，确保文件完全关闭
        time.sleep(1)

        # 使用唯一的临时文件名避免冲突
        temp_pdf = output_file + f".temp_{int(time.time())}"

        # 安全地复制文件而不是重命名，避免文件锁定问题
        try:
            import shutil
            shutil.copy2(output_file, temp_pdf)
            print_info(f"✅ 成功创建临时文件用于添加书签")
        except Exception as e:
            print_info(f"⚠️ 创建临时文件失败: {e}")
            temp_pdf = output_file
            return

        # 尝试添加书签
        bookmark_added = False

        # 使用PyPDF2添加书签
        try:
            # 导入PyPDF2，兼容新旧版API
            try:
                # 尝试新版PyPDF2
                from PyPDF2 import PdfReader, PdfWriter
                new_api = True
                print_info("✅ 使用PyPDF2添加书签...")
            except ImportError:
                # 尝试旧版PyPDF2
                try:
                    from PyPDF2 import PdfFileReader as PdfReader
                    from PyPDF2 import PdfFileWriter as PdfWriter
                    new_api = False
                    print_info("✅ 使用PyPDF2(旧版)添加书签...")
                except ImportError:
                    raise ImportError("⚠️ PyPDF2未安装，请使用pip install pypdf2安装")

            # 使用另一个唯一的临时文件名用于输出
            output_temp = output_file + f".out_{int(time.time())}"

            with open(temp_pdf, 'rb') as file:
                reader = PdfReader(file)
                writer = PdfWriter()

                # 复制所有页面
                for page in reader.pages:
                    writer.add_page(page)

                if DEBUG_MODE:
                    print_info(f"📄 PDF总页数: {len(reader.pages)}")

                # 添加书签 - 使用每个日期的第一条消息所在页面
                parent = None
                bookmark_count = 0
                for title, page_num in bookmarks:
                    if page_num < len(reader.pages):
                        if DEBUG_MODE:
                            print_info(f"📑 添加书签: '{title}' -> 第 {page_num} 页")
                        try:
                            if new_api:
                                parent = writer.add_outline_item(title, page_num, parent=None)
                            else:
                                parent = writer.addBookmark(title, page_num, parent=None)
                            bookmark_count += 1
                        except Exception as e:
                            print_info(f"⚠️ 书签 '{title}' 添加错误: {e}")

                # 保存带书签的PDF到临时文件
                with open(output_temp, 'wb') as out_file:
                    writer.write(out_file)

            # 关闭所有文件句柄并等待
            time.sleep(1)

            # 安全地替换原始文件
            try:
                # 如果原始文件被锁定，先尝试删除它
                if os.path.exists(output_file):
                    try:
                        os.remove(output_file)
                    except:
                        print_info("⚠️ 无法删除原始文件，可能正被其他程序使用")

                # 重命名新文件为原始文件名
                os.rename(output_temp, output_file)
                print_info(f"✅ 成功添加 {bookmark_count} 个书签到PDF")

                # 删除临时文件
                try:
                    if os.path.exists(temp_pdf):
                        os.remove(temp_pdf)
                except:
                    if DEBUG_MODE:
                        print_info(f"⚠️ 无法删除临时文件: {temp_pdf}")

                bookmark_added = True
            except Exception as e:
                print_info(f"⚠️ 替换文件失败: {e}")
                print_info(f"✅ 带书签的PDF已保存至: {output_temp}")
                print_info(f"请手动将 {output_temp} 重命名为 {output_file}")

        except Exception as e:
            print_info(f"⚠️ PyPDF2方法失败: {e}")
            if DEBUG_MODE:
                traceback.print_exc()  # 只在调试模式下打印详细错误堆栈

            # 保留临时文件供用户手动处理
            print_info(f"⚠️ 处理失败，临时文件保留在: {temp_pdf}")

        # 如果未成功添加书签，提示安装依赖
        if not bookmark_added:
            print_info("⚠️ 无法添加书签，请确保已安装PyPDF2: pip install pypdf2")
            print_info(f"✅ 生成的PDF已保存到: {output_file}")

    except Exception as e:
        print_info(f"⚠️ 书签处理过程中出错: {e}")
        if DEBUG_MODE:
            traceback.print_exc()  # 只在调试模式下打印详细错误堆栈

    # 清理临时文件
    try:
        if os.path.exists(MEDIA_CACHE_DIR) and len(os.listdir(MEDIA_CACHE_DIR)) > 100:
            print_info("🔄 清理临时文件...")
            cleaned = 0
            for file in os.listdir(MEDIA_CACHE_DIR):
                file_path = os.path.join(MEDIA_CACHE_DIR, file)
                if os.path.isfile(file_path) and (time.time() - os.path.getmtime(file_path)) > 3600:
                    os.remove(file_path)
                    cleaned += 1
            print_info(f"✅ 清理了 {cleaned} 个临时文件")
    except Exception as e:
        if DEBUG_MODE:
            print_info(f"⚠️ 清理临时文件出错: {e}")

    # 显示总结信息
    elapsed_time = time.time() - start_time
    print_info(f"✅ PDF已生成: {output_file} (耗时: {elapsed_time:.2f}秒)")
    print_info(f"✅ 处理了 {len(dates_dict)} 个日期的 {total_messages} 条聊天记录")


def compress_image(input_path, max_dimension=MAX_IMAGE_DIMENSION, quality=IMAGE_QUALITY):
    """
    压缩图片以减小PDF文件大小

    参数:
        input_path: 输入图片路径
        max_dimension: 最大图片尺寸(宽或高)，单位像素
        quality: JPEG压缩质量(1-100)，越小文件越小但质量越低

    返回:
        压缩后的图片路径，如果压缩失败则返回原路径
    """
    if not os.path.exists(input_path):
        print_info(f"⚠️ 要压缩的图片不存在: {input_path}")
        return input_path

    try:
        # 创建一个带有原始文件名的临时文件
        filename, ext = os.path.splitext(os.path.basename(input_path))
        output_path = os.path.join(MEDIA_CACHE_DIR, f"{filename}_compressed{ext}")

        # 如果压缩版本已经存在，直接返回
        if os.path.exists(output_path):
            print_info(f"✅ 使用现有压缩图片: {output_path}")
            return output_path

        with Image.open(input_path) as img:
            # 检查图像格式
            original_format = img.format
            # 保存原始尺寸用于日志
            original_width, original_height = img.size
            original_size = os.path.getsize(input_path)

            # 调整大小保持纵横比
            if img.width > max_dimension or img.height > max_dimension:
                ratio = min(max_dimension / img.width, max_dimension / img.height)
                new_width = int(img.width * ratio)
                new_height = int(img.height * ratio)
                img = img.resize((new_width, new_height), Image.LANCZOS)
                print_info(f"🔄 调整图片大小: {original_width}x{original_height} -> {new_width}x{new_height}")

            # 保存为合适的格式
            # GIF或带透明度的图像需要特殊处理以保留透明度
            if original_format == 'GIF' or (img.mode == 'RGBA' and img.getcolors(maxcolors=1)):
                # 保留透明度的格式
                if original_format == 'GIF':
                    img.save(output_path, format='GIF')
                else:
                    img.save(output_path, format='PNG', optimize=True)
            else:
                # 转换为RGB（如果需要）并保存为JPEG以提高压缩率
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.save(output_path, format='JPEG', quality=quality, optimize=True)

            # 检查压缩效果
            if os.path.exists(output_path):
                compressed_size = os.path.getsize(output_path)
                # 计算节省的空间百分比
                savings = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
                print_info(
                    f"✅ 图片压缩: {original_size / 1024:.1f}KB -> {compressed_size / 1024:.1f}KB (节省 {savings:.1f}%)")
                return output_path
            else:
                print_info(f"⚠️ 图片压缩失败，使用原始图片: {input_path}")
                return input_path
    except Exception as e:
        print_info(f"⚠️ 图片压缩错误: {e}")
        return input_path


def get_file_hash(file_path):
    """
    计算文件的哈希值

    参数:
        file_path: 文件路径
    返回:
        文件的MD5哈希值
    """
    try:
        with open(file_path, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()
        return file_hash
    except Exception as e:
        logger.error(f"计算文件哈希值出错: {e}")
        return hashlib.md5(file_path.encode()).hexdigest()


def save_wechat_image(img_data, output_path):
    """
    保存微信图片数据到文件

    参数:
        img_data: 图片数据
        output_path: 输出文件路径
    返回:
        是否成功保存
    """
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'wb') as f:
            f.write(img_data)
        logger.debug(f"已保存图片: {output_path}")
        return True
    except Exception as e:
        logger.error(f"保存图片失败: {e}")
        return False


if __name__ == "__main__":
    """
    主程序入口
    """
    parser = argparse.ArgumentParser(description="导出微信聊天记录为PDF格式")
    parser.add_argument('chat_file', help='聊天记录JSON文件路径')
    parser.add_argument('--user-file', '-u', help='用户信息JSON文件路径')
    parser.add_argument('--output', '-o', default='wechat.pdf', help='输出PDF文件名')
    parser.add_argument('--avatars', '-a', action='store_true', help='下载头像')
    parser.add_argument('--speech', '-s', action='store_true', default=True, help='启用语音转文字')
    parser.add_argument('--quality', '-q', type=int, default=60, help='图像质量(1-100)')
    parser.add_argument('--debug', '-d', action='store_true', help='启用调试模式')
    parser.add_argument('--emoji-font', '-e', action='store_true', help='强制下载彩色表情字体')

    args = parser.parse_args()

    # 设置调试模式
    if args.debug:
        DEBUG_MODE = True

    # 如果指定了下载表情字体，则尝试下载
    if args.emoji_font:
        download_emoji_font(force=True)

    # 生成PDF
    generate_pdf(
        chat_file=args.chat_file,
        user_file=args.user_file,
        output_file=args.output,
        download_avatars=args.avatars,
        enable_speech_to_text=args.speech,
        image_quality=args.quality
    )
# 启用所有功能使用文件： python export2pdf_last.py chats.json -u users.json -o 高质量聊天记录.pdf -a -s -q 80 -d -e
# python export2pdf_0424.py chats.json -u users.json -o wechat.pdf -a -s -q 60 -e