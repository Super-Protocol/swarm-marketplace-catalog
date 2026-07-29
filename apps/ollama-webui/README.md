# Ollama + Open WebUI

Run large language models with a chat interface powered by [Open WebUI](https://openwebui.com/).

## Components

- **Ollama** — LLM inference server with OpenAI-compatible API
- **Open WebUI** — Feature-rich web interface for interacting with LLMs

## Features

- Select from popular open-source models (Llama 3, Mistral, Gemma, Qwen, DeepSeek)
- Optional NVIDIA GPU acceleration
- Configurable persistent storage for model files
- Optional public access via ingress (chat UI + API endpoint)
- OpenAI-compatible API for IDE integrations (Cursor, Continue.dev, etc.)

## Usage

After deployment, access the chat interface via the Open WebUI URL. On first visit, create an admin account. The selected model is automatically downloaded and ready to use.

### API Access

The Ollama API is OpenAI-compatible. Configure your IDE or tools with:

- **Base URL:** `https://<api-hostname>/v1` (if ingress enabled) or `http://<ollama-release>:11434/v1` (internal)
- **API Key:** any non-empty string (not validated)
- **Model:** the model name selected during deployment
