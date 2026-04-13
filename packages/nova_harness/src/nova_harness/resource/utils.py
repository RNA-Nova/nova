import yaml
from .types import ParsedFrontmatter

def normalize_newlines(value: str) -> str:
    """将文本中的换行符统一为 \\n。"""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def extract_frontmatter(content: str) -> tuple[str | None, str]:
    """
    提取 YAML frontmatter 和正文内容。
    
    Args:
        content: 原始文本内容
        
    Returns:
        一个元组，包含 YAML 字符串（如果不存在则为 None）和正文内容
    """
    normalized = normalize_newlines(content)

    if not normalized.startswith("---"):
        return None, normalized

    end_index = normalized.find("\n---", 3)
    if end_index == -1:
        return None, normalized

    yaml_string = normalized[4:end_index]
    body = normalized[end_index + 4:].strip()

    return yaml_string, body


def parse_frontmatter(content: str) -> ParsedFrontmatter:
    """
    解析包含 YAML frontmatter 的文本内容。
    
    Args:
        content: 原始文本内容
        
    Returns:
        ParsedFrontmatter 对象，包含解析后的 frontmatter 字典和正文内容
    """
    yaml_string, body = extract_frontmatter(content)
    if yaml_string is None:
        return ParsedFrontmatter({}, body)
    
    try:
        parsed = yaml.safe_load(yaml_string)
        if parsed is None:
            parsed = {}
    except yaml.YAMLError:
        parsed = {}
    
    return ParsedFrontmatter(parsed, body)


def strip_frontmatter(content: str) -> str:
    """
    移除文本中的 YAML frontmatter，仅返回正文。
    
    Args:
        content: 原始文本内容
        
    Returns:
        不包含 frontmatter 的正文内容
    """
    return parse_frontmatter(content).body