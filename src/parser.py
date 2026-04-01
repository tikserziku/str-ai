"""
Парсер литовских строительных регламентов (STR) с сайта e-tar.lt.

Скачивает DOCX-версии актуальных суvestинių redakcijų,
парсит текст на пункты с номерами, определяет статус каждого пункта.
Результат сохраняется в data/str_parsed.json.
"""

import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

# Fix console encoding on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests
from docx import Document

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
OUTPUT_FILE = BASE_DIR / "data" / "str_parsed.json"

ETAR_BASE = "https://www.e-tar.lt"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}

# 5 основных STR — захардкожены
STR_REGISTRY: list[dict] = [
    {
        "str_number": "STR 1.01.08:2002",
        "str_title": "Statinio statybos rūšys",
        "document_id": "TAR.B49EEDC9171B",
        "source_url": "https://www.e-tar.lt/portal/lt/legalAct/TAR.B49EEDC9171B/asr",
    },
    {
        "str_number": "STR 1.05.01:2017",
        "str_title": (
            "Statybą leidžiantys dokumentai. Statybos užbaigimas. "
            "Nebaigto statinio registravimas ir perleidimas. "
            "Statybos sustabdymas. Savavališkos statybos padarinių šalinimas. "
            "Statybos pagal neteisėtai išduotą statybą leidžiantį dokumentą padarinių šalinimas"
        ),
        "document_id": "585f9850c05211e688d0ed775a2e782a",
        "source_url": "https://www.e-tar.lt/portal/lt/legalAct/585f9850c05211e688d0ed775a2e782a/asr",
    },
    {
        "str_number": "STR 1.04.04:2017",
        "str_title": "Statinio projektavimas, projekto ekspertizė",
        "document_id": "ad75ac40a7dd11e69ad4c8713b612d0f",
        "source_url": "https://www.e-tar.lt/portal/lt/legalAct/ad75ac40a7dd11e69ad4c8713b612d0f/asr",
    },
    {
        "str_number": "STR 2.05.08:2005",
        "str_title": "Plieninių konstrukcijų projektavimas. Pagrindinės nuostatos",
        "document_id": "TAR.3B040391D530",
        "source_url": "https://www.e-tar.lt/portal/lt/legalAct/TAR.3B040391D530/asr",
    },
    {
        "str_number": "STR 1.06.01:2016",
        "str_title": "Statybos darbai. Statinio statybos priežiūra",
        "document_id": "3ecef840bae411e688d0ed775a2e782a",
        "source_url": "https://www.e-tar.lt/portal/lt/legalAct/3ecef840bae411e688d0ed775a2e782a/asr",
    },
]


# ---------------------------------------------------------------------------
# Dataclass для результата
# ---------------------------------------------------------------------------

@dataclass
class ParsedPunkt:
    str_number: str
    str_title: str
    punkt: str
    text: str
    status: str           # "galioja" | "neteko galios nuo YYYY-MM-DD"
    expired_date: Optional[str]
    source_url: str


# ---------------------------------------------------------------------------
# Скачивание
# ---------------------------------------------------------------------------

def fetch_actual_edition_id(document_id: str) -> str:
    """Получить ID актуальной суvestinės redakcijos со страницы /asr."""
    url = f"{ETAR_BASE}/portal/lt/legalAct/{document_id}/asr"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    # Ищем паттерн DOCX-ссылки
    match = re.search(
        rf"/rs/actualedition/{re.escape(document_id)}/([A-Za-z0-9]+)/format/MSO2010_DOCX/",
        resp.text,
    )
    if match:
        return match.group(1)

    # Альтернативный поиск actualEditionId
    match2 = re.search(r'actualEditionId[=:]\s*["\']?([A-Za-z0-9]+)', resp.text)
    if match2:
        return match2.group(1)

    raise ValueError(
        f"Не удалось извлечь edition ID для документа {document_id}. "
        f"Страница вернула {len(resp.text)} байт."
    )


def download_docx(document_id: str, edition_id: str, filename: str) -> Path:
    """Скачать DOCX-файл актуальной редакции."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    filepath = RAW_DIR / filename

    # Если файл уже скачан (кэш) — не скачиваем повторно
    if filepath.exists() and filepath.stat().st_size > 1000:
        print(f"  [cache] {filepath.name} ({filepath.stat().st_size:,} bytes)")
        return filepath

    url = f"{ETAR_BASE}/rs/actualedition/{document_id}/{edition_id}/format/MSO2010_DOCX/"
    print(f"  Скачиваю: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()

    if "wordprocessingml" not in resp.headers.get("Content-Type", ""):
        raise ValueError(
            f"Ответ не DOCX: Content-Type={resp.headers.get('Content-Type')}"
        )

    filepath.write_bytes(resp.content)
    print(f"  Сохранено: {filepath.name} ({len(resp.content):,} bytes)")
    return filepath


# ---------------------------------------------------------------------------
# Парсинг DOCX → пункты
# ---------------------------------------------------------------------------

# Regex для номера пункта: 1. / 1.1. / 12.12.3. / 1 priedas и т.д.
RE_PUNKT_NUM = re.compile(
    r"^(\d+(?:\.\d+)*)\.\s"     # стандартный: 1. / 7.3.1.
)

# «Neteko galios nuo YYYY-MM-DD» или «Neteko galios nuo YYYY-MM-DD»
RE_EXPIRED = re.compile(
    r"[Nn]eteko\s+galios\s+nuo\s+(\d{4}-\d{2}-\d{2})"
)

# Пропускаемые строки — метаданные изменений
RE_SKIP_META = re.compile(
    r"^(Punkto\s+pakeitimai|Punkto\s+naikinimas|Skyriaus\s+pakeitimai|"
    r"Preambulės\s+pakeitimai|Papildyta\s+punktu|Pakeistas\s+skyriaus|"
    r"Priedo\s+pakeitimai|Pastaba\.\s+Pripažintas|"
    r"Nr\.\s*,|Pakeistas\s+punktas|Nauja\s+redakcija)",
    re.IGNORECASE,
)

# Номер приказа-ссылки (используется в мета-блоках)
RE_ORDER_REF = re.compile(
    r"^\d{4}-\d{2}-\d{2},?\s*$|"
    r"^paskelbta\s+TAR|"
    r"^Žin\.|"
    r"^Nr\.\s*$|"
    r"^\d+-\d+\s*\(",
)


def is_meta_line(text: str) -> bool:
    """Проверить, является ли строка метаданными изменений (не содержательный текст)."""
    t = text.strip()
    if not t:
        return True
    if RE_SKIP_META.match(t):
        return True
    if RE_ORDER_REF.match(t):
        return True
    # Строки типа "Nr. , 2018-06-19, paskelbta TAR..."
    if t.startswith("Nr.") and len(t) < 200 and ("TAR" in t or "Žin" in t or "i. k." in t):
        return True
    return False


def parse_docx(filepath: Path, str_info: dict) -> list[ParsedPunkt]:
    """Распарсить DOCX-файл на пункты."""
    doc = Document(str(filepath))
    results: list[ParsedPunkt] = []

    # Ищем начало самого регламента (после «PATVIRTINTA» или заголовка STR)
    paragraphs = [p.text.strip() for p in doc.paragraphs]
    start_idx = 0
    for i, text in enumerate(paragraphs):
        if "STATYBOS TECHNINIS REGLAMENTAS" in text.upper() and "STR" in text.upper():
            start_idx = i
            break
        if text.upper().startswith("PATVIRTINTA"):
            start_idx = i
            break

    current_punkt: Optional[str] = None
    current_text_lines: list[str] = []
    current_status = "galioja"
    current_expired: Optional[str] = None
    in_meta_block = False

    def flush_punkt():
        nonlocal current_punkt, current_text_lines, current_status, current_expired
        if current_punkt and current_text_lines:
            full_text = " ".join(current_text_lines).strip()
            # Убираем повторные пробелы
            full_text = re.sub(r"\s{2,}", " ", full_text)
            if full_text:
                results.append(ParsedPunkt(
                    str_number=str_info["str_number"],
                    str_title=str_info["str_title"],
                    punkt=current_punkt,
                    text=full_text,
                    status=current_status,
                    expired_date=current_expired,
                    source_url=str_info["source_url"],
                ))
        current_punkt = None
        current_text_lines = []
        current_status = "galioja"
        current_expired = None

    for text in paragraphs[start_idx:]:
        if not text:
            continue

        # Проверяем мета-блок (изменения, ссылки на приказы)
        if is_meta_line(text):
            in_meta_block = True
            continue

        # После мета-блока — возвращаемся к нормальному парсингу
        if in_meta_block:
            in_meta_block = False

        # Заголовки глав (SKYRIUS) — пропускаем как не-пункт
        if re.match(r"^[IVXLCDM]+\s+SKYRIUS", text, re.IGNORECASE):
            flush_punkt()
            continue

        # Заголовки разделов (капсом, короткие)
        if text.isupper() and len(text) < 150 and not RE_PUNKT_NUM.match(text):
            flush_punkt()
            continue

        # Проверяем «Neteko galios»
        expired_match = RE_EXPIRED.search(text)
        if expired_match:
            # Извлекаем номер пункта, если есть
            expired_status = f"neteko galios nuo {expired_match.group(1)}"
            expired_text = f"Neteko galios nuo {expired_match.group(1)}"
            punkt_match = RE_PUNKT_NUM.match(text)
            if punkt_match:
                flush_punkt()
                current_punkt = punkt_match.group(1)
                current_status = expired_status
                current_expired = expired_match.group(1)
                current_text_lines = [expired_text]
                flush_punkt()
            elif current_punkt:
                # Текущий пункт потерял силу
                current_status = expired_status
                current_expired = expired_match.group(1)
                current_text_lines = [expired_text]
                flush_punkt()
            else:
                # Отдельная строка «X.Y. Neteko galios nuo ...»
                m2 = re.match(r"^(\d+(?:\.\d+)*)\.\s", text)
                if m2:
                    flush_punkt()
                    current_punkt = m2.group(1)
                    current_status = expired_status
                    current_expired = expired_match.group(1)
                    current_text_lines = [expired_text]
                    flush_punkt()
            continue

        # Новый пункт
        punkt_match = RE_PUNKT_NUM.match(text)
        if punkt_match:
            flush_punkt()
            current_punkt = punkt_match.group(1)
            # Убираем номер из текста
            current_text_lines = [RE_PUNKT_NUM.sub("", text).strip()]
            continue

        # Продолжение текущего пункта
        if current_punkt:
            current_text_lines.append(text)

    # Последний пункт
    flush_punkt()

    return results


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------

def parse_all_str() -> list[dict]:
    """Скачать и распарсить все STR из реестра."""
    all_results: list[dict] = []

    for idx, str_info in enumerate(STR_REGISTRY, 1):
        str_num = str_info["str_number"]
        doc_id = str_info["document_id"]
        print(f"\n{'='*60}")
        print(f"[{idx}/{len(STR_REGISTRY)}] {str_num} — {str_info['str_title'][:60]}...")
        print(f"{'='*60}")

        try:
            # 1. Получить ID актуальной редакции
            print("  Получаю ID актуальной редакции...")
            edition_id = fetch_actual_edition_id(doc_id)
            print(f"  Edition ID: {edition_id}")

            # 2. Скачать DOCX
            safe_name = str_num.replace(" ", "_").replace(":", "_").replace(".", "_")
            filename = f"{safe_name}.docx"
            filepath = download_docx(doc_id, edition_id, filename)

            # 3. Распарсить
            print("  Парсинг...")
            punkts = parse_docx(filepath, str_info)
            print(f"  Найдено пунктов: {len(punkts)}")

            galioja = sum(1 for p in punkts if p.status == "galioja")
            expired = len(punkts) - galioja
            print(f"  Действующих: {galioja}, утративших силу: {expired}")

            all_results.extend(asdict(p) for p in punkts)

            # Пауза между запросами
            if idx < len(STR_REGISTRY):
                time.sleep(1)

        except Exception as e:
            print(f"  ОШИБКА: {e}")
            import traceback
            traceback.print_exc()

    return all_results


def save_results(results: list[dict]) -> None:
    """Сохранить результаты в JSON."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено в {OUTPUT_FILE}: {len(results)} пунктов")


def main():
    print("=" * 60)
    print("STR Parser — Литовские строительные регламенты")
    print("Источник: e-tar.lt (DOCX актуальных редакций)")
    print("=" * 60)

    results = parse_all_str()
    save_results(results)

    # Статистика
    print("\n" + "=" * 60)
    print("ИТОГО:")
    print("=" * 60)
    by_str: dict[str, dict] = {}
    for r in results:
        key = r["str_number"]
        if key not in by_str:
            by_str[key] = {"total": 0, "galioja": 0, "expired": 0}
        by_str[key]["total"] += 1
        if r["status"] == "galioja":
            by_str[key]["galioja"] += 1
        else:
            by_str[key]["expired"] += 1

    for str_num, stats in by_str.items():
        print(f"  {str_num}: {stats['total']} пунктов "
              f"({stats['galioja']} действ., {stats['expired']} утратили силу)")

    print(f"\nВсего: {len(results)} пунктов")
    return results


if __name__ == "__main__":
    main()
