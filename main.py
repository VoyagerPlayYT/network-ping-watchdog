import os
import time
import socket
import asyncio
from datetime import datetime
from typing import List, Dict, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

app = FastAPI(title="Network & Ping Watchdog")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Default targets list
targets = [
    {"id": "router", "name": "Домашний роутер", "host": "192.168.1.1", "port": 80, "type": "LAN"},
    {"id": "cloudflare", "name": "Cloudflare DNS", "host": "1.1.1.1", "port": 53, "type": "DNS"},
    {"id": "google", "name": "Google DNS", "host": "8.8.8.8", "port": 53, "type": "DNS"},
    {"id": "yandex", "name": "Яндекс Портал", "host": "ya.ru", "port": 443, "type": "WEB"},
    {"id": "github", "name": "GitHub Core", "host": "github.com", "port": 443, "type": "DEV"}
]

# In-memory monitoring metrics
metrics_store: Dict[str, Dict] = {}
for t in targets:
    metrics_store[t["id"]] = {
        "history": [],
        "current_ping": 0,
        "status": "ONLINE",
        "packet_loss": 0,
        "sent_count": 0,
        "lost_count": 0,
        "avg_ping": 0
    }

incidents_log = [
    {
        "time": datetime.now().strftime("%H:%M:%S"),
        "target": "Сетевой мониторинг",
        "type": "INFO",
        "message": "Система сторожевого контроля сети (Watchdog) запущена"
    }
]


def check_latency(host: str, port: int, timeout: float = 1.5) -> Optional[float]:
    """Быстрый замер задержки через TCP Socket handshake (не требует прав root/admin)"""
    t_start = time.perf_counter()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        t_end = time.perf_counter()
        return round((t_end - t_start) * 1000, 1)
    except socket.timeout:
        return None
    except Exception:
        # Для DNS / роутера иногда порт закрыт, но узел отвечает отказом (RST), что тоже означает доступность
        t_end = time.perf_counter()
        return round((t_end - t_start) * 1000, 1)
    finally:
        s.close()


async def background_watchdog_loop():
    """Фоновый цикл непрерывного мониторинга"""
    while True:
        now_str = datetime.now().strftime("%H:%M:%S")
        for t in list(targets):
            tid = t["id"]
            if tid not in metrics_store:
                metrics_store[tid] = {
                    "history": [], "current_ping": 0, "status": "ONLINE",
                    "packet_loss": 0, "sent_count": 0, "lost_count": 0, "avg_ping": 0
                }

            store = metrics_store[tid]
            store["sent_count"] += 1

            # Замеряем в отдельном потоке, чтобы не блокировать event loop
            latency = await asyncio.to_thread(check_latency, t["host"], t["port"])

            if latency is not None:
                store["current_ping"] = latency
                store["status"] = "ONLINE" if latency < 150 else "DEGRADED"
                store["history"].append({"time": now_str, "val": latency})
            else:
                store["lost_count"] += 1
                store["current_ping"] = None
                if store["status"] != "OFFLINE":
                    store["status"] = "OFFLINE"
                    incidents_log.insert(0, {
                        "time": now_str,
                        "target": t["name"],
                        "type": "ERROR",
                        "message": f"Потеря пакетов! Узел {t['host']} недоступен"
                    })
                store["history"].append({"time": now_str, "val": None})

            if len(store["history"]) > 20:
                store["history"].pop(0)

            # Расчет потерь
            if store["sent_count"] > 0:
                store["packet_loss"] = round((store["lost_count"] / store["sent_count"]) * 100, 1)

            # Расчет среднего пинга
            valid_vals = [h["val"] for h in store["history"] if h["val"] is not None]
            if valid_vals:
                store["avg_ping"] = round(sum(valid_vals) / len(valid_vals), 1)

        await asyncio.sleep(2.0)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(background_watchdog_loop())


class NewTargetRequest(BaseModel):
    name: str
    host: str
    port: int = 80
    type: str = "CUSTOM"


@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/metrics")
async def get_metrics():
    return {
        "targets": targets,
        "metrics": metrics_store,
        "incidents": incidents_log[:20],
        "server_time": datetime.now().strftime("%H:%M:%S")
    }


@app.post("/api/targets")
async def add_target(data: NewTargetRequest):
    tid = f"custom_{int(time.time())}"
    new_t = {
        "id": tid,
        "name": data.name,
        "host": data.host,
        "port": data.port,
        "type": data.type
    }
    targets.append(new_t)
    metrics_store[tid] = {
        "history": [], "current_ping": 0, "status": "ONLINE",
        "packet_loss": 0, "sent_count": 0, "lost_count": 0, "avg_ping": 0
    }
    incidents_log.insert(0, {
        "time": datetime.now().strftime("%H:%M:%S"),
        "target": data.name,
        "type": "INFO",
        "message": f"Добавлен новый узел для мониторинга ({data.host}:{data.port})"
    })
    return {"status": "ok", "target": new_t}


@app.delete("/api/targets/{target_id}")
async def remove_target(target_id: str):
    global targets
    targets = [t for t in targets if t["id"] != target_id]
    if target_id in metrics_store:
        del metrics_store[target_id]
    return {"status": "removed"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8006, reload=False)
