# Consignabot

Consignabot est un projet pour les clubs de robotiques de l'ÉTS. Il s'agit d'un bot Discord qui permet de gérer les rappels d'évenements en lien avec le ramassage des canettes. Le bot permet aux membres du club de créer des rappels pour les événements de ramassage de canettes, et de recevoir des notifications lorsque ces événements approchent.

## Fonctionnalités
- Création de rappels pour les événements de ramassage de canettes
- Notifications pour les rappels créés
- Assignation de responsables pour les événements
- Affichage de la liste des rappels créés
- Suppression de rappels

## Installation et utilisation

### Méthode traditionnelle
Clonez le dépôt GitHub du projet, ajoutez le token Discord et exécutez le fichier Consignabot.py :
```
git clone https://github.com/consignactionets/Consignabot
cd Consignabot
echo "<your token>" > token.txt
python Consignabot.py
```

### Méthode Docker (recommandée)
Clonez le dépôt et utilisez Docker :
```
git clone https://github.com/consignactionets/Consignabot
cd Consignabot
```

Créez un fichier `.env` avec votre token Discord :
```
DISCORD_TOKEN=your_discord_bot_token_here
```

Puis lancez le bot avec Docker Compose :
```
docker-compose up -d
```

Ou avec Docker directement :
```
docker build -t consignabot .
docker run -e DISCORD_TOKEN=your_token_here -v $(pwd)/data:/app/data consignabot
```

Le répertoire `data` sera monté en volume pour persister les données des séries d'événements.

## CI/CD

Ce projet utilise GitHub Actions pour automatiser la construction de l'image Docker :

- **Déclenchement** : Sur chaque push vers la branche `main`, et sur les pull requests
- **Construction** : L'image Docker est construite et testée automatiquement
- **Publication** : L'image est publiée sur GitHub Container Registry (GHCR) pour les pushes directs
- **Cache** : Utilise le cache GitHub Actions pour accélérer les builds

L'image Docker construite peut être trouvée dans l'onglet "Packages" de ce dépôt GitHub.

# Auteurs
Ewan (Nawaque sur GitHub et Discord) (SONIA)