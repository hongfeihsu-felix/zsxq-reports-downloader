# 知识星球投行报告下载器

自动从知识星球下载投行研究报告，支持行业/公司过滤、每日定时任务、邮件通知。

## 功能特点

1. **下载报告过滤**：覆盖以下行业和公司：
   - 行业：AI核心供应链（GPU/TPU/CSP ASIC，Memory，interconnect/connecting，cooling，power），foundry， top fabless companies
   - 公司：
     - Foundries: TSMC/SMIC/UMC/Intel/Samsung/Global Foundries, TowerSemi, PSMC, Huahong Group
     - Fabless: Broadcom/Qualcomm/MediaTek/Marvell/
     - Interconnect/Connecting: Lumentum/Coherent/Fabrinet/新易盛/中际旭创/联特科技
     - Memory: SK Hynix/Samsung/Micron/SDNK.us/WDC.US/STX.us/兆易创新/Gigadevice/佰维存储/江波龙
     - US Giant Tech: NVDA/AMZN/MSFT/META/TSLA/GOOGLE/AAPL/AMD/Intel/MRVL/ORCL/AVGO
     - Power: XE(X-Energy)/BE(Bloom Energy)
     - AI Application: Palantir/CoreWeave/Tempus AI/
     - MISC: AMKR/MPWR/BABA

2. **自动下载周期**：每天 23:50 启动任务，捞取当天报告
3. **报告保存路径**：`~/hermes_reports/Investment_Banking_Report/子目录（格式YYYYMMDD）`
4. **下载结果邮件发送**：包含下载成功列表/失败列表，only file name

## 安装

```bash
# 克隆仓库
git clone https://github.com/hongfeihsu-felix/zsxq-reports-downloader.git
cd zsxq-reports-downloader

# 复制配置文件
cp config.example.json config.json
```

## 配置

编辑 `config.json`，填入以下信息：

```json
{
    "cookie": "YOUR_ZSXQ_ACCESS_TOKEN_HERE",
    "group_id": "YOUR_GROUP_ID_HERE",
    "smtp_server": "smtp.qq.com",
    "smtp_port": 587,
    "sender_email": "your_email@foxmail.com",
    "sender_password": "YOUR_EMAIL_AUTH_CODE",
    "recipient_email": "your_email@foxmail.com",
    "proxy": {
        "http": "socks5://127.0.0.1:7897",
        "https": "socks5://127.0.0.1:7897"
    }
}
```

### 配置说明

| 配置项 | 说明 |
|--------|------|
| `cookie` | 知识星球登录 Cookie，从浏览器开发者工具抓取 |
| `group_id` | 星球 ID，从星球 URL 中获取（如 `https://wx.zsxq.com/group/51111812185184` 中的 `51111812185184`） |
| `sender_email` | 发件人邮箱（QQ邮箱） |
| `sender_password` | QQ邮箱授权码（非登录密码） |
| `recipient_email` | 收件人邮箱 |
| `proxy` | SOCKS5 代理地址（如使用 VPN） |

### 获取 Cookie

1. 在浏览器中登录知识星球网页版（wx.zsxq.com）
2. 按 F12 打开开发者工具 → Network 标签
3. 找一个 `api.zsxq.com` 的请求
4. 在请求头中找到 `Cookie` 字段，复制完整值

### 获取 QQ 邮箱授权码

1. 登录 QQ 邮箱：https://mail.qq.com
2. 设置 → 账户 → POP3/IMAP/SMTP/Exchange 服务
3. 开启 SMTP 服务 → 生成授权码

## 运行

```bash
# 手动运行（测试）
python3 zsxq_downloader.py

# 定时任务（每天 23:50 自动运行）
# 通过 macOS LaunchAgent 实现，已配置在 ~/Library/LaunchAgents/com.investment.downloader.zsxq-reports.plist
```

## 邮件通知示例

```
投行报告下载完成 - 2026-05-08

处理文件数: 6
成功: 6
失败: 0

=== 成功下载 (6) ===
  - MS-Coherent Corp Executing to Plan-260507.pdf
  - Goldman Sachs-Scaling the AI Infrastructure...
  ...

=== 下载失败 (0) ===
```

## 文件结构

```
zsxq-reports-downloader/
├── .gitignore
├── config.example.json    # 配置示例
├── config.json            # 实际配置（不上传）
├── zsxq_downloader.py     # 主程序
└── README.md
```

## 注意事项

- Cookie 有有效期，过期后需重新抓取并更新配置
- 下载间隔 30-90 秒，防止被封
- 已下载文件会记录到数据库，不会重复下载