# cozytouch-homekit

Exposer une PAC **Atlantic Alféa Extensa AI Duo** (ou autre appareil Atlantic
piloté par **Cozytouch**) dans **Apple HomeKit**, en local, sur un
**Raspberry Pi Zero W**, via un **bridge HAP** (un accessoire par fonction,
chacun rangeable dans sa propre pièce).

Les données sont lues depuis l'API cloud **Overkiz / Cozytouch** via
[`pyoverkiz`](https://github.com/iMicknl/python-overkiz-api) et publiées en
HomeKit via [HAP-python](https://github.com/ikalchev/HAP-python).

> **État — lecture seule, auto-détectée.** `configure` détecte les capacités
> de ton compte (capteurs de température, d'humidité, consignes…) et te laisse
> cocher ce que tu exposes ; chaque choix = un accessoire du bridge. Le contrôle
> (thermostat, boost ECS) est prévu en V2 sur le même principe.

---

## En trois étapes

```bash
git clone https://github.com/AlexBtlle/cozytouch-homekit.git
cd cozytouch-homekit

# 1) Installer (deps système, venv, requirements piwheels, service systemd)
./install.sh

# 2) Configurer  (lancé par install.sh, re-lançable à tout moment)
python -m cozytouch_homekit configure

# 3) Appairer : démarrer le service, scanner le QR code dans l'app Maison
sudo systemctl restart cozytouch-homekit
journalctl -u cozytouch-homekit -f
```

---

## Architecture : **bridge** (un accessoire par fonction)

- La PAC = **1 appareil physique** dont chaque fonction (capteur ambiant,
  capteur extérieur, ECS, thermostat…) est exposée comme un **accessoire HAP
  distinct**, hébergé par un **bridge** (`CozytouchBridge`, hérite de `Bridge`).
- **Pourquoi un bridge** (et plus un accessoire standalone) : dans HomeKit,
  l'affectation à une **pièce** se fait par accessoire, pas par service. Comme
  les sondes sont physiquement à des endroits différents (intérieur / extérieur
  / ballon), il faut des accessoires séparés pour les ranger dans des pièces
  différentes. C'est exactement le rôle d'un bridge : un seul appairage, N
  accessoires indépendants.
- **AID stables entre redémarrages** : le bridge = AID `1` ; chaque accessoire
  enfant reçoit un AID **déterministe** dérivé de sa position dans
  `config.FEATURE_ORDER` (`feature_aid()`), stable même si on active/désactive
  des fonctions. Les IID sont stables au sein de chaque accessoire enfant.
- **Ajouter une fonction** après coup : nouvel accessoire = nouvelle tuile, sans
  ré-appairage. **Retirer** une fonction déjà appairée peut laisser une tuile
  fantôme (cf. plus bas).

---

## Étape 0 — Découverte (À FAIRE AVANT le mapping)

Les noms de states Overkiz **dépendent de votre firmware**. Ne codez pas le
mapping en aveugle : dumpez d'abord votre machine.

```bash
python -m cozytouch_homekit explore                 # dump complet -> explore_dump.json
python -m cozytouch_homekit explore --anonymize     # masque l'ID de passerelle (pour commit)
```

Le dump liste tous les devices, leurs `states` (avec valeurs) et `commands`.
Repérez :

- le `device_url` de la **PAC** (et du composant **ECS** pour le Duo) ;
- les noms de states de température (ex. `core:TemperatureState`,
  `core:OutsideTemperatureState`, …).

Reportez-les dans `config.yaml` (via `configure` pour les URLs, ou en éditant
les sections `device` et `sensors`).

> ⚠️ Le dump **non anonymisé** contient l'ID de votre passerelle : il est
> `.gitignore`. Utilisez `--anonymize` avant tout commit de référence.

---

## Configuration

`configure` écrit `config.yaml` (gitignored, `chmod 0600`). Il gère :

1. **identifiants Cozytouch** (login + mot de passe + serveur) ;
2. **connexion au compte → détection automatique** des capacités exposables ;
3. **cases à cocher** : tu choisis ce que tu veux exposer à HomeKit.

Pas de mapping à écrire à la main : `configure` interroge ton compte Overkiz,
reconnaît les states mappables (températures, humidité, consignes…) et te
présente la liste. Chaque capacité cochée devient un **accessoire du bridge**
(sa propre tuile, rangeable dans sa propre pièce). Le résultat est stocké dans
`config.yaml` sous `exposed:` (cf. [`config.example.yaml`](config.example.yaml)) :

```yaml
exposed:
  - aid: 2
    type: "temperature_sensor"
    name: "Température ambiante"
    device_url: "io://…/…"
    state: "core:TemperatureState"
```

Types reconnus actuellement : `temperature_sensor`, `temperature_setpoint`
(consigne, lecture), `humidity_sensor`. Le contrôle (thermostat, boost ECS…)
viendra en V2 sur le même principe (détection → choix).

### ⚠️ Changer la structure APRÈS appairage

- **Avant le 1er appairage** : aucun souci, configurez librement.
- **En cours de vie** : ré-exécuter `configure` puis **redémarrer** le service →
  HAP-python republie en incrémentant le **config number (c#)** → HomeKit relit.
- **Ajouter** un accessoire après coup : propre (nouvelle tuile).
- **Retirer** un accessoire déjà appairé : peut laisser une **tuile fantôme**.
  Dans le pire cas, retirer puis ré-ajouter le bridge dans Maison.
- Les **AID restent stables** (figés par entrée `exposed`) → pas de réorganisation
  des accessoires conservés.

---

## Source de données & robustesse

- **Cloud uniquement** : Cozytouch n'expose pas d'API locale → dépendance
  Internet assumée.
- **Rate-limit Overkiz** : polling **espacé (120 s par défaut ; 90–300 s
  conseillé, 30 s minimum)**, **backoff exponentiel** sur erreur (plafond configurable),
  **refresh de session** automatique si le token expire.
- Si l'API ne répond pas, les caractéristiques passent en **`StatusFault`**
  (indisponible) plutôt que de figer une valeur trompeuse.
- `pyoverkiz` (async/aiohttp) et HAP-python tournent sur **le même event
  loop** asyncio : la boucle de polling est la coroutine `run()` de
  l'accessoire, schedulée par l'`AccessoryDriver`. Pas de thread bloquant.

---

## Matériel testé & ARMv6

- Cible : **Raspberry Pi Zero W v1** (ARMv6, 1 GHz, 512 Mo) + **Raspberry Pi
  OS**. Raspberry Pi OS est requis pour que **piwheels** soit actif.
  - **Bookworm** → Python 3.11 (wheels `cp311`)
  - **Trixie** → Python 3.13 (wheels `cp313`)
  - ⚠️ Les wheels piwheels sont taguées par version de Python : sur Trixie, une
    version épinglée qui n'a qu'une wheel `cp311` retombera sur une compilation
    depuis les sources. Vérifier la dispo `cp313`/`armv6l` sur piwheels.
- **piwheels** fournit des wheels précompilées ARMv6 → `pip` récupère des
  binaires au lieu de compiler la plupart des paquets depuis les sources.
- ⚠️ **Appairage lent** : le handshake crypto sur single-core ARMv6 peut
  prendre **plusieurs minutes**. Ce n'est pas un plantage — laissez tourner.

### ⚠️ `cryptography` sur ARMv6 : via apt, pas via pip

C'est **le** point dur de l'install, et il n'y a pas de wheel pip miracle :

- `cryptography ≥ 3.5` est un paquet **Rust**. **piwheels ne build pas de
  wheel Rust pour armv6** → `pip install cryptography` retombe sur une
  compilation depuis les sources → `error: can't find Rust compiler` (et même
  avec Rust : des heures de compilation + risque d'OOM sur 512 Mo).
- Les versions pré-Rust (`≤ 3.4.8`) n'ont pas de wheel `cp313` et ne tournent
  pas sous Python 3.13. **Aucune version de `cryptography` n'est donc
  pip-installable sur Pi Zero + Trixie.**

**Solution (gérée par `install.sh`)** : installer `cryptography` depuis
**Debian** (`apt install python3-cryptography`, précompilé par Debian, Rust
inclus) et créer le venv avec **`--system-site-packages`** pour qu'il le voie.
Idem pour `zeroconf` (`python3-zeroconf`). `cryptography` est donc
**volontairement absent** de `requirements.txt`.

### Reproductibilité de l'install

`requirements.txt` est **épinglé** (`==`) pour les paquets pip. Les paquets à
composants natifs lourds (`cryptography`, `zeroconf`) viennent d'**apt** et
suivent la version de Debian Trixie. Tout le reste récupère des wheels
`cp313`/`armv6l` sur piwheels.

> Validé sur **Pi Zero W v1 / Raspberry Pi OS Trixie (Python 3.13)** : toutes
> les deps pip se posent en wheels, `cryptography`/`zeroconf` viennent d'apt,
> aucune compilation sur la carte.

---

## Commandes

| Commande | Rôle |
|---|---|
| `python -m cozytouch_homekit configure` | Menu : identifiants + choix des fonctions |
| `python -m cozytouch_homekit explore`   | Dump des devices/states/commands (Étape 0) |
| `python -m cozytouch_homekit run`       | Démarre le service HomeKit (utilisé par systemd) |

Service systemd : `cozytouch-homekit` (`Restart=on-failure`, démarrage au boot,
user non-privilégié). Logs : `journalctl -u cozytouch-homekit -f`.

---

## Page de statut

Le service expose une **mini page web** sur `http://<pi>.local:8080/`
(configurable / désactivable sous `web:` dans `config.yaml`). Elle affiche :

- **Appairage HomeKit** : QR code + PIN tant que non appairé, sinon « appairé » ;
- **Raspberry Pi** : hostname, IP, température CPU, uptime, charge, RAM ;
- **Overkiz** : connecté ou non, heure de la dernière lecture, dernière erreur,
  intervalle de polling ;
- **Accessoires exposés** : nom, type, valeur courante, disponibilité.

Recharge la page pour actualiser ; un endpoint JSON est dispo sur
`/api/status`. Servie en interne par **aiohttp** (déjà tiré par `pyoverkiz`) et
**pyqrcode** → **pas de dépendance supplémentaire**.

> ⚠️ Elle montre le PIN d'appairage et des infos système : à n'exposer que sur
> un **réseau de confiance**. Elle n'affiche **jamais** le mot de passe Cozytouch.

---

## Secrets

`config.yaml`, `.env`, `accessory.state` et les dumps `explore_dump*.json` sont
`.gitignore`. **Ne les commitez jamais** : ils contiennent identifiants
Cozytouch, clés d'appairage HAP et/ou l'ID de votre passerelle.

---

## Feuille de route

- **V1** : install reproductible + `configure` + capteurs de température +
  QR code + polling espacé + systemd. ✅ Validé Pi Zero W v1 / Trixie.
- **V1.5 (actuel)** : passage en **bridge** (un accessoire par fonction →
  rangeable par pièce).
- **V2** : écriture — `Thermostat`/`HeaterCooler` (consigne + mode), `Switch`
  boost ECS, sur la base du dump complet (states + commands). ⚠️ HomeKit n'a pas
  de type « PAC pilotée par loi d'eau » : mapping à assumer et documenter.
- **V3 (option)** : cache offline, healthcheck, procédure de nettoyage des
  tuiles fantômes.

## Licence

GPL-3.0 — voir [LICENSE](LICENSE).
