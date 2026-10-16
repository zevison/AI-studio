from __future__ import annotations

# Seedance Studio 本地化吸收 Hypit 方法论后的工作流契约。
# 这里只保留方法/结构原则，不复制 Hypit 官方实现代码。
WORKFLOW_SCHEMA = "seedance-hypit-workflow/v1"

TRACKS = (
    "footage",            # 真实镜头/画面轨
    "dialogue",           # 口播轨；优先逐词锚定
    "captions",           # 画面文字/字幕轨
    "broll_candidates",   # B-roll 候选轨
    "effects_candidates", # 转场/特效候选轨
    "audio",              # 静音/音频结构轨
)

PRINCIPLES = (
    "工作流必须可编辑、可重跑，而不是一次性脚本。",
    "优先使用视频内容驱动的真实镜头边界，不按固定百分比硬切。",
    "有逐词时间戳时优先使用词锚点；缺失时安全回退到精确时间锚点。",
    "Hook、证明、CTA 等语义只允许作为带置信度和证据的候选，不伪装成确定事实。",
    "画面、口播、字幕、B-roll、特效、音频分别建轨，修改一条轨不应自动破坏其它轨。",
    "最终生成仍携带原参考视频，让视频模型看到真实运动、构图和时序。",
    "最终视频只使用用户明确确认的工作流版本。",
)

PRESERVE = ("镜头顺序", "真实动作链", "节奏关系", "证明结构", "未要求修改的编排")
REPLACE = ("用户指定的人物/产品/场景/语言", "原品牌 Logo", "原水印", "高识别度原文案与版权元素")
