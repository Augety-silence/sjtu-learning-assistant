"""只读、可审计的课程领域术语种子库。

种子词表只提供低优先级的通用 baseline，不写入 ``CourseMemory``。调用方可把
课程记忆传给检索函数；同一术语发生冲突时，任何课程记忆记录都优先于种子记录。
"""

from __future__ import annotations

import json
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

SCHEMA_VERSION = 1
SEED_PRIORITY = 0
MIN_TERMS_PER_PACK = 24
MAX_PACK_BYTES = 1_000_000

DOMAIN_IDS = (
    "traditional_machine_learning",
    "artificial_intelligence",
    "regression_analysis",
    "finance_economics",
    "accounting",
    "marketing",
)

def _default_resource_dir() -> Path:
    """Resolve bundled PyInstaller data and editable-source resources."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_root) if isinstance(bundle_root, str) and bundle_root else Path(__file__).resolve().parent.parent
    return root / "resources" / "seed_glossary"


DEFAULT_RESOURCE_DIR = _default_resource_dir()

_ALLOWED_SOURCE_URLS = frozenset({
    "https://developers.google.com/machine-learning/glossary",
    "https://airc.nist.gov/glossary/",
    "https://www.nist.gov/publications/language-trustworthy-ai-depth-glossary-terms",
    "https://openstax.org/books/introductory-business-statistics-2e/pages/13-introduction",
    "https://openstax.org/books/introductory-business-statistics-2e/pages/13-4-the-regression-equation",
    "https://openstax.org/books/principles-finance-2e/pages/3-key-terms",
    "https://openstax.org/books/principles-economics-3e/pages/1-key-terms",
    "https://openstax.org/books/principles-economics-3e/pages/3-key-terms",
    "https://openstax.org/books/principles-financial-accounting/pages/1-key-terms",
    "https://openstax.org/books/principles-financial-accounting/pages/2-key-terms",
    "https://openstax.org/books/principles-financial-accounting/pages/3-key-terms",
    "https://openstax.org/books/principles-financial-accounting/pages/4-key-terms",
    "https://openstax.org/books/principles-marketing/pages/1-key-terms",
    "https://openstax.org/books/principles-marketing/pages/5-key-terms",
})

_PACK_FIELDS = frozenset({
    "schema_version", "pack_id", "pack_version", "display_name",
    "domain_keywords", "source_note", "sources", "terms",
})
_SOURCE_FIELDS = frozenset({"source_id", "title", "url"})
_TERM_FIELDS = frozenset({
    "id", "canonical", "zh_name", "aliases", "category", "definition_zh",
    "asr_variants", "source_id",
})
_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
_PACK_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION = re.compile(r"^[1-9]\d*\.\d+\.\d+$")
_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_HAN = re.compile(r"[\u3400-\u9fff]")


class SeedGlossaryValidationError(ValueError):
    """词包不符合封闭 schema 或安全约束。"""


@dataclass(frozen=True)
class SourceReference:
    source_id: str
    title: str
    url: str | None
    origin: str = "seed"


@dataclass(frozen=True)
class GlossaryTerm:
    id: str
    canonical: str
    zh_name: str
    aliases: tuple[str, ...]
    category: str
    definition_zh: str
    asr_variants: tuple[str, ...]
    source_id: str
    pack_id: str
    origin: str = "seed"
    source_priority: int = SEED_PRIORITY

    @property
    def lookup_forms(self) -> tuple[str, ...]:
        return (self.canonical, self.zh_name, *self.aliases, *self.asr_variants)


@dataclass(frozen=True)
class GlossaryPack:
    schema_version: int
    pack_id: str
    pack_version: str
    display_name: str
    domain_keywords: tuple[str, ...]
    source_note: str
    sources: tuple[SourceReference, ...]
    terms: tuple[GlossaryTerm, ...]


@dataclass(frozen=True)
class GlossarySelection:
    domains: tuple[str, ...]
    terms: tuple[GlossaryTerm, ...]
    sources: tuple[SourceReference, ...]
    char_count: int
    estimated_tokens: int

    def as_prompt_context(self) -> str:
        return render_glossary_context(self.terms)


def _fail(path: str, message: str) -> None:
    raise SeedGlossaryValidationError(f"{path}: {message}")


def _object(value: object, fields: frozenset[str], path: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(path, "expected object")
    row = value
    missing = fields - set(row)
    unknown = set(row) - fields
    if missing:
        _fail(path, "missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        _fail(path, "unknown fields: " + ", ".join(sorted(unknown)))
    return row


def _text(value: object, path: str, *, limit: int = 240) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        _fail(path, "expected non-empty bounded string")
    if value != value.strip() or unicodedata.normalize("NFC", value) != value:
        _fail(path, "must be trimmed NFC text")
    return value


def _text_list(
    value: object,
    path: str,
    *,
    nonempty: bool = False,
    ascii_only: bool = False,
) -> tuple[str, ...]:
    if type(value) is not list:
        _fail(path, "expected array")
    result = tuple(_text(item, f"{path}[{index}]") for index, item in enumerate(value))
    if nonempty and not result:
        _fail(path, "must not be empty")
    if len(result) != len(set(item.casefold() for item in result)):
        _fail(path, "must not contain duplicates")
    if ascii_only and any(not item.isascii() for item in result):
        _fail(path, "English aliases must be ASCII")
    return result


def _load_json(path: Path) -> dict[str, Any]:
    def reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = value
        return result

    try:
        metadata = path.stat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            _fail(str(path), "unsafe resource file")
        if metadata.st_size > MAX_PACK_BYTES:
            _fail(str(path), "resource exceeds size limit")
        parsed = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_fields,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except SeedGlossaryValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SeedGlossaryValidationError(f"{path}: invalid JSON resource") from exc
    if type(parsed) is not dict:
        _fail(str(path), "expected top-level object")
    return parsed


def validate_pack(value: object, *, expected_pack_id: str | None = None) -> GlossaryPack:
    """严格校验单个词包，并转换为不可变对象。"""

    row = _object(value, _PACK_FIELDS, "$")
    if type(row["schema_version"]) is not int or row["schema_version"] != SCHEMA_VERSION:
        _fail("$.schema_version", "unsupported schema version")
    pack_id = _text(row["pack_id"], "$.pack_id", limit=80)
    if not _PACK_ID.fullmatch(pack_id) or pack_id not in DOMAIN_IDS:
        _fail("$.pack_id", "unsupported pack id")
    if expected_pack_id is not None and pack_id != expected_pack_id:
        _fail("$.pack_id", "does not match resource filename")
    version = _text(row["pack_version"], "$.pack_version", limit=32)
    if not _VERSION.fullmatch(version):
        _fail("$.pack_version", "expected semantic version")
    display_name = _text(row["display_name"], "$.display_name", limit=80)
    keywords = _text_list(row["domain_keywords"], "$.domain_keywords", nonempty=True)
    source_note = _text(row["source_note"], "$.source_note", limit=160)
    if "仅作为术语参考" not in source_note:
        _fail("$.source_note", "must state that sources are terminology references only")

    if type(row["sources"]) is not list or not row["sources"]:
        _fail("$.sources", "expected non-empty array")
    sources: list[SourceReference] = []
    source_ids: set[str] = set()
    for index, raw_source in enumerate(row["sources"]):
        path = f"$.sources[{index}]"
        source = _object(raw_source, _SOURCE_FIELDS, path)
        source_id = _text(source["source_id"], path + ".source_id", limit=80)
        title = _text(source["title"], path + ".title", limit=200)
        url = _text(source["url"], path + ".url", limit=500)
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or not parsed_url.netloc or url not in _ALLOWED_SOURCE_URLS:
            _fail(path + ".url", "source URL is not on the approved allowlist")
        if source_id in source_ids:
            _fail(path + ".source_id", "duplicate source id")
        source_ids.add(source_id)
        sources.append(SourceReference(source_id, title, url))

    if type(row["terms"]) is not list or len(row["terms"]) < MIN_TERMS_PER_PACK:
        _fail("$.terms", f"expected at least {MIN_TERMS_PER_PACK} terms")
    terms: list[GlossaryTerm] = []
    term_ids: set[str] = set()
    for index, raw_term in enumerate(row["terms"]):
        path = f"$.terms[{index}]"
        term = _object(raw_term, _TERM_FIELDS, path)
        term_id = _text(term["id"], path + ".id", limit=120)
        if not _ID.fullmatch(term_id) or not term_id.startswith(pack_id + "."):
            _fail(path + ".id", "invalid or cross-pack stable id")
        if term_id in term_ids:
            _fail(path + ".id", "duplicate term id")
        term_ids.add(term_id)
        canonical = _text(term["canonical"], path + ".canonical", limit=120)
        if not canonical.isascii():
            _fail(path + ".canonical", "canonical term must be English/ASCII")
        zh_name = _text(term["zh_name"], path + ".zh_name", limit=80)
        if not _HAN.search(zh_name):
            _fail(path + ".zh_name", "expected a Simplified Chinese name")
        aliases = _text_list(term["aliases"], path + ".aliases", ascii_only=True)
        category = _text(term["category"], path + ".category", limit=60)
        if not _PACK_ID.fullmatch(category):
            _fail(path + ".category", "expected snake_case category")
        definition = _text(term["definition_zh"], path + ".definition_zh", limit=120)
        if not _HAN.search(definition):
            _fail(path + ".definition_zh", "expected a Simplified Chinese definition")
        asr_variants = _text_list(term["asr_variants"], path + ".asr_variants")
        known_forms = {canonical.casefold(), *(item.casefold() for item in aliases)}
        if any(item.casefold() in known_forms for item in asr_variants):
            _fail(path + ".asr_variants", "ASR variants must not duplicate canonical/aliases")
        source_id = _text(term["source_id"], path + ".source_id", limit=80)
        if source_id not in source_ids:
            _fail(path + ".source_id", "unknown source id")
        terms.append(GlossaryTerm(
            term_id, canonical, zh_name, aliases, category, definition,
            asr_variants, source_id, pack_id,
        ))
    return GlossaryPack(
        SCHEMA_VERSION, pack_id, version, display_name, keywords, source_note,
        tuple(sources), tuple(terms),
    )


def load_pack(path: str | Path) -> GlossaryPack:
    """从磁盘只读加载一个词包。"""

    target = Path(path)
    if target.suffix != ".json":
        _fail(str(target), "expected .json resource")
    return validate_pack(_load_json(target), expected_pack_id=target.stem)


def load_seed_packs(resource_dir: str | Path | None = None) -> tuple[GlossaryPack, ...]:
    """按固定领域顺序加载全部六个词包，不修改资源或课程记忆。"""

    root = DEFAULT_RESOURCE_DIR if resource_dir is None else Path(resource_dir)
    packs = tuple(load_pack(root / f"{pack_id}.json") for pack_id in DOMAIN_IDS)
    all_ids = [term.id for pack in packs for term in pack.terms]
    if len(all_ids) != len(set(all_ids)):
        _fail("$.terms", "term ids must be globally unique")
    return packs


def _flatten_metadata(value: Mapping[str, Any] | str | None) -> tuple[str, str | None]:
    if value is None:
        return "", None
    if type(value) is str:
        return value, None
    fields = (
        "course_name", "course_subject", "course_description", "domain",
        "known_topics", "current_chapter", "title", "name", "description",
    )
    pieces: list[str] = []
    explicit_domain = value.get("domain")
    for field in fields:
        item = value.get(field)
        if type(item) is str:
            pieces.append(item)
        elif type(item) in (list, tuple):
            pieces.extend(part for part in item if type(part) is str)
    return " ".join(pieces), explicit_domain if type(explicit_domain) is str else None


def _normalized(value: str) -> str:
    return " ".join(_WORD.findall(unicodedata.normalize("NFKC", value).casefold()))


def _contains(haystack: str, phrase: str) -> bool:
    normalized_phrase = _normalized(phrase)
    normalized_haystack = _normalized(haystack)
    # 空格边界避免短缩写产生子串误命中，例如 AI 不应命中 financial。
    if normalized_phrase and f" {normalized_phrase} " in f" {normalized_haystack} ":
        return True
    compact_phrase = "".join(character for character in phrase if _HAN.match(character))
    compact_haystack = "".join(character for character in haystack if _HAN.match(character))
    return bool(compact_phrase and compact_phrase in compact_haystack)


def _domain_score(pack: GlossaryPack, metadata_text: str, current_text: str) -> int:
    score = 0
    for keyword in pack.domain_keywords:
        if _contains(metadata_text, keyword):
            score += 6
        if _contains(current_text, keyword):
            score += 2
    if _contains(metadata_text, pack.display_name) or _contains(metadata_text, pack.pack_id):
        score += 12
    return score


def select_domains(
    packs: Sequence[GlossaryPack],
    course_metadata: Mapping[str, Any] | str | None,
    current_text: str = "",
    *,
    max_domains: int = 2,
) -> tuple[str, ...]:
    """依据课程元数据优先、当前文本补充，确定要检索的领域。"""

    if type(current_text) is not str or max_domains <= 0:
        raise ValueError("current_text/max_domains 参数无效")
    metadata_text, explicit = _flatten_metadata(course_metadata)
    if explicit in DOMAIN_IDS and any(pack.pack_id == explicit for pack in packs):
        return (explicit,)
    ranked = [
        (_domain_score(pack, metadata_text, current_text), index, pack.pack_id)
        for index, pack in enumerate(packs)
    ]
    ranked = [row for row in ranked if row[0] >= 2]
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return tuple(row[2] for row in ranked[:max_domains])


def _term_score(
    term: GlossaryTerm,
    metadata_text: str,
    current_text: str,
    domain_score: int,
) -> int:
    score = domain_score
    for form in term.lookup_forms:
        if _contains(current_text, form):
            score += 30
        if _contains(metadata_text, form):
            score += 16
    query_words = set(_WORD.findall((metadata_text + " " + current_text).casefold()))
    term_words = set(_WORD.findall(
        (term.canonical + " " + " ".join(term.aliases) + " " + term.category).casefold()
    ))
    score += 2 * len({word for word in query_words & term_words if len(word) >= 3})
    return score


def _lookup_keys(term: GlossaryTerm) -> frozenset[str]:
    result = set()
    for value in (term.id, *term.lookup_forms):
        english = _normalized(value)
        chinese = "".join(character for character in value if _HAN.match(character))
        if english:
            result.add(english)
        if chinese:
            result.add(chinese)
    return frozenset(result)


def _memory_terms(course_glossary: Mapping[str, Any] | None) -> tuple[GlossaryTerm, ...]:
    if not course_glossary:
        return ()
    result: list[GlossaryTerm] = []
    for key in sorted(course_glossary):
        raw_record = course_glossary[key]
        if type(raw_record) is not dict:
            continue
        value = raw_record.get("value", raw_record)
        if type(value) is not dict:
            continue
        canonical = value.get("canonical") or value.get("term") or value.get("en_name")
        if type(canonical) is not str or not canonical.strip():
            continue
        canonical = canonical.strip()
        zh_name = value.get("zh_name")
        if type(zh_name) is not str or not zh_name.strip():
            zh_name = canonical
        aliases_raw = value.get("aliases", [])
        aliases = tuple(
            item.strip() for item in aliases_raw
            if type(item) is str and item.strip() and item.strip().casefold() != canonical.casefold()
        ) if type(aliases_raw) in (list, tuple) else ()
        asr_raw = value.get("asr_variants", [])
        asr_variants = tuple(
            item.strip() for item in asr_raw if type(item) is str and item.strip()
        ) if type(asr_raw) in (list, tuple) else ()
        definition = value.get("definition_zh") or value.get("meaning") or "课程记忆中的已确认术语。"
        if type(definition) is not str or not definition.strip():
            definition = "课程记忆中的已确认术语。"
        source = raw_record.get("source", "course_memory")
        source = source if type(source) is str and source else "course_memory"
        priority = raw_record.get("source_priority", 1)
        priority = priority if type(priority) is int and priority > SEED_PRIORITY else 1
        result.append(GlossaryTerm(
            id=str(raw_record.get("item_id") or key),
            canonical=canonical,
            zh_name=zh_name.strip(),
            aliases=tuple(dict.fromkeys(aliases)),
            category=str(value.get("category") or "course_memory"),
            definition_zh=definition.strip(),
            asr_variants=tuple(dict.fromkeys(asr_variants)),
            source_id=f"course_memory:{source}",
            pack_id="course_memory",
            origin="course_memory",
            source_priority=priority,
        ))
    return tuple(result)


def estimate_tokens(value: str) -> int:
    """保守、无第三方依赖的中英混合 token 估算。"""

    han_count = len(_HAN.findall(value))
    ascii_count = sum(1 for character in value if ord(character) < 128)
    other_count = len(value) - han_count - ascii_count
    return han_count + (ascii_count + 3) // 4 + (other_count + 1) // 2


def format_glossary_term(term: GlossaryTerm) -> str:
    aliases = f"；aliases: {', '.join(term.aliases)}" if term.aliases else ""
    variants = f"；ASR: {', '.join(term.asr_variants)}" if term.asr_variants else ""
    return (
        f"- [{term.id}|{term.source_id}] {term.canonical}（{term.zh_name}）："
        f"{term.definition_zh}{aliases}{variants}"
    )


def render_glossary_context(terms: Iterable[GlossaryTerm]) -> str:
    lines = [format_glossary_term(term) for term in terms]
    return "\n".join(lines) + ("\n" if lines else "")


def retrieve_seed_glossary(
    course_metadata: Mapping[str, Any] | str | None,
    current_text: str,
    *,
    packs: Sequence[GlossaryPack] | None = None,
    course_glossary: Mapping[str, Any] | None = None,
    domains: Sequence[str] | None = None,
    max_domains: int = 2,
    max_terms: int = 32,
    max_chars: int = 2400,
    max_tokens: int = 700,
) -> GlossarySelection:
    """检索并预算化种子术语；课程记忆覆盖同名或别名冲突的 seed。

    返回值保持来源、领域和预算统计；函数无写入副作用，可安全重复调用。
    """

    if type(current_text) is not str:
        raise TypeError("current_text 必须是字符串")
    if min(max_terms, max_chars, max_tokens, max_domains) <= 0:
        raise ValueError("检索预算必须为正数")
    loaded = tuple(packs) if packs is not None else load_seed_packs()
    by_id = {pack.pack_id: pack for pack in loaded}
    if domains is None:
        selected_domains = select_domains(
            loaded, course_metadata, current_text, max_domains=max_domains
        )
    else:
        selected_domains = tuple(dict.fromkeys(domains))
        unknown = set(selected_domains) - set(by_id)
        if unknown:
            raise ValueError("未知术语领域: " + ", ".join(sorted(unknown)))
    metadata_text, _explicit = _flatten_metadata(course_metadata)

    candidates: list[tuple[int, int, GlossaryTerm]] = []
    ordinal = 0
    for domain in selected_domains:
        pack = by_id[domain]
        domain_score = max(1, _domain_score(pack, metadata_text, current_text))
        for term in pack.terms:
            candidates.append((
                _term_score(term, metadata_text, current_text, domain_score),
                ordinal,
                term,
            ))
            ordinal += 1

    memory = _memory_terms(course_glossary)
    for term in memory:
        direct = any(
            _contains(metadata_text, form) or _contains(current_text, form)
            for form in term.lookup_forms
        )
        if selected_domains or direct:
            candidates.append((1000 + term.source_priority + (100 if direct else 0), ordinal, term))
            ordinal += 1

    candidates.sort(key=lambda row: (-row[0], -row[2].source_priority, row[1], row[2].id))
    accepted: list[GlossaryTerm] = []
    occupied: set[str] = set()
    for _score, _ordinal, candidate in candidates:
        keys = _lookup_keys(candidate)
        if keys & occupied:
            continue
        accepted.append(candidate)
        occupied.update(keys)
        if len(accepted) >= max_terms:
            break

    # 若课程记忆排在 seed 前，它会自然占用冲突键。这里再做一次防御性替换，
    # 保证调用方传入异常排序时低优先级 seed 也绝不覆盖课程记忆。
    memory_keys = set().union(*(_lookup_keys(term) for term in memory)) if memory else set()
    if memory_keys:
        accepted = [
            term for term in accepted
            if term.origin == "course_memory" or not (_lookup_keys(term) & memory_keys)
        ]

    budgeted: list[GlossaryTerm] = []
    context = ""
    for term in accepted:
        line = format_glossary_term(term) + "\n"
        proposed = context + line
        if len(proposed) > max_chars or estimate_tokens(proposed) > max_tokens:
            continue
        budgeted.append(term)
        context = proposed

    source_lookup = {
        source.source_id: source
        for pack in loaded for source in pack.sources
    }
    for term in budgeted:
        if term.origin == "course_memory" and term.source_id not in source_lookup:
            source_lookup[term.source_id] = SourceReference(
                term.source_id,
                "Course memory (higher-priority course-specific evidence)",
                None,
                origin="course_memory",
            )
    used_source_ids = {term.source_id for term in budgeted}
    sources = tuple(
        source for source_id, source in source_lookup.items() if source_id in used_source_ids
    )
    return GlossarySelection(
        domains=selected_domains,
        terms=tuple(budgeted),
        sources=sources,
        char_count=len(context),
        estimated_tokens=estimate_tokens(context),
    )


__all__ = [
    "DEFAULT_RESOURCE_DIR",
    "DOMAIN_IDS",
    "GlossaryPack",
    "GlossarySelection",
    "GlossaryTerm",
    "MIN_TERMS_PER_PACK",
    "SCHEMA_VERSION",
    "SEED_PRIORITY",
    "SeedGlossaryValidationError",
    "SourceReference",
    "estimate_tokens",
    "format_glossary_term",
    "load_pack",
    "load_seed_packs",
    "render_glossary_context",
    "retrieve_seed_glossary",
    "select_domains",
    "validate_pack",
]
