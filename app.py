import sys
import os

# Ajouter le répertoire du projet au sys.path
sys.path.append(os.path.abspath("test/WebStreamSync"))

# Importer l'application Flask
from test.WebStreamSync.realweb.final_website.app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)