# obs-assemblyai-captions

> Live captions in OBS Studio, powered by [AssemblyAI](https://www.assemblyai.com/)'s streaming transcription.

Captures your microphone, streams audio to AssemblyAI's [streaming speech-to-text](https://www.assemblyai.com/docs/streaming/getting-started/transcribe-streaming-audio) models, and updates a Text source with live captions via an OBS script.

## Features

- **All streaming models:** Universal-3.6 Pro (default; 32 languages with code-switching), Universal-3.5 Pro, Universal-Streaming English, and Universal-Streaming Multilingual
- **Two caption styles:** wrapped subtitle lines, or a "word pop" of the most recent words
- **Language control:** steer toward specific languages, or log the detected language of each turn
- **Keyterms and context prompts:** boost names and jargon, and describe what's being said
- **Speaker labels:** prefix each caption with who's talking (`A: ...`, `B: ...`)
- **Voice Focus:** suppress background noise for near-field or far-field mics
- **Medical Mode, profanity filtering, and PII redaction**
- **Turn detection tuning:** silence thresholds, VAD sensitivity, a force-end-of-turn button, and the 3.5 Pro latency modes
- **Mid-stream updates:** change languages, keyterms, prompt, and turn detection without restarting
- **US and EU data residency endpoints**

## Requirements

- [OBS Studio](https://obsproject.com/)
- Python 3.10+ (matching the version your OBS build uses)
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
   .venv/bin/pip install -r requirements.txt
   ```

3. **Launch OBS with the Python preload** (required for C extensions like numpy/sounddevice):

   ```bash
   LD_PRELOAD=$(python3 -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")/libpython$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").so.1.0 obs
   ```

4. **Add the script:** OBS → Tools → Scripts → **+** → select `obs_assemblyai_captions.py`

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
   .venv/bin/pip install -r requirements.txt
   ```

3. **Configure OBS Python path:** OBS → Tools → Scripts → Python Settings → set to your Python install path (e.g. `/opt/homebrew/opt/python@3.12/Frameworks/Python.framework/Versions/3.12`).

4. **Add the script:** OBS → Tools → Scripts → **+** → select `obs_assemblyai_captions.py`

### Windows

1. **Install Python** from [python.org](https://www.python.org/downloads/). Use the version that matches your OBS build (check OBS → Tools → Scripts → Python Settings). Typically Python 3.11 or 3.12.

2. **Clone and install** (in PowerShell):

   ```powershell
   git clone https://github.com/he-james/obs-assemblyai-captions.git
   cd obs-assemblyai-captions
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

3. **Configure OBS Python path:** OBS → Tools → Scripts → Python Settings → Browse to your Python install folder (e.g. `C:\Users\YOU\AppData\Local\Programs\Python\Python312`).

4. **Add the script:** OBS → Tools → Scripts → **+** → select `obs_assemblyai_captions.py`

## Usage

1. Add a **Text** source to your scene and style it (font, size, color, position).
2. In the script settings, enter your API key (or set the `ASSEMBLYAI_API_KEY` environment variable before launching OBS), then pick your microphone and the Text source.
3. Click **Start Captions**. Status and errors appear in OBS → Tools → Scripts → **Script Log**.

## Settings

| Section | Setting | Notes |
| --- | --- | --- |
| Main | Region | Global, US, or EU [streaming endpoint](https://www.assemblyai.com/docs/streaming/endpoints-and-data-zones) |
| | Speech Model | See [model selection](https://www.assemblyai.com/docs/streaming/select-the-speech-model). Universal-Streaming English sends the most frequent word-by-word partials. |
| Captions | Caption Mode, Max Lines, Chars per Line, Visible Words | Layout of the caption text |
| | Display Hold Time | How long a finished caption stays on screen |
| | Show Previous Final | Keep the previous sentence above the current one |
| | Center Horizontally / Vertically | Re-center the source as its size changes |
| Language & Accuracy | Languages | Pro models. Comma-separated codes such as `en, es`. Blank = automatic code-switching. |
| | Log Detected Language | Writes each turn's language to the Script Log (Pro models and Multilingual) |
| | Keyterms | [Keyterms prompting](https://www.assemblyai.com/docs/streaming/prompting-and-keyterms): up to 100 terms |
| | Context Prompt | Pro models. A short description of the content. |
| | Medical Mode | [Medical terminology](https://www.assemblyai.com/docs/streaming/medical-mode) (en, es, de, fr) |
| | Latency Mode | Universal-3.5 Pro only: balanced, min latency, or max accuracy |
| | Apply Changes to Running Captions | Sends languages, keyterms (Pro models), prompt, and turn detection mid-stream |
| Speakers & Audio | Label Speakers, Max Speakers | [Streaming diarization](https://www.assemblyai.com/docs/streaming/label-speakers-and-separate-channels) |
| | Voice Focus, Strength | Pro models. [Background-noise suppression](https://www.assemblyai.com/docs/streaming/voice-focus) |
| Content Filtering | Filter Profanity, Redact PII, PII Replacement | Redaction applies to finished captions only |
| Turn Detection | End-of-Turn Confidence, Min/Max Turn Silence, VAD Threshold | [Turn detection](https://www.assemblyai.com/docs/streaming/turn-detection). 0 = server default. End-of-turn confidence applies to Universal-Streaming models only. |
| | Force End of Turn | Finalizes the current caption immediately |

Settings a model doesn't support aren't sent to it.
