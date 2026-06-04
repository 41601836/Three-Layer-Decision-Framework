# tokens.py —— 全局密钥配置（禁止硬编码到其他文件）
# ⚠️  此文件已被 .gitignore 排除，绝不要提交到版本控制！
# 请复制本文件为 tokens.py 并填入你自己的密钥：
#   cp scripts/tokens.example.py scripts/tokens.py

# Tushare API Token
# 获取地址：https://tushare.pro/register?reg=your_invite_code
# 建议账户积分 >= 5000（日线、资金流等高频接口需要）
TOKEN = "在此填入你的 Tushare Token"

# 飞书自定义机器人 Webhook URL
# 创建方式：飞书群 → 设置 → 机器人 → 添加自定义机器人 → 复制 Webhook
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/你的机器人ID"
