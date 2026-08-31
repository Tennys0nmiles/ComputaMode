"""Voice command registry: app entries, intent definitions, slot types, and embedding cache."""

import configparser
import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

# ── Word-number parsing ────────────────────────────────────────────────────────

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NON_NUMBER_RE = re.compile(r'\b(?:percent|volume|to|at|the|a)\b', re.I)


def parse_number(text: str) -> Optional[int]:
    """Parse digit or English word numbers from text.

    Examples:
        "42"                   → 42
        "forty two"            → 42
        "forty-two"            → 42
        "volume forty percent" → 40
        "nineteen"             → 19
        "blorp"                → None
    """
    text = text.strip().lower()
    # Strip non-number contextual words
    stripped = _NON_NUMBER_RE.sub("", text).strip()
    # Try direct int
    try:
        return int(stripped)
    except ValueError:
        pass

    # Normalize hyphens → spaces
    stripped = stripped.replace("-", " ").strip()
    words = stripped.split()

    total = 0
    found = False
    i = 0
    while i < len(words):
        w = words[i]
        if w in _ONES:
            total += _ONES[w]
            found = True
        elif w in _TENS:
            total += _TENS[w]
            found = True
            # check for compound: "forty two"
            if i + 1 < len(words) and words[i + 1] in _ONES:
                total += _ONES[words[i + 1]]
                i += 1
        i += 1

    return total if found else None


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class AppEntry:
    name: str
    aliases: list
    verbs: dict  # {"open": "...", "close": "...", ...}
    keywords: list
    source: str  # "yaml" or "desktop"


class SlotType(Enum):
    APP = "app"
    NUMBER = "number"
    TEXT = "text"
    LEVEL = "level"   # 0–10 scale; resolved value = str(n * 10) (percentage)


@dataclass
class SlotSpec:
    name: str
    slot_type: SlotType


@dataclass
class ParsedPattern:
    raw: str
    segments: list  # alternating str (literal) and SlotSpec

    @classmethod
    def from_string(cls, pattern: str) -> "ParsedPattern":
        """Parse a pattern like "set volume to {number}" or "open {app}".

        Returns a ParsedPattern with segments alternating between
        literal strings and SlotSpec objects.
        """
        segments = []
        slot_re = re.compile(r'\{(\w+)\}')
        last = 0
        for m in slot_re.finditer(pattern):
            literal = pattern[last:m.start()].strip()
            if literal:
                segments.append(literal)
            slot_name = m.group(1)
            if slot_name == "app":
                slot_type = SlotType.APP
            elif slot_name == "number":
                slot_type = SlotType.NUMBER
            elif slot_name == "level":
                slot_type = SlotType.LEVEL
            else:
                slot_type = SlotType.TEXT
            segments.append(SlotSpec(name=slot_name, slot_type=slot_type))
            last = m.end()
        trailing = pattern[last:].strip()
        if trailing:
            segments.append(trailing)
        return cls(raw=pattern, segments=segments)


@dataclass
class IntentEntry:
    name: str
    phrases: list
    action_template: str
    pattern: Optional[ParsedPattern] = None
    confirm: bool = False


# ── Registry ───────────────────────────────────────────────────────────────────

class Registry:
    CACHE_DIR = Path("~/.cache/compu-mode").expanduser()
    CACHE_FILE = CACHE_DIR / "embeddings.npz"
    DESKTOP_DIRS = [
        Path("/usr/share/applications"),
        Path("~/.local/share/applications").expanduser(),
    ]

    def __init__(self, config_path: str):
        self._config_path = config_path
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        yaml_apps = self._load_apps_from_yaml(cfg.get("apps", {}))
        desktop_apps = self._load_apps_from_desktop()
        self._apps = self._merge_apps(yaml_apps, desktop_apps)
        self._intents = self._load_intents(cfg.get("intents", {}))

        mc = cfg.get("matching", {})
        self._v2_threshold = mc.get("v2_confidence_threshold", 0.75)
        self._v2_margin = mc.get("v2_margin_gap", 0.10)
        self._tier3_timeout = mc.get("tier3_timeout_seconds", 6)

    # ── App loading ────────────────────────────────────────────────────────────

    def _load_apps_from_yaml(self, apps_cfg: dict) -> dict:
        apps = {}
        for name, cfg in (apps_cfg or {}).items():
            verbs = {k: v for k, v in cfg.items()
                     if k not in ("aliases", "keywords")}
            apps[name] = AppEntry(
                name=name,
                aliases=cfg.get("aliases", [name]),
                verbs=verbs,
                keywords=cfg.get("keywords", []),
                source="yaml",
            )
        return apps

    @staticmethod
    def _clean_exec(exec_str: str) -> str:
        """Strip %U %F %u %f field codes and leading env-var assignments."""
        cleaned = re.sub(r'%[UuFfDdNnickvm]', '', exec_str).strip()
        while re.match(r'^\w+=\S*\s+', cleaned):
            cleaned = re.sub(r'^\w+=\S*\s+', '', cleaned)
        return cleaned.strip()

    def _load_apps_from_desktop(self) -> dict:
        apps = {}
        for desktop_dir in self.DESKTOP_DIRS:
            if not desktop_dir.exists():
                continue
            for f in desktop_dir.glob("*.desktop"):
                parser = configparser.ConfigParser(strict=False, interpolation=None)
                try:
                    parser.read(str(f))
                except Exception:
                    continue
                if "Desktop Entry" not in parser:
                    continue
                de = parser["Desktop Entry"]
                if de.get("Type", "") != "Application":
                    continue
                if de.get("NoDisplay", "false").lower() == "true":
                    continue
                raw_name = de.get("Name", "")
                exec_cmd = de.get("Exec", "")
                if not raw_name or not exec_cmd:
                    continue
                name_key = raw_name.lower().replace(" ", "_")
                clean_cmd = self._clean_exec(exec_cmd)
                keywords_str = de.get("Keywords", "")
                keywords = [k.strip().lower() for k in keywords_str.split(";") if k.strip()]
                generic = de.get("GenericName", "")
                aliases = [raw_name.lower()]
                if generic:
                    aliases.append(generic.lower())
                aliases = list(dict.fromkeys(aliases))
                apps[name_key] = AppEntry(
                    name=name_key,
                    aliases=aliases,
                    verbs={"open": clean_cmd},
                    keywords=keywords,
                    source="desktop",
                )
        n = len(apps)
        print(f"[Registry] loaded {n} apps from .desktop files")
        return apps

    def _merge_apps(self, yaml_apps: dict, desktop_apps: dict) -> dict:
        merged = dict(desktop_apps)
        merged.update(yaml_apps)  # YAML wins on conflicts
        return merged

    # ── Intent loading ─────────────────────────────────────────────────────────

    def _load_intents(self, intents_cfg: dict) -> dict:
        intents = {}
        for name, cfg in (intents_cfg or {}).items():
            pattern_str = cfg.get("pattern")
            pattern = ParsedPattern.from_string(pattern_str) if pattern_str else None
            intents[name] = IntentEntry(
                name=name,
                phrases=cfg.get("phrases", []),
                action_template=cfg.get("action", ""),
                pattern=pattern,
                confirm=cfg.get("confirm", False),
            )
        return intents

    # ── Lookup API ─────────────────────────────────────────────────────────────

    def resolve_app(self, text: str) -> Optional[AppEntry]:
        """Find AppEntry by longest matching alias/keyword (word-boundary match)."""
        text_lower = text.lower()
        best_entry = None
        best_len = 0
        for entry in self._apps.values():
            for alias in entry.aliases + entry.keywords:
                if not alias:
                    continue
                try:
                    if re.search(r'\b' + re.escape(alias) + r'\b', text_lower):
                        if len(alias) > best_len:
                            best_len = len(alias)
                            best_entry = entry
                except re.error:
                    continue
        return best_entry

    def resolve_number(self, text: str) -> Optional[int]:
        return parse_number(text)

    def get_intent(self, name: str) -> Optional[IntentEntry]:
        return self._intents.get(name)

    def all_intents(self) -> list:
        return list(self._intents.values())

    def all_apps(self) -> list:
        return list(self._apps.values())

    # ── Embedding cache ────────────────────────────────────────────────────────

    def _compute_phrase_hash(self) -> str:
        lines = []
        for intent in sorted(self._intents.values(), key=lambda x: x.name):
            for phrase in sorted(intent.phrases):
                lines.append(f"{intent.name}|{phrase}")
        for app in sorted(self._apps.values(), key=lambda x: x.name):
            for alias in sorted(app.aliases):
                lines.append(f"__app__|{app.name}|{alias}")
        content = "\n".join(lines)
        return hashlib.sha256(content.encode()).hexdigest()

    def _normalize_phrase_for_embedding(self, phrase: str) -> str:
        """Replace slot placeholders with representative words for embedding."""
        phrase = re.sub(r'\{app\}', 'firefox', phrase)
        phrase = re.sub(r'\{number\}', 'fifty', phrase)
        phrase = re.sub(r'\{level\}', 'five', phrase)
        phrase = re.sub(r'\{text\}', 'hello world', phrase)
        return phrase

    def precompute_embeddings(self) -> None:
        """Encode all phrases with sentence-transformers and save to .npz cache."""
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError:
            print("[Registry] sentence-transformers not installed — skipping embeddings")
            return

        model = SentenceTransformer("all-MiniLM-L6-v2")
        phrases = []
        intent_names = []
        for intent in self._intents.values():
            for phrase in intent.phrases:
                normalized = self._normalize_phrase_for_embedding(phrase)
                phrases.append(normalized)
                intent_names.append(intent.name)
        if not phrases:
            return
        print(f"[Registry] encoding {len(phrases)} phrases...")
        embeddings = model.encode(phrases, convert_to_numpy=True, show_progress_bar=True)
        phrase_hash = self._compute_phrase_hash()
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.savez(
            str(self.CACHE_FILE),
            embeddings=embeddings,
            intent_names=np.array(intent_names),
            phrases=np.array(phrases),
            phrase_hash=np.array([phrase_hash]),
        )
        print(f"[Registry] saved embedding cache to {self.CACHE_FILE}")

    def load_embeddings(self):
        """Load embedding cache if present and hash matches.

        Returns:
            (embeddings np.ndarray, intent_names list[str], phrases list[str])
            or None if cache missing/stale.
        """
        try:
            import numpy as np
        except ImportError:
            return None

        if not self.CACHE_FILE.exists():
            return None
        try:
            data = np.load(str(self.CACHE_FILE), allow_pickle=False)
            stored_hash = str(data["phrase_hash"][0])
            current_hash = self._compute_phrase_hash()
            if stored_hash != current_hash:
                return None
            embeddings = data["embeddings"]
            intent_names = list(data["intent_names"])
            phrases = list(data["phrases"])
            return embeddings, intent_names, phrases
        except Exception:
            return None

    # ── LLM prompt ────────────────────────────────────────────────────────────

    def to_llm_prompt_json(self) -> str:
        """Build compact JSON intent list for Tier 3 LLM prompt (~1500 tokens max)."""
        items = []
        for intent in self._intents.values():
            slots = {}
            if intent.pattern:
                for seg in intent.pattern.segments:
                    if isinstance(seg, SlotSpec):
                        slots[seg.name] = seg.slot_type.value
            items.append({
                "intent": intent.name,
                "examples": intent.phrases[:3],
                "slots": slots,
            })
        return json.dumps(items, separators=(",", ":"))
