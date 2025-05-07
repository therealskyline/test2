import os
import sys

# Ajouter les chemins d'accès aux modules
sys.path.append("./realweb/final_website")
sys.path.append("./realweb/API")

# Importer l'application Flask
from realweb.final_website.app import app

if __name__ == "__main__":
    # Écouter sur toutes les interfaces (0.0.0.0) pour que Replit puisse accéder au serveur
    # Utiliser le port 5000 par défaut, ou celui spécifié par Replit
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)