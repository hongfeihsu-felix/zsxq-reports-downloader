#!/usr/bin/env python3
"""推送 Computex Day0 报道到公众号草稿箱"""
import sys
sys.path.insert(0, "/Users/hongfeihsu/ClaudeCode/hermes")

from wechat_push import push_draft

TITLE = "Computex Day0 现场速递｜老黄掏出 RTX Spark，PC 行业 40 年平静被打破"

HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:0 auto;padding:16px;color:#333;background:#fff;line-height:1.8;font-size:15px">

<h1 style="font-size:18px;border-bottom:2px solid #dc3545;padding-bottom:10px;margin:0 0 16px;line-height:1.4">Computex Day0 现场速递｜老黄掏出 RTX Spark，PC 行业 40 年平静被打破</h1>

<p style="color:#888;font-size:12px;margin:-8px 0 16px">台北南港 · Day0 深夜整理</p>

<p>台北南港，熟悉的皮衣，熟悉的脚步声。Jensen Huang 今天在 Computex Day0 的主题演讲，一句话总结：<strong>NVIDIA 正式杀入 PC 处理器市场。</strong></p>

<!-- 主角：RTX Spark -->
<h2 style="font-size:16px;border-left:4px solid #76b900;padding-left:10px;margin:24px 0 12px">主角：RTX Spark 超级芯片</h2>

<p>这不是一张显卡，这是一颗 <strong>SoC</strong>——CPU + GPU 合封，直接塞进笔记本主板，跟苹果 M 系列一个路数，但更激进：</p>

<div style="background:#f8f9fa;padding:12px 16px;margin:10px 0;border-radius:8px;font-size:14px;line-height:2">
<b>CPU</b>：20 核 Arm 架构（MediaTek 联合设计，台积电 3nm）<br>
<b>GPU</b>：Blackwell 架构，6144 CUDA Core，约等于笔记本 RTX 5070<br>
<b>统一内存</b>：最高 128GB LPDDR5X，NVLink 互联带宽 600GB/s<br>
<b>AI 算力</b>：1 petaFLOP（FP4），本地跑 120B 大模型<br>
<b>功耗</b>：轻载个位数瓦特，满载 80W<br>
<b>厚度</b>：可塞进 14mm 笔记本
</div>

<p>老黄原话：<em>"这是 40 年来第一次，PC 被彻底重新设计。"</em></p>

<!-- 五巨头众生相 -->
<h2 style="font-size:16px;border-left:4px solid #76b900;padding-left:10px;margin:24px 0 12px">五巨头众生相</h2>

<p><strong style="color:#76b900">NVIDIA — 进攻者</strong></p>
<p>从数据中心杀回消费端，一手 CUDA 生态，一手 Arm CPU，软硬通吃。逻辑很直白：未来的 PC 是 AI Agent 的本地容器，谁能让你在本地跑满血大模型，谁就定义下一代设备。</p>

<p><strong style="color:#1e90ff">MediaTek — 幕后功臣</strong></p>
<p>N1X 的 Arm CPU 部分由联发科联合设计。发哥在手机 SoC 积累了十几年的低功耗和异构调度经验，这次直接平移到了 PC 战场。从手机芯片厂到 PC 核心供应商，这一步够大。</p>

<p><strong style="color:#0071c5">Intel — 同一天反击</strong></p>
<p>老黄话音未落，Intel 就在同一场子亮了两张牌：</p>
<div style="background:#f0f7ff;padding:10px 14px;margin:8px 0;border-radius:6px;font-size:14px">
1. <b>Xeon 6+</b>（Intel 18A 制程，数据中心 AI 管控层定位）<br>
2. <b>"Crescent Island" AI 推理芯片</b>（便宜、风冷、专抢 NVIDIA 中端推理市场）
</div>
<p>你打我家 PC，我打你家数据中心，双向入侵。</p>

<p><strong style="color:#5c2d91">Microsoft — 最大赌注</strong></p>
<p>Surface Laptop Ultra 成为 RTX Spark 首发旗舰。微软在 Windows 11 上做了大量适配：Prism 模拟器优化 x86 兼容、OpenShell 安全运行时、异构调度框架。14 年前 Surface RT + NVIDIA Tegra 惨败收场，这次卷土重来，赌注比上次大得多。</p>

<p><strong style="color:#ed1c24">AMD — 暂时沉默</strong></p>
<p>Day0 没有正式回应。但处境微妙：NVIDIA 从 AI GPU 向下挤压 PC，Intel 从 x86 向上反攻数据中心，AMD 两个方向同时承压。好在 Ryzen 和 Instinct 产品线还在推进，静观其变。</p>

<!-- 一句话划重点 -->
<h2 style="font-size:16px;border-left:4px solid #76b900;padding-left:10px;margin:24px 0 12px">一句话划重点</h2>

<table style="width:100%;border-collapse:collapse;font-size:14px;margin:10px 0" border="1" bordercolor="#ddd">
<tr style="background:#f8f9fa"><td style="padding:8px 10px;font-weight:700;width:25%">RTX Spark</td><td style="padding:8px 10px">Arm CPU + Blackwell GPU 合体，PC 版的 M 系列打法</td></tr>
<tr><td style="padding:8px 10px;font-weight:700">合作方</td><td style="padding:8px 10px">MediaTek 做 CPU，微软做系统，台积电做制造</td></tr>
<tr style="background:#f8f9fa"><td style="padding:8px 10px;font-weight:700">时机</td><td style="padding:8px 10px">2026 秋季，30+ 款笔电，10+ 款台式机</td></tr>
<tr><td style="padding:8px 10px;font-weight:700">核心场景</td><td style="padding:8px 10px">本地 AI Agent 7×24 运行，不是跑分是跑模型</td></tr>
<tr style="background:#f8f9fa"><td style="padding:8px 10px;font-weight:700">市场格局</td><td style="padding:8px 10px">x86 双头垄断正式被打破，PC 走向三国杀</td></tr>
</table>

<!-- 现场金句 -->
<h2 style="font-size:16px;border-left:4px solid #76b900;padding-left:10px;margin:24px 0 12px">现场金句</h2>

<div style="background:#f8f9fa;padding:12px 16px;margin:10px 0;border-radius:8px">
<p style="margin:8px 0;font-style:italic;color:#555">"Everything starts with a Spark."</p>
<p style="margin:8px 0;font-style:italic;color:#555">"Microsoft and Nvidia are going to reinvent the PC. This is the first completely re-engineered, reinvented line of PCs that has happened in 40 years."</p>
<p style="margin:8px 0;font-style:italic;color:#555">"这次 PC 的重新发明，跟手机变成智能手机一样大。"</p>
</div>

<p style="margin-top:24px">老黄这次没把 AI 挂在嘴上当概念讲——他直接给了一个可以塞进 14mm 笔记本里的答案。至于 Intel 和 AMD 怎么接招，秋季见分晓。</p>

<div style="margin:24px 0 0;padding:10px 14px;background:#f0f0f0;border-radius:6px;font-size:10px;color:#999;line-height:1.6">
<b>免责声明：</b>本文由 Hermes AI 基于 Computex 2026 Day0 公开报道整理生成。内容仅供参考，不构成投资建议。<br>
<b>整理时间：</b>2026-06-01 · Hermes AI Research
</div>

</body></html>"""

DIGEST = "Jensen Huang在Computex Day0掏出RTX Spark超级芯片，NVIDIA正式杀入PC处理器市场。五巨头众生相，一篇速递讲清楚。"

if __name__ == "__main__":
    try:
        media_id = push_draft(TITLE, HTML, digest=DIGEST, author="Hermes")
        print(f"\n✅ 推送成功！media_id: {media_id}")
    except Exception as e:
        print(f"\n❌ 推送失败: {e}")
        sys.exit(1)
