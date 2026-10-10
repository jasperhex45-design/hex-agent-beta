"""Bind each design choice to prompt text, not to a claim of visual quality."""
from __future__ import annotations

import re
import unicodedata

from .contracts import DesignPlan, PromptDraft
from .io import AgentError


def _relation_text(text: str, *, english: bool) -> str:
    value = text.strip()
    if not value or len(value) > 2000:
        raise AgentError('每项设计关系须为非空文本且不超过2000字符')
    if re.search(r'(?<![\w-])--[^\s-]', value):
        raise AgentError('设计关系不能混入--参数；参数请放setup_notes')
    if english and any(unicodedata.name(char, '').startswith(
            ('CJK UNIFIED IDEOGRAPH', 'CJK COMPATIBILITY IDEOGRAPH')) for char in value):
        raise AgentError('英文设计关系仍含中文；请完整转译choice_translations')
    return value


def compile_relations(packet: dict, design: DesignPlan | None, row: PromptDraft, language: str) -> str:
    """Return all choices in design order, keeping legacy prompt bytes unchanged.

    Mapping and language checks prove coverage, not that a translation preserves
    the intended meaning or that the design is aesthetically successful.
    """
    version = packet.get('source_contract_version')
    if version is None:
        return ''
    if type(version) is not int or version != 1:
        raise AgentError('未知需求来源合同版本，不能推断设计关系编译规则')
    if design is None or not design.choices:
        raise AgentError('新版提示词编译需要包含设计选择的design，不能省略关系传递')
    if language not in ('zh', 'en'):
        raise AgentError('设计关系只支持已声明的zh或en编译语言')
    ids = [choice.id for choice in design.choices]
    if any(not ident.strip() for ident in ids) or len(ids) != len(set(ids)):
        raise AgentError('设计选择ID为空或重复，无法绑定提示词关系')

    texts = []
    if language == 'zh':
        for choice in design.choices:
            if not choice.action.strip() or not choice.cooperation.strip():
                raise AgentError('设计选择缺少action或cooperation，不能只传入局部动作')
            texts.append(_relation_text(choice.action.strip() + '；配合关系：' + choice.cooperation.strip(),
                                        english=False))
    else:
        translations = row.choice_translations
        translated_ids = [item.choice_id for item in translations]
        if len(translated_ids) != len(set(translated_ids)):
            raise AgentError('choice_translations的选择ID重复')
        if set(translated_ids) != set(ids):
            raise AgentError('choice_translations须一一覆盖全部设计选择，不能缺失或包含未知ID')
        by_id = {item.choice_id:item.text for item in translations}
        texts = [_relation_text(by_id[ident], english=True) for ident in ids]

    title = 'Visual relationships:' if language == 'en' else '视觉关系：'
    return title + '\n' + '\n'.join(f'{index}. {text}' for index, text in enumerate(texts, 1))
