import requests, time
from datetime import datetime

TOKEN = "7749364195:AAHGXvt3Ml61-XSIJ0Kb-iLQ_erx6cKP6FA"
ID = "6192895163"

SYMBOLS = {
    "1": {"name": "الذهب (XAU/USD) 🟡", "pair": "PAXGUSDT"},
    "3": {"name": "الباوند (GBP/USD) 🇬🇧", "pair": "GBPUSDT"},
    "4": {"name": "البيتكوين (BTC/USD) 🧡", "pair": "BTCUSDT"}
}

def get_market_data(pair):
    try:
        res = requests.get(f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=5m&limit=100").json()
        closes = [float(x[4]) for x in res]
        p = closes[-1]
        diff = [closes[i] - closes[i-1] for i in range(1, 15)]
        up = sum([d for d in diff if d > 0]) / 14
        down = abs(sum([d for d in diff if d < 0])) / 14
        rsi = 100 - (100 / (1 + (up/down))) if down != 0 else 100
        macd = (sum(closes[-12:]) / 12) - (sum(closes[-26:]) / 26)
        depth = requests.get(f"https://api.binance.com/api/v3/depth?symbol={pair}&limit=10").json()
        wall = float(depth['bids'][0][0]) if depth['bids'] else p
        eur_res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=EURUSDT").json()
        dxy = 100 / float(eur_res['price'])
        return p, rsi, macd, wall, dxy
    except: return None

def send_royal_report(name, p, r, m, w, dxy):
    trend = "🚀 انفجار شرائي" if r < 35 and m > 0 else "📉 هبوط وشيك" if r > 65 and m < 0 else "⚖️ منطقة توازن"
    msg = f"🏛️ <b>KATIA11 ROYAL SYSTEM PRO</b>\n━━━━━━━━━━━━━━━━━━━━━\n📦 <b>الأصل:</b> <code>{name}</code>\n💵 <b>السعر الحالي:</b> <code>{p:,.2f}</code>\n📊 <b>حالة السوق:</b> <b>{trend}</b>\n━━━━━━━━━━━━━━━━━━━━━\n🔍 <b>رادار الحيتان (Bookmap):</b>\n• <b>جدار السيولة:</b> <code>{w:,.2f}</code>\n• <b>مؤشر الدولار 💵:</b> <code>{dxy:.2f}</code>\n━━━━━━━━━━━━━━━━━━━━━\n📈 <b>الفنيات:</b>\n• <b>RSI:</b> <code>{r:.1f}</code> | <b>MACD:</b> <code>{m:.4f}</code>\n━━━━━━━━━━━━━━━━━━━━━\n🎯 <b>TP:</b> <code>{p*1.003:,.2f}</code> | <b>SL:</b> <code>{p*0.997:,.2f}</code>\n━━━━━━━━━━━━━━━━━━━━━\n🛡️ ⏱️ {datetime.now().strftime('%H:%M:%S')}"
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": ID, "text": msg, "parse_mode": "HTML"})

def main():
    print("🚀 Katia11 is LIVE!")
    last_id = 0
    while True:
        try:
            updates = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_id+1}&timeout=30").json()
            for u in updates.get("result", []):
                last_id = u["update_id"]
                if "message" in u and "text" in u["message"]:
                    txt = u["message"]["text"].strip()
                    if txt in SYMBOLS:
                        data = get_market_data(SYMBOLS[txt]['pair'])
                        if data: send_royal_report(SYMBOLS[txt]['name'], *data)
        except: time.sleep(5)
        time.sleep(1)

if __name__ == "__main__":
    main()
