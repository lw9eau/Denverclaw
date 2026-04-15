"""
web_server.py — Interfaz Web Chat para Denver.
FastAPI + WebSockets + LangGraph integration.
"""

import os
import uuid
import logging
import asyncio
import base64
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from langchain_core.messages import HumanMessage

from core import invoke_graph, extract_response, stream_graph
from graph import build_graph

agent_app = None

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("denver.web_server")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_app
    # Initialize DB and compile graph
    from db.database import init_db
    from metrics import tracker # Importar para asegurar creación de tablas
    
    logger.info("[WebServer] Inicializando DB...")
    await init_db()
    
    logger.info("[WebServer] Construyendo grafo Denver...")
    agent_app = await build_graph()
    
    # Load required env for main.py helpers (like ALLOWED_TELEGRAM_USER_ID if needed)
    load_dotenv()
    
    try:
        yield
    finally:
        if agent_app and hasattr(agent_app.checkpointer, "conn"):
            await agent_app.checkpointer.conn.close()

app = FastAPI(lifespan=lifespan)

# --- DASHBOARD HTML ---
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Denver Observability</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
    <style>
        :root {
            --bg: #0f0f0f;
            --card-bg: #1a1a1a;
            --border: #333;
            --text: #e0e0e0;
            --accent-green: #2ecc71;
            --accent-blue: #3498db;
            --accent-orange: #e67e22;
            --accent-red: #e74c3c;
        }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
        }
        h1 { font-weight: 300; margin: 0; }
        .controls button {
            background: var(--card-bg);
            border: 1px solid var(--border);
            color: var(--text);
            padding: 8px 16px;
            cursor: pointer;
            border-radius: 4px;
            margin-left: 5px;
        }
        .controls button.active {
            border-color: var(--accent-blue);
            color: var(--accent-blue);
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
        }
        @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
        
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
        }
        .card.full-width { grid-column: 1 / -1; }
        .card-title {
            font-size: 0.9rem;
            color: #888;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .kpi-row {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 20px;
        }
        .kpi-card {
            text-align: center;
        }
        .kpi-value {
            font-size: 2rem;
            font-family: 'Courier New', Courier, monospace;
            font-weight: bold;
            display: block;
        }
        .chart-container {
            position: relative;
            height: 300px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
            margin-top: 10px;
        }
        th, td {
            text-align: left;
            padding: 10px;
            border-bottom: 1px solid var(--border);
        }
        th { color: #888; text-transform: uppercase; font-size: 0.8rem; }
        .error-tag { color: var(--accent-red); }
        .success-tag { color: var(--accent-green); }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Denver <span>Observability</span></h1>
            <div class="controls">
                <button onclick="updateWindow(1)" id="btn-1">1h</button>
                <button onclick="updateWindow(6)" id="btn-6">6h</button>
                <button onclick="updateWindow(24)" id="btn-24" class="active">24h</button>
                <button onclick="updateWindow(168)" id="btn-168">7d</button>
            </div>
        </header>

        <div class="card full-width kpi-row">
            <div class="kpi-card">
                <span class="card-title">Total Turnos</span>
                <span class="kpi-value" id="total_turns">-</span>
            </div>
            <div class="kpi-card">
                <span class="card-title">Latencia p50</span>
                <span class="kpi-value" id="latency_p50">-</span>
            </div>
            <div class="kpi-card">
                <span class="card-title">Delegaciones</span>
                <span class="kpi-value" id="avg_delegations">-</span>
            </div>
            <div class="kpi-card">
                <span class="card-title">Tasa de Error</span>
                <span class="kpi-value" id="error_rate">-</span>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <span class="card-title">Distribución de Ruteo</span>
                <div class="chart-container"><canvas id="routeChart"></canvas></div>
            </div>
            <div class="card">
                <span class="card-title">Uso por Especialista</span>
                <div class="chart-container"><canvas id="specialistChart"></canvas></div>
            </div>
            <div class="card">
                <span class="card-title">Interfaz</span>
                <div class="chart-container"><canvas id="interfaceChart"></canvas></div>
            </div>
            
            <div class="card full-width">
                <span class="card-title">Volumen por Hora</span>
                <div class="chart-container"><canvas id="volumeChart"></canvas></div>
            </div>
            <div id="error-section" class="card full-width" style="display:none; margin-top:20px;">
                <span class="card-title">Últimos Errores</span>
                <table id="error-table">
                    <thead>
                        <tr>
                            <th>Especialista</th>
                            <th>Timestamp</th>
                            <th>Mensaje de Error</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>

            <div id="routing-optimization" class="card full-width" style="margin-top:20px;">
                <span class="card-title">Oportunidades de mejora de ruteo (7d)</span>
                <div class="grid" style="grid-template-columns: 1fr 1fr; gap: 40px; margin-top:10px;">
                    <div>
                        <h4 style="margin:0 0 10px 0; font-weight:300; color:var(--accent-blue);">Candidatos a FastRoute</h4>
                        <p style="font-size:0.8rem; color:#666;">Frases ruteadas por LLM que podrían ser deterministas.</p>
                        <table id="fastroute-table">
                            <thead>
                                <tr>
                                    <th>Texto Usuario</th>
                                    <th>Destino</th>
                                    <th>Freq</th>
                                </tr>
                            </thead>
                            <tbody></tbody>
                        </table>
                    </div>
                    <div>
                        <h4 style="margin:0 0 10px 0; font-weight:300; color:var(--accent-green);">Candidatos a DirectRoute</h4>
                        <p style="font-size:0.8rem; color:#666;">Frases con 2 delegaciones que podrían ser una sola Tool.</p>
                        <table id="directroute-table">
                            <thead>
                                <tr>
                                    <th>Texto Usuario</th>
                                    <th>Especialista</th>
                                    <th>Ahorro Est.</th>
                                </tr>
                            </thead>
                            <tbody></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentHours = 24;
        let routeChart, specialistChart, interfaceChart, volumeChart;

        async function fetchData() {
            try {
                const resp = await fetch(`/api/metrics?hours=${currentHours}`);
                const data = await resp.json();
                updateUI(data);
                fetchOptimization();
            } catch (e) {
                console.error("Error fetching metrics:", e);
            }
        }

        function updateUI(data) {
            document.getElementById('total_turns').innerText = data.total_turns_24h;
            document.getElementById('latency_p50').innerText = (data.latency_p50_ms / 1000).toFixed(2) + 's';
            document.getElementById('avg_delegations').innerText = data.avg_delegations.toFixed(1);
            document.getElementById('error_rate').innerText = (data.error_rate * 100).toFixed(1) + '%';

            updateRouteChart(data.route_distribution);
            updateSpecialistChart(data.top_specialists);
            updateInterfaceChart(data.interface_breakdown);
            updateVolumeChart(data.hourly_volume);
            updateErrorTable(data.recent_errors);
        }

        function updateRouteChart(dist) {
            const ctx = document.getElementById('routeChart').getContext('2d');
            const data = {
                labels: ['Direct', 'Fast', 'LLM'],
                datasets: [{
                    data: [dist.direct || 0, dist.fast || 0, dist.llm || 0],
                    backgroundColor: ['#2ecc71', '#3498db', '#e67e22'],
                    borderWidth: 0
                }]
            };
            if (routeChart) routeChart.destroy();
            routeChart = new Chart(ctx, { type: 'doughnut', data, options: { maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { color: '#888' } } } } });
        }

        function updateSpecialistChart(dist) {
            const ctx = document.getElementById('specialistChart').getContext('2d');
            const sorted = Object.entries(dist).sort((a,b) => b[1] - a[1]);
            const data = {
                labels: sorted.map(e => e[0]),
                datasets: [{
                    label: 'Llamadas',
                    data: sorted.map(e => e[1]),
                    backgroundColor: '#3498db'
                }]
            };
            if (specialistChart) specialistChart.destroy();
            specialistChart = new Chart(ctx, { type: 'bar', data, options: { indexAxis: 'y', maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { color: '#333' } }, y: { grid: { display: false } } } } });
        }

        function updateInterfaceChart(dist) {
            const ctx = document.getElementById('interfaceChart').getContext('2d');
            const data = {
                labels: Object.keys(dist),
                datasets: [{
                    data: Object.values(dist),
                    backgroundColor: ['#9b59b6', '#3498db', '#1abc9c', '#f1c40f', '#e67e22', '#2ecc71', '#e74c3c'],
                    borderWidth: 0
                }]
            };
            if (interfaceChart) interfaceChart.destroy();
            interfaceChart = new Chart(ctx, { type: 'pie', data, options: { maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { color: '#888' } } } } });
        }

        function updateVolumeChart(history) {
            const ctx = document.getElementById('volumeChart').getContext('2d');
            const data = {
                labels: history.map(h => h.hour.split(' ')[1].substring(0,5)),
                datasets: [{
                    label: 'Turnos',
                    data: history.map(h => h.count),
                    backgroundColor: 'rgba(52, 152, 219, 0.2)',
                    borderColor: '#3498db',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4
                }]
            };
            if (volumeChart) volumeChart.destroy();
            volumeChart = new Chart(ctx, { type: 'line', data, options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { color: '#333' } }, y: { grid: { color: '#333' } } } } });
        }

        function updateErrorTable(errors) {
            const section = document.getElementById('error-section');
            const tbody = document.querySelector('#error-table tbody');
            tbody.innerHTML = '';
            
            if (errors && errors.length > 0) {
                section.style.display = 'block';
                errors.forEach(err => {
                    const row = `<tr>
                        <td class="error-tag">${err.specialist}</td>
                        <td style="white-space:nowrap">${new Date(err.timestamp).toLocaleString()}</td>
                        <td>${err.error_msg}</td>
                    </tr>`;
                    tbody.innerHTML += row;
                });
            } else {
                section.style.display = 'none';
            }
        }

        function updateWindow(h) {
            currentHours = h;
            document.querySelectorAll('.controls button').forEach(b => b.classList.remove('active'));
            document.getElementById(`btn-${h}`).classList.add('active');
            fetchData();
        }

        async function fetchOptimization() {
            try {
                const resp = await fetch(`/api/metrics/routing-improvement`);
                const data = await resp.json();
                updateOptimizationUI(data);
            } catch (e) {
                console.error("Error fetching optimization metrics:", e);
            }
        }

        function updateOptimizationUI(data) {
            const frBody = document.querySelector('#fastroute-table tbody');
            frBody.innerHTML = '';
            if (!data.fastroute_candidates || data.fastroute_candidates.length === 0) {
                frBody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:#555;">No hay datos</td></tr>';
            } else {
                data.fastroute_candidates.forEach(c => {
                    frBody.innerHTML += `<tr>
                        <td>${c.user_text}</td>
                        <td><span class="success-tag">${c.destination}</span></td>
                        <td style="text-align:right">${c.frequency}</td>
                    </tr>`;
                });
            }

            const drBody = document.querySelector('#directroute-table tbody');
            drBody.innerHTML = '';
            if (!data.directroute_candidates || data.directroute_candidates.length === 0) {
                drBody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:#555;">No hay datos</td></tr>';
            } else {
                data.directroute_candidates.forEach(c => {
                    drBody.innerHTML += `<tr>
                        <td>${c.user_text}</td>
                        <td><span class="success-tag">${c.specialist}</span></td>
                        <td style="text-align:right">${(c.savings / 1000).toFixed(2)}s</td>
                    </tr>`;
                });
            }
        }

        fetchData();
        setInterval(fetchData, 30000);
    </script>
</body>
</html>
"""

@app.get("/metrics")
async def get_dashboard():
    """Sirve el dashboard de observabilidad."""
    return HTMLResponse(content=DASHBOARD_HTML)

@app.get("/api/metrics")
async def get_metrics_api(hours: int = 24):
    """Endpoint JSON para las métricas."""
    from metrics.queries import get_summary
    return await get_summary(hours=hours)

@app.get("/api/metrics/routing-improvement")
async def get_routing_improvement(hours: int = 168):
    """Endpoint para sugerencias de optimización de ruteo."""
    from metrics.queries import get_llm_route_by_text, get_direct_route_candidates
    from datetime import datetime
    
    fast = await get_llm_route_by_text(hours=hours)
    direct = await get_direct_route_candidates(hours=hours)
    
    return {
        "fastroute_candidates": fast,
        "directroute_candidates": direct,
        "generated_at": datetime.utcnow().isoformat(),
        "window_hours": hours
    }

# Mount static files directory
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def get_index():
    """Sirve la página principal de chat."""
    index_path = os.path.join("static", "index.html")
    if not os.path.exists(index_path):
        return HTMLResponse(content="<h1>index.html no encontrado en /static</h1>", status_code=404)
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    Maneja la conexión de chat bidireccional vía WebSockets.
    Asigna un thread_id único por sesión de navegación.
    """
    await websocket.accept()
    # Generar un thread_id único por conexión
    thread_id = str(uuid.uuid4())
    logger.info(f"[WebServer] Nueva conexión WS | thread_id={thread_id}")
    
    try:
        # Enviar mensaje de bienvenida opcional o confirmación de conexión
        await websocket.send_json({
            "type": "status",
            "text": "Conectado a Denver",
            "thread_id": thread_id
        })
        
        while True:
            # Recibir mensaje del frontend
            data = await websocket.receive_json()
            user_text = data.get("text", "")
            image_b64_in = data.get("image")   # imagen adjunta desde el frontend (base64, opcional)
            
            if not user_text and not image_b64_in:
                continue
            
            # Decodificar imagen si fue enviada
            image_bytes_in: bytes | None = None
            if image_b64_in:
                try:
                    image_bytes_in = base64.b64decode(image_b64_in)
                    # Inyectar en el side channel de tools para que analizar_imagen la encuentre
                    import tools as tools_module
                    tools_module._captured_image = image_bytes_in
                    if not user_text:
                        user_text = "Analizá esta imagen y decime qué ves."
                    logger.info(f"[WebServer] Chat[{thread_id}] imagen recibida: {len(image_bytes_in)} bytes")
                except Exception as e:
                    logger.warning(f"[WebServer] No se pudo decodificar imagen base64: {e}")
                
            logger.info(f"[WebServer] Chat[{thread_id}] dice: {user_text!r}")
            
            # Notificar que Denver está procesando
            await websocket.send_json({"type": "status", "status": "typing"})
            
            try:
                # Enviar inicio de stream al cliente
                await websocket.send_json({
                    "type": "stream_start",
                    "thread_id": thread_id
                })
                
                result = None
                # Invocar el grafo de Denver en modo streaming
                async for item_type, data in stream_graph(
                    agent_app,
                    user_text,
                    chat_id=thread_id,
                    is_voice=False,
                    interface="web",
                    image_binary=image_bytes_in,
                ):
                    if item_type == "chunk":
                        # Enviar chunk
                        await websocket.send_json({
                            "type": "stream_chunk",
                            "chunk": data
                        })
                    elif item_type == "result":
                        result = data
                
                
                # Manejo de imágenes (capturas de cámara) y respuesta final (para casos sin streaming compo DirectRoute)
                image_b64 = None
                final_text = ""
                if result:
                    image_binary = result.get("image_binary")
                    if image_binary:
                        image_b64 = base64.b64encode(image_binary).decode('utf-8')
                    
                    # Extraer texto final en caso de que el streaming haya estado vacío (DirectRoute / AutoFinish)
                    final_text = extract_response(result)
                
                # Enviar fin de stream con el texto final como respaldo
                await websocket.send_json({
                    "type": "stream_end",
                    "image": image_b64,
                    "final_text": final_text,
                    "thread_id": thread_id
                })
                
            except Exception as e:
                logger.error(f"[WebServer] Error en Denver: {e}", exc_info=True)
                await websocket.send_json({
                    "type": "error",
                    "text": f"Error procesando: {str(e)[:150]}"
                })
                
    except WebSocketDisconnect:
        logger.info(f"[WebServer] Cliente desconectado | thread_id={thread_id}")
    except Exception as e:
        logger.error(f"[WebServer] Error inesperado en WS: {e}")
        try:
            await websocket.close()
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    # Load dotenv here for standalone execution
    load_dotenv()
    port = int(os.getenv("WEB_SERVER_PORT", "8002"))
    logger.info(f"Iniciando Web Chat Server en puerto {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
