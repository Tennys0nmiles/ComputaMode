"""V2 voice command interpreter: 3-tier matching pipeline.

Tier 1: deterministic slot-grammar       (<50ms)
Tier 2: rapidfuzz + sentence-transformers (<100ms total)
Tier 3: LLM JSON command selection        (≤6s)
"""

import json
import re
import shlex
import threading
import time
from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import quote_plus

from src.voice.registry import Registry, SlotType, SlotSpec, IntentEntry

# LEVEL slot: valid input range 0–10; resolved value = str(n * 10) percentage
_LEVEL_MIN, _LEVEL_MAX = 0, 10

_PUNCT_RE = re.compile(r"[^\w\s]")

_FILLER_DEFAULTS = frozenset({"um", "uh", "like", "so", "please", "hey",
                               "okay", "ok", "can you", "could you"})


def _normalize(text: str, fillers: set) -> str:
    """Lowercase, strip punctuation, remove filler words."""
    text = _PUNCT_RE.sub("", text.lower()).strip()
    for filler in sorted(fillers, key=len, reverse=True):
        text = re.sub(r'\b' + re.escape(filler) + r'\b', ' ', text)
    return ' '.join(text.split())


@dataclass
class MatchResult:
    intent_name: str          # "_error_" signals Tier 3 hard failure
    action: str               # fully resolved shell command
    confidence: float
    tier: Literal["1", "2", "3", "conv"]
    args: dict
    needs_confirm: bool
    clarification_question: Optional[str] = None


class Interpreter:
    _CHAIN_SPLIT = re.compile(
        r'\b(?:and then|after that|and also|and|then|also|next)\b'
    )
    _DICTATE_PREFIX = re.compile(
        r'^(?:write|right|type|dictate|input)\s+(.+)$', re.I
    )
    _SEARCH_PREFIX = re.compile(
        r'^(?:search(?:\s+for)?|google|look\s+up|find)\s+(.+?)(?:\s+online)?$',
        re.I
    )

    def __init__(self, registry: Registry, config: dict):
        self._registry = registry
        mc = config.get("matching", {})
        self._threshold = mc.get("v2_confidence_threshold", 0.75)
        self._margin = mc.get("v2_margin_gap", 0.10)
        self._tier3_timeout = mc.get("tier3_timeout_seconds", 6)
        self._fillers = set(mc.get("filler_words", list(_FILLER_DEFAULTS)))

        # Lazy embedding fields (thread-safe init)
        self._embeddings = None
        self._emb_intent_names = None
        self._emb_phrases = None
        self._model = None
        self._emb_lock = threading.Lock()
        self._emb_loaded = False

        # LLM config
        assistant_cfg = config.get("assistant", {})
        llm_cfg = assistant_cfg.get("llm", {})
        self._llm_base_url = llm_cfg.get("base_url", "http://localhost:11434")
        self._llm_model = llm_cfg.get("model", "qwen3:4b")

    # ── Tier 1 ────────────────────────────────────────────────────────────────

    def _tier1_match(self, normalized: str) -> Optional[MatchResult]:
        """Deterministic slot-grammar matching against all patterned intents."""
        for intent in self._registry.all_intents():
            if intent.pattern is None:
                continue
            result = self._try_pattern(normalized, intent)
            if result is not None:
                return result
        return None

    def _try_pattern(self, text: str, intent: IntentEntry) -> Optional[MatchResult]:
        """Attempt to match text against intent's ParsedPattern."""
        segs = intent.pattern.segments
        if not segs:
            return None

        args = {}
        if not self._match_segments(text, segs, 0, args):
            return None

        action = self._substitute_action(intent.action_template, args)
        if action is None:
            return None
        return MatchResult(
            intent_name=intent.name,
            action=action,
            confidence=1.0,
            tier="1",
            args={k: v for k, v in args.items() if not k.startswith("_")},
            needs_confirm=intent.confirm,
        )

    def _match_segments(self, text: str, segs: list, seg_idx: int, args: dict) -> bool:
        """Recursive segment matcher. Modifies args in-place on success."""
        if seg_idx == len(segs):
            return text.strip() == ""

        seg = segs[seg_idx]

        if isinstance(seg, str):
            lit = seg.lower()
            t = text.lstrip()
            if not t.lower().startswith(lit):
                return False
            remaining = t[len(lit):].lstrip()
            return self._match_segments(remaining, segs, seg_idx + 1, args)

        # SlotSpec
        assert isinstance(seg, SlotSpec)
        next_segs = segs[seg_idx + 1:]

        if seg.slot_type == SlotType.TEXT:
            if not next_segs:
                val = self._extract_slot_value(text.strip(), seg.slot_type)
                if val is None:
                    return False
                args[seg.name] = val
                return True
            # Find next literal to bound the text slot
            next_literal = next((s.lower() for s in next_segs if isinstance(s, str)), None)
            if next_literal:
                idx = text.lower().find(next_literal)
                if idx < 0:
                    return False
                val = self._extract_slot_value(text[:idx].strip(), seg.slot_type)
                if val is None:
                    return False
                args[seg.name] = val
                return self._match_segments(text[idx:], segs, seg_idx + 1, args)
            val = self._extract_slot_value(text.strip(), seg.slot_type)
            if val is None:
                return False
            args[seg.name] = val
            return True

        elif seg.slot_type == SlotType.APP:
            resolved = self._registry.resolve_app(text)
            if resolved is None:
                return False
            for alias in sorted(resolved.aliases + resolved.keywords, key=len, reverse=True):
                if not alias:
                    continue
                try:
                    m = re.search(r'\b' + re.escape(alias.lower()) + r'\b', text.lower())
                    if m:
                        args[seg.name] = resolved.name
                        args["_app_entry"] = resolved
                        remaining = text[m.end():].strip()
                        if self._match_segments(remaining, segs, seg_idx + 1, args):
                            return True
                except re.error:
                    continue
            return False

        else:  # NUMBER
            if not next_segs:
                val = self._extract_slot_value(text.strip(), seg.slot_type)
                if val is None:
                    return False
                args[seg.name] = val
                return True
            next_literal = next((s.lower() for s in next_segs if isinstance(s, str)), None)
            if next_literal:
                idx = text.lower().find(next_literal)
                if idx < 0:
                    val = self._extract_slot_value(text.strip(), seg.slot_type)
                    if val is None:
                        return False
                    args[seg.name] = val
                    return self._match_segments("", segs, seg_idx + 1, args)
                val = self._extract_slot_value(text[:idx].strip(), seg.slot_type)
                if val is None:
                    return False
                args[seg.name] = val
                return self._match_segments(text[idx:], segs, seg_idx + 1, args)
            val = self._extract_slot_value(text.strip(), seg.slot_type)
            if val is None:
                return False
            args[seg.name] = val
            return True

    def _extract_slot_value(self, raw: str, slot_type: SlotType) -> Optional[str]:
        if slot_type == SlotType.APP:
            entry = self._registry.resolve_app(raw)
            return entry.name if entry else None
        elif slot_type == SlotType.NUMBER:
            n = self._registry.resolve_number(raw)
            return str(n) if n is not None else None
        elif slot_type == SlotType.LEVEL:
            n = self._registry.resolve_number(raw)
            if n is None or not (_LEVEL_MIN <= n <= _LEVEL_MAX):
                return None
            return str(n * 10)
        else:  # TEXT
            return raw if raw else None

    def _substitute_action(self, template: str, args: dict, app_entry=None) -> Optional[str]:
        """Substitute slot values into action template."""
        if app_entry is None:
            app_entry = args.get("_app_entry")

        result = template

        # Handle {app.VERB} style templates first
        app_verb_re = re.compile(r'\{app\.(\w+)\}')
        for m in app_verb_re.finditer(template):
            verb_name = m.group(1)
            if app_entry and verb_name in app_entry.verbs:
                result = result.replace(m.group(0), app_entry.verbs[verb_name])
            else:
                return None  # verb not available for this app

        # Handle plain {app}
        if "{app}" in result:
            if app_entry:
                result = result.replace("{app}", app_entry.name)
            else:
                return None

        # Handle other slots
        for key, val in args.items():
            if key.startswith("_"):
                continue
            placeholder = "{" + key + "}"
            if placeholder in result:
                if key == "text":
                    result = result.replace(placeholder, shlex.quote(val))
                else:
                    result = result.replace(placeholder, str(val))

        return result

    # ── Tier 2 ────────────────────────────────────────────────────────────────

    def _ensure_embeddings_loaded(self) -> bool:
        """Thread-safe lazy load of embedding cache and sentence-transformer model."""
        with self._emb_lock:
            if self._emb_loaded:
                return self._embeddings is not None
            self._emb_loaded = True
            data = self._registry.load_embeddings()
            if data is None:
                return False
            self._embeddings, self._emb_intent_names, self._emb_phrases = data
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer("all-MiniLM-L6-v2")
            except ImportError:
                self._model = None
            return True

    def _tier2_match(self, normalized: str) -> Optional[MatchResult]:
        """rapidfuzz fuzzy match + optional semantic scoring on top-10 candidates."""
        try:
            from rapidfuzz import process as rfuzz_process
            from rapidfuzz.fuzz import token_set_ratio
        except ImportError:
            return None

        has_embeddings = self._ensure_embeddings_loaded()

        # Build phrase list (from cache or from intents directly)
        if has_embeddings and self._emb_phrases:
            all_phrases = self._emb_phrases
            all_intent_names = self._emb_intent_names
        else:
            all_phrases = []
            all_intent_names = []
            for intent in self._registry.all_intents():
                for phrase in intent.phrases:
                    all_phrases.append(phrase.lower())
                    all_intent_names.append(intent.name)

        if not all_phrases:
            return None

        candidates = rfuzz_process.extract(
            normalized, all_phrases,
            scorer=token_set_ratio,
            limit=10,
        )
        if not candidates:
            return None

        # Semantic scoring on top-10 rapidfuzz candidates only (~5 vectors max)
        scores = []
        if has_embeddings and self._model is not None and self._embeddings is not None:
            try:
                import numpy as np
                candidate_texts = [c[0] for c in candidates]
                cand_embs = self._model.encode(candidate_texts, convert_to_numpy=True)
                query_emb = self._model.encode([normalized], convert_to_numpy=True)[0]
                norms_c = np.linalg.norm(cand_embs, axis=1)
                norm_q = float(np.linalg.norm(query_emb))
                for i, (phrase, rfuzz_score, idx) in enumerate(candidates):
                    if norms_c[i] > 0 and norm_q > 0:
                        cos_sim = float(np.dot(cand_embs[i], query_emb) / (norms_c[i] * norm_q))
                    else:
                        cos_sim = 0.0
                    combined = max(rfuzz_score / 100.0, cos_sim)
                    intent_name = all_intent_names[idx] if idx < len(all_intent_names) else ""
                    scores.append((intent_name, phrase, combined))
            except Exception:
                # Fall through to rapidfuzz-only
                for phrase, rfuzz_score, idx in candidates:
                    intent_name = all_intent_names[idx] if idx < len(all_intent_names) else ""
                    scores.append((intent_name, phrase, rfuzz_score / 100.0))
        else:
            for phrase, rfuzz_score, idx in candidates:
                intent_name = all_intent_names[idx] if idx < len(all_intent_names) else ""
                scores.append((intent_name, phrase, rfuzz_score / 100.0))

        if not scores:
            return None

        scores.sort(key=lambda x: x[2], reverse=True)
        best_intent_name, best_phrase, best_score = scores[0]
        second_score = scores[1][2] if len(scores) > 1 else 0.0

        if best_score < self._threshold:
            return None
        if (best_score - second_score) < self._margin:
            return None

        intent = self._registry.get_intent(best_intent_name)
        if intent is None:
            return None

        # Attempt slot resolution; set clarification if slot unresolvable
        args = {}
        clarification = None
        if intent.pattern:
            for seg in intent.pattern.segments:
                if isinstance(seg, SlotSpec):
                    val = self._extract_slot_value(normalized, seg.slot_type)
                    if val:
                        args[seg.name] = val
                    elif seg.slot_type != SlotType.TEXT:
                        clarification = f"Which {seg.slot_type.value} did you mean?"

        action = self._substitute_action(intent.action_template, args)
        if action is None:
            action = intent.action_template

        return MatchResult(
            intent_name=intent.name,
            action=action,
            confidence=best_score,
            tier="2",
            args=args,
            needs_confirm=intent.confirm,
            clarification_question=clarification,
        )

    # ── Tier 3 ────────────────────────────────────────────────────────────────

    def _call_llm_json(self, system_prompt: str, user_text: str) -> Optional[dict]:
        """POST to Ollama /api/chat with timeout. Returns parsed JSON or None."""
        import requests
        url = f"{self._llm_base_url}/api/chat"
        payload = {
            "model": self._llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        try:
            resp = requests.post(url, json=payload, timeout=self._tier3_timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            # Strip <think>...</think> blocks
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            # Extract first JSON object via brace-depth parser
            brace_depth = 0
            start = None
            for i, ch in enumerate(content):
                if ch == '{':
                    if start is None:
                        start = i
                    brace_depth += 1
                elif ch == '}':
                    brace_depth -= 1
                    if brace_depth == 0 and start is not None:
                        return json.loads(content[start:i + 1])
        except Exception:
            return None
        return None

    def _validate_llm_args(self, intent: IntentEntry, args: dict) -> bool:
        """Validate LLM-provided args against intent slot schema."""
        if intent.pattern is None:
            return True
        for seg in intent.pattern.segments:
            if isinstance(seg, SlotSpec) and seg.name in args:
                val = args[seg.name]
                if seg.slot_type == SlotType.APP:
                    if self._registry.resolve_app(str(val)) is None:
                        return False
                elif seg.slot_type == SlotType.NUMBER:
                    try:
                        int(val)
                    except (ValueError, TypeError):
                        return False
        return True

    def _tier3_match(self, original_text: str) -> Optional[MatchResult]:
        """LLM-based intent selection as last resort."""
        intents_json = self._registry.to_llm_prompt_json()
        system_prompt = (
            "You are a voice command parser. Given a voice command, identify the intent.\n"
            'Respond with ONLY a JSON object: {"intent": "<intent_name>", "args": {...}}\n'
            'If no intent matches, respond: {"intent": "unknown"}\n'
            f"Available intents:\n{intents_json}"
        )

        result = self._call_llm_json(system_prompt, original_text)
        if result is None:
            return MatchResult(
                intent_name="_error_",
                action="",
                confidence=0.0,
                tier="3",
                args={},
                needs_confirm=False,
            )

        intent_name = result.get("intent", "unknown")
        if intent_name == "unknown":
            return None  # route to Nova

        intent = self._registry.get_intent(intent_name)
        if intent is None:
            return MatchResult(
                intent_name="_error_",
                action="",
                confidence=0.0,
                tier="3",
                args={},
                needs_confirm=False,
            )

        args = result.get("args") or {}
        if not self._validate_llm_args(intent, args):
            return MatchResult(
                intent_name="_error_",
                action="",
                confidence=0.0,
                tier="3",
                args={},
                needs_confirm=False,
            )

        action = self._substitute_action(intent.action_template, args)
        if action is None:
            action = intent.action_template

        return MatchResult(
            intent_name=intent.name,
            action=action,
            confidence=0.9,
            tier="3",
            args=args,
            needs_confirm=intent.confirm,
        )

    # ── Main entry ────────────────────────────────────────────────────────────

    def interpret(self, transcript: str) -> Optional[MatchResult]:
        """Run Tier 1 → Tier 2 → Tier 3 on a single transcript segment."""
        normalized = _normalize(transcript, self._fillers)
        if not normalized:
            return None

        result = self._tier1_match(normalized)
        if result is not None:
            return result

        result = self._tier2_match(normalized)
        if result is not None:
            return result

        return self._tier3_match(transcript)

    def interpret_all(self, transcript: str) -> list:
        """Handle pre-tier patterns, chain splitting, and return list of MatchResults."""
        transcript = transcript.strip()
        if not transcript:
            return []

        # 1. Dictation check BEFORE chain split
        m = self._DICTATE_PREFIX.match(transcript)
        if m:
            content = m.group(1).strip()
            if content:
                action = f"ydotool type --delay 100 -- {shlex.quote(content)}"
                return [MatchResult(
                    intent_name="type_text",
                    action=action,
                    confidence=1.0,
                    tier="1",
                    args={"text": content},
                    needs_confirm=False,
                )]
            return []

        # 2. Search check BEFORE chain split
        m = self._SEARCH_PREFIX.match(transcript)
        if m:
            query = m.group(1).strip()
            if query:
                url = f"https://www.google.com/search?q={quote_plus(query)}"
                action = f"xdg-open {shlex.quote(url)}"
                return [MatchResult(
                    intent_name="search",
                    action=action,
                    confidence=1.0,
                    tier="1",
                    args={"query": query},
                    needs_confirm=False,
                )]
            return []

        # 3. Chain split → interpret each segment
        segments = [s.strip() for s in self._CHAIN_SPLIT.split(transcript) if s.strip()]
        if not segments:
            segments = [transcript]

        results = []
        for seg in segments:
            # Check search prefix per-segment too
            sm = self._SEARCH_PREFIX.match(seg)
            if sm:
                query = sm.group(1).strip()
                if query:
                    url = f"https://www.google.com/search?q={quote_plus(query)}"
                    action = f"xdg-open {shlex.quote(url)}"
                    results.append(MatchResult(
                        intent_name="search",
                        action=action,
                        confidence=1.0,
                        tier="1",
                        args={"query": query},
                        needs_confirm=False,
                    ))
                continue

            result = self.interpret(seg)
            if result is not None:
                results.append(result)

        return results
