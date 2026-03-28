import os
import logging
from flask import Flask, render_template_string, jsonify
from datetime import datetime
from vision_engine import VisionEngine

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VISION")

app = Flask(__name__)
engine = VisionEngine()

# Vision Dark-Mode UI
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>VISION | Warrior Trading Bot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        body { background: #06060f; color: #c4c4d8; font-family: 'JetBrains Mono', monospace; padding: 20px; }
        .status-bar { display: flex; justify-content: space-between; margin-bottom: 20px; padding: 10px; border-bottom: 1px solid #18182e; }
        .glow { color: #a855f7; text-shadow: 0 0 10px #a855f7; }
        .card { background: #0b0b18; border: 1px solid #18182e; border-left: 4px solid #a855f7; border-radius: 8px; padding: 15px; margin-bottom: 12px; }
        .ticker { font-size: 1.4rem; font-weight: bold; color: #fff; }
        .pill { background: #18182e; padding: 4px 10px; border-radius: 4px; font-size: 0.8rem; margin-right: 5px; }
        .green { color: #22c55e; }
        .red { color: #ef4444; }
        .test-btn { background: #a855f7; color: white; border: none; padding: 10px 20px; border-radius: 8px; cursor: pointer; font-family: monospace; margin-bottom: 20px; font-size: 1rem; }
        .test-btn:hover { background: #7c3aed; }
    </style>
</head>
<body>
    <div class="status-bar">
        <div><span class="glow">●</span> VISION | Warrior Trading Bot</div>
        <div id="clock"></div>
    </div>
    <button class="test-btn" onclick="triggerScan()">🔄 Manual Test Scan</button>
    <h2>🏆 Top 10 Momentum Stocks</h2>
    <div id="top10">
        <div class="card">Loading scanner data... Click the button above to test.</div>
    </div>
    <script>
        function triggerScan() {
            document.getElementById('top10').innerHTML = '<div class="card">⏳ Scanning 100+ stocks... Please wait 10-15 seconds.</div>';
            fetch('/api/test_scan')
                .then(res => res.json())
                .then(data => {
                    alert(data.message);
                    updateDashboard();
                })
                .catch(err => {
                    alert('Error: ' + err);
                });
        }
        function updateDashboard() {
            fetch('/api/top10')
                .then(res => res.json())
                .then(data => {
                    if(data.candidates && data.candidates.length > 0) {
                        let html = '';
                        data.candidates.forEach((stock, i) => {
                            html += `
                                <div class="card">
                                    <div class="ticker">${i+1}. $${stock.symbol}</div>
                                    <div>
                                        <span class="pill">💰 $${stock.price}</span>
                                        <span class="pill ${stock.pct_change > 0 ? 'green' : 'red'}">Gap: ${stock.pct_change}%</span>
                                        <span class="pill">📊 RVOL: ${stock.rvol}x</span>
                                        <span class="pill">🪙 Float: ${stock.float}M</span>
                                        <span class="pill">🚨 ${stock.reversal ? 'REVERSAL READY' : 'MOMENTUM'}</span>
                                    </div>
                                </div>
                            `;
                        });
                        document.getElementById('top10').innerHTML = html;
                    } else {
                        document.getElementById('top10').innerHTML = '<div class="card">No qualified stocks found. Try again or wait for market hours.</div>';
                    }
                    document.getElementById('clock').innerText = new Date().toLocaleTimeString('en-US', {timeZone: 'America/New_York'});
                });
        }
        updateDashboard();
        setInterval(updateDashboard, 30000);
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/status')
def status():
    return jsonify({
        "status": "online",
        "bot": "Vision Warrior Trading Bot",
        "uptime_sync": datetime.now().isoformat(),
        "engine_active": engine.scheduler.running if engine.scheduler else False
    })

@app.route('/api/top10')
def get_top10():
    """Returns the current top 10 ranked candidates"""
    return jsonify({
        "candidates": engine.top_candidates if hasattr(engine, 'top_candidates') else []
    })

@app.route('/api/test_scan')
def test_scan():
    """Manual test endpoint to force a scan (weekend testing only)"""
    try:
        engine.hunt_momentum()
        return jsonify({"status": "success", "message": "Manual scan triggered. Check Telegram for alerts."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == "__main__":
    engine.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
