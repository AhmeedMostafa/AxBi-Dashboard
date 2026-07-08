"""
Gemini Live API WebSocket proxy.

The browser cannot authenticate to the Gemini Live WebSocket directly:
ephemeral tokens are rejected for our key, and embedding the raw API key in the
client would leak it. So this consumer acts as a secure bridge:

    Browser  <-- ws/live/ -->  Django (Channels)  <-- wss -->  Gemini Live API

The API key never leaves the server. The consumer:
  - authenticates the browser via a Supabase JWT passed in the query string,
  - opens an upstream Live session (gemini-3.1-flash-live-preview),
  - injects a dataset-aware, Egyptian-Arabic system instruction,
  - relays audio + transcripts both ways,
  - forwards barge-in / interruption events,
  - enforces a hard session cap to protect quota.

Browser → server protocol (JSON text frames):
    {"type": "audio", "data": "<base64 PCM16 mono 16kHz>"}
    {"type": "audio_end"}                # mic muted; flush VAD
    {"type": "text",  "text": "..."}     # optional typed turn
    {"type": "interrupt"}                # manual barge-in

server → browser protocol (JSON text frames):
    {"type": "ready"}
    {"type": "audio", "data": "<base64 PCM16 mono 24kHz>"}
    {"type": "user_transcript", "text": "..."}
    {"type": "assistant_transcript", "text": "..."}
    {"type": "turn_complete"}
    {"type": "interrupted"}
    {"type": "error", "message": "..."}
    {"type": "closed", "reason": "..."}
"""

import asyncio
import json
import logging
import os
import time

import websockets
from channels.generic.websocket import AsyncWebsocketConsumer

from .supabase_client import verify_supabase_token
from .live_transcript_filter import user_transcript_allowed

logger = logging.getLogger(__name__)

# Only this Live model is available to our key (verified via probe). The other
# live model ids return "not found for API version v1alpha".
LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")

LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
)

# Hard cap on a single Live session (seconds) to protect audio quota even if the
# client misbehaves. The frontend also enforces idle/turn limits.
LIVE_MAX_SESSION_SECONDS = int(os.environ.get("LIVE_MAX_SESSION_SECONDS", "300") or 300)

# Default prebuilt Live voices per language.
LIVE_DEFAULT_VOICE = {
    "ar-EG": "Kore",
    "en-US": "Aoede",
}

# ── Function-calling support ─────────────────────────────────────────────────
# The voice agent reuses the SAME tool catalog as the typed chatbot so it can
# take real actions (navigate, run forecast, query data, generate charts, …).
_LIVE_TOOLS_CACHE = None


def _live_tools():
    """Serialize the chat TOOLS catalog into Live setup JSON (cached once)."""
    global _LIVE_TOOLS_CACHE
    if _LIVE_TOOLS_CACHE is None:
        try:
            from .chat import TOOLS
            _LIVE_TOOLS_CACHE = [
                t.model_dump(exclude_none=True, by_alias=True, mode="json") for t in TOOLS
            ]
        except Exception:
            logger.exception("Live: failed to serialize tool declarations")
            _LIVE_TOOLS_CACHE = []
    return _LIVE_TOOLS_CACHE


# Per-tool execution budgets. Slow analytical actions get longer leashes; the
# model is told to announce them first so the gap feels natural.
_LIVE_TOOL_TIMEOUTS = {
    "run_forecast": 240,
    "run_segmentation": 180,
    "get_recommendations": 120,
    "export_pdf": 120,
}
_LIVE_TOOL_DEFAULT_TIMEOUT = 45


def _resolve_api_key() -> str:
    return (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
        or ""
    ).strip()


def _normalize_lang(lang: str) -> str:
    low = (lang or "").strip().lower()
    if low in ("ar", "ar-eg", "ar-xa", "masri", "egyptian"):
        return "ar-EG"
    return "en-US"


def _build_system_instruction(dataset_id: str | None, lang: str) -> str:
    """Dataset-aware system instruction with voice-conversation rules."""
    # Reuse the chat prompt builder for dataset context (filename/category).
    try:
        from .chat import _build_system_prompt
        base = _build_system_prompt(dataset_id)
    except Exception:
        logger.exception("Live: failed to build base system prompt")
        base = "You are a helpful business-intelligence voice assistant."

    if lang == "ar-EG":
        rules = (
            "\n\nVOICE CONVERSATION MODE (Egyptian Arabic / المصري):\n"
            "- The user is SPEAKING to you and HEARING your spoken reply in real time.\n"
            "- The user speaks Egyptian Arabic (ar-EG). Transcribe their speech ONLY in Arabic script "
            "(Egyptian Arabic). NEVER transcribe as Hindi, Devanagari, English, Italian, or other languages.\n"
            "- CRITICAL: All input transcription text you produce must be in Arabic script matching ar-EG.\n"
            "- Reply ONLY in casual, natural Egyptian Arabic (Masri). NEVER use Fusha / MSA.\n"
            "- Keep replies SHORT and spoken: 1-2 sentences, ~40 words max.\n"
            "- No markdown, lists, code, or headings — just plain conversational speech.\n"
            "- Be warm, friendly and helpful, like chatting with a colleague.\n"
            "- When citing numbers, say them naturally in spoken Arabic.\n"
            "- If asked about the dataset, answer from the dataset context above.\n"
            "- نطاقك بس AxBi والداتا والتحليلات — مش مساعد عام. لو السؤال برا النطاق "
            "(رياضة، مشاهير، سياسة، أسئلة عامة، مواضيع شخصية): ارفض بلطف في جملة أو اتنين "
            "وقول إنك متخصص في داتا المشروع والميزات جوه المنصة، واقترح حاجة تقدر تعملها "
            "(مثلاً KPIs، رسم بياني، توقعات). متعرضش تتكلم في مواضيع تانية."
        )
        actions = (
            "\n\nالأكشنز والأدوات (مهم):\n"
            "- إنت مش بس بتتكلم، إنت كمان بتقدر تعمل حاجات جوه الموقع. عندك أدوات تنقل بين الصفحات، "
            "تشغّل التوقّعات (forecast)، تعمل تقسيم العملاء (segmentation)، تسأل عن البيانات، "
            "تعمل رسومات بيانية أو ثري دي، تجيب التوصيات، تتأكد من جودة البيانات، وتعمل export لتقرير الـ PDF.\n"
            "- أول ما المستخدم يطلب أي ميزة من الموقع أو أي أكشن، نادِ الأداة المناسبة على طول.\n"
            f"- الـ dataset المفتوح دلوقتي الـ id بتاعه: {dataset_id or 'مفيش'} — استخدمه لما الأداة تحتاج dataset_id إلا لو المستخدم سمّى داتا تانية.\n"
            "- قبل أي أكشن بياخد وقت (forecast أو segmentation أو التوصيات أو export PDF)، قول جملة قصيرة إنك بتشتغل عليها، وبعدين نادِ الأداة.\n"
            "- بعد ما الأداة ترجّع نتيجة، لخّصها في جملة أو اتنين بالكلام.\n"
            "- الرسومات والثري دي اللي بتعملها بتتعرض في شباك الشات، فقول للمستخدم يبص على الشات عشان يشوفها."
        )
    else:
        rules = (
            "\n\nVOICE CONVERSATION MODE:\n"
            "- The user is SPEAKING to you and HEARING your spoken reply in real time.\n"
            "- The user speaks English (en-US). Transcribe their speech ONLY in English.\n"
            "- NEVER transcribe user speech as Hindi, Devanagari, Arabic, or other languages.\n"
            "- Keep replies SHORT and spoken: 1-2 sentences, ~40 words max.\n"
            "- No markdown, lists, code, or headings — just plain conversational speech.\n"
            "- Be warm, friendly and natural, like talking, not writing.\n"
            "- If asked about the dataset, answer from the dataset context above.\n"
            "- You are NOT a general assistant. If the question is outside AxBi, their data, "
            "or business analytics (sports, celebrities, trivia, politics, etc.), politely refuse "
            "in one short sentence and redirect to what you CAN do with their data."
        )
        actions = (
            "\n\nACTIONS / TOOLS (important):\n"
            "- You can DO things in the app, not just talk. You have tools to navigate pages, "
            "run forecasts, run segmentation, query the data, generate charts and 3D visuals, "
            "get recommendations, check data quality, and export the PDF report.\n"
            "- Whenever the user asks for an app feature or an action, CALL the matching tool right away.\n"
            f"- The dataset currently open has id: {dataset_id or 'none'} — use it when a tool needs dataset_id unless the user names another dataset.\n"
            "- Before a SLOW action (run_forecast, run_segmentation, get_recommendations, export_pdf), say one short sentence that you're working on it, THEN call the tool.\n"
            "- After a tool returns, summarize the result in one or two spoken sentences.\n"
            "- Charts and 3D visuals you create are shown in the chat window, so tell the user to check the chat to see them."
        )
    return base + rules + actions


class LiveProxyConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.upstream = None
        self._pump_task = None
        self._watchdog_task = None
        self._tool_tasks = set()
        self._closing = False
        self._session_start = time.time()

        # ── Parse query params ───────────────────────────────────────────────
        qs = self.scope.get("query_string", b"").decode("utf-8", "ignore")
        params = {}
        for pair in qs.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k] = _url_unquote(v)

        token = params.get("token", "")
        self.lang = _normalize_lang(params.get("lang", "en-US"))
        self.dataset_id = params.get("dataset") or None
        voice_override = (params.get("voice") or "").strip()
        self.voice = voice_override or LIVE_DEFAULT_VOICE.get(self.lang, "Kore")

        # ── Authenticate ─────────────────────────────────────────────────────
        try:
            user_info = await asyncio.to_thread(verify_supabase_token, f"Bearer {token}")
            self.user_id = user_info.get("user_id", "")
            if not self.user_id:
                raise ValueError("no user id")
        except Exception as e:
            logger.warning(f"Live: auth rejected: {e}")
            await self.close(code=4401)
            return

        api_key = _resolve_api_key()
        if not api_key:
            await self.accept()
            await self._safe_send({"type": "error", "message": "Live not configured: missing GEMINI_API_KEY"})
            await self.close()
            return

        await self.accept()

        # ── Open upstream Gemini Live session ────────────────────────────────
        try:
            system_instruction = await asyncio.to_thread(
                _build_system_instruction, self.dataset_id, self.lang
            )
            setup = {
                "setup": {
                    "model": f"models/{LIVE_MODEL}",
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "temperature": 0.7,
                        "speechConfig": {
                            "languageCode": self.lang,
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {"voiceName": self.voice}
                            },
                        },
                    },
                    "systemInstruction": {"parts": [{"text": system_instruction}]},
                    # Give the voice agent the same action catalog as the typed chat.
                    "tools": _live_tools(),
                    # Transcription enabled; language is driven by speechConfig.languageCode
                    # + system instruction (languageCodes is not supported on v1alpha).
                    "inputAudioTranscription": {},
                    "outputAudioTranscription": {},
                }
            }
            url = f"{LIVE_WS_URL}?key={api_key}"
            self.upstream = await websockets.connect(url, max_size=None, ping_interval=20)
            await self.upstream.send(json.dumps(setup))
            # Confirm setup before accepting mic audio — fail fast on bad config.
            first = await asyncio.wait_for(self.upstream.recv(), timeout=15.0)
            first_data = json.loads(first)
            if "error" in first_data:
                err = first_data.get("error") or {}
                detail = err.get("message") or str(err)[:160]
                raise RuntimeError(f"Live setup rejected: {detail}")
            if "setupComplete" not in first_data:
                logger.warning(f"Live: unexpected first upstream frame: {str(first_data)[:200]}")
        except Exception as e:
            logger.exception("Live: failed to open upstream session")
            await self._safe_send({"type": "error", "message": f"Live connect failed: {str(e)[:160]}"})
            await self.close()
            return

        self._pump_task = asyncio.create_task(self._pump_downstream())
        self._watchdog_task = asyncio.create_task(self._session_watchdog())
        # setupComplete already consumed during connect handshake — tell the client.
        await self._safe_send({"type": "ready"})
        logger.info(
            f"[LIVE] user={self.user_id[:8]} lang={self.lang} voice={self.voice} "
            f"dataset={self.dataset_id} — session opened"
        )

    async def disconnect(self, code):
        self._closing = True
        for task in (self._pump_task, self._watchdog_task):
            if task:
                task.cancel()
        for task in list(getattr(self, "_tool_tasks", ())):
            task.cancel()
        if self.upstream:
            try:
                await self.upstream.close()
            except Exception:
                pass
        dur = int(time.time() - getattr(self, "_session_start", time.time()))
        logger.info(f"[LIVE] user={getattr(self,'user_id','?')[:8]} — session closed after {dur}s (code={code})")

    async def receive(self, text_data=None, bytes_data=None):
        """Browser → Gemini. Translate the small client protocol to Live frames."""
        if not self.upstream or self._closing:
            return
        try:
            msg = json.loads(text_data) if text_data else {}
        except Exception:
            return

        mtype = msg.get("type")
        try:
            if mtype == "audio":
                data = msg.get("data")
                if data:
                    await self.upstream.send(json.dumps({
                        "realtimeInput": {
                            "audio": {"data": data, "mimeType": "audio/pcm;rate=16000"}
                        }
                    }))
            elif mtype == "audio_end":
                await self.upstream.send(json.dumps({
                    "realtimeInput": {"audioStreamEnd": True}
                }))
            elif mtype == "text":
                text = (msg.get("text") or "").strip()
                if text:
                    await self.upstream.send(json.dumps({
                        "clientContent": {
                            "turns": [{"role": "user", "parts": [{"text": text}]}],
                            "turnComplete": True,
                        }
                    }))
            # "interrupt" is implicit: new audio input triggers server-side VAD
            # barge-in automatically; no explicit frame needed.
        except Exception as e:
            logger.warning(f"Live: upstream send failed: {e}")

    async def _pump_downstream(self):
        """Gemini → browser. Relay audio, transcripts and control events."""
        try:
            async for raw in self.upstream:
                if self._closing:
                    break
                try:
                    data = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
                except Exception:
                    continue
                await self._forward_to_client(data)
        except websockets.ConnectionClosed:
            pass
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f"Live: downstream pump error: {e}")
            await self._safe_send({"type": "error", "message": str(e)[:160]})
        finally:
            if not self._closing:
                await self._safe_send({"type": "closed", "reason": "upstream ended"})
                await self.close()

    async def _forward_to_client(self, data: dict):
        if "error" in data:
            err = data.get("error") or {}
            detail = err.get("message") or str(err)[:160]
            await self._safe_send({"type": "error", "message": detail})
            return

        if "setupComplete" in data:
            await self._safe_send({"type": "ready"})
            return

        # The model wants us to run one or more tools. Do it off the pump loop so
        # downstream audio/cancellation frames keep flowing while tools execute.
        tc = data.get("toolCall")
        if tc:
            task = asyncio.create_task(self._handle_tool_call(tc))
            self._tool_tasks.add(task)
            task.add_done_callback(self._tool_tasks.discard)
            return

        if "toolCallCancellation" in data:
            for task in list(self._tool_tasks):
                task.cancel()
            return

        sc = data.get("serverContent")
        if sc:
            if sc.get("interrupted"):
                await self._safe_send({"type": "interrupted"})
            # User speech transcript (drop wrong-script hallucinations from Live STT).
            it = sc.get("inputTranscription") or {}
            if it.get("text"):
                utext = it["text"]
                if user_transcript_allowed(utext, self.lang):
                    await self._safe_send({"type": "user_transcript", "text": utext})
                else:
                    logger.debug(
                        "Live: dropped user transcript (lang=%s): %s",
                        self.lang,
                        utext[:80],
                    )
            # Assistant speech transcript
            ot = sc.get("outputTranscription") or {}
            if ot.get("text"):
                await self._safe_send({"type": "assistant_transcript", "text": ot["text"]})
            # Audio chunks
            mt = sc.get("modelTurn") or {}
            for part in mt.get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if inline.get("data"):
                    await self._safe_send({"type": "audio", "data": inline["data"]})
                elif part.get("text"):
                    await self._safe_send({"type": "output_text", "text": part["text"]})
            if sc.get("turnComplete"):
                await self._safe_send({"type": "turn_complete"})
            return

        if "goAway" in data:
            await self._safe_send({"type": "closed", "reason": "server goaway"})
            await self.close()

    async def _handle_tool_call(self, tc: dict):
        """Execute the model's requested tool(s) and return matching responses."""
        fcs = tc.get("functionCalls") or []
        if not fcs:
            return
        responses = []
        for fc in fcs:
            fid = fc.get("id")
            name = fc.get("name") or ""
            args = fc.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            # Default any dataset_id arg to the dataset open in this session.
            if self.dataset_id:
                args.setdefault("dataset_id", self.dataset_id)
            result = await self._run_tool(name, args)
            responses.append({"id": fid, "name": name, "response": result})

        if self._closing or not self.upstream:
            return
        try:
            # default=str guards against numpy scalars from tools like query_data.
            await self.upstream.send(json.dumps({
                "toolResponse": {"functionResponses": responses}
            }, default=str))
        except Exception as e:
            logger.warning(f"Live: toolResponse send failed: {e}")

    async def _run_tool(self, name: str, args: dict) -> dict:
        """Run a single chat tool in a worker thread with a per-tool timeout.

        Side-effects (navigation actions, charts, 3D visuals) are pushed to the
        browser; the compact result dict is returned to the model to narrate.
        """
        from .chat import _execute_function
        timeout = _LIVE_TOOL_TIMEOUTS.get(name, _LIVE_TOOL_DEFAULT_TIMEOUT)
        logger.info(f"[LIVE] tool call name={name} args={list(args.keys())} timeout={timeout}s")
        try:
            result, action, chart, visual_3d = await asyncio.wait_for(
                asyncio.to_thread(_execute_function, name, args, self.user_id),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[LIVE] tool '{name}' timed out after {timeout}s")
            return {"error": f"The {name} action took too long and was stopped. "
                             "Suggest the user opens the page and runs it there."}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"[LIVE] tool '{name}' failed")
            return {"error": f"Action failed: {str(e)[:160]}"}

        if action:
            await self._safe_send({"type": "action", "action": action})
        if chart:
            await self._safe_send({"type": "chart", "data": chart})
        if visual_3d:
            await self._safe_send({"type": "visual3d", "data": visual_3d})
        # KPI cards come back inside the result dict (not as a chart/visual), so
        # push them to the board explicitly when generate_metrics runs by voice.
        if isinstance(result, dict) and result.get("__kind__") == "metrics":
            await self._safe_send({"type": "metrics", "data": result})

        return result if isinstance(result, dict) else {"result": result}

    async def _session_watchdog(self):
        try:
            await asyncio.sleep(LIVE_MAX_SESSION_SECONDS)
            if not self._closing:
                await self._safe_send({
                    "type": "closed",
                    "reason": f"session limit ({LIVE_MAX_SESSION_SECONDS}s)",
                })
                await self.close()
        except asyncio.CancelledError:
            return

    async def _safe_send(self, payload: dict):
        try:
            await self.send(text_data=json.dumps(payload, default=str))
        except Exception:
            pass


def _url_unquote(value: str) -> str:
    from urllib.parse import unquote
    return unquote(value)
