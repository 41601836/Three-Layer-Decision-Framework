import json

with open('config.json', 'r', encoding='utf-8') as f:
    cfg = json.load(f)

# 更新 strategy 节
cfg['strategy']['version'] = 'v3.3 Final'
cfg['strategy']['name'] = '主力资金提前嗅探·横盘吸筹识别器 v3.3 Final'
cfg['strategy']['position_limits'] = {
    "attack":  {"strong": 0.15, "medium": 0.08},   # 进攻：强信号15%（回测验证）
    "defense": {"strong": 0.05, "medium": 0.02},   # 防守：强信号5%（回测验证最大回撤-34.5%）
    "neutral": {"strong": 0.08, "medium": 0.03},   # 中性（备用）
}
cfg['strategy']['max_single_stock'] = 0.20          # 单股绝对上限20%

with open('config.json', 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)

print("config.json 已更新：")
print(f"  版本: {cfg['strategy']['version']}")
print(f"  defense.strong: {cfg['strategy']['position_limits']['defense']['strong']*100:.0f}%")
print(f"  max_single_stock: {cfg['strategy']['max_single_stock']*100:.0f}%")
print("✅ 完成")
