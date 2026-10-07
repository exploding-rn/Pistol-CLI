# Pistol CLI

A Windows-first developer toolkit for running, inspecting, debugging, and mapping projects from the terminal.

**No AI functionality, providers, models, embeddings, accounts, API keys, containers, hosted services, or Windows Service.**

Core operations work offline. LECAP's background proxy starts only when explicitly requested. Initial package installation and explicitly requested dependency installation may need the internet unless you have a local wheel cache.

`chamber` · `fire` · `doctor` · `medic` · `shrimp` · `detective` · `vent`

## Install

Pistol requires **Python 3.11 or newer**. Python 3.14 is supported.

### Windows

Open PowerShell:

```powershell
irm https://raw.githubusercontent.com/exploding-rn/Pistol-CLI/main/install.ps1 | iex
```

Then open a new terminal and run:

```powershell
pistol --help
pistol doctor
```

The Windows installer downloads Pistol, creates an isolated Python environment, installs its dependencies, adds `pistol` to your user PATH, and verifies the installation.

### Linux

> **Linux support is currently experimental.**
>
> Pistol is Windows-first. Core functionality may work on Linux, but Windows-specific features such as WSL management and some system integrations are not available natively.

Requires Python 3.11+ and Git.

```bash
git clone https://github.com/exploding-rn/Pistol-CLI.git
cd Pistol-CLI

python3 -m venv .venv
source .venv/bin/activate

python -m pip install -e .
```

Then:

```bash
pistol --help
pistol doctor
```

You can also install it with `pipx`:

```bash
git clone https://github.com/exploding-rn/Pistol-CLI.git
cd Pistol-CLI
pipx install .
```

## Requirements

Pistol uses:

- Python 3.11 or newer
- `psutil` for process inspection
- `cryptography` for local certificate issuance and validation

HTTP proxying uses the Python standard library.

On Windows, Pistol can be used from PowerShell, CMD, Windows Terminal, or Git Bash with the installed Python environment available on PATH.

## What is Pistol?

Pistol gives local development projects a common command-line toolkit without requiring Docker, cloud services, accounts, or an AI provider.

A project can be placed inside an isolated **chamber**, inspected, launched, diagnosed, and exposed through a friendly local development address.

For example:

```text
Project
   ↓
Pistol Chamber
   ↓
pistol fire
   ↓
127.0.0.1:5000
   ↓
LECAP
   ↓
https://api.lnet
```

## Chambers

Create a chamber:

```bash
pistol chamber --name my-app --entrypoint main.py
```

A chamber provides an isolated environment for the project without requiring Docker.

Run it:

```bash
pistol fire
```

Inspect the environment:

```bash
pistol doctor
```

## Requirements Scanner

Pistol can inspect Python imports and determine which third-party packages a project needs.

```bash
pistol req
```

Install missing packages into the current chamber:

```bash
pistol req --install
```

Pistol keeps chamber dependencies isolated. It can use pip's package cache to avoid unnecessary downloads when packages are already cached.

## LECAP

**LECAP** stands for **Local Environment Chamber Access Point**.

It maps friendly local development domains to Pistol chambers.

For example:

```text
https://api.lnet
        ↓
      LECAP
        ↓
127.0.0.1:5000
```

Configure a project:

```bash
pistol lecap --domain api.lnet --https true
```

View the DNS changes Pistol would make:

```bash
pistol lecap --dns plan
```

Install the local DNS mapping explicitly:

```bash
pistol lecap --dns install
```

Start the LECAP proxy:

```bash
pistol lecap --serve
```

Pistol does not silently modify DNS configuration or certificate trust. Operations that change system configuration require explicit commands.

## Local HTTPS

Pistol can create and manage a local certificate authority for development HTTPS.

Enable HTTPS:

```bash
pistol config --https true
```

Certificate trust is explicit. Pistol does not silently add a certificate authority to the operating system trust store.

## WSL

On Windows, Pistol includes tooling for working with WSL environments.

```bash
pistol wsl --help
```

WSL functionality is Windows-only.

## Useful Commands

```text
pistol chamber     Manage isolated project chambers
pistol fire        Run a chamber
pistol doctor      Inspect the Pistol environment
pistol medic       Diagnose and repair problems
pistol req         Detect and install Python requirements
pistol lecap       Manage local development access
pistol detective   Inspect processes and ports
pistol shrimp      Project/code utilities
pistol vent        Development utilities
pistol wsl         WSL integration
```

Run:

```bash
pistol --help
```

for the complete command list.

## Updating

### Windows installer

Run the installer again:

```powershell
irm https://raw.githubusercontent.com/exploding-rn/Pistol-CLI/main/install.ps1 | iex
```

An existing installation will be updated.

### Manual installation

From the Pistol repository:

```bash
git pull
python -m pip install -e .
```

## Development

Clone Pistol:

```bash
git clone https://github.com/exploding-rn/Pistol-CLI.git
cd Pistol-CLI
```

Install development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the test suite:

```bash
pytest
```

## Platform Support

**Windows**

Primary supported platform. Chambers, requirements scanning, LECAP, HTTPS, WSL integration, and Windows-specific utilities are designed for Windows.

**Linux**

Experimental. The Python CLI and platform-independent functionality may work, but Linux is not yet considered a fully supported Pistol platform.

**macOS**

Not currently tested or officially supported.

## Philosophy

Pistol is intended to stay local-first and developer-controlled.

It does not require:

- AI
- API keys
- Accounts
- Docker
- Hosted services
- A Windows Service

Features that affect system configuration are intended to be explicit rather than silently enabled.

## License

MIT


## DISCLAIMER!!!

This project was vibe coded meaning AI helped code this project! The code has been human reviewed and revises! 
