# 原单文件脚本的附录（历史文档，原文保存）

原 `vocab_anki_generator.py`（3025 行版）末尾附有两个纯文档字符串，拆包时
代码已迁入 `app/` 包，这两个附录不参与任何运行逻辑。为不丢失原文，收录于此。

## 附录 A: .env.example 文件内容

以下内容应保存为 .env.example 文件，与脚本同目录。

---- 文件: .env.example ----
```
# ============================================================
#  Anki 词汇卡牌生成器 — 环境变量模板
#  复制为 .env 并填入实际值
# ============================================================

# --- LLM API 配置 (OpenAI 兼容接口) ---
# 默认适配 DeepSeek API；也兼容 OpenAI / Azure / vLLM / Ollama 等
LLM_API_KEY=sk-your-deepseek-api-key-here
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# --- 并发与速率控制 ---
MAX_CONCURRENCY=8
RATE_LIMIT_PER_MIN=60
BATCH_SIZE=5
BATCH_MAX_TOKENS=8192

# --- 行为开关 ---
REPLACE_ALL=false
AUDIO_ENABLED=true
IMPORTANCE_MIN_LEVEL=低
```
---- 文件结束 ----

## 附录 B: 如何替换 TTS 引擎

### 替换 gTTS 为 Azure Speech SDK

1. 安装依赖:
   pip install azure-cognitiveservices-speech

2. 在 .env 中添加:
   AZURE_SPEECH_KEY=your-key
   AZURE_SPEECH_REGION=eastasia

3. 替换 `generate_audio_sync()` 函数:

   ```python
   import azure.cognitiveservices.speech as speechsdk

   def generate_audio_sync(word: str, output_dir: str = "media") -> str:
       filename = sanitize_filename(word) + ".mp3"
       filepath = Path(output_dir) / filename

       key = os.getenv("AZURE_SPEECH_KEY", "")
       region = os.getenv("AZURE_SPEECH_REGION", "eastasia")

       speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
       speech_config.speech_synthesis_voice_name = "en-US-JennyNeural"
       audio_config = speechsdk.audio.AudioOutputConfig(filename=str(filepath))
       synthesizer = speechsdk.SpeechSynthesizer(
           speech_config=speech_config, audio_config=audio_config
       )
       result = synthesizer.speak_text_async(word).get()
       if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
           return filename
       else:
           raise RuntimeError(f"Azure TTS 失败: {result.reason}")
   ```

### 替换 gTTS 为 pyttsx3（离线 TTS）

1. 安装依赖:
   pip install pyttsx3

2. 替换 `generate_audio_sync()`:

   ```python
   import pyttsx3

   def generate_audio_sync(word: str, output_dir: str = "media") -> str:
       filename = sanitize_filename(word) + ".mp3"
       filepath = Path(output_dir) / filename

       engine = pyttsx3.init()
       engine.setProperty("rate", 150)
       engine.setProperty("voice", "english")
       engine.save_to_file(word, str(filepath))
       engine.runAndWait()
       return filename
   ```

   注意: pyttsx3 依赖操作系统 TTS 引擎，音质因平台而异。
