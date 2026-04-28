# Confidant

Confidant is a local, persistent, and secure personal AI companion. It provides a web-based chat interface bundled as a desktop application. Confidant stores all data (conversations, profiles, memories) in a local SQLite database that is securely encrypted using an AES-128-CBC (Fernet) key derived from a master passphrase.

## Features
- **Local & Secure:** All data stays on your machine, fully encrypted. Supports WebAuthn (Windows Hello/Biometrics) for seamless unlock.
- **Persistent Memory:** Confidant automatically maintains profiles (`partner.md`, `significant_others.md`, `context.md`, etc.) to learn and remember things about you.
- **Memory Vault:** Curated beautiful moments and quotes from your conversations are saved in the vault.
- **Autonomous Tool Use:** Confidant can autonomously search the web (via Brave Search) and recall its own memory.
- **Dreaming:** While you are away, Confidant processes your conversations and "dreams", forming deeper connections.
- **Flexible Providers:** Supports local inference (`llama.cpp`) or external API providers (OpenAI, Anthropic, Gemini, DeepSeek).

## Installation

1. Clone the repository
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the application:
   ```bash
   python app.py
   ```
   *(Use `python app.py --browser` to run it in your default web browser instead of the bundled app window).*

## Building an Executable

You can build a standalone executable for Windows using PyInstaller:
```bash
python build.py
```
This will create a self-contained `Confidant.exe` in the `dist/Confidant` directory.
