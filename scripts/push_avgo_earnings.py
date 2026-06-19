#!/usr/bin/env python3
"""
Push AVGO earnings analysis to WeChat draft box.
Usage: python3 scripts/push_avgo_earnings.py [--preview]
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from wechat_push import push_draft, send_preview

HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:0 auto;padding:16px;color:#333;background:#fff">

<h1 style="font-size:18px;border-bottom:2px solid #dc3545;padding-bottom:10px;margin:0 0 16px;line-height:1.4">
AVGO Q2 FY2026 财报解读：业绩超预期，为何盘后大跌11%？
</h1>

<div style="background:#f8f9fa;border-left:4px solid #dc3545;padding:12px 16px;margin:10px 0;border-radius:0 8px 8px 0;font-size:13px;line-height:1.8">
  <div style="font-size:14px;font-weight:700;margin:0 0 8px;color:#dc3545">核心洞察</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #dc354520">
    博通Q2营收$221.9亿（+48% YoY），AI半导体$108亿（+143% YoY），双双beat预期。但Q3 AI指引$160亿低于共识$172亿，叠加未上调全年目标，盘后一度重挫11~13%——这是"买预期、卖事实"的经典预期修正，而非AI叙事逆转。
  </div>
</div>

<div style="background:#f8f9fa;border-left:4px solid #d2991d;padding:12px 16px;margin:10px 0;border-radius:0 8px 8px 0;font-size:13px;line-height:1.8">
  <div style="font-size:14px;font-weight:700;margin:0 0 8px;color:#d2991d">财务数据一览</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20">总营收 $221.9亿（共识$221.3亿）Beat</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20">调整后EPS $2.44（共识$2.39~2.40）Beat</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20">AI半导体 $108亿（+143% YoY）Beat</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20">基础设施软件 $71.8亿（预期$73.2亿）Miss</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20">净利润 $93.1亿（去年同期$49.7亿）+87%</div>
</div>

<div style="background:#f8f9fa;border-left:4px solid #d2991d;padding:12px 16px;margin:10px 0;border-radius:0 8px 8px 0;font-size:13px;line-height:1.8">
  <div style="font-size:14px;font-weight:700;margin:0 0 8px;color:#d2991d">Q3指引 vs 预期</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20">总营收指引 ~$294亿（共识$285~286亿）Above</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20;color:#dc3545">AI半导体指引 ~$160亿（共识$172亿）Below by 7%</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20">FY2027 AI目标 >$1000亿，维持不变，未上修</div>
</div>

<div style="background:#f8f9fa;border-left:4px solid #d2991d;padding:12px 16px;margin:10px 0;border-radius:0 8px 8px 0;font-size:13px;line-height:1.8">
  <div style="font-size:14px;font-weight:700;margin:0 0 8px;color:#d2991d">大跌的三大推手</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20"><b>1. Q3 AI指引miss：</b>$160亿 vs $172亿共识，差距虽仅7%，但在90x PE估值下任何miss都会被放大</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20"><b>2. 未上调全年目标：</b>CEO陈福阳重申FY2027 AI >$1000亿不变，市场此前price in了上修预期</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #d2991d20"><b>3. 获利了结：</b>财报前5天市值暴增~$3000亿，股价创新高$495，任何风吹草动都会触发抛售</div>
</div>

<div style="background:#f8f9fa;border-left:4px solid #3fb950;padding:12px 16px;margin:10px 0;border-radius:0 8px 8px 0;font-size:13px;line-height:1.8">
  <div style="font-size:14px;font-weight:700;margin:0 0 8px;color:#3fb950">操作建议</div>
  <div style="margin:2px 0;padding-left:8px;border-left:2px solid #3fb95020">
    AI定制芯片结构性需求未变（Google/Meta/OpenAI/Anthropic六大客户），短期情绪宣泄后关注$400~420支撑区间。多数分析师维持Strong Buy（25 Buy / 4 Hold），定性为"预期重置而非AI叙事受损"。等待Q3实际交付数据验证指引保守是否为烟雾弹。
  </div>
</div>

<p style="font-size:10px;color:#888;margin:8px 0 0;padding:8px 12px;background:#f5f5f5;border-radius:4px">
Sources: Broadcom IR, Yahoo Finance, TipRanks, NAI500, Futunn — June 3, 2026
</p>

<div style="margin:16px 0 0;padding:10px 14px;background:#f0f0f0;border-radius:6px;font-size:10px;color:#999;line-height:1.6">
<b>免责声明：</b>本文由Hermes AI自动生成，基于公开信息整理。内容仅供参考，不构成投资建议。投资有风险，决策须谨慎。<br>
<b>生成时间：</b>2026-06-04 · Hermes AI Research
</div>

</body></html>"""

TITLE = "AVGO Q2 FY2026 财报解读：业绩超预期，为何盘后大跌11%？"
DIGEST = "博通Q2营收$221.9亿（+48% YoY）beat预期，但Q3 AI指引$160亿低于共识$172亿，盘后一度跌超11%。拆解三大核心原因与后市展望。"

if __name__ == "__main__":
    preview = "--preview" in sys.argv

    print("Pushing AVGO earnings analysis to WeChat draft box...")
    media_id = push_draft(TITLE, HTML, digest=DIGEST)
    print(f"Done — media_id: {media_id}")

    if preview:
        send_preview(media_id)
