import os
import re
import json
import sys
import logging
import datetime
import shutil
import asyncio
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import urllib.parse

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Ajouter le chemin de l'API pour pouvoir l'importer
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'API'))
try:
    from anime_sama_api.top_level import AnimeSama
    API_IMPORT_SUCCESS = True
    logger.info("Import de l'API Anime-Sama réussi!")
except ImportError as e:
    API_IMPORT_SUCCESS = False
    logger.error(f"Erreur d'import de l'API Anime-Sama: {e}")

# URL de base pour l'API Anime-Sama
ANIME_SAMA_BASE_URL = "https://anime-sama.fr/"

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "default_secret_key_for_development")

# Initialize database
# Utiliser SQLite en attendant de résoudre les problèmes avec PostgreSQL
app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///anime.db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Initialize login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    # Nouvelles colonnes pour stocker les préférences utilisateur
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_login = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# Modèle pour suivre la progression des utilisateurs sur les animes
class UserProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    anime_id = db.Column(db.Integer, nullable=False)
    season_number = db.Column(db.Integer, nullable=False)
    episode_number = db.Column(db.Integer, nullable=False)
    time_position = db.Column(db.Float, default=0)  # Position en secondes dans l'épisode
    completed = db.Column(db.Boolean, default=False)
    last_watched = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    # Relation avec l'utilisateur
    user = db.relationship('User', backref=db.backref('progress', lazy='dynamic'))

    # Contrainte d'unicité pour éviter les doublons
    __table_args__ = (
        db.UniqueConstraint('user_id', 'anime_id', 'season_number', 'episode_number'),
    )

# Modèle pour les favoris des utilisateurs
class UserFavorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    anime_id = db.Column(db.Integer, nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    # Relation avec l'utilisateur
    user = db.relationship('User', backref=db.backref('favorites', lazy='dynamic'))

    # Contrainte d'unicité pour éviter les doublons
    __table_args__ = (
        db.UniqueConstraint('user_id', 'anime_id'),
    )

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Load anime data from JSON file
def load_anime_data():
    try:
        # Définir le chemin absolu vers le fichier JSON
        base_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(base_dir, 'static', 'data', 'anime.json')
        logger.info(f"Chargement du fichier anime.json depuis: {json_path}")

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Ensure we're getting a dictionary with an anime key
            if isinstance(data, dict) and 'anime' in data:
                logger.info(f"Données chargées: {len(data['anime'])} animes trouvés")
                return data['anime']
            elif isinstance(data, list):
                # If it's just a list (no wrapper), return it directly
                logger.info(f"Données chargées (format liste): {len(data)} animes trouvés")
                return data
            else:
                # Create a default structure
                logger.warning("Anime data file has unexpected format. Creating default structure.")
                return []
    except FileNotFoundError:
        logger.error(f"Anime data file not found. Creating empty data file.")
        # Create empty data file with proper structure
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base_dir, 'static', 'data')
        json_path = os.path.join(data_dir, 'anime.json')

        os.makedirs(data_dir, exist_ok=True)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({'anime': []}, f, indent=4)
        logger.info(f"Fichier vide créé: {json_path}")
        return []
    except json.JSONDecodeError:
        logger.error("Error decoding anime data file. Returning empty list.")
        return []

# Fonction pour rechercher des animes avec l'API Anime-Sama
async def search_anime_api(query, limit=20, fetch_seasons=False):
    """
    Recherche des animes en utilisant l'API Anime-Sama de manière simplifiée
    :param query: Texte de recherche
    :param limit: Nombre maximum de résultats à retourner (défaut: 20)
    :param fetch_seasons: Si True, récupère également les saisons et épisodes pour chaque anime
    :return: Liste des animes trouvés (limitée à 'limit', avec peu de détails)
    """
    try:
        if not API_IMPORT_SUCCESS:
            logger.error("L'API Anime-Sama n'est pas disponible")
            return []

        logger.info(f"Recherche d'anime via l'API pour: {query} (limite: {limit})")
        api = AnimeSama(ANIME_SAMA_BASE_URL)
        results = await api.search(query)

        if not results:
            logger.info(f"Aucun résultat trouvé pour: {query}")
            return []

        # Filtrer les résultats pour ne garder que les animes (pas les scans/mangas)
        filtered_results = []
        for anime in results:
            # Vérification simplifiée
            is_anime = True
            try:
                if hasattr(anime, 'is_manga') and callable(anime.is_manga) and anime.is_manga():
                    is_anime = False
            except Exception:
                pass

            if is_anime:
                filtered_results.append(anime)
                # Limite très tôt le nombre de résultats traités
                if len(filtered_results) >= limit:
                    break

        logger.info(f"Nombre d'animes après filtrage: {len(filtered_results)}")

        # Convertir les résultats de l'API au format attendu par l'application, mais de façon minimaliste
        anime_list = []

        # Charger les données existantes pour la gestion des IDs
        current_data = load_anime_data()

        # Trouver le plus grand ID existant pour attribuer de nouveaux IDs uniques
        next_id = 1
        if current_data:
            next_id = max(int(a.get('id', 0)) for a in current_data) + 1

        for i, anime in enumerate(filtered_results):
            # Rechercher si l'anime existe déjà dans notre base locale
            existing_anime = next((a for a in current_data if a.get('title', '').lower() == anime.name.lower()), None)

            # Si l'anime existe déjà, on le réutilise directement
            if existing_anime:
                # Mais on vérifie si on doit récupérer les saisons
                if fetch_seasons and not existing_anime.get('seasons_fetched', False):
                    logger.info(f"Récupération des saisons pour l'anime existant: {existing_anime['title']}")
                    try:
                        existing_anime = await fetch_anime_seasons(anime, existing_anime)
                    except Exception as e:
                        logger.error(f"Erreur lors de la récupération des saisons pour {existing_anime['title']}: {e}")
                anime_list.append(existing_anime)
                continue

            # Créer une entrée minimale pour cet anime 
            anime_id = next_id + i

            # Récupérer l'URL de l'image de base
            image_url = ''
            if hasattr(anime, 'image_url') and anime.image_url:
                image_url = anime.image_url

            # Formater l'image correctement
            if not image_url or not image_url.startswith(('http://', 'https://')):
                image = '/static/img/anime-placeholder.jpg'
            else:
                image = image_url

            # Version simplifiée des saisons - juste une saison par défaut pour commencer
            seasons_data = [{
                'season_number': 1,
                'name': "Saison 1",
                'episodes': []
            }]

            # Créer une entrée anime minimale
            anime_entry = {
                'id': anime_id,
                'title': anime.name,
                'original_title': anime.name,
                'description': 'Chargez la page de l\'anime pour voir sa description',
                'image': image,
                'image_url': image_url,
                'genres': anime.genres if hasattr(anime, 'genres') else [],
                'seasons': seasons_data,
                'featured': False,
                'year': '',
                'status': 'Disponible',
                'rating': 7.5,
                'languages': ['VOSTFR'],
                'seasons_fetched': False
            }

            # Si demandé, récupérer les saisons et les épisodes
            if fetch_seasons:
                try:
                    anime_entry = await fetch_anime_seasons(anime, anime_entry)
                except Exception as e:
                    logger.error(f"Erreur lors de la récupération des saisons pour {anime.name}: {e}")

            anime_list.append(anime_entry)

        logger.info(f"Résultats retournés: {len(anime_list)} animes")
        return anime_list

    except Exception as e:
        logger.error(f"Erreur lors de la recherche d'anime: {e}")
        return []

async def fetch_anime_seasons(anime_obj, anime_entry):
    """
    Récupère les saisons, films et épisodes pour un anime.
    Les films sont inclus comme une saison spéciale nommée "Films".

    :param anime_obj: L'objet anime de l'API Anime-Sama
    :param anime_entry: L'entrée anime au format du site
    :return: L'entrée anime mise à jour avec les saisons et épisodes
    """
    try:
        logger.info(f"Récupération des saisons pour: {anime_entry['title']}")

        # Récupérer la description/synopsis
        try:
            synopsis = await anime_obj.synopsis()
            if synopsis:
                anime_entry['description'] = synopsis
        except Exception as e:
            logger.error(f"Erreur lors de la récupération du synopsis: {e}")

        # Récupérer toutes les saisons
        seasons = await anime_obj.seasons()

        if not seasons:
            logger.info(f"Aucune saison trouvée pour: {anime_entry['title']}")
            anime_entry['seasons_fetched'] = True
            return anime_entry

        logger.info(f"Nombre de saisons trouvées: {len(seasons)}")

        # Structure pour organiser les saisons et films
        regular_seasons = []
        films = []

        # Pour chaque saison, récupérer les épisodes
        for i, season in enumerate(seasons):
            try:
                season_name = season.name
                logger.info(f"Traitement de la saison: {season_name}")

                # Déterminer si c'est un film ou une saison régulière
                is_film = False
                if "Film" in season_name or "Movie" in season_name:
                    is_film = True
                    logger.info(f"Film détecté: {season_name}")

                # Récupérer les épisodes de cette saison
                episodes = await season.episodes()

                if not episodes:
                    logger.info(f"Aucun épisode trouvé pour la saison: {season_name}")
                    continue

                logger.info(f"Nombre d'épisodes trouvés: {len(episodes)}")

                # Déterminer le numéro de saison
                season_number = i + 1
                try:
                    # Essayer d'extraire le numéro de saison du nom
                    season_match = re.search(r'Saison\s+(\d+)', season_name, re.IGNORECASE)
                    if season_match:
                        season_number = int(season_match.group(1))
                except Exception:
                    pass

                # Créer la structure de saison
                season_data = {
                    'season_number': 99 if is_film else season_number,  # Films auront le numéro 99
                    'name': "Films" if is_film else season_name,
                    'episodes': []
                }

                # Ajouter les épisodes
                for j, episode in enumerate(episodes):
                    # Langues disponibles
                    available_langs = []
                    for lang in episode.languages.availables:
                        if lang in ["VF", "VOSTFR"]:
                            available_langs.append(lang)

                    # Créer l'entrée d'épisode
                    episode_data = {
                        'episode_number': j + 1,
                        'title': episode.name,
                        'description': '',
                        'duration': 0,  # Durée inconnue pour l'instant
                        'languages': available_langs,
                        'urls': {}  # Sera rempli plus tard lors de la lecture
                    }

                    season_data['episodes'].append(episode_data)

                # Ajouter la saison à la bonne catégorie
                if is_film:
                    # Pour les films, ajouter chaque épisode comme un film dans la liste films
                    films.extend(season_data['episodes'])
                else:
                    regular_seasons.append(season_data)

            except Exception as e:
                logger.error(f"Erreur lors du traitement de la saison {season.name}: {e}")

        # Créer une entrée pour les films si nécessaire
        if films:
            film_season = {
                'season_number': 99,
                'name': "Films",
                'episodes': films
            }
            regular_seasons.append(film_season)

        # Mettre à jour l'entrée anime
        anime_entry['seasons'] = regular_seasons
        anime_entry['seasons_fetched'] = True

        return anime_entry

    except Exception as e:
        logger.error(f"Erreur lors de la récupération des saisons pour {anime_entry['title']}: {e}")
        return anime_entry

# Wrapper synchrone pour la fonction de recherche asynchrone
def search_anime(query, limit=20, fetch_seasons=False):
    """
    Wrapper synchrone pour la fonction de recherche asynchrone
    :param query: Texte de recherche
    :param limit: Nombre maximum de résultats à retourner
    :param fetch_seasons: Si True, récupère aussi les saisons et épisodes
    :return: Liste des animes trouvés (limitée à 'limit')
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(search_anime_api(query, limit=limit, fetch_seasons=fetch_seasons))
        loop.close()
        return results
    except Exception as e:
        logger.error(f"Erreur dans le wrapper de recherche: {e}")
        return []

def save_anime_data(data):
    try:
        # Définir le chemin absolu pour le stockage
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base_dir, 'static', 'data')
        json_path = os.path.join(data_dir, 'anime.json')

        # Créer le dossier data s'il n'existe pas
        os.makedirs(data_dir, exist_ok=True)
        logger.info(f"Sauvegarde des données anime vers: {json_path}")

        # Ensure we're saving with the expected structure
        if isinstance(data, list):
            save_data = {'anime': data}
        else:
            # If somehow data is not a list, create a default structure
            logger.warning("Unexpected data format when saving anime data")
            save_data = {'anime': []}

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, indent=4)
        logger.info(f"Données sauvegardées avec succès: {len(save_data['anime'])} animes")
        return True
    except Exception as e:
        logger.error(f"Error saving anime data: {e}")
        return False

# Extract unique genres from anime data
def get_all_genres():
    anime_data = load_anime_data()
    genres = set()
    for anime in anime_data:
        for genre in anime.get('genres', []):
            genres.add(genre.lower())
    return sorted(list(genres))

# Helper function to extract Google Drive ID from URL
def extract_drive_id(url):
    # Looking for patterns like drive.google.com/file/d/ID/view
    drive_patterns = [
        r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
        r'drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)'
    ]

    for pattern in drive_patterns:
        match = re.search(pattern, url)
        if match:
            logger.debug(f"Extracted Google Drive ID: {match.group(1)}")
            return match.group(1)

    # If it's just the ID itself
    if not url.startswith(('http://', 'https://')):
        logger.debug(f"Using provided ID: {url}")
        return url

    # If it contains the ID in the URL but doesn't match the patterns above
    parts = url.split('/')
    for part in parts:
        if len(part) > 20 and re.match(r'^[a-zA-Z0-9_-]+$', part):
            logger.debug(f"Extracted potential Google Drive ID from parts: {part}")
            return part

    logger.warning(f"Could not extract Google Drive ID from URL: {url}")
    return None

@app.route('/')
def index():
    try:
        # Rediriger vers la page de connexion si l'utilisateur n'est pas connecté
        if not current_user.is_authenticated:
            return redirect(url_for('login'))

        anime_data = load_anime_data()

        # Récupérer les animes en cours de visionnage (limité à 15 maximum)
        continue_watching = []
        if current_user.is_authenticated:
            try:
                # Récupérer les progressions non terminées les plus récentes par anime
                latest_progress_by_anime = UserProgress.query.filter_by(
                    user_id=current_user.id
                ).order_by(
                    UserProgress.last_watched.desc()
                ).all()

                # Pour chaque anime, trouver les données et ajouter à la liste (limité à 15)
                processed_animes = set()
                for progress in latest_progress_by_anime:
                    if progress.anime_id not in processed_animes and len(continue_watching) < 15:
                        anime = next((a for a in anime_data if int(a.get('id', 0)) == progress.anime_id), None)
                        if anime:
                            # Trouver la saison et l'épisode correspondants
                            season = next((s for s in anime.get('seasons', []) if s.get('season_number') == progress.season_number), None)
                            if season:
                                episode = next((e for e in season.get('episodes', []) if e.get('episode_number') == progress.episode_number), None)
                                if episode:
                                    continue_watching.append({
                                        'anime': anime,
                                        'progress': progress,
                                        'season': season,
                                        'episode': episode
                                    })
                                    processed_animes.add(progress.anime_id)
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des animes en cours de visionnage: {e}")
                continue_watching = []

        # Récupérer les favoris (limité à 15 maximum)
        favorite_anime = []
        if current_user.is_authenticated:
            try:
                favorites = UserFavorite.query.filter_by(user_id=current_user.id).all()
                for favorite in favorites:
                    if len(favorite_anime) >= 15:
                        break
                    anime = next((a for a in anime_data if a.get('id') == favorite.anime_id), None)
                    if anime:
                        favorite_anime.append(anime)
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des favoris: {e}")
                favorite_anime = []

        # Créer des animes fixes pour "Découvrir de Nouvelles Séries"
        # Ces animes ne changent jamais et ne sont pas affectés par les recherches
        featured_anime = [
            {
                'id': 1, 
                'title': 'Dragon Ball Z', 
                'description': 'Son Goku et ses amis combattent pour défendre la Terre contre des menaces cosmiques.',
                'image': '/static/img/anime-placeholder.jpg',
                'genres': ['Action', 'Aventure', 'Arts martiaux'],
                'rating': 9.2,
                'featured': True
            },
            {
                'id': 1002, 
                'title': 'Solo Leveling', 
                'description': 'Dans un monde où des chasseurs combattent des monstres, Sung Jinwoo trouve un programme mystérieux qui lui permet de devenir plus fort.',
                'image': 'https://cdn.statically.io/gh/Anime-Sama/IMG/img/contenu/solo-leveling0.jpg',
                'genres': ['Action', 'Aventure', 'Fantasy'],
                'rating': 9.1,
                'featured': True
            },
            {
                'id': 1003, 
                'title': 'Death Note', 
                'description': 'Light Yagami trouve un carnet mystérieux qui lui permet de tuer n\'importe qui en écrivant leur nom.',
                'image': 'https://i.pinimg.com/originals/5a/f6/2e/5af62eeeca7ccea871e062337a1bd467.jpg',
                'genres': ['Thriller', 'Psychologique', 'Surnaturel'],
                'rating': 9.4,
                'featured': True
            },
            {
                'id': 1004, 
                'title': 'My Hero Academia', 
                'description': 'Dans un monde où 80% de la population possède un super-pouvoir, Izuku Midoriya rêve de devenir un héros malgré le fait qu\'il n\'a pas de pouvoir.',
                'image': 'https://m.media-amazon.com/images/I/81Za5Ogs+KL._AC_UF1000,1000_QL80_.jpg',
                'genres': ['Action', 'Super-héros', 'Ecole'],
                'rating': 8.7,
                'featured': True
            },
            {
                'id': 1005, 
                'title': 'One Punch Man', 
                'description': 'Saitama, un super-héros capable de vaincre n\'importe quel ennemi d\'un seul coup de poing, s\'ennuie de sa toute-puissance.',
                'image': 'https://m.media-amazon.com/images/I/81vKv2S7dbL._AC_UF1000,1000_QL80_.jpg',
                'genres': ['Action', 'Comédie', 'Super-héros'],
                'rating': 8.8,
                'featured': True
            },
            {
                'id': 1006, 
                'title': 'One Piece', 
                'description': 'Monkey D. Luffy et son équipage de pirates naviguent sur Grand Line à la recherche du trésor ultime, le One Piece.',
                'image': 'https://cdn.statically.io/gh/Anime-Sama/IMG/img/contenu/one-piece.jpg',
                'genres': ['Action', 'Aventure', 'Pirates'],
                'rating': 9.5,
                'featured': True
            },
            {
                'id': 1007, 
                'title': 'Demon Slayer', 
                'description': 'Tanjiro Kamado devient un chasseur de démons après que sa famille est massacrée et sa sœur transformée en démon.',
                'image': 'https://static.posters.cz/image/750/affiches-et-posters/demon-slayer-saison-2-i116348.jpg',
                'genres': ['Action', 'Démons', 'Historique'],
                'rating': 9.0,
                'featured': True
            },
            {
                'id': 1008, 
                'title': 'Hunter x Hunter', 
                'description': 'Gon Freecss part à la recherche de son père qui est un Hunter d\'élite.',
                'image': 'https://m.media-amazon.com/images/M/MV5BZjNmZDhkN2QtNDYyZC00YzJmLTg0ODUtN2FjNjhhMzE3ZmUxXkEyXkFqcGdeQXVyNjc2NjA5MTU@._V1_FMjpg_UX1000_.jpg',
                'genres': ['Action', 'Aventure', 'Shônen'],
                'rating': 9.3,
                'featured': True
            }
        ]

        # Maximum 12 animes dans cette section
        featured_anime = featured_anime[:12]

        return render_template('index_new.html', 
                        anime_list=featured_anime,
                        continue_watching=continue_watching,
                        favorite_anime=favorite_anime)

    except Exception as e:
        logger.error(f"Erreur dans la page d'accueil: {e}")
        # En cas d'erreur, afficher une page d'accueil avec un minimum de contenu
        return render_template('index_new.html', 
                        anime_list=[],
                        continue_watching=[],
                        favorite_anime=[],
                        error_message="Une erreur s'est produite lors du chargement de la page d'accueil.")

@app.route('/search')
@login_required
def search():
    try:
        query = request.args.get('query', '').lower()
        genre = request.args.get('genre', '').lower()

        # Si la requête est vide ou trop courte, renvoyer directement les résultats locaux (uniquement les 4 derniers)
        if not query or len(query) < 3:
            logger.info("Requête vide ou trop courte, utilisation des données locales uniquement")
            # Charger les données et prendre uniquement les 4 derniers animes ajoutés
            local_data = load_anime_data()
            recent_animes = local_data[-4:] if len(local_data) > 0 else []

            return render_template('search.html', 
                                anime_list=[], 
                                query=query, 
                                selected_genre=genre, 
                                genres=get_all_genres(),
                                api_error="Veuillez entrer au moins 3 caractères pour rechercher",
                                other_anime_list=recent_animes)

        # Définir une limite de temps pour la recherche API
        api_timeout = 8  # Réduit pour éviter les timeouts

        # Nombre maximum de résultats (sans limite)
        MAX_RESULTS = 100  # Augmenté comme demandé

        # Cas spécial pour les requêtes problématiques connues
        problematic_queries = ["1", "2", "3", "a", "e", "o", "100"]
        if query in problematic_queries:
            logger.warning(f"Requête problématique détectée: {query}")
            return render_template('search.html', 
                                anime_list=[], 
                                query=query, 
                                selected_genre=genre, 
                                genres=get_all_genres(),
                                api_error="Cette requête est trop générique et peut causer des problèmes. Veuillez préciser votre recherche.")

        # Obtenir d'abord les résultats de la base locale
        local_data = load_anime_data()
        filtered_local = []

        # Cas spécial : si la recherche contient "one piece", forcer l'utilisation de notre entrée One Piece
        if query and "one piece" in query.lower():
            # Rechercher One Piece dans notre base de données locale
            one_piece = next((a for a in local_data if "one piece" in a.get('title', '').lower()), None)
            if one_piece:
                logger.info(f"Utilisation de l'entrée One Piece locale: {one_piece.get('title')}")
                return render_template('search.html',
                                    anime_list=[one_piece],
                                    query=query,
                                    selected_genre=genre,
                                    genres=get_all_genres())

        # Filtrer les données locales
        for anime in local_data:
            title_match = query in anime.get('title', '').lower()
            genre_match = not genre or genre in [g.lower() for g in anime.get('genres', [])]

            if (not query or title_match) and genre_match:
                filtered_local.append(anime)

        # Limiter le nombre de résultats locaux pour des raisons de performance
        filtered_local = filtered_local[:MAX_RESULTS]

        # Si nous avons assez de résultats locaux, ne pas utiliser l'API
        if len(filtered_local) >= MAX_RESULTS//2:  # Si on a au moins la moitié des résultats max
            logger.info(f"Utilisation des résultats locaux uniquement (suffisant): {len(filtered_local)}")
            return render_template('search.html',
                                anime_list=filtered_local,
                                query=query,
                                selected_genre=genre,
                                genres=get_all_genres())

        # Préparer la liste des résultats
        merged_results = []
        local_titles = [anime.get('title', '').lower() for anime in filtered_local]

        # D'abord ajouter les résultats locaux
        merged_results.extend(filtered_local)

        # Si une requête est spécifiée et que l'API est disponible, chercher via l'API
        # pour compléter jusqu'à MAX_RESULTS
        api_results = []
        api_error = None
        remaining_slots = min(20, MAX_RESULTS - len(merged_results))  # Limité à 20 résultats API maximum

        if query and API_IMPORT_SUCCESS and remaining_slots > 0:
            try:
                logger.info(f"Recherche via API pour: {query} (limite: {remaining_slots} résultats)")

                # Utiliser asyncio avec timeout pour éviter que l'API ne bloque trop longtemps
                async def search_with_timeout():
                    try:
                        return await asyncio.wait_for(search_anime_api(query, limit=remaining_slots), timeout=api_timeout)
                    except asyncio.TimeoutError:
                        logger.error(f"Timeout lors de la recherche API pour: {query}")
                        return []

                # Exécuter la recherche avec timeout
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                api_results = loop.run_until_complete(search_with_timeout())
                loop.close()

                # Si des résultats sont trouvés, filtrer par genre si nécessaire
                if api_results and genre:
                    api_results = [anime for anime in api_results 
                                if any(g.lower() == genre for g in anime.get('genres', []))]

                # Limiter les résultats API
                api_results = api_results[:remaining_slots]

                # Puis ajouter les résultats de l'API qui ne sont pas déjà dans les résultats locaux
                for anime in api_results:
                    # Vérifier que l'image existe et est accessible
                    if not anime.get('image_url') or not anime['image_url'].startswith(('http://', 'https://')):
                        # Utiliser une image par défaut
                        anime['image'] = '/staticimg/anime-placeholder.jpg'
                    else:
                        # Utiliser l'URL de l'image de l'API
                        anime['image'] = anime['image_url']

                    if anime.get('title', '').lower() not in local_titles:
                        merged_results.append(anime)
                        local_titles.append(anime.get('title', '').lower())

                        # Sauvegarder les résultats de recherche dans le fichier local 
                        # Ajouter les nouveaux résultats et conserver jusqu'à 20 animes au total
                        existing_titles = [a.get('title', '').lower() for a in local_data]
                        if anime.get('title', '').lower() not in existing_titles:
                            # Ajouter le nouvel anime à la liste
                            local_data.append(anime)
                            # Si on dépasse 20 animes, supprimer les plus anciens
                            if len(local_data) > 20:  # Limiter à 20 animes maximum au lieu de 15
                                # Supprimer les plus anciens pour revenir à 20
                                local_data = local_data[-20:]
                            # Sauvegarder les changements dans le fichier local
                            save_anime_data(local_data)

                        # Arrêter si on atteint MAX_RESULTS
                        if len(merged_results) >= MAX_RESULTS:
                            break

            except Exception as e:
                logger.error(f"Erreur lors de la recherche API: {e}")
                api_error = "Erreur lors de la recherche. Veuillez réessayer avec des termes différents."

        logger.info(f"Résultats de recherche: {len(merged_results)} animes trouvés")

        # Si aucun résultat n'est trouvé, fournir les 4 derniers animes recherchés
        other_anime_list = load_anime_data()
        # Limiter à seulement les 4 derniers animes
        other_anime_list = other_anime_list[-4:] if len(other_anime_list) > 0 else []
        # Si nous avons des résultats, nous n'affichons pas les dernières recherches
        if merged_results:
            other_anime_list = []

        return render_template('search.html', 
                            anime_list=merged_results, 
                            query=query, 
                            selected_genre=genre, 
                            genres=get_all_genres(),
                            api_error=api_error,
                            other_anime_list=other_anime_list)

    except Exception as e:
        # En cas d'erreur, retourner une page d'erreur claire
        logger.error(f"Erreur critique lors de la recherche: {e}")
        # Charger seulement les 4 derniers animes recherchés même en cas d'erreur
        other_anime_list = load_anime_data()
        other_anime_list = other_anime_list[-4:] if len(other_anime_list) > 0 else []

        return render_template('search.html', 
                              anime_list=[], 
                              query=query if 'query' in locals() else '', 
                              selected_genre=genre if 'genre' in locals() else '', 
                              genres=get_all_genres(),
                              api_error=f"Une erreur s'est produite. Veuillez réessayer plus tard.",
                              other_anime_list=other_anime_list)

@app.route('/anime/<int:anime_id>')
@login_required
def anime_detail(anime_id):
    try:
        anime_data = load_anime_data()

        # Protection des IDs invalides
        if anime_id <= 0:
            logger.warning(f"Tentative d'accès à un anime avec ID invalide: {anime_id}")
            return render_template('404.html', message="ID d'anime invalide"), 404

        # Find the anime by ID (anime_id est un int, assurons-nous de comparer avec des int)
        anime = next((a for a in anime_data if int(a.get('id', 0)) == anime_id), None)

        if not anime:
            logger.warning(f"Anime avec ID {anime_id} non trouvé")
            return render_template('404.html', message="Anime non trouvé"), 404

        # Vérifier si les saisons et épisodes ont déjà été récupérés pour cet anime
        # Si non, essayer de les récupérer maintenant
        if not anime.get('seasons_fetched', False) and API_IMPORT_SUCCESS:
            try:
                logger.info(f"Récupération des saisons pour l'anime {anime['title']} lors de la consultation")
                # Rechercher l'anime pour avoir l'objet API
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                api = AnimeSama(ANIME_SAMA_BASE_URL)
                search_results = loop.run_until_complete(api.search(anime['title']))

                # Trouver l'anime correspondant dans les résultats
                api_anime = None
                for result in search_results:
                    if result.name.lower() == anime['title'].lower():
                        api_anime = result
                        break

                if api_anime:
                    # Récupérer les saisons et épisodes
                    updated_anime = loop.run_until_complete(fetch_anime_seasons(api_anime, anime))

                    # Mettre à jour l'anime dans la liste
                    for i, a in enumerate(anime_data):
                        if int(a.get('id', 0)) == anime_id:
                            anime_data[i] = updated_anime
                            anime = updated_anime
                            break

                    # Sauvegarder les modifications
                    save_anime_data(anime_data)
                    logger.info(f"Saisons et épisodes récupérés avec succès pour {anime['title']}")
                else:
                    logger.warning(f"Impossible de trouver l'anime {anime['title']} dans l'API")

                loop.close()
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des saisons pour {anime['title']}: {e}")

        # Vérifier si l'anime est dans les favoris de l'utilisateur
        is_favorite = False
        episode_progress = {}
        latest_progress = None

        if current_user.is_authenticated:
            try:
                # Vérifier le statut favori
                favorite = UserFavorite.query.filter_by(
                    user_id=current_user.id,
                    anime_id=anime_id
                ).first()
                is_favorite = favorite is not None

                # Récupérer la progression pour tous les épisodes de cet anime
                progress_data = UserProgress.query.filter_by(
                    user_id=current_user.id,
                    anime_id=anime_id
                ).all()

                # Créer un dictionnaire de progression pour un accès facile dans le template
                for progress in progress_data:
                    key = f"{progress.season_number}_{progress.episode_number}"
                    episode_progress[key] = {
                        'time_position': progress.time_position,
                        'completed': progress.completed,
                        'last_watched': progress.last_watched
                    }

                # Trouver le dernier épisode regardé pour cet anime
                latest_progress = UserProgress.query.filter_by(
                    user_id=current_user.id,
                    anime_id=anime_id,
                    completed=False
                ).order_by(
                    UserProgress.last_watched.desc()
                ).first()
            except Exception as e:
                # En cas d'erreur avec la progression, on continue quand même
                logger.error(f"Erreur lors de la récupération de la progression: {e}")
                episode_progress = {}
                latest_progress = None

        # Vérifiez que les saisons sont complètes
        if not anime.get('seasons'):
            anime['seasons'] = [{
                'season_number': 1,
                'name': "Saison 1",
                'episodes': []
            }]

        return render_template('anime_new.html', 
                              anime=anime, 
                              is_favorite=is_favorite,
                              episode_progress=episode_progress,
                              latest_progress=latest_progress)

    except Exception as e:
        logger.error(f"Erreur lors de l'affichage de l'anime {anime_id}: {e}")
        return render_template('404.html', message="Une erreur s'est produite lors du chargement de l'anime"), 500

@app.route('/player/<int:anime_id>/<int:season_num>/<int:episode_num>')
@login_required
def player(anime_id, season_num, episode_num):
    try:
        # Récupérer éventuellement une source spécifique depuis l'URL
        source_url = request.args.get('source', None)

        # Protection des paramètres invalides
        if anime_id <= 0 or season_num <= 0 or episode_num <= 0:
            logger.warning(f"Tentative d'accès au player avec des paramètres invalides: anime={anime_id}, saison={season_num}, episode={episode_num}")
            return render_template('404.html', message="Paramètres de lecteur invalides"), 404

        anime_data = load_anime_data()

        # Find the anime by ID (même logique de conversion que anime_detail)
        anime = next((a for a in anime_data if int(a.get('id', 0)) == anime_id), None)

        if not anime:
            logger.error(f"Anime with ID {anime_id} not found")
            return render_template('404.html', message="Anime non trouvé"), 404

        # Find the season
        season = next((s for s in anime.get('seasons', []) if s.get('season_number') == season_num), None)

        if not season:
            logger.error(f"Season {season_num} not found for anime {anime_id}")
            return render_template('404.html', message=f"Saison {season_num} non trouvée"), 404

        # Find the episode
        episode = next((e for e in season.get('episodes', []) if e.get('episode_number') == episode_num), None)

        if not episode:
            logger.error(f"Episode {episode_num} not found for anime {anime_id}, season {season_num}")
            return render_template('404.html', message=f"Épisode {episode_num} non trouvé"), 404

        # Generate download URL for Google Drive (avec vérification)
        # Forcer la récupération des URLs de vidéo à chaque fois pour avoir les sources les plus récentes
        video_urls = episode.get('urls', {})
        # Forcer une nouvelle recherche si on a des données existantes avec Vidmoly
        force_refresh = False
        if video_urls:
            for lang, url in video_urls.items():
                if "vidmoly.to" in url:
                    force_refresh = True
                    break

        # Si on n'a pas d'URLs ou on a besoin de rafraîchir pour éviter Vidmoly
        if (not video_urls or force_refresh) and API_IMPORT_SUCCESS:
            try:
                logger.info(f"Récupération des URLs vidéo pour l'anime {anime['title']}, saison {season_num}, épisode {episode_num}")

                # Rechercher l'anime pour avoir l'objet API
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                api = AnimeSama(ANIME_SAMA_BASE_URL)
                search_results = loop.run_until_complete(api.search(anime['title']))

                # Trouver l'anime correspondant
                api_anime = None
                for result in search_results:
                    if result.name.lower() == anime['title'].lower():
                        api_anime = result
                        break

                if api_anime:
                    # Récupérer les saisons
                    seasons = loop.run_until_complete(api_anime.seasons())

                    # Trouver la saison correspondante (en gérant le cas spécial des films avec numéro 99)
                    target_season = None
                    if season_num == 99:  # Cas spécial pour les films
                        for s in seasons:
                            if "Film" in s.name or "Movie" in s.name:
                                target_season = s
                                break
                    else:
                        # Trouver la saison normale par numéro
                        for s in seasons:
                            try:
                                season_match = re.search(r'Saison\s+(\d+)', s.name, re.IGNORECASE)
                                if season_match and int(season_match.group(1)) == season_num:
                                    target_season = s
                                    break
                            except Exception:
                                continue

                    if target_season:
                        # Récupérer les épisodes de cette saison
                        eps = loop.run_until_complete(target_season.episodes())

                        # Trouver l'épisode correspondant
                        if 0 <= episode_num - 1 < len(eps):
                            ep = eps[episode_num - 1]

                            # Récupérer TOUTES les URLs des players disponibles
                            video_urls = {}
                            # List des langues dans l'ordre de priorité
                            langs = ["VF", "VOSTFR"]

                            # Pour chaque langue, récupérer tous les lecteurs disponibles
                            for lang in langs:
                                try:
                                    lang_urls = []

                                    # Récupérer tous les lecteurs pour cette langue
                                    if lang in ep.languages.availables():
                                        players = ep.languages[lang]
                                        if players:
                                            # Trier les players par ordre de préférence
                                            # 1. SendVid
                                            # 2. OneUpload
                                            # 3. MixDrop 
                                            # 4. DoodStream
                                            # 5. Autres non-Vidmoly
                                            # 6. Vidmoly en dernier recours

                                            # Classifier les players
                                            sendvid_urls = [url for url in players if "sendvid.com" in url]
                                            oneupload_urls = [url for url in players if "oneupload.to" in url]
                                            mixdrop_urls = [url for url in players if "mixdrop.co" in url]
                                            dood_urls = [url for url in players if "dood" in url]
                                            other_urls = [url for url in players if "vidmoly.to" not in url and 
                                                         "sendvid.com" not in url and 
                                                         "oneupload.to" not in url and 
                                                         "mixdrop.co" not in url and 
                                                         "dood" not in url]
                                            vidmoly_urls = [url for url in players if "vidmoly.to" in url]

                                            # Ajouter les URLs dans l'ordre de préférence
                                            if sendvid_urls:
                                                lang_urls.extend(sendvid_urls)
                                            if oneupload_urls:
                                                lang_urls.extend(oneupload_urls)
                                            if mixdrop_urls:
                                                lang_urls.extend(mixdrop_urls)
                                            if dood_urls:
                                                lang_urls.extend(dood_urls)
                                            if other_urls:
                                                lang_urls.extend(other_urls)
                                            if vidmoly_urls and not lang_urls:  # Vidmoly en dernier recours seulement
                                                lang_urls.extend(vidmoly_urls)

                                    # S'il y a des URLs pour cette langue, stocker la meilleure
                                    if lang_urls:
                                        video_urls[lang] = lang_urls[0]  # Prendre la meilleure URL (première de la liste triée)
                                        # Enregistrer toutes les URLs alternatives aussi
                                        if not 'all_sources' in episode:
                                            episode['all_sources'] = {}
                                        episode['all_sources'][lang] = lang_urls
                                except Exception as e:
                                    logger.error(f"Erreur lors de la récupération des sources pour {lang}: {e}")

                            # Si aucune URL trouvée, utiliser la méthode simple (fallback)
                            if not video_urls:
                                for lang in langs:
                                    player_url = ep.best([lang])
                                    if player_url:
                                        video_urls[lang] = player_url

                            # Mettre à jour l'épisode avec les URLs
                            if video_urls:
                                episode['urls'] = video_urls

                                # Mettre à jour l'anime dans la liste et sauvegarder
                                for i, a in enumerate(anime_data):
                                    if int(a.get('id', 0)) == anime_id:
                                        anime_data[i] = anime
                                        break

                                save_anime_data(anime_data)
                                logger.info(f"URLs vidéo récupérées avec succès pour {anime['title']}")
                        else:
                            logger.warning(f"Épisode {episode_num} non trouvé dans la saison {target_season.name}")
                    else:
                        logger.warning(f"Saison {season_num} non trouvée pour l'anime {anime['title']}")
                else:
                    logger.warning(f"Anime {anime['title']} non trouvé dans l'API")

                loop.close()
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des URLs vidéo: {e}")

        # Préparer les URLs pour le template
        # Priorité aux lecteurs autres que Vidmoly
        # Puis choisir entre VF et VOSTFR
        video_url = ""
        vidmoly_url = ""  # Pour stocker l'URL Vidmoly comme fallback
        episode_lang = "?"  # Variable pour stocker la langue sélectionnée

        if video_urls and isinstance(video_urls, dict):
            # Récupérer toutes les URLs disponibles et les trier par langue et qualité
            vf_urls = []
            vostfr_urls = []

            # Récupérer toutes les sources alternatives si disponibles
            if episode.get('all_sources'):
                if 'VF' in episode['all_sources']:
                    vf_urls.extend(episode['all_sources']['VF'])
                if 'VOSTFR' in episode['all_sources']:
                    vostfr_urls.extend(episode['all_sources']['VOSTFR'])

            # Ajouter aussi les URLs principales
            if 'VF' in video_urls:
                if video_urls['VF'] not in vf_urls:
                    vf_urls.append(video_urls['VF'])
            if 'VOSTFR' in video_urls:
                if video_urls['VOSTFR'] not in vostfr_urls:
                    vostfr_urls.append(video_urls['VOSTFR'])

            # Filtrer les URLs par source (non-Vidmoly en premier)
            vf_non_vidmoly = [url for url in vf_urls if "vidmoly.to" not in url]
            vf_vidmoly = [url for url in vf_urls if "vidmoly.to" in url]
            vostfr_non_vidmoly = [url for url in vostfr_urls if "vidmoly.to" not in url]
            vostfr_vidmoly = [url for url in vostfr_urls if "vidmoly.to" in url]

            # Sélectionner la meilleure URL disponible (priorité VF et non-Vidmoly)
            video_url = ""
            if vf_non_vidmoly:
                video_url = vf_non_vidmoly[0]
                episode_lang = "VF"
            elif vf_vidmoly:
                video_url = vf_vidmoly[0]
                episode_lang = "VF"
            elif vostfr_non_vidmoly:
                video_url = vostfr_non_vidmoly[0]
                episode_lang = "VOSTFR"
            elif vostfr_vidmoly:
                video_url = vostfr_vidmoly[0]
                episode_lang = "VOSTFR"


        # Si une source spécifique a été demandée via le paramètre d'URL
        if source_url:
            video_url = source_url
            # Détecter la langue utilisée
            if episode.get('all_sources'):
                for lang, urls in episode.get('all_sources', {}).items():
                    if source_url in urls:
                        episode_lang = lang
                        break
            logger.info(f"Utilisation d'une source spécifique: {video_url} (langue: {episode_lang})")

        # Si pas d'URL trouvée via API, essayer l'ancienne méthode
        if not video_url:
            video_url = episode.get('video_url', '')
            logger.info(f"Utilisation de l'URL de secours pour l'épisode: {video_url}")

        # Si toujours rien, on renvoie une erreur
        if not video_url:
            logger.warning(f"URL de vidéo non trouvée pour anime {anime_id}, saison {season_num}, épisode {episode_num}")
            return render_template('404.html', message="Source vidéo non disponible - Nous ajouterons bientôt ce contenu."), 404

        # Mettre à jour les langues disponibles dans l'épisode
        if episode_lang != "?" and episode_lang not in episode.get('languages', []):
            if not episode.get('languages'):
                episode['languages'] = []
            episode['languages'].append(episode_lang)

            # Mettre à jour dans la base de données
            for i, a in enumerate(anime_data):
                if int(a.get('id', 0)) == anime_id:
                    anime_data[i] = anime
                    break
            save_anime_data(anime_data)

        # Préparer l'URL de téléchargement/lecture selon la source
        download_url = "#"

        # Liste des lecteurs par ordre de préférence (sans Vidmoly qui est bloqué)
        # 1. SendVid
        # 2. OneUpload
        # 3. MixDrop
        # 4. DoodStream
        # 5. Google Drive
        # 6. Autres

        if "sendvid.com" in video_url:
            # Pour SendVid, on utilise le format embed
            if "/embed/" not in video_url:
                video_id = video_url.split("/")[-1].split(".")[0]
                download_url = f"https://sendvid.com/embed/{video_id}"
            else:
                download_url = video_url

        elif "oneupload.to" in video_url:
            # Pour OneUpload, on s'assure d'avoir le format embed
            if "/embed-" not in video_url:
                video_id = video_url.split("/")[-1].split(".")[0]
                download_url = f"https://oneupload.to/embed-{video_id}.html"
            else:
                download_url = video_url

        elif "mixdrop.co" in video_url:
            # Pour MixDrop, on s'assure d'avoir le format embed
            if "/e/" not in video_url:
                video_id = video_url.split("/")[-1]
                download_url = f"https://mixdrop.co/e/{video_id}"
            else:
                download_url = video_url

        elif "dood" in video_url:  # doodstream, dood.to, etc.
            # Pour DoodStream, on s'assure d'avoir le format embed
            if "/e/" not in video_url:
                parts = video_url.split("/")
                video_id = parts[-1]
                domain = ".".join(parts[2].split(".")[-2:])
                download_url = f"https://dood.{domain}/e/{video_id}"
            else:
                download_url = video_url

        elif "drive.google.com" in video_url:
            # Pour Google Drive, on extrait l'ID et on construit l'URL embed
            file_id = extract_drive_id(video_url)
            if file_id:
                download_url = f"https://drive.google.com/file/d/{file_id}/preview"

        elif "vidmoly.to" in video_url:
            # Pour Vidmoly, on utilise l'URL directe sans modification
            logger.warning(f"Vidmoly détecté, utilisation directe: {video_url}")
            download_url = video_url

        else:
            # Pour les autres sources, on utilise l'URL directement
            download_url = video_url

        # Si l'URL est toujours invalide, on renvoie une erreur
        if not download_url or download_url == "#":
            logger.warning(f"URL de vidéo invalide pour anime {anime_id}, saison {season_num}, épisode {episode_num}: {video_url}")
            return render_template('404.html', message="Source vidéo non disponible - Nous ajouterons bientôt ce contenu."), 404

        logger.debug(f"Generated download URL: {download_url}")

        # Si l'utilisateur est connecté, récupérer sa progression et statut favori
        time_position = 0
        is_favorite = False

        if current_user.is_authenticated:
            try:
                # Récupérer la progression
                progress = UserProgress.query.filter_by(
                    user_id=current_user.id,
                    anime_id=anime_id,
                    season_number=season_num,
                    episode_number=episode_num
                ).first()

                if progress:
                    time_position = progress.time_position

                # Vérifier si l'anime est dans les favoris
                favorite = UserFavorite.query.filter_by(
                    user_id=current_user.id,
                    anime_id=anime_id
                ).first()

                is_favorite = favorite is not None
            except Exception as e:
                # En cas d'erreur avec la progression, continuer quand même
                logger.error(f"Erreur lors de la récupération de la progression: {e}")
                time_position = 0
                is_favorite = False

        return render_template('player.html', 
                            anime=anime, 
                            season=season, 
                            episode=episode, 
                            download_url=download_url,
                            time_position=time_position,
                            is_favorite=is_favorite)

    except Exception as e:
        logger.error(f"Erreur lors du chargement du lecteur pour anime {anime_id}, saison {season_num}, épisode {episode_num}: {e}")
        return render_template('404.html', message="Une erreur s'est produite lors du chargement du lecteur"), 500

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.args.get('password', '')
        admin_password = os.environ.get('ADMIN_PASSWORD', 'admin1234')  # Fallback to default for development

        if password == admin_password:
            session['admin'] = True
            return redirect(url_for('admin_panel'))
        else:
            return render_template('admin_login.html', error="Invalid password")

    return render_template('admin_login.html')

@app.route('/admin')
def admin_panel():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    return render_template('admin.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('index'))

@app.route('/admin/add_anime', methods=['POST'])
def add_anime():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    # Get form data
    title = request.form.get('title')
    description = request.form.get('description')
    image = request.form.get('image')
    genres = [g.strip().lower() for g in request.form.get('genres', '').split(',')]
    rating = float(request.form.get('rating', 0))
    featured = request.form.get('featured') == 'yes'
    episode_count = int(request.form.get('episode_count', 1))

    # Load existing anime data
    anime_data = load_anime_data()

    # Generate a new ID (max + 1)
    new_id = 1
    if anime_data:
        new_id = max(a.get('id', 0) for a in anime_data) + 1

    # Create episodes list
    episodes = []
    for i in range(1, episode_count + 1):
        episodes.append({
            'episode_number': i,
            'title': request.form.get(f'episode_title_{i}'),
            'description': request.form.get(f'episode_description_{i}'),
            'video_url': request.form.get(f'episode_video_{i}')
        })

    # Create the new anime object
    new_anime = {
        'id': new_id,
        'title': title,
        'description': description,
        'image': image,
        'genres': genres,
        'rating': rating,
        'featured': featured,
        'seasons': [
            {
                'season_number': 1,
                'episodes': episodes
            }
        ]
    }

    # Add to the anime data and save
    anime_data.append(new_anime)
    success = save_anime_data(anime_data)

    if success:
        return render_template('admin.html', message="Anime added successfully!", success=True)
    else:
        return render_template('admin.html', message="Error adding anime. Please try again.", success=False)

@app.route('/categories')
@login_required
def categories():
    anime_data = load_anime_data()
    genres = get_all_genres()

    # Create dictionary of genres and their anime
    genres_dict = {genre: [] for genre in genres}

    for anime in anime_data:
        for genre in anime.get('genres', []):
            if genre.lower() in genres_dict:
                genres_dict[genre.lower()].append(anime)

    return render_template('categories.html', all_anime=anime_data, genres=genres, genres_dict=genres_dict)

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Si l'utilisateur est déjà connecté, rediriger vers l'accueil
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    # Traitement du formulaire de connexion
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Vérification des identifiants
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            # Mettre à jour la date de dernière connexion
            user.last_login = datetime.datetime.utcnow()
            db.session.commit()

            # Connecter l'utilisateur
            login_user(user)
            logger.debug(f"User {username} logged in successfully")

            # Redirection vers la page demandée ou l'accueil
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            else:
                return redirect(url_for('index'))
        else:
            logger.debug(f"Failed login attempt for user {username}")
            flash('Nom d\'utilisateur ou mot de passe incorrect.', 'danger')

    return render_template('login_new.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    # Si l'utilisateur est déjà connecté, rediriger vers l'accueil
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    # Traitement du formulaire d'inscription
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        # Vérifier si les mots de passe correspondent
        if password != confirm_password:
            flash('Les mots de passe ne correspondent pas.', 'danger')
            return render_template('register_new.html')

        # Vérifier si le nom d'utilisateur existe déjà
        existing_user = User.query.filter_by(username=username).first()

        if existing_user:
            flash('Ce nom d\'utilisateur est déjà pris.', 'danger')
        else:
            # Créer un nouvel utilisateur
            new_user = User(username=username)
            new_user.set_password(password)

            # Enregistrer en base de données
            db.session.add(new_user)
            db.session.commit()

            logger.debug(f"New user registered: {username}")
            flash('Votre compte a été créé avec succès! Vous pouvez maintenant vous connecter.', 'success')
            return redirect(url_for('login'))

    return render_template('register_new.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Vous avez été déconnecté.', 'info')
    return redirect(url_for('index'))

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        # Récupérer les données du formulaire
        current_password = request.form.get('current_password')
        new_username = request.form.get('new_username')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        # Vérifier le mot de passe actuel
        if not current_user.check_password(current_password):
            flash('Mot de passe actuel incorrect', 'danger')
            return redirect(url_for('settings'))

        # Mettre à jour le nom d'utilisateur si fourni
        if new_username and new_username != current_user.username:
            # Vérifier si le nom d'utilisateur existe déjà
            if User.query.filter_by(username=new_username).first():
                flash('Ce nom d\'utilisateur est déjà pris', 'danger')
                return redirect(url_for('settings'))
            current_user.username = new_username

        # Mettre à jour le mot de passe si fourni
        if new_password:
            if new_password != confirm_password:
                flash('Les nouveaux mots de passe ne correspondent pas', 'danger')
                return redirect(url_for('settings'))
            current_user.set_password(new_password)

        # Sauvegarder les modifications
        db.session.commit()
        flash('Paramètres mis à jour avec succès', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html')

@app.route('/profile')
@login_required
def profile():
    # Récupérer les animes en cours de visionnage
    progress_data = UserProgress.query.filter_by(user_id=current_user.id).order_by(UserProgress.last_watched.desc()).all()

    # Récupérer les détails des animes
    anime_data = load_anime_data()
    watching_anime = []

    for progress in progress_data:
        anime = next((a for a in anime_data if int(a.get('id', 0)) == progress.anime_id), None)
        if anime:
            # Trouver la saison et l'épisode
            season = next((s for s in anime.get('seasons', []) if s.get('season_number') == progress.season_number), None)
            episode = None
            if season:
                episode = next((e for e in season.get('episodes', []) if e.get('episode_number') == progress.episode_number), None)

            watching_anime.append({
                'progress': progress,
                'anime': anime,
                'season': season,
                'episode': episode
            })

    # Récupérer les favoris
    favorites = UserFavorite.query.filter_by(user_id=current_user.id).all()
    favorite_anime = []

    for favorite in favorites:
        anime = next((a for a in anime_data if int(a.get('id', 0)) == favorite.anime_id), None)
        if anime:
            favorite_anime.append(anime)

    return render_template('profile_new.html', 
                          watching_anime=watching_anime, 
                          favorite_anime=favorite_anime)

@app.route('/remove-from-watching', methods=['POST'])
@login_required
def remove_from_watching():
    try:
        if request.method == 'POST':
            anime_id = request.form.get('anime_id', type=int)
            if anime_id:
                # Supprimer toutes les entrées de progression pour cet anime
                UserProgress.query.filter_by(
                    user_id=current_user.id,
                    anime_id=anime_id
                ).delete()

                db.session.commit()
                return jsonify({'success': True})

        return jsonify({'success': False, 'error': 'ID anime manquant'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/save-progress', methods=['POST'])
@login_required
def save_progress():
    if request.method == 'POST':
        anime_id = request.form.get('anime_id', type=int)
        season_number = request.form.get('season_number', type=int)
        episode_number = request.form.get('episode_number', type=int)
        time_position = request.form.get('time_position', type=float)
        completed = request.form.get('completed') == 'true'

        # Chercher une entrée existante
        progress = UserProgress.query.filter_by(
            user_id=current_user.id,
            anime_id=anime_id,
            season_number=season_number,
            episode_number=episode_number
        ).first()

        if progress:
            # Mettre à jour l'entrée existante
            progress.time_position = time_position
            progress.completed = completed
            progress.last_watched = datetime.datetime.utcnow()
        else:
            # Créer une nouvelle entrée
            progress = UserProgress(
                user_id=current_user.id,
                anime_id=anime_id,
                season_number=season_number,
                episode_number=episode_number,
                time_position=time_position,
                completed=completed,
                last_watched=datetime.datetime.utcnow()
            )
            db.session.add(progress)

        if progress:
            # Mettre à jour l'entrée existante, mais conserver le statut "terminé" si déjà marqué
            if not progress.completed:  # Si l'épisode n'était pas déjà terminé
                progress.time_position = time_position
                progress.completed = completed
            else:
                # Si l'épisode était déjà marqué comme terminé, ne le remettre "en cours" que si explicitement demandé
                if not completed:
                    # On ne remet pas à "non terminé" un épisode déjà marqué terminé
                    # sauf si le temps est revenu en arrière (par ex. début de l'épisode)
                    if time_position < progress.time_position * 0.5:  # Si position actuelle < 50% de la position sauvegardée
                        progress.completed = False
                        progress.time_position = time_position

            # Toujours mettre à jour la date de dernière visualisation
            progress.last_watched = datetime.datetime.utcnow()
        else:
            # Créer une nouvelle entrée
            progress = UserProgress(
                user_id=current_user.id,
                anime_id=anime_id,
                season_number=season_number,
                episode_number=episode_number,
                time_position=time_position,
                completed=completed
            )
            db.session.add(progress)

        db.session.commit()
        return {'success': True}, 200

    return {'success': False, 'error': 'Invalid request'}, 400

@app.route('/toggle-favorite', methods=['POST'])
@login_required
def toggle_favorite():
    if request.method == 'POST':
        anime_id = request.form.get('anime_id', type=int)

        # Vérifier si l'anime est déjà dans les favoris
        favorite = UserFavorite.query.filter_by(
            user_id=current_user.id, 
            anime_id=anime_id
        ).first()

        if favorite:
            # Supprimer des favoris
            db.session.delete(favorite)
            db.session.commit()
            return {'success': True, 'action': 'removed'}, 200
        else:
            # Ajouter aux favoris
            favorite = UserFavorite(
                user_id=current_user.id,
                anime_id=anime_id
            )
            db.session.add(favorite)
            db.session.commit()
            return {'success': True, 'action': 'added'}, 200

    return {'success': False, 'error': 'Invalid request'}, 400
@app.route('/documentation')
@login_required
def documentation():
    return render_template('documentation.html')

# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    # Choisir le template en fonction de l'authentification
    if current_user.is_authenticated:
        return render_template('404.html'), 404
    else:
        return render_template('404_public.html'), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {e}")
    # Choisir le template en fonction de l'authentification
    if current_user.is_authenticated:
        return render_template('404.html'), 500
    else:
        return render_template('404_public.html'), 500

# Créer les tables au démarrage
with app.app_context():
    try:
        db.create_all()
        logger.info("Database tables created successfully!")
    except Exception as e:
        logger.error(f"Error creating database tables: {e}")

@app.route('/download-episode/<int:anime_id>/<int:season_num>/<int:episode_num>')
@login_required
def download_episode(anime_id, season_num, episode_num):
    try:
        from anime_sama_api.downloader import download
        from pathlib import Path

        logger.info(f"Début du téléchargement - Anime: {anime_id}, Saison: {season_num}, Episode: {episode_num}")

        # Récupérer l'épisode depuis l'API
        anime_data = load_anime_data()
        anime = next((a for a in anime_data if int(a.get('id', 0)) == anime_id), None)
        if not anime:
            return jsonify({'error': 'Anime non trouvé'}), 404

        # Obtenir l'URL vidéo directement depuis les URLs stockées
        season = next((s for s in anime.get('seasons', []) if s.get('season_number') == season_num), None)
        if not season:
            return jsonify({'error': 'Saison non trouvée'}), 404

        episode = next((e for e in season.get('episodes', []) if e.get('episode_number') == episode_num), None)
        if not episode:
            return jsonify({'error': 'Episode non trouvé'}), 404

        video_url = episode.get('urls', {}).get('VF', '') or episode.get('urls', {}).get('VOSTFR', '')
        if not video_url:
            return jsonify({'error': 'URL vidéo non trouvée'}), 404

        # Créer le dossier de téléchargement
        download_dir = Path("downloads")
        
        try:
            # Utiliser le downloader de l'API
            success = download(episode, download_dir, prefer_languages=["VF", "VOSTFR"])
            if success:
                return jsonify({'success': True}), 200
            else:
                return jsonify({'error': 'Échec du téléchargement'}), 500
        except Exception as e:
            logger.error(f"Erreur lors du téléchargement: {str(e)}")
            return jsonify({'error': f'Erreur lors du téléchargement: {str(e)}'}), 500

        # Récupérer l'information de l'anime
        anime_data = load_anime_data()
        anime = next((a for a in anime_data if int(a.get('id', 0)) == anime_id), None)

        if not anime:
            logger.error("Anime non trouvé")
            return jsonify({'error': 'Anime non trouvé'}), 404

        # Trouver la saison et l'épisode
        season = next((s for s in anime.get('seasons', []) if s.get('season_number') == season_num), None)
        if not season:
            logger.error("Saison non trouvée")
            return jsonify({'error': 'Saison non trouvée'}), 404

        episode = next((e for e in season.get('episodes', []) if e.get('episode_number') == episode_num), None)
        if not episode:
            logger.error("Episode non trouvé")
            return jsonify({'error': 'Episode non trouvé'}), 404

        # Obtenir l'URL de la vidéo
        video_url = episode.get('urls', {}).get('VOSTFR', '') or episode.get('urls', {}).get('VF', '')
        if not video_url:
            logger.error("URL de la vidéo non trouvée")
            return jsonify({'error': 'URL de la vidéo non trouvée'}), 404

        # Créer le dossier de téléchargement
        download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads', anime['title'], f"Saison {season_num}")
        os.makedirs(download_dir, exist_ok=True)

        # Configuration de yt-dlp
        ydl_opts = {
            'format': 'best',
            'outtmpl': os.path.join(download_dir, f'Episode {episode_num}.%(ext)s'),
            'quiet': True,
            'no_warnings': True
        }

        with YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Tentative de téléchargement depuis: {video_url}")
            ydl.download([video_url])

        # Trouver le fichier téléchargé
        downloaded_file = None
        for file in os.listdir(download_dir):
            if file.startswith(f'Episode {episode_num}.'):
                downloaded_file = os.path.join(download_dir, file)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            logger.error("Fichier non trouvé après téléchargement")
            return jsonify({'error': 'Erreur lors du téléchargement'}), 500

        return send_file(
            downloaded_file,
            as_attachment=True,
            download_name=f"{anime['title']} - S{season_num}E{episode_num}{os.path.splitext(downloaded_file)[1]}"
        )

    except Exception as e:
        logger.error(f"Erreur lors du téléchargement: {str(e)}")
        return jsonify({'error': f"Erreur lors du téléchargement: {str(e)}"}), 500
    try:
        # Récupérer l'information de l'anime
        anime_data = load_anime_data()
        anime = next((a for a in anime_data if int(a.get('id', 0)) == anime_id), None)

        if not anime:
            flash("Anime non trouvé", "error")
            return jsonify({'error': 'Anime non trouvé'}), 404

        # Vérifier si l'API est disponible
        if not API_IMPORT_SUCCESS:
            return jsonify({'error': 'API non disponible'}), 503

        # Créer un objet Episode pour l'API
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        api = AnimeSama(ANIME_SAMA_BASE_URL)
        search_results = loop.run_until_complete(api.search(anime['title']))

        if not search_results:
            return jsonify({'error': 'Anime non trouvé dans l\'API'}), 404

        # Trouver l'anime correspondant
        api_anime = None
        for result in search_results:
            if result.name.lower() == anime['title'].lower():
                api_anime = result
                break

        if not api_anime:
            return jsonify({'error': 'Anime non trouvé dans l\'API'}), 404

        # Récupérer les saisons
        seasons = loop.run_until_complete(api_anime.seasons())
        target_season = None

        # Trouver la bonne saison
        if season_num == 99:  # Cas spécial pour les films
            for s in seasons:
                if "Film" in s.name or "Movie" in s.name:
                    target_season = s
                    break
        else:
            for s in seasons:
                try:
                    season_match = re.search(r'Saison\s+(\d+)', s.name, re.IGNORECASE)
                    if season_match and int(season_match.group(1)) == season_num:
                        target_season = s
                        break
                except Exception:
                    continue

        if not target_season:
            return jsonify({'error': 'Saison non trouvée'}), 404

        # Récupérer les épisodes
        episodes = loop.run_until_complete(target_season.episodes())

        if not episodes or episode_num > len(episodes):
            return jsonify({'error': 'Épisode non trouvé'}), 404

        # Récupérer l'épisode à télécharger
        episode_obj = episodes[episode_num-1]  # Ajuster l'index

        # Configurer les langues préférées
        prefer_langs = ["VF", "VOSTFR"]

        # Créer le dossier de téléchargement
        download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
        os.makedirs(download_dir, exist_ok=True)

        # Lancer le téléchargement
        from anime_sama_api.downloader import download
        from pathlib import Path

        download_path = Path(download_dir)
        try:
            success = download(
                episode_obj, 
                download_path,
                prefer_languages=prefer_langs,
                concurrent_fragment_downloads=3
            )

            if success:
                # Construire le chemin du fichier téléchargé
                expected_file = os.path.join(
                    download_dir, 
                    anime['title'], 
                    f"Saison {season_num}", 
                    f"Episode {episode_num}.mp4"
                )

                if os.path.exists(expected_file):
                    # Envoyer le fichier
                    return send_file(
                        expected_file,
                        as_attachment=True,
                        download_name=f"{anime['title']} - S{season_num}E{episode_num}.mp4"
                    )
                else:
                    return jsonify({'error': 'Fichier non trouvé après téléchargement'}), 404

            else:
                return jsonify({'error': 'Échec du téléchargement'}), 500

        except Exception as e:
            logger.error(f"Erreur lors du téléchargement: {e}")
            return jsonify({'error': f'Erreur lors du téléchargement: {str(e)}'}), 500

        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Erreur lors du téléchargement: {e}")
        return jsonify({'error': str(e)}), 500

    except Exception as e:
        logger.error(f"Erreur lors du téléchargement: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)