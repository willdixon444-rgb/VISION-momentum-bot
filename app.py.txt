import eventlet
eventlet.monkey_patch()

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

# Vision Dark-Mode UI (Merlin Architecture Inspired)
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>VISION | Momentum</title>
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
    </style>
</head>
<body>
    <div class="status-bar">
        <div><span class="glow">●</span> VISION LIVE</div>
        <div id="clock">07:00:00 AM ET</div>
    </div>
    <h2>Active Gappers (Top 100)</h2>
    <div id="heatmap">
        <div class="card">
            <div class="ticker">$ASTS</div>
            <div>
                <span class="pill">RVOL: 12.4x</span>
                <span class="pill">GAP: <span class="green">+18.2%</span></span>
                <span class="pill">FLOAT: 14M</span>
            </div>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/status')
def status():
    """Target for your Anytimer/UptimeRobot ping"""
    return jsonify({
        "status": "online",
        "bot": "Vision Momentum",
        "uptime_sync": datetime.now().isoformat(),
        "engine_active": engine.scheduler.running
    })

if __name__ == "__main__":
    # Start the background hunter before the web server
    engine.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
