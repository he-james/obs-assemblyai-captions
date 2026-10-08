"""
obs-assemblyai-captions: OBS Studio script for live captions via AssemblyAI.

See README.md for setup instructions.

Architecture:
  Background thread:  Microphone → AssemblyAI v3 WebSocket → CaptionState
  OBS timer (100ms):  CaptionState.get() → format → update Text source
"""

import os
import sys
import glob
import logging

# Add the project's venv site-packages and src/ to sys.path so OBS can
# find our dependencies (assemblyai, sounddevice, numpy) and our modules.
_script_dir = os.path.dirname(os.path.abspath(__file__))

# Venv layout differs by OS:
#   Linux/macOS: .venv/lib/pythonX.Y/site-packages
#   Windows:     .venv/Lib/site-packages
_venv_candidates = (
    glob.glob(os.path.join(_script_dir, ".venv", "lib", "python*", "site-packages"))
    + glob.glob(os.path.join(_script_dir, ".venv", "Lib", "site-packages"))
)
for _sp in _venv_candidates:
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

_src_dir = os.path.join(_script_dir, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import obspython as obs


# Route Python logging to print() so it shows up in OBS Script Log.
# Handlers are reset so OBS script reloads don't stack duplicates.
class _OBSLogHandler(logging.Handler):
    def emit(self, record):
        try:
            print(f"[{record.levelname}] {record.name}: {record.getMessage()}")
        except Exception:
            pass


for _name, _level in (("obs_assemblyai_captions_core", logging.INFO), ("assemblyai", logging.WARNING)):
    _logger = logging.getLogger(_name)
    _logger.handlers.clear()
    _logger.addHandler(_OBSLogHandler())
    _logger.setLevel(_level)
    _logger.propagate = False

from obs_assemblyai_captions_core.caption_state import CaptionState
from obs_assemblyai_captions_core.config import (
    DEFAULT_SPEECH_MODEL,
    SPEECH_MODELS,
    AudioConfig,
    CaptionConfig,
    TranscriptionConfig,
    api_key_from_env,
    parse_list,
)
from obs_assemblyai_captions_core.formatter import CaptionFormatter
from obs_assemblyai_captions_core.microphone import list_microphones
from obs_assemblyai_captions_core.transcription import TranscriptionEngine

log = logging.getLogger("obs_assemblyai_captions_core")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_caption_state = CaptionState()
_engine: TranscriptionEngine | None = None
_formatter: CaptionFormatter | None = None
_settings = None  # latest OBS settings object, read when starting captions

# Settings read every timer tick
_text_source_name: str = ""
_center_horizontal: bool = False
_center_vertical: bool = False


# ---------------------------------------------------------------------------
# OBS script interface
# ---------------------------------------------------------------------------

def script_description():
    return (
        "<h2>obs-assemblyai-captions</h2>"
        "<p>Live captions powered by AssemblyAI streaming transcription.</p>"
        "<p>Create a <b>Text</b> source, then select it below. "
        "Style the text source in OBS (font, size, color, position).</p>"
    )


def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "api_key", "")
    obs.obs_data_set_default_string(settings, "region", "global")
    obs.obs_data_set_default_int(settings, "mic_device", -1)  # -1 = default
    obs.obs_data_set_default_string(settings, "text_source", "")
    obs.obs_data_set_default_string(settings, "speech_model", DEFAULT_SPEECH_MODEL)
    # Captions
    obs.obs_data_set_default_string(settings, "caption_mode", "subtitle")
    obs.obs_data_set_default_int(settings, "max_lines", 2)
    obs.obs_data_set_default_int(settings, "chars_per_line", 40)
    obs.obs_data_set_default_int(settings, "max_words", 5)
    obs.obs_data_set_default_double(settings, "fade_out_seconds", 4.0)
    obs.obs_data_set_default_bool(settings, "show_previous_final", False)
    obs.obs_data_set_default_bool(settings, "center_horizontal", False)
    obs.obs_data_set_default_bool(settings, "center_vertical", False)
    # Language & accuracy
    obs.obs_data_set_default_string(settings, "language_codes", "")
    obs.obs_data_set_default_bool(settings, "language_detection", False)
    obs.obs_data_set_default_string(settings, "keyterms_prompt", "")
    obs.obs_data_set_default_string(settings, "prompt", "")
    obs.obs_data_set_default_bool(settings, "medical_mode", False)
    obs.obs_data_set_default_string(settings, "mode", "")
    # Speakers & audio
    obs.obs_data_set_default_bool(settings, "speaker_labels", False)
    obs.obs_data_set_default_int(settings, "max_speakers", 0)
    obs.obs_data_set_default_string(settings, "voice_focus", "")
    obs.obs_data_set_default_double(settings, "voice_focus_threshold", 0.0)
    # Content filtering
    obs.obs_data_set_default_bool(settings, "filter_profanity", False)
    obs.obs_data_set_default_bool(settings, "redact_pii", False)
    obs.obs_data_set_default_string(settings, "redact_pii_sub", "hash")
    # Turn detection
    obs.obs_data_set_default_double(settings, "end_of_turn_confidence", 0.0)
    obs.obs_data_set_default_int(settings, "min_turn_silence", 0)
    obs.obs_data_set_default_int(settings, "max_turn_silence", 0)
    obs.obs_data_set_default_double(settings, "vad_threshold", 0.0)


def _add_list(props, key, label, options):
    prop = obs.obs_properties_add_list(
        props, key, label, obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING,
    )
    for value, name in options:
        obs.obs_property_list_add_string(prop, name, value)
    return prop


def _add_help(props, key, text):
    obs.obs_properties_add_text(props, key, text, obs.OBS_TEXT_INFO)


def script_properties():
    props = obs.obs_properties_create()

    # Connection
    obs.obs_properties_add_text(props, "api_key", "AssemblyAI API Key", obs.OBS_TEXT_PASSWORD)
    _add_list(props, "region", "Region", [
        ("global", "Global (default)"),
        ("us", "US data residency"),
        ("eu", "EU data residency"),
    ])
    _add_list(props, "speech_model", "Speech Model", list(SPEECH_MODELS.items()))

    # Microphone selector
    mic_list = obs.obs_properties_add_list(
        props, "mic_device", "Microphone",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT,
    )
    obs.obs_property_list_add_int(mic_list, "System Default", -1)
    try:
        for mic in list_microphones():
            label = f"{mic['name']} ({mic['channels']}ch, {mic['sample_rate']} Hz)"
            if mic["is_default"]:
                label += " [default]"
            obs.obs_property_list_add_int(mic_list, label, mic["index"])
    except Exception as e:
        log.warning("Could not enumerate microphones: %s", e)

    # Text source selector
    source_list = obs.obs_properties_add_list(
        props, "text_source", "Text Source",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING,
    )
    obs.obs_property_list_add_string(source_list, "(none)", "")
    sources = obs.obs_enum_sources()
    if sources:
        for source in sources:
            source_id = obs.obs_source_get_unversioned_id(source)
            if source_id in ("text_gdiplus", "text_gdiplus_v2", "text_ft2_source", "text_ft2_source_v2"):
                name = obs.obs_source_get_name(source)
                obs.obs_property_list_add_string(source_list, name, name)
        obs.source_list_release(sources)

    # Start/Stop buttons
    obs.obs_properties_add_button(props, "start_btn", "Start Captions", _on_start_clicked)
    obs.obs_properties_add_button(props, "stop_btn", "Stop Captions", _on_stop_clicked)

    # --- Captions ---
    cap = obs.obs_properties_create()
    _add_list(cap, "caption_mode", "Caption Mode", [
        ("subtitle", "Subtitle (wrapped lines)"),
        ("wordpop", "Word Pop (recent words)"),
    ])
    obs.obs_properties_add_int(cap, "max_lines", "Max Lines (subtitle)", 1, 10, 1)
    obs.obs_properties_add_int(cap, "chars_per_line", "Chars per Line (subtitle)", 20, 80, 5)
    obs.obs_properties_add_int(cap, "max_words", "Visible Words (wordpop)", 1, 10, 1)
    obs.obs_properties_add_float_slider(
        cap, "fade_out_seconds", "Display Hold Time (s)", 1.0, 15.0, 0.5,
    )
    obs.obs_properties_add_bool(cap, "show_previous_final", "Show Previous Final")
    obs.obs_properties_add_bool(cap, "center_horizontal", "Center Horizontally")
    obs.obs_properties_add_bool(cap, "center_vertical", "Center Vertically")
    obs.obs_properties_add_group(props, "captions", "Captions", obs.OBS_GROUP_NORMAL, cap)

    # --- Language & Accuracy ---
    lang = obs.obs_properties_create()
    obs.obs_properties_add_text(lang, "language_codes", "Languages", obs.OBS_TEXT_DEFAULT)
    _add_help(lang, "_lang_help",
              "Pro models only. Comma-separated codes (e.g. \"en, es\") to steer toward. "
              "Leave blank for automatic code-switching across all supported languages.")
    obs.obs_properties_add_bool(lang, "language_detection", "Log Detected Language")
    obs.obs_properties_add_text(lang, "keyterms_prompt", "Keyterms", obs.OBS_TEXT_MULTILINE)
    _add_help(lang, "_keyterms_help",
              "Names, jargon, or products to boost, one per line or comma-separated "
              "(max 100, 50 chars each).")
    obs.obs_properties_add_text(lang, "prompt", "Context Prompt", obs.OBS_TEXT_MULTILINE)
    _add_help(lang, "_prompt_help",
              "Pro models only. Short description of the content, e.g. "
              "\"Live coding stream about Rust and WebAssembly.\" Context, not instructions.")
    obs.obs_properties_add_bool(lang, "medical_mode", "Medical Mode")
    _add_help(lang, "_medical_help",
              "Improve accuracy for medical terms (medications, procedures, conditions). "
              "Supported languages: en, es, de, fr.")
    _add_list(lang, "mode", "Latency Mode (3.5 Pro only)", [
        ("", "Server default"),
        ("balanced", "Balanced"),
        ("min_latency", "Min latency"),
        ("max_accuracy", "Max accuracy"),
    ])
    obs.obs_properties_add_button(
        lang, "apply_btn", "Apply Changes to Running Captions", _on_apply_clicked,
    )
    _add_help(lang, "_apply_help",
              "Updates languages, keyterms, prompt, and turn detection without restarting.")
    obs.obs_properties_add_group(props, "language", "Language & Accuracy", obs.OBS_GROUP_NORMAL, lang)

    # --- Speakers & Audio ---
    spk = obs.obs_properties_create()
    obs.obs_properties_add_bool(spk, "speaker_labels", "Label Speakers (e.g. \"A: ...\")")
    obs.obs_properties_add_int(spk, "max_speakers", "Max Speakers (0 = auto)", 0, 10, 1)
    _add_list(spk, "voice_focus", "Voice Focus (Pro models)", [
        ("", "Off"),
        ("near-field", "Near-field (headset / close mic)"),
        ("far-field", "Far-field (room / laptop mic)"),
    ])
    obs.obs_properties_add_float_slider(
        spk, "voice_focus_threshold", "Voice Focus Strength (0 = default)", 0.0, 1.0, 0.05,
    )
    obs.obs_properties_add_group(props, "speakers", "Speakers & Audio", obs.OBS_GROUP_NORMAL, spk)

    # --- Content Filtering ---
    flt = obs.obs_properties_create()
    obs.obs_properties_add_bool(flt, "filter_profanity", "Filter Profanity")
    obs.obs_properties_add_bool(flt, "redact_pii", "Redact PII")
    _add_list(flt, "redact_pii_sub", "PII Replacement", [
        ("hash", "Hash (####)"),
        ("entity_name", "Entity name ([PERSON_NAME])"),
    ])
    _add_help(flt, "_pii_help", "Redaction applies to finished turns only; live partials are not redacted.")
    obs.obs_properties_add_group(props, "filtering", "Content Filtering", obs.OBS_GROUP_NORMAL, flt)

    # --- Turn Detection ---
    adv = obs.obs_properties_create()
    obs.obs_properties_add_float_slider(
        adv, "end_of_turn_confidence",
        "End-of-Turn Confidence (Universal-Streaming only)",
        0.0, 1.0, 0.05,
    )
    _add_help(adv, "_eot_help",
              "0 = server default. Higher = waits for more confidence before ending a turn.")
    obs.obs_properties_add_int(adv, "min_turn_silence", "Min Turn Silence (ms)", 0, 10000, 50)
    _add_help(adv, "_min_help", "0 = server default. Minimum silence before a turn can end.")
    obs.obs_properties_add_int(adv, "max_turn_silence", "Max Turn Silence (ms)", 0, 10000, 100)
    _add_help(adv, "_max_help", "0 = server default. Maximum silence before a turn is forced to end.")
    obs.obs_properties_add_float_slider(adv, "vad_threshold", "VAD Threshold", 0.0, 1.0, 0.05)
    _add_help(adv, "_vad_help",
              "0 = server default. Voice activity detection sensitivity (lower = more sensitive).")
    obs.obs_properties_add_button(adv, "force_endpoint_btn", "Force End of Turn", _on_force_endpoint_clicked)
    obs.obs_properties_add_group(props, "advanced", "Turn Detection", obs.OBS_GROUP_NORMAL, adv)

    return props


def script_update(settings):
    global _settings, _formatter, _text_source_name
    global _center_horizontal, _center_vertical

    _settings = settings
    _text_source_name = obs.obs_data_get_string(settings, "text_source")
    _center_horizontal = obs.obs_data_get_bool(settings, "center_horizontal")
    _center_vertical = obs.obs_data_get_bool(settings, "center_vertical")

    _formatter = CaptionFormatter(CaptionConfig(
        mode=obs.obs_data_get_string(settings, "caption_mode"),
        max_words=obs.obs_data_get_int(settings, "max_words"),
        fade_out_seconds=obs.obs_data_get_double(settings, "fade_out_seconds"),
        max_lines=obs.obs_data_get_int(settings, "max_lines"),
        chars_per_line=obs.obs_data_get_int(settings, "chars_per_line"),
        show_previous_final=obs.obs_data_get_bool(settings, "show_previous_final"),
        show_speaker_labels=obs.obs_data_get_bool(settings, "speaker_labels"),
    ))


def _transcription_config(settings) -> TranscriptionConfig:
    """Read the transcription settings. 0 / blank means "use the server default"."""
    speech_model = obs.obs_data_get_string(settings, "speech_model")
    if speech_model not in SPEECH_MODELS:
        log.warning("Speech model %r is no longer available; using %s",
                    speech_model, DEFAULT_SPEECH_MODEL)
        speech_model = DEFAULT_SPEECH_MODEL

    return TranscriptionConfig(
        api_key=obs.obs_data_get_string(settings, "api_key") or api_key_from_env(),
        speech_model=speech_model,
        region=obs.obs_data_get_string(settings, "region"),
        language_codes=parse_list(obs.obs_data_get_string(settings, "language_codes").lower()),
        language_detection=obs.obs_data_get_bool(settings, "language_detection"),
        keyterms_prompt=parse_list(obs.obs_data_get_string(settings, "keyterms_prompt")),
        prompt=obs.obs_data_get_string(settings, "prompt").strip() or None,
        domain="medical-v1" if obs.obs_data_get_bool(settings, "medical_mode") else None,
        mode=obs.obs_data_get_string(settings, "mode") or None,
        speaker_labels=obs.obs_data_get_bool(settings, "speaker_labels"),
        max_speakers=obs.obs_data_get_int(settings, "max_speakers") or None,
        voice_focus=obs.obs_data_get_string(settings, "voice_focus") or None,
        voice_focus_threshold=obs.obs_data_get_double(settings, "voice_focus_threshold") or None,
        filter_profanity=obs.obs_data_get_bool(settings, "filter_profanity"),
        redact_pii=obs.obs_data_get_bool(settings, "redact_pii"),
        redact_pii_sub=obs.obs_data_get_string(settings, "redact_pii_sub") or None,
        end_of_turn_confidence_threshold=obs.obs_data_get_double(settings, "end_of_turn_confidence") or None,
        min_turn_silence=obs.obs_data_get_int(settings, "min_turn_silence") or None,
        max_turn_silence=obs.obs_data_get_int(settings, "max_turn_silence") or None,
        vad_threshold=obs.obs_data_get_double(settings, "vad_threshold") or None,
    )


def script_load(settings):
    log.info("obs-assemblyai-captions script loaded")


def script_unload():
    _stop_engine()
    log.info("obs-assemblyai-captions script unloaded")


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------

def _on_start_clicked(props, prop):
    _start_engine()
    return True


def _on_stop_clicked(props, prop):
    _stop_engine()
    return True


def _on_apply_clicked(props, prop):
    if _engine is not None and _settings is not None:
        _engine.update_configuration(_transcription_config(_settings))
    else:
        log.info("Captions are not running; changes apply on next start")
    return False


def _on_force_endpoint_clicked(props, prop):
    if _engine is not None:
        _engine.force_endpoint()
    return False


# ---------------------------------------------------------------------------
# Start / Stop
# ---------------------------------------------------------------------------

def _start_engine():
    global _engine, _caption_state

    if _engine is not None and _engine.is_running:
        log.info("Already running")
        return

    if _settings is None:
        return

    tx_config = _transcription_config(_settings)
    if not tx_config.api_key:
        log.error("No API key set. Enter your AssemblyAI API key or set ASSEMBLYAI_API_KEY.")
        return

    if not _text_source_name:
        log.error("No text source selected. Create a Text source and select it in settings.")
        return

    mic_val = obs.obs_data_get_int(_settings, "mic_device")
    audio_config = AudioConfig(device=None if mic_val == -1 else mic_val)

    _caption_state = CaptionState()
    _engine = TranscriptionEngine(tx_config, audio_config)
    _engine.start(_caption_state)

    # Timer to push captions to OBS text source every 100ms
    obs.timer_add(_update_text_source, 100)

    log.info("Captions started (model=%s, mic=%s, source=%s)",
             tx_config.speech_model, audio_config.device, _text_source_name)


def _stop_engine():
    global _engine

    obs.timer_remove(_update_text_source)

    if _engine is not None:
        _engine.stop()
        _engine = None

    # Clear the text source
    _set_text("")

    log.info("Captions stopped")


# ---------------------------------------------------------------------------
# Timer callback — runs on OBS main thread, updates the Text source
# ---------------------------------------------------------------------------

def _update_text_source():
    if _caption_state is None or _formatter is None:
        return

    text = _formatter.format(_caption_state.get())
    _set_text(text)

    if _center_horizontal or _center_vertical:
        _center_source()


def _set_text(text: str):
    """Update the OBS text source. Must be called from the main thread."""
    if not _text_source_name:
        return

    source = obs.obs_get_source_by_name(_text_source_name)
    if source is not None:
        settings = obs.obs_data_create()
        obs.obs_data_set_string(settings, "text", text)
        obs.obs_source_update(source, settings)
        obs.obs_data_release(settings)
        obs.obs_source_release(source)


def _center_source():
    """Reposition the text source to center it on the canvas."""
    if not _text_source_name:
        return

    source = obs.obs_get_source_by_name(_text_source_name)
    if source is None:
        return

    src_w = obs.obs_source_get_width(source)
    src_h = obs.obs_source_get_height(source)

    # Get canvas size
    ovi = obs.obs_video_info()
    obs.obs_get_video_info(ovi)
    canvas_w = ovi.base_width
    canvas_h = ovi.base_height

    # Find the scene item in the current scene
    current_scene_source = obs.obs_frontend_get_current_scene()
    if current_scene_source is None:
        obs.obs_source_release(source)
        return

    scene = obs.obs_scene_from_source(current_scene_source)
    scene_item = obs.obs_scene_find_source(scene, _text_source_name)

    if scene_item is not None:
        pos = obs.vec2()
        obs.obs_sceneitem_get_pos(scene_item, pos)

        if _center_horizontal and canvas_w > 0 and src_w > 0:
            pos.x = (canvas_w - src_w) / 2.0
        if _center_vertical and canvas_h > 0 and src_h > 0:
            pos.y = (canvas_h - src_h) / 2.0

        obs.obs_sceneitem_set_pos(scene_item, pos)

    obs.obs_source_release(current_scene_source)
    obs.obs_source_release(source)
