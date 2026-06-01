# DevTools Radar Local

DevTools Radar Local is a local-first Windows desktop automation tool for collecting, organizing, and summarizing developer-tool news using ChatGPT Web UI automation.

It is designed for developers who want to track emerging open-source tools, AI coding tools, CLI utilities, frontend frameworks, DevOps tools, security tools, and community trends on a daily schedule.

The app provides a Vue + Electron desktop interface, a Python automation runner, task scheduling, Markdown output, and run history.

---

## Why This Project Exists

The main goal of this project is to automatically collect and summarize computer science and developer-tool updates every day.

Example use cases:

- Track trending open-source repositories
- Discover new free or open-source developer tools
- Monitor AI coding tools and agent frameworks
- Scan CLI, DevOps, frontend, security, and local-first tooling trends
- Run multiple prompts step by step
- Use ChatGPT context to progressively consolidate information
- Generate a final Traditional Chinese Markdown report

---

## Features

- Local Windows desktop app
- Vue + Electron UI
- Python runner
- Microsoft Edge CDP automation
- ChatGPT Web UI interaction
- Multi-task prompt workflow
- JSON-based task management
- Windows Task Scheduler integration
- Run history
- Markdown output
- Date/time-based output filenames
- Traditional Chinese report generation
- Local-first design

---

## Architecture

```text
DevTools Radar Local
│
├─ Electron + Vue Desktop UI
│  ├─ Dashboard
│  ├─ Chat
│  ├─ Tasks
│  ├─ Outputs
│  ├─ Runs
│  ├─ Logs
│  └─ Settings
│
├─ Electron Main Process
│  ├─ Reads and writes local files
│  ├─ Calls Python runner
│  ├─ Creates Windows scheduled tasks
│  └─ Bridges UI and local system
│
├─ Python Runner
│  ├─ Connects to Edge through CDP
│  ├─ Sends prompts to ChatGPT Web UI
│  ├─ Waits for responses
│  ├─ Extracts Markdown-like output
│  └─ Writes result files
│
└─ Windows Task Scheduler
   └─ Runs scheduled task JSON files