# Denverclaw: Intelligent Personal Assistant

Denverclaw is a highly advanced personal assistant based on AI agents, designed for home automation, productivity management, and visual analysis. It uses a **LangGraph** architecture to coordinate different "specialists" that can solve complex tasks autonomously.

## 🚀 Key Features

- **Multi-Agent Architecture**: A supervisor node coordinates three specialists:
  - **HomeAutomation**: Full control of Home Assistant (lights, climate, cameras, sensors, music).
  - **GoogleWorkspace**: Management of Gmail, Google Calendar, contacts, and morning briefings.
  - **Utility**: Image analysis (Vision), web search (Wikipedia/News), file management, and calculations.
- **Persistent Memory**: Ability to remember facts about the user and follow personalized dynamic rules.
- **Multimodal Interface**:
  - **Telegram**: Interactive chatbot with photo and voice support.
  - **Web Chat**: Modern interface with response streaming.
  - **Voice**: Dedicated server for voice interaction. See [Denverclaw_Voice](https://github.com/lw9eau/Denverclaw_Voice) for the implementation with Atom Echo Client.
- **Computer Vision**: Detailed analysis of security camera captures or user-uploaded photos.
- **Morning Briefing**: Automatic daily summary (weather, calendar events, pending emails).

## 🛠️ Technologies

- **LangGraph & LangChain**: Agent orchestration.
- **FastAPI**: Web servers and WebSockets.
- **Python 3.10+**: Core language.
- **Home Assistant API**: Smart home integration.
- **Google APIs**: Workspace integration.
- **SQLite**: Checkpoint and memory persistence.

## 📋 Prerequisites

1. **Python 3.10+** installed.
2. Access to an OpenAI-compatible LLM server (e.g., Ollama, LM Studio, or official OpenAI).
3. **Home Assistant** token.
4. **Google Cloud Console** credentials (for Gmail and Calendar).
5. **Telegram Bot** token.

## ⚙️ Configuration

1. Clone the repository:
   ```bash
   git clone https://github.com/your-user/denverclaw.git
   cd denverclaw
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment:
   - Copy example file: `cp .env.example .env`
   - Edit `.env` with your real keys.

4. Configure Google authentication:
   - Run `python setup_google_auth.py` to generate the initial `token.json`.

## 🏃 Execution

You can start the unified ecosystem entry point using `run.py`.

```bash
python run.py [options]
```

**Parameters:**
- `--all`: Starts all available servers (default if no parameters are provided).
- `--telegram`: Starts the Telegram server.
- `--web`: Starts the Web server.
- `--voice`: Starts the Voice server (Refer to [Denverclaw_Voice](https://github.com/lw9eau/Denverclaw_Voice) for client setup).

## 📂 Project Structure

- `agents.py`: Specialist agent definitions.
- `graph.py`: State graph logic and supervisor routing.
- `tools/`: Tool collection (Home Assistant, Google, Vision, etc.).
- `db/`: Database models and memory management.
- `metrics/`: Latency and usage tracking.
- `scheduler/`: Scheduled tasks (HA monitor, briefings).

## 🛡️ Security

This project uses environment variables to manage sensitive keys. Ensure you never upload your `.env` file, `denver.db`, or credential `json` files to the public repository. The `.gitignore` file is already configured to protect these data.

---
*Developed for personal automation and efficiency.*
