import os
import re
import json
import math
import random
import urllib.request
import urllib.error
from flask import Flask, render_template, request, jsonify, Response
from dotenv import load_dotenv
import anthropic

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SLITE_API_KEY = os.getenv("SLITE_API_KEY", "")
SLITE_FOLDER_ID = os.getenv("SLITE_FOLDER_ID", "")

THEMES = {
    "ergo":      os.getenv("SLITE_FOLDER_ERGO",      SLITE_FOLDER_ID),
    "psycho":    os.getenv("SLITE_FOLDER_PSYCHO",    SLITE_FOLDER_ID),
    "recherche": os.getenv("SLITE_FOLDER_RECHERCHE", SLITE_FOLDER_ID),
    "cnam":      os.getenv("SLITE_FOLDER_CNAM",      SLITE_FOLDER_ID),
}

SLITE_BASE = "https://api.slite.com/v1"
MODEL = "claude-sonnet-4-5"
MAX_NOTES = 15  # limite raisonnable pour le contexte


def slite_get(path: str) -> dict:
    url = f"{SLITE_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {SLITE_API_KEY}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def get_notes_content(parent_id: str) -> list[dict]:
    """Retourne une liste de {title, content} pour toutes les notes d'un dossier Slite."""
    # Récupérer la liste des notes enfants du dossier
    data = slite_get(f"/notes?parentNoteId={parent_id}")
    notes_meta = data.get("notes", [])[:MAX_NOTES]

    results = []
    for meta in notes_meta:
        note_id = meta.get("id")
        title = meta.get("title") or "Sans titre"
        # Le contenu n'est pas dans la liste — on le fetch individuellement
        try:
            note = slite_get(f"/notes/{note_id}")
            content = note.get("content") or ""
        except Exception:
            content = ""
        url = meta.get("url") or ""
        if content:
            results.append({"title": title, "content": content, "url": url})

    return results


PROMPT_QCM = """\
Tu es un professeur expert en formation. Voici des notes de cours extraites d'un espace Slite :

{contenu}

---

Génère exactement {nb_qcm} questions QCM et {nb_ouvertes} questions ouvertes basées sur ces notes.
Varie les angles : définitions, applications, causes/conséquences, comparaisons, exemples concrets.
Pour garantir la diversité, choisis des notions différentes à chaque génération.
Seed de diversité : {seed}

Réponds UNIQUEMENT avec un tableau JSON valide, sans texte autour. Mélange les types dans le tableau.

Format QCM :
{{
  "type": "qcm",
  "question": "Question ?",
  "choices": ["A. ...", "B. ...", "C. ...", "D. ..."],
  "answer": 0,
  "source": "Titre exact de la note source"
}}

Format question ouverte :
{{
  "type": "ouvert",
  "question": "Question ouverte ?",
  "reponse": "Réponse complète attendue en 2-4 phrases.",
  "source": "Titre exact de la note source"
}}

Règles :
- "answer" est l'index (0-3) de la bonne réponse QCM
- Les mauvaises réponses QCM doivent être plausibles
- "source" correspond exactement au titre d'une des notes fournies
- Varie les notes sources sur toutes les questions
- Si {nb_ouvertes} vaut 0, génère uniquement des QCM
"""


THEME_LABELS = {
    "ergo":      "Ergo IHM / FH",
    "psycho":    "Psycho cognitive",
    "recherche": "Recherche",
    "cnam":      "Cours CNAM",
}


@app.route("/lister-notes")
def lister_notes():
    result = {}
    seen_folders = set()
    for theme_key, folder_id in THEMES.items():
        if not folder_id or folder_id in seen_folders:
            result[theme_key] = []
            continue
        seen_folders.add(folder_id)
        try:
            data = slite_get(f"/notes?parentNoteId={folder_id}")
            notes = data.get("notes", [])[:MAX_NOTES]
            result[theme_key] = [
                {"id": n.get("id"), "title": n.get("title") or "Sans titre"}
                for n in notes
            ]
        except Exception:
            result[theme_key] = []
    return jsonify(result)


@app.route("/")
def index():
    return render_template("prof-virtuel.html")


@app.route("/fetch-gdoc", methods=["POST"])
def fetch_gdoc():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        return jsonify({"erreur": "URL Google Docs invalide."}), 400
    doc_id = m.group(1)
    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    try:
        req = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            contenu = r.read().decode("utf-8", errors="replace")
            titre = dict(r.headers).get("Content-Disposition", "")
            m2 = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';\n]+)', titre)
            titre = m2.group(1).strip().rstrip(".txt") if m2 else "Document Google"
        return jsonify({"titre": titre, "contenu": contenu})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@app.route("/generer-qcm", methods=["POST"])
def generer_qcm():
    data = request.get_json() or {}
    nb_questions = int(data.get("nb_questions", 10))
    avec_ouvertes = bool(data.get("avec_ouvertes", False))
    nb_ouvertes = math.ceil(nb_questions * 0.2) if avec_ouvertes else 0
    nb_qcm = nb_questions - nb_ouvertes
    theme = data.get("theme", "ergo")
    channel_id = THEMES.get(theme, SLITE_FOLDER_ID)
    note_id = data.get("note_id", "")
    note_ids = data.get("note_ids", [])
    gdoc_content = data.get("gdoc_content", "")
    gdoc_title = data.get("gdoc_title", "Document Google")

    if not gdoc_content:
        if not channel_id:
            return jsonify({"erreur": "SLITE_FOLDER_ID manquant dans le .env"}), 400
        if not SLITE_API_KEY or "COLLE" in SLITE_API_KEY:
            return jsonify({"erreur": "Clé API Slite non configurée dans le .env"}), 500
    if not ANTHROPIC_API_KEY or "COLLE" in ANTHROPIC_API_KEY:
        return jsonify({"erreur": "Clé API Anthropic non configurée dans le .env"}), 500

    def stream():
        import json as _j

        # Étape 1 : source du contenu
        if gdoc_content:
            notes = [{"title": gdoc_title, "content": gdoc_content, "url": ""}]
        elif note_ids:
            yield f"data: {_j.dumps({'etape': 'slite', 'msg': 'Récupération des notes de révision…'})}\n\n"
            notes = []
            for nid in note_ids:
                try:
                    note = slite_get(f"/notes/{nid}")
                    content = note.get("content", "")
                    if content:
                        notes.append({"title": note.get("title", "Sans titre"), "content": content, "url": note.get("url", "")})
                except Exception:
                    pass
        elif note_id:
            yield f"data: {_j.dumps({'etape': 'slite', 'msg': 'Récupération du document…'})}\n\n"
            try:
                note = slite_get(f"/notes/{note_id}")
                content = note.get("content", "")
                if not content:
                    yield f"data: {_j.dumps({'erreur': 'Document vide ou non accessible.'})}\n\n"
                    return
                notes = [{"title": note.get("title", "Sans titre"), "content": content, "url": note.get("url", "")}]
            except Exception as e:
                yield f"data: {_j.dumps({'erreur': f'Erreur Slite : {str(e)}'})}\n\n"
                return
        else:
            yield f"data: {_j.dumps({'etape': 'slite', 'msg': 'Récupération des notes Slite…'})}\n\n"
            try:
                notes = get_notes_content(channel_id)
            except urllib.error.HTTPError as e:
                yield f"data: {_j.dumps({'erreur': f'Erreur Slite {e.code} : {e.reason}'})}\n\n"
                return
            except Exception as e:
                yield f"data: {_j.dumps({'erreur': f'Erreur Slite : {str(e)}'})}\n\n"
                return

        if not notes:
            yield f"data: {_j.dumps({'erreur': 'Aucune note trouvée dans ce channel.'})}\n\n"
            return

        yield f"data: {_j.dumps({'etape': 'slite_ok', 'msg': f'{len(notes)} note(s) récupérée(s). Génération du QCM…'})}\n\n"

        # Étape 2 : construire le contexte et appeler Claude
        contenu = "\n\n".join(
            f"### {n['title']}\n{n['content']}" for n in notes
        )
        seed = random.randint(1000, 9999)
        prompt = PROMPT_QCM.format(contenu=contenu[:40000], seed=seed, nb_qcm=nb_qcm, nb_ouvertes=nb_ouvertes)

        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            message = client.messages.create(
                model=MODEL,
                max_tokens=8192,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    },
                    {
                        # Forcer Claude à commencer directement par le JSON
                        "role": "assistant",
                        "content": "[",
                    },
                ],
            )
            raw = "[" + message.content[0].text.strip()

            # S'assurer que le tableau est fermé
            if not raw.rstrip().endswith("]"):
                # Couper au dernier objet complet valide
                last = raw.rfind("},")
                if last != -1:
                    raw = raw[:last + 1] + "]"
                else:
                    raw = raw + "]"

            questions = _j.loads(raw)

            # Enrichir chaque question avec l'URL de la note source
            url_map = {n["title"]: n["url"] for n in notes}
            for q in questions:
                q["source_url"] = url_map.get(q.get("source"), "")

        except Exception as e:
            yield f"data: {_j.dumps({'erreur': f'Erreur Claude : {str(e)}'})}\n\n"
            return

        yield f"data: {_j.dumps({'succes': True, 'questions': questions})}\n\n"

    return Response(stream(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.route("/evaluer-reponse", methods=["POST"])
def evaluer_reponse():
    data = request.get_json()
    question = data.get("question", "")
    reponse_utilisateur = data.get("reponse_utilisateur", "").strip()
    reponse_attendue = data.get("reponse_attendue", "")

    if not reponse_utilisateur:
        return jsonify({"correct": False, "feedback": "Aucune réponse saisie."})

    prompt = f"""Tu es un correcteur bienveillant. Évalue si la réponse de l'étudiant est correcte.

Question : {question}
Réponse attendue : {reponse_attendue}
Réponse de l'étudiant : {reponse_utilisateur}

Sois tolérant : accepte les formulations différentes, les synonymes, les réponses incomplètes mais qui montrent une bonne compréhension. Refuse uniquement si la réponse est clairement fausse ou hors sujet.

Réponds UNIQUEMENT avec ce JSON (sans texte autour) :
{{"correct": true ou false, "feedback": "Une phrase courte et encourageante expliquant pourquoi."}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        result = json.loads(raw[start:end])
        return jsonify(result)
    except Exception as e:
        return jsonify({"correct": False, "feedback": str(e)}), 500


if __name__ == "__main__":
    print("Prof Virtuel démarré → http://127.0.0.1:5001")
    app.run(debug=False, port=5001, threaded=True)
