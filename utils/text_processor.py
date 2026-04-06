import re

def normalize_text(text: str) -> str:
    """Normalizes whitespace and unicode"""
    if not text:
        return ""
    # Replace non-breaking spaces
    text = text.replace('\u00A0', ' ')
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def clean_tags(text: str) -> str:
    """Removes standard inline tags like {1} or <bpt>"""
    # Remove XML-like tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove MemoQ/Trados style tags like {1}
    text = re.sub(r'\{\d+\}', '', text)
    return text.strip()