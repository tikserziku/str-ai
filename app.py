"""
STR AI — Flask application for Lithuanian building regulations search.
Runs on port 5400. Uses RAG engine when available, demo mode otherwise.
"""

import os
import json
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Try to import the real RAG engine; fall back to demo stubs
# ---------------------------------------------------------------------------
try:
    from src.rag_engine import search, answer  # noqa: F401
    DEMO_MODE = False
except Exception:
    DEMO_MODE = True

# ---------------------------------------------------------------------------
# Demo data (used when rag_engine is not available)
# ---------------------------------------------------------------------------
DEMO_RESULTS = {
    "paprastasis remontas": {
        "answer": "Paprastasis remontas — tai statinio ar jo dalių atnaujinimas, "
                  "nekeičiant statinio laikančiųjų konstrukcijų ir kitų pagrindinių "
                  "statinio parametrų, nedidinant statinio užimamo ploto ir tūrio.",
        "results": [
            {
                "str_number": "STR 1.01.03:2017",
                "title": "Statinių klasifikavimas",
                "punkt": "2.49 p.",
                "quote": "Paprastasis remontas — statinio atnaujinimas, "
                         "nekeičiant jo laikančiųjų konstrukcijų ir nedidinant "
                         "statinio užimamo ploto bei tūrio.",
                "status": "galioja",
                "status_label": "Galioja",
                "etar_url": "https://www.e-tar.lt/portal/lt/legalAct/TAR.F31E79DEC55D",
            }
        ],
    },
    "sld": {
        "answer": "Statybą leidžiantis dokumentas (SLD) privalomas ypatingiems ir "
                  "neypatingiems statiniams (nauja statyba, rekonstravimas, "
                  "kapitalinis remontas, griovimas). Paprastajam remontui SLD "
                  "nereikalingas.",
        "results": [
            {
                "str_number": "STR 1.05.01:2017",
                "title": "Statybą leidžiantys dokumentai",
                "punkt": "6 p.",
                "quote": "Statybą leidžiantis dokumentas privalomas: naujai "
                         "statybai, rekonstravimui, kapitaliniam remontui, "
                         "pastato paskirties keitimui, griovimui.",
                "status": "galioja",
                "status_label": "Galioja",
                "etar_url": "https://www.e-tar.lt/portal/lt/legalAct/TAR.91F1E5E6C4E5",
            },
            {
                "str_number": "STR 1.05.01:2017",
                "title": "Statybą leidžiantys dokumentai",
                "punkt": "7 p.",
                "quote": "Paprastajam remontui statybą leidžiantis dokumentas "
                         "nereikalingas, išskyrus atvejus, kai paprastojo remonto "
                         "darbai atliekami kultūros paveldo statinyje.",
                "status": "galioja",
                "status_label": "Galioja",
                "etar_url": "https://www.e-tar.lt/portal/lt/legalAct/TAR.91F1E5E6C4E5",
            },
        ],
    },
    "plieniniu konstrukciju": {
        "answer": "Plieninių konstrukcijų projektavimas ir gamyba reglamentuojami "
                  "STR 2.05.08:2005, kuris neteko galios nuo 2014-01-01 (pakeistas "
                  "Eurokodų sistema — LST EN 1993 serija).",
        "results": [
            {
                "str_number": "STR 2.05.08:2005",
                "title": "Plieninių konstrukcijų projektavimas",
                "punkt": "1.1 p.",
                "quote": "Šis reglamentas nustato plieninių konstrukcijų "
                         "projektavimo taisykles.",
                "status": "neteko_galios",
                "status_label": "NETEKO GALIOS nuo 2014-01-01",
                "etar_url": "https://www.e-tar.lt/portal/lt/legalAct/TAR.0B531C244003",
            }
        ],
    },
    "gaisrine sauga": {
        "answer": "Gaisrinės saugos reikalavimai statiniams nustatyti "
                  "STR 2.01.01(2):1999. Reglamentas reglamentuoja priešgaisrinius "
                  "atstumus, evakuacijos kelius, gaisro aptikimo sistemas.",
        "results": [
            {
                "str_number": "STR 2.01.01(2):1999",
                "title": "Esminiai statinio reikalavimai. Gaisrinė sauga",
                "punkt": "5 p.",
                "quote": "Statiniai turi būti suprojektuoti ir pastatyti taip, "
                         "kad kilus gaisrui statinio laikančiosios konstrukcijos "
                         "tam tikrą laiką galėtų atlaikyti apkrovas.",
                "status": "galioja",
                "status_label": "Galioja",
                "etar_url": "https://www.e-tar.lt/portal/lt/legalAct/TAR.DA5A8AB8B155",
            }
        ],
    },
    "statinio kategorija": {
        "answer": "Statiniai skirstomi į tris kategorijas pagal sudėtingumą: "
                  "I grupė (nesudėtingi/neypatingi), II grupė (neypatingi), "
                  "III grupė (ypatingi statiniai).",
        "results": [
            {
                "str_number": "STR 1.01.03:2017",
                "title": "Statinių klasifikavimas",
                "punkt": "6 p.",
                "quote": "Statiniai pagal jų naudojimo paskirtį, požymius ir "
                         "techninius parametrus skirstomi į nesudėtinguosius, "
                         "neypatingus ir ypatingus statinius.",
                "status": "galioja",
                "status_label": "Galioja",
                "etar_url": "https://www.e-tar.lt/portal/lt/legalAct/TAR.F31E79DEC55D",
            }
        ],
    },
}


def demo_search(query: str) -> dict:
    """Find best matching demo result for the query."""
    q = query.lower().strip()
    # Try substring match against demo keys
    best_key = None
    for key in DEMO_RESULTS:
        if key in q or q in key:
            best_key = key
            break
    # Partial word match fallback
    if best_key is None:
        for key in DEMO_RESULTS:
            for word in key.split():
                if word in q:
                    best_key = key
                    break
            if best_key:
                break
    if best_key:
        data = DEMO_RESULTS[best_key]
        return {"query": query, "answer": data["answer"],
                "results": data["results"], "demo": True}
    # Nothing matched
    return {
        "query": query,
        "answer": f"Atsiprašome, demo režime nėra atsakymo į klausimą: \"{query}\". "
                  "Prijunkite RAG variklį su tikrais STR duomenimis.",
        "results": [],
        "demo": True,
    }


def do_search(query: str) -> dict:
    """Dispatch to real engine or demo."""
    if DEMO_MODE:
        return demo_search(query)
    # Real RAG engine path
    raw = search(query)
    try:
        ans_data = answer(query)
        ans_text = ans_data.get("answer", "")
        model = ans_data.get("model")
    except Exception:
        ans_text = ""
        model = None
    if not ans_text:
        parts = []
        sources = []
        for i, r in enumerate(raw[:5], 1):
            status = "✅ Galioja" if r["status"] == "galioja" else "⚠️ NETEKO GALIOS"
            parts.append(f"[{i}] {r['str_number']}, {r['punkt']} p. [{status}]\n\"{r['text'][:300]}\"")
            sources.append(f"[{i}] {r['str_number']}, {r['punkt']} p. — {r.get('source_url', '')}")
        ans_text = "Rasti STR punktai:\n\n" + "\n\n".join(parts) + "\n\n---\nŠaltiniai:\n" + "\n".join(sources)
    return {"query": query, "answer": ans_text, "results": raw, "demo": False, "model": model}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", demo=DEMO_MODE)


@app.route("/search", methods=["POST"])
def search_post():
    data = request.get_json(silent=True) or {}
    query = data.get("query", "").strip() or request.form.get("query", "").strip()
    if not query:
        return jsonify({"error": "Tuščias užklausos laukas"}), 400
    return jsonify(do_search(query))


@app.route("/api/search")
def search_api():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Parametras ?q= privalomas"}), 400
    return jsonify(do_search(query))


@app.route("/health")
def health():
    return jsonify({"status": "ok", "demo_mode": DEMO_MODE})


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.run(host="0.0.0.0", port=port, debug=True)
