# Denverclaw: Asistente Personal Inteligente

Denverclaw es un asistente personal altamente avanzado basado en agentes de IA, diseñado para la automatización del hogar, gestión de productividad y análisis visual. Utiliza una arquitectura de **LangGraph** para coordinar diferentes "especialistas" que pueden resolver tareas complejas de forma autónoma.

## 🚀 Características Principales

- **Arquitectura Multi-Agente**: Un nodo supervisor coordina a tres especialistas:
  - **HomeAutomation**: Control total de Home Assistant (luces, clima, cámaras, sensores, música).
  - **GoogleWorkspace**: Gestión de Gmail, Calendario de Google, contactos y briefings matutinos.
  - **Utility**: Análisis de imágenes (Visión), búsqueda en la web (Wikipedia/Noticias), gestión de archivos y cálculos.
- **Memoria Persistente**: Capacidad para recordar hechos sobre el usuario y seguir reglas dinámicas personalizadas.
- **Interfaz Multimodal**:
  - **Telegram**: Chatbot interactivo con soporte para fotos y voz.
  - **Web Chat**: Interfaz moderna con streaming de respuestas.
  - **Voice**: Servidor dedicado para interacción por voz.
- **Visión Artificial**: Análisis detallado de capturas de cámaras de seguridad o fotos enviadas por el usuario.
- **Briefing Matutino**: Resumen automático del día (clima, eventos de calendario, correos pendientes).

## 🛠️ Tecnologías

- **LangGraph & LangChain**: Orquestación de agentes.
- **FastAPI**: Servidores web y WebSockets.
- **Python 3.10+**: Lenguaje principal.
- **Home Assistant API**: Integración domótica.
- **Google APIs**: Integración con Workspace.
- **SQLite**: Persistencia de checkpoints y memoria.

## 📋 Requisitos Previos

1. **Python 3.10+** instalado.
2. Acceso a un servidor de LLM compatible con OpenAI (e.g., Ollama, LM Studio o OpenAI oficial).
3. Token de **Home Assistant**.
4. Credenciales de **Google Cloud Console** (para Gmail y Calendar).
5. Token de **Bot de Telegram**.

## ⚙️ Configuración

1. Clona el repositorio:
   ```bash
   git clone https://github.com/tu-usuario/denverclaw.git
   cd denverclaw
   ```

2. Instala las dependencias:
   ```bash
   pip install -r requirements.txt
   ```

3. Configura el entorno:
   - Copia el archivo de ejemplo: `cp .env.example .env`
   - Edita `.env` con tus claves reales.

4. Configura la autenticación de Google:
   - Ejecuta `python setup_google_auth.py` para generar el `token.json` inicial.

## 🏃 Ejecución

Puedes iniciar los diferentes servidores según tus necesidades:

- **Web Server**: `python web_server.py`
- **Telegram Server**: `python telegram_server.py`
- **Voice Server**: `python voice_server.py`

## 📂 Estructura del Proyecto

- `agents.py`: Definición de los agentes especialistas.
- `graph.py`: Lógica del grafo de estados y ruteo del supervisor.
- `tools/`: Colección de herramientas (Home Assistant, Google, Visión, etc.).
- `db/`: Modelos y gestión de la base de datos de memoria.
- `metrics/`: Seguimiento de latencia y uso.
- `scheduler/`: Tareas programadas (monitor de HA, briefings).

## 🛡️ Seguridad

Este proyecto utiliza variables de entorno para gestionar claves sensibles. Asegúrate de nunca subir tu archivo `.env`, `denver.db` o archivos `json` de credenciales al repositorio público. El archivo `.gitignore` ya está configurado para proteger estos datos.

---
*Desarrollado para la automatización personal y eficiencia.*
