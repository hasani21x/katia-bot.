import requests, time
from datetime import datetime

# بيانات Katia11 الملكية
TOKEN = "7749364195:AAHGXvt3Ml61-XSIJ0Kb-iLQ_erx6cKP6FA"
ID = "6192895163"

SYMBOLS = {
    "1": {"name": "الذهب (XAU/USD) 🟡", "pair": "PAXGUSDT"},
    "3": {"name": "الباوند (GBP/USD) 🇬🇧", "pair": "GBPUSDT"},
    "4": {"name": "البيتكوين (BTC/USD) 🧡", "pair": "BTCUSDT"}
}

def get_analysis(pair):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=5m&limit=100"
        res = requests.get(url, timeout=10).json()
        closes = [float(x[4]) for x in res]
        p = closes[-1]
        
        # حساب المؤشرات الفنية (RSI & MACD)
        diff = [closes[i] - closes[i-1] for i in range(1, 15)]
        up = sum([d for d in diff if d > 0]) / 14
        down = abs(sum([d for d in diff if d < 0])) / 14
        rsi = 100 - (100 / (1 + (up/down))) if down != 0 else 100
        macd = (sum(closes[-12:]) / 12) - (sum(closes[-26:]) / 26)
        
        return p, rsi, macd
    except: return None

def send_luxury_msg(name, p, r, m):
    trend = "🟢 شراء قوي" if r < 35 else "🔴 بيع قوي" if r > 65 else "⏳ منطقة حيادية"
    msg = f"""
🏛️ <b>KATIA11 ROYAL SYSTEM (RENDER)</b>
━━━━━━━━━━━━━━━━━━━━━
📦 <b>الأصل:</b> <code>{name}</code>
💵 <b>السعر:</b> <code>{p:,.2f}</code>
📊 <b>الحالة:</b> <b>{trend}</b>
━━━━━━━━━━━━━━━━━━━━━
📈 <b>البيانات الفنية:</b>
• <b>RSI:</b> <code>{r:.1f}</code> | <b>MACD:</b> <code>{m:.4f}</code>
━━━━━━━━━━━━━━━━━━━━━
🎯 <b>توصية السكالبينج:</b>
• <b>TP:</b> <code>{p*1.002:,.2f}</code> | <b>SL:</b> <code>{p*0.998:,.2f}</code>
━━━━━━━━━━━━━━━━━━━━━
🛡️ <b>المحلل الذكي:</b> <code>@Katia11_Pro</code>
⏱️ <b>التوقيت:</b> {datetime.now().strftime('%H:%M:%S')}
    """
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": ID, "text": msg, "parse_mode": "HTML"})

def main():
    print("🚀 Katia11 System is LIVE on Render!")
    last_id = 0
    while True:
        try:
            updates = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_id+1}&timeout=30").json()
            for u in updates.get("result", []):
                last_id = u["update_id"]
                if "message" in u and "text" in u["message"]:
                    txt = u["message"]["text"].strip()
                    if txt in SYMBOLS:
                        data = get_analysis(SYMBOLS[txt]['pair'])
                        if data: send_luxury_msg(SYMBOLS[txt]['name'], *data)
        except: time.sleep(5)
        time.sleep(1)

if __name__ == "__main__":
    main()
