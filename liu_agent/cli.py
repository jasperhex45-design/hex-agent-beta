from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import ValidationError
from . import __version__
from .contracts import Brief, Need, Solution, Review, ModelProfile, OUTPUT_MODELS, ReferenceAnalysis, ImageEval
from .design import load_design
from . import host, library
from . import aesthetic_library, template_library, private_style
from .io import ROOT, AgentError, load_env, read_json, write_json, atomic_text, sha, digest, now, normalize_image, within
from .retrieval import load_cards, Retriever
from .profiles import profiles, install, rollback, stale
from .workflow import make_packet, read_packet, refresh_packet, finish, run_cloud, validate_spec, validate_review, add_reference
from .provider import OpenAIProvider, Budget
from .network import PublicFetcher
from .research import check_updates, collect_commons, analyze_materials, promote_card
from .visual_decision import DEFAULT_PROFILE, PROFILES, build_policy, policy

COMMONS_HOSTS = {"commons.wikimedia.org", "upload.wikimedia.org"}


def build_parser():
    p=argparse.ArgumentParser(prog="hex-agent", description="Hex Agent：把模糊想法变成有依据的视觉方案和多模型提示词。")
    p.add_argument("--version",action="version",version=__version__)
    p.add_argument("--kb",type=Path,default=ROOT/"knowledge")
    p.add_argument("--state",type=Path,default=ROOT/".state")
    p.add_argument('--format', choices=['json', 'human'], default='json', help='保留JSON接口，human展示当前事实与下一步')
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("doctor",help="只检查本地环境，不调用API")
    sub.add_parser("validate",help="校验全部知识和模型配置")
    s=sub.add_parser("status",help="只读核对任务证据、下一步与已用预算；不调用模型")
    s.add_argument("--run",type=Path,required=True)
    h=sub.add_parser('harness', help='读取代码定义的流程；单步推进已有本地恢复，不生图')
    hs=h.add_subparsers(dest='action', required=True)
    hs.add_parser('definition')
    s=hs.add_parser('plan');s.add_argument('--run',type=Path,required=True)
    s=hs.add_parser('case');s.add_argument('--run',type=Path,required=True)
    s.add_argument('--request-id',help='多个请求时必须明确选择原请求；不自动选最后一张')
    s=hs.add_parser('advance');s.add_argument('--run',type=Path,required=True)
    s.add_argument('--checkpoint',required=True)
    s=sub.add_parser('executors',help='列出执行器实现与恢复边界，不探测服务或生成')
    s.add_argument('--evidence-run',type=Path,action='append',default=[],
                   help='可重复指定已保存任务，只读核对该次执行证据，不提升为通用视觉资格')
    s=sub.add_parser('stop-generation',help='绑定用户停止消息，阻止本版本的新提交；查询原任务仍可用')
    s.add_argument('--run',type=Path,required=True);s.add_argument('--source-id',required=True)
    s.add_argument('--sources',type=Path)
    s=sub.add_parser('execute',help='按冻结dispatch路由已接通执行器；不切换模型')
    s.add_argument('--run',type=Path,required=True);s.add_argument('--request-id',required=True)
    s.add_argument('--cli-path',type=Path)
    s=sub.add_parser('query',help='通过原执行器查询同一请求，不重新提交')
    s.add_argument('--run',type=Path,required=True);s.add_argument('--request-id',required=True)
    s=sub.add_parser("resume",help="按已保存证据准备复查、比较或导出；不重发生图或重置预算")
    s.add_argument("--run",type=Path,required=True)
    s.add_argument("--allow-cloud",action="store_true",help="仅API任务：授权恢复缺失文字阶段、复查或比较；不生成图片")
    s=sub.add_parser("demo",help="作者编写的安装演示；非AI生成")
    s.add_argument("--out",type=Path,required=True)
    s=sub.add_parser("prepare",help="为Codex宿主准备知识包，不调用模型")
    s.add_argument('--workflow-profile', choices=['legacy', 'full-flow-v1'], default='legacy')
    s.add_argument('--deliverable-kind', choices=['prompt_only', 'image', 'local_edit'])
    s.add_argument('--knowledge-config', type=Path, help='仅 knowledge-assisted-v1：有限检索配置；省略为本地词法，不连接外部服务')
    s.add_argument('--interaction-profile', choices=['legacy', 'selective-co-creation-v1', 'natural-co-creation-v1'], default=None, help='新生产任务按需共创；显式legacy关闭，不迁移历史任务')
    s.add_argument('--execution-profile', choices=PROFILES, default=DEFAULT_PROFILE,
                   help='新任务默认精简基线；visual-relations-v1 是实验候选，legacy 仅用于显式兼容')
    s.add_argument('--sources', type=Path, help='宿主记录的原始消息来源；不从需求摘要推断用户要求')
    s.add_argument("--brief",type=Path,required=True);s.add_argument("--out",type=Path,required=True);s.add_argument("--need",type=Path)
    s.add_argument('--library', type=Path, help='当前用户授权的本地图库索引；检索后仍需看图采用')
    s.add_argument('--aesthetic-library', type=Path, help='已看图审美卡片库；只写入任务检索结果，不自动当作参考图')
    s.add_argument('--aesthetic-query', default='', help='审美卡片检索词；默认使用需求意图')
    s.add_argument('--no-style-context', dest='style_context_enabled', action='store_false', default=None, help='本任务关闭私人图库与成对反馈召回')
    s.add_argument('--template-library', type=Path, help='任务模板库；只写入结构检索结果，不自动当作审美真值')
    s.add_argument('--template-query', default='', help='任务模板检索词；默认使用需求意图')
    s=sub.add_parser("refresh",help="根据宿主提供的需求卡重新检索")
    s.add_argument('--sources', type=Path, help='追加消息来源，保留原有消息')
    s.add_argument('--out', type=Path, help='已有生成时创建关联的新需求版本；不重置或自动转接预算')
    s.add_argument('--edit-reference', type=Path, help='仅 refresh --out：显式授权的选中父图编辑参考 JSON')
    s.add_argument('--parent-request-id', help='仅 refresh --out --edit-reference：明确要编辑的父图请求ID')
    s.add_argument('--reuse-parent-input', action='store_true', help='仅 refresh --out：复用上一编辑任务的原输入；新增许可及累计预算仍须核验')
    s.add_argument("--run",type=Path,required=True);s.add_argument("--need",type=Path,required=True)
    s.add_argument('--library', type=Path)
    s.add_argument('--aesthetic-library', type=Path)
    s.add_argument('--aesthetic-query', default='')
    s.add_argument('--no-style-context', dest='style_context_enabled', action='store_false', default=None)
    s.add_argument('--template-library', type=Path)
    s.add_argument('--template-query', default='')
    s=sub.add_parser("finalize",help="校验宿主方案并导出HTML/MD/JSON/TXT")
    s.add_argument("--run",type=Path,required=True);s.add_argument("--solution",type=Path,required=True)
    s=sub.add_parser("run",help="使用OpenAI API独立执行完整Agent；需要授权")
    s.add_argument('--workflow-profile', choices=['legacy', 'full-flow-v1'], default='legacy')
    s.add_argument('--deliverable-kind', choices=['prompt_only', 'image', 'local_edit'])
    s.add_argument('--knowledge-config', type=Path, help='仅 knowledge-assisted-v1：无密钥的有限检索配置')
    s.add_argument('--interaction-profile', choices=['legacy', 'selective-co-creation-v1', 'natural-co-creation-v1'], default=None, help='新生产任务按需共创；显式legacy关闭，不迁移历史任务')
    s.add_argument('--execution-profile', choices=PROFILES, default=DEFAULT_PROFILE,
                   help='新任务默认精简基线；实验策略须显式选择，不迁移历史任务')
    s.add_argument('--sources', type=Path, required=True)
    s.add_argument("--brief",type=Path,required=True);s.add_argument("--out",type=Path,required=True)
    s.add_argument("--allow-cloud",action="store_true");s.add_argument("--research",action="store_true")
    s.add_argument("--image-tests",type=int,choices=(0,1,2),default=0)
    s.add_argument("--max-calls",type=int,default=14)
    s.add_argument("--collect-references",action="store_true");s.add_argument("--allow-network",action="store_true")
    s.add_argument("--aesthetic-library",type=Path,help="已看图审美卡片库；写入云端规划上下文")
    s.add_argument("--aesthetic-query",default="",help="审美卡片检索词；默认使用模型重写后的需求意图")
    s.add_argument('--no-style-context', dest='style_context_enabled', action='store_false', default=None)
    s.add_argument('--allow-library-analysis', action='store_true', help='允许把最多3张命中的案例原图提交云端分析；不自动作为生成参考')
    s.add_argument("--template-library",type=Path,help="任务模板库；写入云端规划上下文")
    s.add_argument("--template-query",default="",help="任务模板检索词；默认使用模型重写后的需求意图")
    s=sub.add_parser("record-test",help="记录宿主实际取得的候选图；不冒充本地自动生成")
    s.add_argument("--run",type=Path,required=True);s.add_argument("--image",type=Path,required=True)
    s.add_argument("--review",type=Path,required=True);s.add_argument("--prompt",type=Path,required=True)
    s.add_argument("--model-label",required=True)
    s.add_argument("--target",default="image",help="本次测试所用提示词档案标签，默认image")
    s.add_argument("--tool-note",required=True)
    s=sub.add_parser("preflight",help="校验设计并固定本任务标准；不是审美打分")
    s.add_argument("--run",type=Path,required=True);s.add_argument("--solution",type=Path,required=True)
    s=sub.add_parser('prepare-compilation',help='依据当前设计、模型档案和已知工具准备智能编译输入；不调用模型')
    s.add_argument('--run',type=Path,required=True);s.add_argument('--plan',type=Path,required=True)
    s.add_argument('--target',help='本次编译目标；省略时仅沿用当前任务唯一已选模型，多模型须显式指定');s.add_argument('--tool')
    s=sub.add_parser("observe",help="记录宿主对实际参考图的观察")
    s.add_argument("--run",type=Path,required=True);s.add_argument("--report",type=Path,required=True);s.add_argument("--tool-note",required=True)
    s=sub.add_parser("prepare-eval",help="给宿主创建对照原话、参考和候选的复查输入")
    s.add_argument("--run",type=Path,required=True);s.add_argument("--image",type=Path,required=True)
    s=sub.add_parser("choose",help="记录两候选的同标准比较，保留更好的版本")
    s.add_argument("--run",type=Path,required=True);s.add_argument("--report",type=Path,required=True)
    s=sub.add_parser("add-reference",help="添加有来源的实际参考图；使旧方案失效")
    s.add_argument("--run",type=Path,required=True);s.add_argument("--image",type=Path,required=True)
    s.add_argument("--id",required=True);s.add_argument("--role",required=True)
    s.add_argument("--note",required=True);s.add_argument("--rights",choices=['unknown','user_authorized'],default='unknown')
    s.add_argument("--source-url",default='');s.add_argument("--credit",default='');s.add_argument("--evaluation-only",action='store_true')

    s=sub.add_parser("add-host-reference",help="登记宿主实际看过的会话图片；仅供研究，非像素证据")
    s.add_argument("--run",type=Path,required=True);s.add_argument("--file",type=Path,required=True)

    s=sub.add_parser("research",help="显式联网补充文字依据，需要API")
    s.add_argument("--query",required=True);s.add_argument("--out",type=Path,required=True);s.add_argument("--allow-cloud",action="store_true")
    s=sub.add_parser("attach-research",help="将带来源的研究记录用于当前任务，不自动入核心库")
    s.add_argument("--run",type=Path,required=True);s.add_argument("--report",type=Path,required=True)
    k=sub.add_parser("kb");ks=k.add_subparsers(dest="action",required=True)
    s=ks.add_parser("search");s.add_argument("--query",required=True);s.add_argument("--domain",choices=['person','illustration','poster','vehicle','character','prop']);s.add_argument("--scope",choices=['create','edit','scene','relight','color_only']);s.add_argument("--limit",type=int,default=8);s.add_argument("--role",action="append",default=[])
    s=ks.add_parser("promote");s.add_argument("--candidate",type=Path,required=True);s.add_argument("--evidence",type=Path,required=True);s.add_argument("--note",required=True)
    m=sub.add_parser("models");ms=m.add_subparsers(dest="action",required=True)
    ms.add_parser("list")
    s=ms.add_parser("check-updates");s.add_argument("--allow-network",action="store_true")
    s=ms.add_parser("install");s.add_argument("--candidate",type=Path,required=True);s.add_argument("--expected-version",type=int,required=True);s.add_argument("--allow-experimental",action="store_true")
    s=ms.add_parser("rollback");s.add_argument("--id",required=True);s.add_argument("--version",type=int,required=True)
    s=ms.add_parser("propose");s.add_argument("--id",required=True);s.add_argument("--out",type=Path,required=True);s.add_argument("--allow-cloud",action="store_true")
    m=sub.add_parser("materials");ms=m.add_subparsers(dest="action",required=True)
    s=ms.add_parser("collect");s.add_argument("--query",required=True);s.add_argument("--out",type=Path,required=True);s.add_argument("--allow-network",action="store_true");s.add_argument("--limit",type=int,choices=[1,2,3],default=3)
    s=ms.add_parser("analyze");s.add_argument("--manifest",type=Path,required=True);s.add_argument("--need",type=Path,required=True);s.add_argument("--allow-cloud",action="store_true")
    s=sub.add_parser("schemas");s.add_argument("--out",type=Path,default=ROOT/"schemas")
    m=sub.add_parser('library', help='本地实图索引/观察/检索，不修改或上传原素材'); ms=m.add_subparsers(dest='action', required=True)
    s=ms.add_parser('scan'); s.add_argument('--root', type=Path, required=True); s.add_argument('--library', type=Path, required=True)
    s=ms.add_parser('observe'); s.add_argument('--library', type=Path, required=True); s.add_argument('--id', required=True); s.add_argument('--report', type=Path, required=True); s.add_argument('--tool-note', required=True)
    s=ms.add_parser('search'); s.add_argument('--library', type=Path, required=True); s.add_argument('--query', required=True); s.add_argument('--limit', type=int, default=6); s.add_argument('--run', type=Path)
    s=ms.add_parser('attach'); s.add_argument('--library', type=Path, required=True); s.add_argument('--id', required=True); s.add_argument('--run', type=Path, required=True); s.add_argument('--reference-id', required=True); s.add_argument('--note', required=True); s.add_argument('--rights', choices=['unknown','user_authorized'], default='unknown'); s.add_argument('--usage', choices=['generation','evaluation_only'], default='evaluation_only', help='用途：默认仅分析；generation要求rights=user_authorized并会进入生成输入')
    s=ms.add_parser('attach-curated', help='将当前任务命中的审美卡片源图加入参考，明确生成或评估用途')
    s.add_argument('--run',type=Path,required=True);s.add_argument('--id',required=True);s.add_argument('--reference-id',required=True)
    s.add_argument('--note',required=True);s.add_argument('--rights',choices=['unknown','user_authorized'],default='unknown')
    s.add_argument('--usage',choices=['generation','evaluation_only'],default='evaluation_only')
    s=ms.add_parser('validate-curated', help='校验已看图审美卡片与原素材 hash'); s.add_argument('--library', type=Path, required=True); s.add_argument('--no-source-check', action='store_true')
    s=ms.add_parser('search-curated', help='检索已看图且有迁移条件的审美卡片'); s.add_argument('--library', type=Path, required=True); s.add_argument('--query', required=True); s.add_argument('--domain', choices=['person','illustration','poster','vehicle','hybrid']); s.add_argument('--limit', type=int, default=6)
    s=ms.add_parser('validate-plan', help='校验审美方案引用的卡片和字段'); s.add_argument('--library', type=Path, required=True); s.add_argument('--plan', type=Path, required=True)
    s=sub.add_parser('dispatch', help='在宿主生图前固定提示词、参考、方案并占用一次预算')
    s.add_argument('--run', type=Path, required=True); s.add_argument('--target', help='省略时仅沿用任务唯一已选模型，多目标须显式指定'); s.add_argument('--tool', required=True)
    s.add_argument('--resolution', help='小云雀已核实的分辨率；在调用快照中冻结')
    s.add_argument('--ratio', type=int, help='小云雀已核实的比例枚举')
    s.add_argument('--provider-options', type=Path, help='本地 ComfyUI 的明确参数 JSON；不能与小云雀参数混用')
    s=sub.add_parser('execute-comfy', help='执行已派发的本地 ComfyUI 请求；不重发未知提交')
    s.add_argument('--run', type=Path, required=True); s.add_argument('--request-id', required=True)
    s=sub.add_parser('query-comfy', help='查询并取回同一 ComfyUI 请求的真实输出')
    s.add_argument('--run', type=Path, required=True); s.add_argument('--request-id', required=True)
    s=sub.add_parser('execute-pippit', help='执行一次已冻结的小云雀调用；重复执行不会重发生图')
    s.add_argument('--run', type=Path, required=True); s.add_argument('--request-id', required=True)
    s.add_argument('--cli-path', type=Path, required=True)
    s=sub.add_parser('query-pippit', help='查询并下载已提交的小云雀任务；不重发生图')
    s.add_argument('--run', type=Path, required=True); s.add_argument('--request-id', required=True)
    s=sub.add_parser('authorize-generation', help='登记真实用户消息中的累计生图许可；产品上限不是默认授权')
    s.add_argument('--run', type=Path, required=True); s.add_argument('--source-id', required=True)
    s.add_argument('--sources', type=Path, help='只追加许可消息，保持当前packet、需求和合同不变')
    s.add_argument('--rebind', action='store_true', help='仅未开始生成且packet已refresh时，凭新的明确许可重新绑定；不能清掉旧支出')
    s=sub.add_parser('recover-lock', help='只解除证实拥有者已退出的任务锁，不清调用或重发生图')
    s.add_argument('--run', type=Path, required=True)
    s=sub.add_parser('transfer-native-budget', help='关联一个已冻结未生成的续作；凭已验证否决或--source-id新增许可，父子累计最多两次')
    s.add_argument('--from-run', type=Path, required=True); s.add_argument('--run', type=Path, required=True)
    s.add_argument('--source-id', help='同一新增用户许可ID；关联需求续作，父子累计最多两次')
    s=sub.add_parser('grant-native-revision', help='旧预算耗尽后，将新用户许可绑定真实父图的一张续改；接受不自动授权')
    s.add_argument('--from-run', type=Path, required=True); s.add_argument('--run', type=Path, required=True)
    s.add_argument('--source-id', required=True)
    s=sub.add_parser('record-result', help='绑定宿主实际结果，失败或未知结果也消耗预算')
    s.add_argument('--run', type=Path, required=True); s.add_argument('--request-id', required=True)
    s.add_argument('--status', choices=['completed','failed','outcome_unknown'], required=True)
    s.add_argument('--image', type=Path); s.add_argument('--tool-note', required=True)
    s.add_argument('--actual-arguments', type=Path, help='实际调用工具参数JSON；completed必填且必须等于dispatch request.arguments')
    s=sub.add_parser('record-feedback', help='记录候选正向评价、接受/拒绝或当前双图相对偏好')
    s.add_argument('--run', type=Path, required=True)
    s.add_argument('--decision', choices=['accepted_by_user','rejected_by_user','relative_preference','positive_feedback'], required=True)
    s.add_argument('--user-quote', required=True); s.add_argument('--source', required=True)
    s.add_argument('--sources', type=Path, help='独立反馈消息快照；不改写原需求来源')
    s.add_argument('--source-id', help='反馈快照中的用户消息ID')
    s.add_argument('--request-id', help='正向评价的当前候选，或相对偏好的较接近候选request_id')
    s.add_argument('--compared-request-id', help='相对偏好的另一候选request_id')
    s.add_argument('--task-term', action='append', default=[], help='可选：宿主按真实任务限定反馈题材，不从评价推断永久风格')
    m=sub.add_parser('calibration', help='记录真实图片的用户成对审美选择；不调用模型、不推断偏好')
    ms=m.add_subparsers(dest='action', required=True)
    s=ms.add_parser('record', help='记录一次明确的 A/B 选择和被否决原因')
    s.add_argument('--ledger', type=Path, required=True)
    s.add_argument('--domain', choices=['person','illustration','poster','vehicle','character','prop','hybrid'], required=True)
    s.add_argument('--criterion', required=True, help='本次比较的具体问题，例如“哪张更有作者性”')
    s.add_argument('--image-a', type=Path, required=True); s.add_argument('--image-b', type=Path, required=True)
    s.add_argument('--choice', choices=['A','B','tie','uncertain'], required=True)
    s.add_argument('--user-quote', required=True)
    s.add_argument('--rejected-reason', action='append', default=[], help='重复传入，格式 A=原因 或 B=原因')
    s.add_argument('--task-id', help='可选：绑定到一个已存在的任务目录或任务标识')
    s.add_argument('--method-id', action='append', default=[], help='可选：本次实验采用的艺术结构方法，可重复传入')
    s.add_argument('--grammar-id', action='append', default=[], help='可选：本次实验采用的空间语法，可重复传入')
    s.add_argument('--recall-domain', action='append', default=[],
                   choices=['person','illustration','poster','vehicle','character','prop','hybrid'],
                   help='可选：明确的反馈适用领域；跨领域须同时有具体题材依据')
    s.add_argument('--task-term', action='append', default=[], help='可选：反馈与后继任务都必须包含的具体题材词')
    s=ms.add_parser('verify', help='核对账本完整性和当前图片 hash')
    s.add_argument('--ledger', type=Path, required=True)
    s=ms.add_parser('summary', help='只统计记录覆盖，不生成偏好分数')
    s.add_argument('--ledger', type=Path, required=True)
    s=ms.add_parser('withdraw', help='按用户明确原话撤回指定偏好，保留原记录')
    s.add_argument('--ledger', type=Path, required=True); s.add_argument('--id', required=True)
    s.add_argument('--user-quote', required=True); s.add_argument('--source', required=True)
    m=sub.add_parser('private-style', help='核对用户私人的审美风格模块；只作建议，不自动放行')
    ms=m.add_subparsers(dest='action', required=True)
    s=ms.add_parser('verify', help='校验私人模块及其已看图卡片引用')
    s.add_argument('--profile', type=Path, required=True)
    s.add_argument('--aesthetic-library', type=Path)
    s=ms.add_parser('summary', help='查看私人模块的方向和可见复查点')
    s.add_argument('--profile', type=Path, required=True)
    for parser in sub.choices.values():
        parser.add_argument('--format', choices=['json', 'human'], default=argparse.SUPPRESS,
                            help='human展示当前事实与下一步；默认保留JSON')
    return p


def _validate_production_options(args, packet=None):
    selected = policy(packet) if packet is not None else build_policy(args.execution_profile)
    if selected is None:
        return
    options = ('library', 'aesthetic_library', 'aesthetic_query', 'template_library',
               'template_query', 'allow_library_analysis')
    if any(getattr(args, name, None) for name in options):
        raise AgentError('生产配置尚未晋升经验检索，不能启用图库或模板召回；当前任务必要素材请通过 brief.references 或 add-reference 提供')


def _register_feedback_recall(result, register, *args, **kwargs):
    """A failed local index must not hide or rewrite already saved feedback."""
    try:
        result['feedback_recall'] = register(*args, **kwargs)
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        result['feedback_recall'] = {
            'status': 'registration_failed', 'detail': str(exc),
            'notice': '原反馈已保存，但后继任务尚不能自动召回；修复本地索引后登记原来源，不补写用户意见。'}
    return result


def main(argv=None):
    args=build_parser().parse_args(argv)
    kb,state=args.kb.resolve(),args.state.resolve()
    try:
        cmd=args.command
        if cmd in ('prepare', 'run'):
            _validate_production_options(args)
        if cmd in ('run', 'research') or (cmd=='models' and args.action=='propose') or (cmd=='materials' and args.action=='analyze'):
            load_env(ROOT/".env")
        if cmd=='harness':
            from . import harness
            if args.action=='definition': result=harness.definition()
            elif args.action=='plan': result=harness.plan(args.run)
            elif args.action=='case': result=harness.case_record(args.run,args.request_id)
            else: result=harness.advance(args.run,args.checkpoint)
        elif cmd=='executors':
            from .executors import catalog
            result={'executors':catalog(evidence_runs=args.evidence_run),'runtime_availability':'not_probed'}
        elif cmd=='execute':
            from .executors import execute
            result=execute(args.run,args.request_id,cli_path=args.cli_path)
        elif cmd=='query':
            from .executors import query
            result=query(args.run,args.request_id)
        elif cmd=='status':
            from .run_status import inspect_run
            result=inspect_run(args.run)
        elif cmd=='resume':
            from .resume import resume_run
            provider=None
            if args.allow_cloud and (args.run/'api-state.json').exists():
                from .run_status import inspect_run
                if inspect_run(args.run)['stage'] in ('needs_parse', 'needs_observation', 'needs_research', 'needs_design',
                                                       'needs_compile', 'needs_eval', 'needs_compare'):
                    load_env(ROOT/".env")
                    provider=OpenAIProvider(consent=True)
                    from .api_recovery import restore_provider
                    provider=restore_provider(args.run,provider)
            result=resume_run(args.run,provider=provider)
        elif cmd=='record-feedback':
            from .feedback import record_feedback
            result=record_feedback(args.run,args.decision,args.user_quote,args.source,
                                   sources_file=args.sources, source_id=args.source_id,
                                   request_id=args.request_id,
                                   compared_request_id=args.compared_request_id)
            from .user_feedback_context import config_path, register_run
            result = _register_feedback_recall(result, register_run, config_path(kb), args.run,
                                               task_terms=args.task_term or None)
        elif cmd=='grant-native-revision':
            from .native_budget import grant_revision
            result=grant_revision(args.from_run,args.run,args.source_id)
        elif cmd=='transfer-native-budget':
            from .native_budget import transfer_budget
            result=transfer_budget(args.from_run,args.run,args.source_id)
        elif cmd=='calibration':
            from . import calibration
            if args.action == 'record':
                result = calibration.record(
                    args.ledger, domain=args.domain, criterion=args.criterion,
                    image_a=args.image_a, image_b=args.image_b, choice=args.choice,
                    user_quote=args.user_quote,
                    rejected_reasons=calibration.parse_rejection(args.rejected_reason),
                    task_id=args.task_id, method_ids=args.method_id, grammar_ids=args.grammar_id,
                )
                from .user_feedback_context import config_path, register_pair
                result = _register_feedback_recall(
                    result, register_pair, config_path(kb), args.ledger, result['id'],
                    domains=args.recall_domain or None, task_terms=args.task_term or None)
            elif args.action == 'verify':
                result = calibration.verify(args.ledger)
            elif args.action == 'withdraw':
                result = calibration.withdraw(args.ledger, args.id, args.user_quote, args.source)
            else:
                result = calibration.summary(args.ledger)
        elif cmd=='private-style':
            if args.action == 'verify':
                result = private_style.verify(args.profile, args.aesthetic_library)
            else:
                result = private_style.summary(args.profile)
        elif cmd=='library':
            if args.action=='scan': result=library.scan(args.root,args.library)
            elif args.action=='observe': result=library.annotate(args.library,args.id,args.report,args.tool_note)
            elif args.action=='search': result=library.search(args.library,args.query,args.limit,args.run)
            elif args.action=='attach':
                packet = library.attach(args.library,args.id,args.run,args.reference_id,args.note,args.rights,args.usage)
                result={'digest':packet['digest'], 'next':('generation用途：仅在已授权前提下作为生成输入；仍需实际工具记录' if args.usage == 'generation' else 'evaluation_only：仅用于分析/对照，不会进入生成输入；实际打开参考图后observe')}
            elif args.action=='attach-curated':
                attached=aesthetic_library.attach_reference(args.run,args.id,args.reference_id,args.note,args.rights,args.usage)
                result={'digest':attached['digest'],'usage':args.usage,'next':'实际看本任务图片并observe，再设计；已附加不代表已提交生成'}
            elif args.action=='validate-curated': result=aesthetic_library.validate(args.library, not args.no_source_check)
            elif args.action=='search-curated': result=aesthetic_library.search(args.library,args.query,args.domain,args.limit)
            else: result=aesthetic_library.validate_plan(args.plan,args.library)
        elif cmd=='dispatch':
            options = {k: getattr(args, k) for k in ('resolution', 'ratio') if getattr(args, k) is not None}
            if args.provider_options is not None:
                if options:
                    raise AgentError('不能混用 provider-options 与小云雀 resolution/ratio')
                options = read_json(args.provider_options)
            result=host.dispatch(args.run,args.target,args.tool,provider_options=options or None)
        elif cmd=='execute-comfy':
            from .executors import execute
            result=execute(args.run,args.request_id,required_executor='comfy_local')
        elif cmd=='query-comfy':
            from .executors import query
            result=query(args.run,args.request_id,required_executor='comfy_local')
        elif cmd=='execute-pippit':
            from .executors import execute
            result=execute(args.run,args.request_id,cli_path=args.cli_path,required_executor='cloud_image')
        elif cmd=='query-pippit':
            from .executors import query
            result=query(args.run,args.request_id,required_executor='cloud_image')
        elif cmd=='authorize-generation':
            from .generation_authorization import record_authorization
            result=record_authorization(args.run,args.source_id,args.sources,rebind=args.rebind)
        elif cmd=='stop-generation':
            from .generation_authorization import stop_generation
            result=stop_generation(args.run,args.source_id,args.sources)
        elif cmd=='recover-lock':
            from .run_lock import recover_lock
            result=recover_lock(args.run)
        elif cmd=='record-result': result=host.record_result(args.run,args.request_id,args.status,args.image,args.tool_note,args.actual_arguments)
        elif cmd=="doctor":
            result={"version":__version__,"python":sys.version.split()[0],"python_supported":sys.version_info>=(3,11),
                    "knowledge_cards":len(load_cards(kb,state)),"target_profiles":list(profiles(kb,state)),
                    "api_text_configured":bool(os.getenv('OPENAI_API_KEY') and os.getenv('LIU_TEXT_MODEL')),
                    "api_image_model_configured":bool(os.getenv('LIU_IMAGE_MODEL')),
                    "design_mechanisms":len(load_design(kb)["mechanisms"]),"network_used":False,"note":"本地程序不包含大模型权重。Codex宿主或API提供智能；doctor不产生费用。"}
        elif cmd=="validate":
            cards=load_cards(kb,state); pp=profiles(kb,state)
            design=load_design(kb)
            result={"cards":len(cards),"colors":sum(c.kind=='color' for c in cards),"design_mechanisms":len(design["mechanisms"]),"relations":len(design["relations"]),"profiles":len(pp),"schema_validation":"passed","visual_validation":"not_performed"}
        elif cmd=="prepare":
            packet=make_packet(args.brief,args.out,kb,state,args.need,
                               sources_file=args.sources,
                               aesthetic_library_path=args.aesthetic_library, aesthetic_query=args.aesthetic_query or None,
                               style_context_enabled=args.style_context_enabled,
                               execution_profile=args.execution_profile, interaction_profile=args.interaction_profile,
                               knowledge_config=read_json(args.knowledge_config) if args.knowledge_config else None,
                               workflow_profile=args.workflow_profile, deliverable_kind=args.deliverable_kind)
            if args.library:
                library.search(args.library,packet['need']['intent'],run=args.out)
                packet=read_packet(args.out)
            if args.template_library:
                query=args.template_query or packet['need']['intent']
                template_library.attach_search(args.out, template_library.search(args.template_library, query, packet['need']['domain']))
                packet=read_packet(args.out)
            result={"run":str(args.out.resolve()),"next":"读取 HOST_TASK.md，由Codex完成需求解释与方案设计", "digest":packet['digest']}
        elif cmd=="refresh":
            packet=read_packet(args.run)
            _validate_production_options(args, packet)
            if args.reuse_parent_input and not args.out:
                raise AgentError('--reuse-parent-input 仅用于 refresh --out')
            if args.edit_reference and not args.out:
                raise AgentError('--edit-reference 仅用于 refresh --out 的关联编辑')
            if args.parent_request_id and (not args.out or not args.edit_reference):
                raise AgentError('--parent-request-id 仅用于 refresh --out --edit-reference')
            if args.out:
                if not args.sources:
                    raise AgentError('新需求版本需要--sources中新增的用户消息')
                from .requirement_revision import branch_requirements
                packet=branch_requirements(args.run,args.out,kb,state,args.need,args.sources,
                                           args.edit_reference,args.parent_request_id,reuse_parent_input=args.reuse_parent_input)
                args.run=args.out
            else:
                if (args.run/'host-image-tests.json').exists() or (args.run/'image-tests.json').exists():
                    raise AgentError('已试图不能改变原标准；使用refresh --out创建关联需求版本')
                packet=refresh_packet(packet,args.run,kb,state,Need.model_validate(read_json(args.need)),"host_supplied",
                                      sources_file=args.sources,
                                      aesthetic_library_path=args.aesthetic_library, aesthetic_query=args.aesthetic_query or None,
                                      style_context_enabled=args.style_context_enabled)
            if args.library:
                library.search(args.library,packet['need']['intent'],run=args.run)
                packet=read_packet(args.run)
            if args.template_library:
                query=args.template_query or packet['need']['intent']
                template_library.attach_search(args.run, template_library.search(args.template_library, query, packet['need']['domain']))
                packet=read_packet(args.run)
            result={"digest":packet['digest'],"hits":len(packet['retrieval']['hits']),"missing":packet['retrieval']['missing']}
        elif cmd=="finalize":
            r=host.finalize(args.run,args.solution)
            result={"result":str((args.run/'result.html').resolve()),"status":r['status'],"visual_validation":r['visual_validation']}
        elif cmd=="preflight":
            result=host.preflight(args.run,args.solution)
        elif cmd=='prepare-compilation':
            result=host.prepare_compilation(args.run,args.plan,args.target,args.tool)
        elif cmd=="observe":
            packet=host.observe(args.run,args.report,args.tool_note)
            result={"digest":packet['digest'],"visual_coverage":packet['visual_coverage']}
        elif cmd=="prepare-eval":
            result=host.prepare_eval(args.run,args.image)
        elif cmd=="choose":
            result=host.choose(args.run,args.report)
        elif cmd=="add-host-reference":
            from .workflow import add_host_reference
            packet=add_host_reference(args.run,read_json(args.file))
            result={"digest":packet["digest"],"host_visible_reference_count":len(packet["host_visible_references"]),
                    "next":"按宿主实际可见图片 observe；元数据不是像素验证"}
        elif cmd=="add-reference":
            packet=add_reference(args.run,args.image,args.id,args.role,args.note,args.rights,args.source_url,args.credit,args.evaluation_only)
            result={"digest":packet['digest'],"reference_count":len(packet['references']),"next":"实际看图后observe，并更新solution"}
        elif cmd=="run":
            if not 1<=args.max_calls<=20:
                raise AgentError('--max-calls 应为1至20')
            fetcher=PublicFetcher(COMMONS_HOSTS,consent=args.allow_network) if args.collect_references else None
            provider=OpenAIProvider(consent=args.allow_cloud,budget=Budget(args.max_calls,args.image_tests))
            if args.image_tests and not provider.image_model:
                raise AgentError('指定了图片测试但未配置LIU_IMAGE_MODEL；未发起模型请求')
            r=run_cloud(args.brief,args.out,kb,state,provider,args.research,args.image_tests,fetcher,
                        args.aesthetic_library,args.aesthetic_query,args.template_library,args.template_query,
                        style_context_enabled=args.style_context_enabled, allow_library_analysis=args.allow_library_analysis,
                        sources_file=args.sources, execution_profile=args.execution_profile, interaction_profile=args.interaction_profile,
                        knowledge_config=read_json(args.knowledge_config) if args.knowledge_config else None,
                        workflow_profile=args.workflow_profile, deliverable_kind=args.deliverable_kind)
            result={"run":str(args.out.resolve()),"status":r['status'],"result":str((args.out/'result.html').resolve()) if (args.out/'result.html').exists() else None}
            if r.get('workflow_completion'):
                result.update(workflow_completion=r['workflow_completion'], next_action=r.get('next_action'))
        elif cmd=="demo":
            packet=make_packet(ROOT/'examples/demo-brief.json',args.out,kb,state,ROOT/'examples/demo-need.json')
            # Authored installation examples use the read/export compatibility
            # path; never invent a real user source or generation allowance.
            from .workflow import save_packet
            packet.pop('source_contract_version', None)
            packet.pop('input_sources', None)
            # Static authored prompts predate model adaptation; this demo is
            # export-only and has no user source permitting model dispatch.
            packet.pop('model_adaptation_version', None)
            packet['producer'] = 'authored_demo_not_model_output'
            from .reference_gate import build_reference_policy
            packet['reference_policy'] = build_reference_policy(packet)
            packet = save_packet(packet, args.out)
            raw=read_json(ROOT/'examples/demo-solution.json');raw['packet_digest']=packet['digest']
            # Authored choices do not establish use of retrieved core cards.
            raw['spec']['selected_knowledge_ids'] = []
            raw['design']['knowledge_skip_reason'] = '作者编写的离线安装演示，不声称采用检索知识或完成模型编译。'
            result=finish(args.out,Solution.model_validate(raw),'authored_demo_not_model_output')
            result={"result":str((args.out/'result.html').resolve()),"mode":"作者编写的安装演示；没有调用模型，没有生成图片"}
        elif cmd=="record-test":
            result=host.record_test(args.run,args.image,args.review,args.prompt,args.model_label,args.tool_note,args.target)
        elif cmd=="research":
            provider=OpenAIProvider(consent=args.allow_cloud,budget=Budget(1,0));result=provider.research([args.query]);write_json(args.out,result)
        elif cmd=="attach-research":
            from .workflow import ensure_references_mutable
            ensure_references_mutable(args.run)
            packet=read_packet(args.run);report=read_json(args.report)
            if (args.run/"host-image-tests.json").exists() or (args.run/"image-tests.json").exists():
                raise AgentError("已有候选，不在本任务内重写依据；建立新任务")
            if not isinstance(report.get('summary'),str) or not report.get('sources'):
                raise AgentError('研究记录必须有summary和sources')
            for source in report['sources']:
                if not source.get('url','').startswith('https://'):
                    raise AgentError('来源需要HTTPS链接')
                source['visual_inspection']=False
            report['state']='host_supplied_text_evidence'
            from .workflow import save_packet
            packet['research']=report
            packet=save_packet(packet,args.run)
            result={'digest':packet['digest'],'notice':'更新了任务证据，旧solution失效；没有修改核心知识库。'}
        elif cmd=="kb":
            if args.action=='search':result=Retriever(load_cards(kb,state)).search(args.query,args.domain,args.scope,args.role,limit=max(1,min(args.limit,30)))
            else:
                from .contracts import Card
                candidate=Card.model_validate(read_json(args.candidate))
                current=next((c for c in load_cards(kb,state) if c.id==candidate.id),None)
                if current and candidate.version<=current.version:
                    raise AgentError('知识版本必须高于已有核心/动态卡片版本')
                result=promote_card(args.candidate,state,args.evidence,args.note)
        elif cmd=="models":
            pp=profiles(kb,state)
            if args.action=='list':result=[{**p.model_dump(),'stale':stale(p)} for p in pp.values()]
            elif args.action=='check-updates':
                hosts={urlsplit(s.url).hostname for p in pp.values() for s in p.sources if s.url.startswith('https://')}
                hosts|={'learn.chatgpt.com','developers.openai.com'}
                result=check_updates(pp,state,PublicFetcher(hosts,consent=args.allow_network))
            elif args.action=='install':result=install(args.candidate,kb,state,args.expected_version,args.allow_experimental)
            elif args.action=='rollback':result=rollback(args.id,args.version,state)
            else:
                if args.id not in pp:raise AgentError('请先为新模型建立独立配置；propose用于更新已有模型')
                if args.out.exists():raise AgentError('候选文件已存在，请使用新文件名')
                p=pp[args.id];provider=OpenAIProvider(consent=args.allow_cloud,budget=Budget(2,0))
                domains=list({urlsplit(s.url).hostname for s in p.sources if s.url.startswith('https://')})
                research=provider.research([p.label+' official prompting guide version changes'],domains)
                candidate=provider.structured('根据官方检索证据修订数据型ModelProfile；不要宣称效果已验证，不生成代码。id不变，version加1，status=experimental，evaluation_files=[]。只改有来源的规则。',{'current':p.model_dump(),'research':research},ModelProfile)
                raw=candidate.model_dump();raw.update(id=p.id,version=p.version+1,status='experimental',evaluation_files=[],last_checked=now()[:10])
                # Provenance comes from actual tool citations, not model-invented URLs.
                raw['sources']=[{'label':s.get('title') or '检索来源','url':s['url'],'kind':'official' if urlsplit(s['url']).hostname in domains else 'external','checked_at':now()[:10]} for s in research['sources']]
                candidate=ModelProfile.model_validate(raw);write_json(args.out,candidate.model_dump());write_json(args.out.with_suffix('.research.json'),research)
                result={'candidate':str(args.out),'active_unchanged':True,'status':'experimental'}
        elif cmd=="materials":
            if args.action=='collect':result=collect_commons(args.query,args.out,PublicFetcher(COMMONS_HOSTS,consent=args.allow_network),args.limit)
            else:
                manifest=read_json(args.manifest)
                for item in manifest.get('items',[]):
                    file=within(args.manifest.parent,item['file'])
                    if sha(file.read_bytes())!=item['sha256']:raise AgentError('素材文件已改变')
                result=analyze_materials(manifest,args.manifest.parent,OpenAIProvider(consent=args.allow_cloud,budget=Budget(1,0)),Need.model_validate(read_json(args.need)).model_dump()).model_dump()
        elif cmd=="schemas":
            for name,model in {'Brief':Brief,**OUTPUT_MODELS}.items():write_json(args.out/(name+'.schema.json'),model.model_json_schema())
            result={'written_to':str(args.out)}
        from .messages import build_presentation, render_text
        task_commands = {'status', 'resume', 'prepare', 'refresh', 'run', 'demo', 'finalize',
                         'preflight', 'observe', 'add-reference', 'add-host-reference', 'prepare-eval', 'choose',
                         'record-test', 'record-result', 'record-feedback', 'dispatch', 'authorize-generation'}
        task_run = getattr(args, 'run', None) or getattr(args, 'out', None)
        presentation = None
        if cmd in task_commands and task_run and isinstance(result, dict):
            presentation = build_presentation(task_run, inspection=result if cmd in ('status', 'resume') else None)
            result['presentation'] = presentation
            result['active_question'] = presentation['active_question']
        if args.format == 'human' and presentation:
            print(render_text(presentation))
            recall = result.get('feedback_recall') or {}
            if recall.get('status') == 'registration_failed':
                print(recall['notice'])
                print(recall['detail'])
        else:
            print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (AgentError,ValidationError,FileNotFoundError,json.JSONDecodeError,OSError) as exc:
        if isinstance(exc,ValidationError):
            message='字段校验失败：'+'; '.join('.'.join(map(str,x['loc']))+': '+x['msg'] for x in exc.errors(include_input=False)[:6])
        else:message=str(exc)
        from .messages import failure_message
        failure = failure_message(getattr(args, 'run', None) or getattr(args, 'out', None), args.command, message)
        if args.format == 'human':
            print('\n'.join([*failure['summary'], failure['reason'], failure['next_action'], failure['user_action']]), file=sys.stderr)
        else:
            print(json.dumps({'error':message, 'status':'stopped_not_success', 'failure':failure},
                             ensure_ascii=False),file=sys.stderr)
        return 2
