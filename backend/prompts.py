"""Reload explicit adventure prompts; callers freeze text for each generation."""
from pathlib import Path


PROMPTS_ROOT = Path(__file__).resolve().parent.parent / 'prompts'

OPERATION_FILES = {
    'adventure_analysis': 'adventures/analysis.md',
    'adventure_analysis_batch': 'adventures/analysis_batch.md',
    'adventure_analysis_merge': 'adventures/analysis_merge.md',
    'adventure_plan': 'adventures/plan.md',
    'adventure_task': 'adventures/task.md',
    'adventure_review_v2': 'adventures/review_v2.md',
    'connectivity_system': 'connectivity/system.md',
    'connectivity_user': 'connectivity/user.md',
}


def _read(relative: str) -> str:
    try:
        text = (PROMPTS_ROOT / relative).read_text(encoding='utf-8-sig').strip()
    except (OSError, UnicodeError) as error:
        raise ValueError(f'无法读取提示词文档 prompts/{relative}，请检查文件是否存在、可读且为 UTF-8 编码。') from error
    if not text:
        raise ValueError(f'提示词文档 prompts/{relative} 为空，请填写后重试。')
    return text


def load_prompt(name: str) -> str:
    """Reload a known operation's documents for each explicit operation."""
    if name not in OPERATION_FILES:
        raise ValueError('未知的提示词名称。')
    return _read(OPERATION_FILES[name])
