import os
import re
import uuid
import json
import urllib.request
import urllib.error
import urllib.parse
from flask import Flask, render_template, request, jsonify, send_from_directory, Response
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "static", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)
API_KEY_FROM_ENV = os.getenv("OPENAI_API_KEY", "")
SLITE_API_KEY = os.getenv("SLITE_API_KEY", "")
SLITE_BASE = "https://api.slite.com/v1"

CHUNK_SIZE = 4000
GPT_CHUNK_SIZE = 12000

VOIX = {
    "alloy":   {"label": "Alloy",   "desc": "Neutre et polyvalente",        "emoji": "🎙️"},
    "echo":    {"label": "Echo",    "desc": "Masculine, chaleureuse",        "emoji": "👨"},
    "fable":   {"label": "Fable",   "desc": "Expressive, accent britannique","emoji": "👨"},
    "onyx":    {"label": "Onyx",    "desc": "Grave et autoritaire",          "emoji": "👨"},
    "nova":    {"label": "Nova",    "desc": "Féminine, douce et claire",     "emoji": "👩"},
    "shimmer": {"label": "Shimmer", "desc": "Féminine, légère et posée",     "emoji": "👩"},
}

PHRASE_PREVIEW = "Bonjour, voici un aperçu de ma voix pour votre contenu de formation audio."


# ── Helpers texte ──────────────────────────────────────────────────────────────

def decouper_texte(texte: str) -> list:
    if len(texte) <= CHUNK_SIZE:
        return [texte]
    morceaux = []
    while texte:
        if len(texte) <= CHUNK_SIZE:
            morceaux.append(texte)
            break
        bout = texte[:CHUNK_SIZE]
        coupe = max(bout.rfind(". "), bout.rfind(".\n"), bout.rfind("! "), bout.rfind("? "), bout.rfind("\n\n"))
        if coupe == -1:
            coupe = bout.rfind(" ")
        if coupe == -1:
            coupe = CHUNK_SIZE
        morceaux.append(texte[:coupe + 1].strip())
        texte = texte[coupe + 1:].strip()
    return [m for m in morceaux if m]


def reformater_texte(texte: str) -> str:
    lignes = texte.split("\n")
    resultat = []
    compteur_chapitres = 0
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF\U00002700-\U000027BF\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF]+",
        flags=re.UNICODE,
    )
    marqueurs_important = [
        r"^\s*[\*\-•]\s*\*\*(.+)\*\*", r"^\s*[\*\-•]\s*!(.+)",
        r"^\s*⚠️(.+)", r"^\s*❗(.+)", r"^\s*✅(.+)", r"^\s*🔑(.+)",
    ]
    for ligne in lignes:
        s = ligne.strip()
        if not s:
            resultat.append("")
            continue
        est_titre = False
        m = re.match(r"^(#{1,3})\s*(.+)$", s)
        if m:
            propre = emoji_pattern.sub("", m.group(2)).strip()
            if propre:
                compteur_chapitres += 1
                resultat.append(f"Chapitre {compteur_chapitres} : {propre}.")
                est_titre = True
        if not est_titre and emoji_pattern.match(s):
            propre = emoji_pattern.sub("", s).strip()
            if propre and len(propre) > 3:
                compteur_chapitres += 1
                resultat.append(f"Chapitre {compteur_chapitres} : {propre}.")
                est_titre = True
        if est_titre:
            continue
        est_important = False
        for pattern in marqueurs_important:
            mi = re.match(pattern, s)
            if mi:
                propre = emoji_pattern.sub("", mi.group(1)).strip()
                resultat.append(f"Note importante : {propre}.")
                est_important = True
                break
        if est_important:
            continue
        propre = emoji_pattern.sub("", s).strip()
        if propre:
            propre = re.sub(r"\*\*(.+?)\*\*", r"\1", propre)
            propre = re.sub(r"\*(.+?)\*", r"\1", propre)
            propre = re.sub(r"`(.+?)`", r"\1", propre)
            resultat.append(propre)
    return "\n".join(resultat)


def _build_prompt(langue: str) -> str:
    langue = {"fr": "en français", "en": "en anglais"}.get(langue, "dans la langue d'origine du texte")
    return f"""\
Tu es un rédacteur spécialisé en contenus audio de formation. \
Transforme le texte brut fourni en narration fluide prête pour une synthèse vocale. \
Rédige le résultat {langue}. \
Règles strictes :
- Les titres (lignes commençant par #, ou lignes courtes avec un emoji) deviennent "Chapitre X : <titre>." en incrémentant X à partir de 1.
- Les ℹ️, ⚠️, ❗, 🔑, ✅ et toute puce marquée comme critique deviennent "Note importante : <contenu>."
- Les listes à puces (-, *, •) sont reformulées en prose naturelle avec des connecteurs logiques (par ailleurs, ensuite, de plus, il convient également de noter que, enfin…).
- Les références à des auteurs, chercheurs ou études sont intégrées dans le flux ("selon les travaux de…", "comme le montrent les recherches de…").
- Supprime tous les symboles Markdown (**, *, #, `, _) et tous les emojis.
- Ne résume pas, ne coupe pas de contenu. Garde toutes les informations.
- Le résultat doit sonner comme un narrateur humain, sans lire des listes.
- Réponds uniquement avec le texte reformaté, sans commentaire ni explication.\
"""


def _reformater_chunk_ia(chunk: str, client, numero: int, total: int, langue: str) -> str:
    contexte = f"[Partie {numero}/{total} — continue la numérotation des chapitres]\n\n" if total > 1 else ""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _build_prompt(langue)},
            {"role": "user", "content": contexte + chunk},
        ],
        temperature=0.4,
    )
    return response.choices[0].message.content.strip()


def _decouper_gpt(texte: str) -> list:
    morceaux = []
    while texte:
        if len(texte) <= GPT_CHUNK_SIZE:
            morceaux.append(texte)
            break
        bout = texte[:GPT_CHUNK_SIZE]
        c = max(bout.rfind("\n\n"), bout.rfind(". "))
        if c == -1:
            c = GPT_CHUNK_SIZE
        morceaux.append(texte[:c + 1].strip())
        texte = texte[c + 1:].strip()
    return morceaux


# ── Google Docs ────────────────────────────────────────────────────────────────

def extract_gdoc_id(url: str) -> str:
    m = re.search(r'/document/d/([a-zA-Z0-9_-]+)', url)
    if m:
        return m.group(1)
    raise ValueError("Impossible d'extraire l'ID depuis l'URL Google Docs")


# ── Slite ──────────────────────────────────────────────────────────────────────

def extract_slite_id(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    m = re.search(r'/docs/([^/]+)', path)
    if m:
        return m.group(1)
    m = re.search(r'/[pn]/([^/\-]+)', path)
    if m:
        return m.group(1)
    segments = [s for s in path.split('/') if s and re.match(r'^[A-Za-z0-9]{4,}$', s)]
    if segments:
        return segments[-1]
    raise ValueError("Impossible d'extraire l'ID depuis l'URL Slite")


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", api_key_loaded=bool(API_KEY_FROM_ENV), voix=VOIX)


@app.route("/fetch-gdoc", methods=["POST"])
def fetch_gdoc():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"erreur": "URL manquante"}), 400
    try:
        doc_id = extract_gdoc_id(url)
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
        req = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            titre = "Document"
            cd = r.headers.get("Content-Disposition", "")
            m_titre = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';\r\n]+)', cd)
            if m_titre:
                titre = urllib.parse.unquote(m_titre.group(1)).replace(".txt", "").strip()
            contenu = r.read().decode("utf-8")
        if not contenu.strip():
            return jsonify({"erreur": "Document vide ou non accessible. Vérifiez qu'il est partagé en lecture publique."}), 400
        return jsonify({"titre": titre, "contenu": contenu})
    except ValueError as e:
        return jsonify({"erreur": str(e)}), 400
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return jsonify({"erreur": "Accès refusé — vérifiez que le document est partagé en « Tout le monde peut consulter »."}), 403
        return jsonify({"erreur": f"Erreur Google Docs {e.code} : {e.reason}"}), 400
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@app.route("/fetch-slite", methods=["POST"])
def fetch_slite():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"erreur": "URL manquante"}), 400
    if not SLITE_API_KEY:
        return jsonify({"erreur": "SLITE_API_KEY manquant dans le .env"}), 500
    try:
        note_id = extract_slite_id(url)
        req = urllib.request.Request(
            f"{SLITE_BASE}/notes/{note_id}",
            headers={"Authorization": f"Bearer {SLITE_API_KEY}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            note = json.loads(r.read().decode())
        content = note.get("content", "")
        if not content:
            return jsonify({"erreur": "Note vide ou contenu non accessible"}), 400
        return jsonify({"titre": note.get("title", ""), "contenu": content})
    except ValueError as e:
        return jsonify({"erreur": str(e)}), 400
    except urllib.error.HTTPError as e:
        return jsonify({"erreur": f"Erreur Slite {e.code} : {e.reason}"}), 400
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@app.route("/api-key-env")
def api_key_env():
    return jsonify({"key": API_KEY_FROM_ENV, "loaded": bool(API_KEY_FROM_ENV)})


@app.route("/preview-voix", methods=["POST"])
def preview_voix():
    data = request.get_json()
    voix = data.get("voix", "nova")
    api_key = data.get("api_key", "").strip() or API_KEY_FROM_ENV
    if voix not in VOIX:
        return jsonify({"erreur": "Voix inconnue."}), 400
    if not api_key:
        return jsonify({"erreur": "Clé API requise."}), 400

    nom_fichier = f"preview_{voix}.mp3"
    chemin = os.path.join(AUDIO_DIR, nom_fichier)
    if not os.path.exists(chemin):
        try:
            client = OpenAI(api_key=api_key)
            r = client.audio.speech.create(model="tts-1", voice=voix, input=PHRASE_PREVIEW)
            with open(chemin, "wb") as f:
                f.write(r.content)
        except Exception as e:
            return jsonify({"erreur": str(e)}), 500

    return jsonify({"url": f"/audio/{nom_fichier}"})


@app.route("/reformater", methods=["POST"])
def reformater():
    data = request.get_json()
    texte = data.get("texte", "")
    api_key = data.get("api_key", "").strip() or API_KEY_FROM_ENV
    mode_ia = data.get("mode_ia", False)
    langue = data.get("langue", "fr")

    if mode_ia and api_key:
        try:
            morceaux = _decouper_gpt(texte)
            client = OpenAI(api_key=api_key)
            parties = [_reformater_chunk_ia(m, client, i + 1, len(morceaux), langue) for i, m in enumerate(morceaux)]
            texte_reformate = "\n\n".join(parties)
        except Exception as e:
            return jsonify({"erreur": str(e)}), 500
    else:
        texte_reformate = reformater_texte(texte)

    return jsonify({"texte_reformate": texte_reformate})


@app.route("/generer", methods=["POST"])
def generer():
    data = request.get_json()
    texte = data.get("texte", "").strip()
    api_key = data.get("api_key", "").strip() or API_KEY_FROM_ENV
    do_reformater = data.get("reformater", True)
    mode_ia = data.get("mode_ia", False)
    langue = data.get("langue", "fr")
    voix = data.get("voix", "nova")
    titre = data.get("titre", "").strip()

    if voix not in VOIX:
        voix = "nova"

    if not texte:
        return jsonify({"erreur": "Le texte est vide."}), 400
    if not api_key:
        return jsonify({"erreur": "Clé API OpenAI requise."}), 400

    # Construire le nom de fichier depuis le titre ou un UUID
    if titre:
        slug = re.sub(r"[^\w\-]", "-", titre.lower().replace(" ", "-"))
        slug = re.sub(r"-+", "-", slug).strip("-")[:60]
        nom_fichier = f"{slug}.mp3"
        nom_telechargement = nom_fichier
    else:
        uid = uuid.uuid4().hex[:8]
        nom_fichier = f"audio_{uid}.mp3"
        nom_telechargement = nom_fichier
    chemin_fichier = os.path.join(AUDIO_DIR, nom_fichier)

    def stream():
        import json as _j
        try:
            client = OpenAI(api_key=api_key)
            texte_final = texte

            if do_reformater:
                if mode_ia:
                    gpt_morceaux = _decouper_gpt(texte)
                    parties = []
                    for idx, morceau in enumerate(gpt_morceaux, 1):
                        yield f"data: {_j.dumps({'etape_ia': True, 'gpt_partie': idx, 'gpt_total': len(gpt_morceaux)})}\n\n"
                        parties.append(_reformater_chunk_ia(morceau, client, idx, len(gpt_morceaux), langue))
                    texte_final = "\n\n".join(parties)
                else:
                    texte_final = reformater_texte(texte)

            morceaux = decouper_texte(texte_final)
            nb = len(morceaux)
            audio_bytes = b""
            for i, morceau in enumerate(morceaux, 1):
                yield f"data: {_j.dumps({'etape': i, 'total': nb})}\n\n"
                r = client.audio.speech.create(model="tts-1", voice=voix, input=morceau)
                audio_bytes += r.content

            with open(chemin_fichier, "wb") as f:
                f.write(audio_bytes)

            yield f"data: {_j.dumps({'succes': True, 'fichier': nom_fichier, 'nom_telechargement': nom_telechargement, 'url': f'/audio/{nom_fichier}', 'nb_morceaux': nb, 'texte_final': texte_final})}\n\n"
        except Exception as e:
            yield f"data: {_j.dumps({'erreur': str(e)})}\n\n"

    return Response(stream(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.route("/audio/<nom_fichier>")
def telecharger_audio(nom_fichier):
    return send_from_directory(AUDIO_DIR, nom_fichier, as_attachment=True)


if __name__ == "__main__":
    print("Démarrage de l'assistant de formation audio...")
    print("Ouvre http://127.0.0.1:5003 dans ton navigateur.")
    app.run(debug=False, port=5003, threaded=True)
