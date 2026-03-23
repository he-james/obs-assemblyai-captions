# obs-assemblyai-captions

> Live captions in OBS Studio, powered by [AssemblyAI](https://www.assemblyai.com/)'s streaming transcription.

Captures your microphone, streams audio to AssemblyAI's streaming models, and updates a Text source with live captions via an OBS script.

## Requirements

- [OBS Studio](https://obsproject.com/)
- Python 3.10+
- An [AssemblyAI API key](https://www.assemblyai.com/dashboard/signup) (free tier available)
- A working microphone

## Setup

### Linux

1. **Install system dependencies:**

   ```bash
   sudo apt install python3-dev portaudio19-dev
   ```

2. **Clone and install:**

   ```bash
   git clone https://github.com/he-james/obs-assemblyai-captions.git
   cd obs-assemblyai-captions
   python3 -m venv .venv
   .venv/bin/pip install -e .
   ```

3. **Launch OBS with the Python preload** (required for C extensions like numpy/sounddevice):

   ```bash
   ./launch-obs.sh
   ```

   Or manually:

   ```bash
   LD_PRELOAD=$(python3 -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")/libpython$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").so.1.0 obs
   ```

4. **Add the script:** OBS → Tools → Scripts → **+** → select `aai_streamer.py`

### macOS

1. **Install system dependencies:**

   ```bash
   brew install portaudio python@3.12
   ```

   > Use the Python version that matches your OBS build. Check with: OBS → Tools → Scripts → Python Settings.

2. **Clone and install:**

   ```bash
   git clone https://github.com/he-james/obs-assemblyai-captions.git
   cd obs-assemblyai-captions
   python3 -m venv .venv
   .venv/bin/pip install -e .
   ```

3. **Configure OBS Python path:** OBS → Tools → Scripts → Python Settings → set to your Python install path (e.g. `/opt/homebrew/opt/python@3.12/Frameworks/Python.framework/Versions/3.12`).

4. **Add the script:** OBS → Tools → Scripts → **+** → select `aai_streamer.py`

### Windows

1. **Install Python** from [python.org](https://www.python.org/downloads/). Use the version that matches your OBS build (check OBS → Tools → Scripts → Python Settings). Typically Python 3.11 or 3.12.

2. **Clone and install** (in PowerShell):

   ```powershell
   git clone https://github.com/he-james/obs-assemblyai-captions.git
   cd obs-assemblyai-captions
   python -m venv .venv
   .venv\Scripts\pip install -e .
   ```

3. **Configure OBS Python path:** OBS → Tools → Scripts → Python Settings → Browse to your Python install folder (e.g. `C:\Users\YOU\AppData\Local\Programs\Python\Python312`).

4. **Add the script:** OBS → Tools → Scripts → **+** → select `aai_streamer.py`
