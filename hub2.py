import os
import subprocess
from flask import Flask, render_template, jsonify

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


@app.route("/")
def index():
    return render_template("hub2.html")


@app.route("/ouvrir-claude")
def ouvrir_claude():
    script = f'tell application "Terminal" to do script "cd {PROJECT_DIR} && claude"'
    try:
        subprocess.Popen(["osascript", "-e", script])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


if __name__ == "__main__":
    print("Hub 2 (Claude + RF = <3) → http://127.0.0.1:5004")
    app.run(debug=False, port=5004, threaded=True)
