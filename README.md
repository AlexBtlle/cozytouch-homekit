# cozytouch-homekit

Exposer une PAC **Atlantic Alféa Extensa AI Duo** (ou autre appareil Atlantic
piloté par **Cozytouch**) dans **Apple HomeKit** comme **accessoire natif**
(pas un bridge), en local, sur un **Raspberry Pi Zero W**.

Les données sont lues depuis l'API cloud **Overkiz / Cozytouch** via
[`pyoverkiz`](https://github.com/iMicknl/python-overkiz-api) et publiées en
HomeKit via [HAP-python](https://github.com/ikalchev/HAP-python).

> **V1 (MVP) — lecture seule.** Capteurs de température (ambiante, extérieure,
> ECS) exposés comme services `TemperatureSensor`. Le contrôle (thermostat,
> boost ECS) est prévu en V2.

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

## Architecture : accessoire **standalone**, pas un bridge

- La PAC = **1 appareil physique = 1 accessoire HAP = N services**.
- L'accessoire (`CozytouchAccessory`) hérite de `Accessory` (**pas** `Bridge`)
  et ajoute ses services via `add_preload_service`.
- Dans l'app Maison : chaque capteur = sa propre tuile, regroupées sous
  l'appareil « PAC Atlantic », **un seul appairage**, aucun pont parent.
- **AID/IID stables entre redémarrages** : l'AID d'un accessoire standalone
  vaut `1`, et les services sont ajoutés dans un **ordre canonique fixe**
  (`config.FEATURE_ORDER`) → les IID ne bougent pas tant que la liste des
  services activés ne change pas.

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
2. **deviceURL** de la PAC / ECS ;
3. **choix des fonctions** exposées à HomeKit (cases à cocher).

Voir [`config.example.yaml`](config.example.yaml) pour toutes les clés. Extrait
des feature flags :

```yaml
features:
  temp_ambiante:   true
  temp_exterieure: true
  temp_ecs:        false
  thermostat:      false   # V2 — non implémenté
  boost_ecs:       false   # V2 — non implémenté
```

Un flag `false` ⇒ service **absent** de l'accessoire ⇒ **aucune tuile** dans Maison.

### ⚠️ Changer la structure APRÈS appairage

- **Avant le 1er appairage** : aucun souci, configurez librement.
- **En cours de vie** : modifier les services d'un accessoire déjà appairé →
  1. `configure`, 2. **redémarrer** le service, 3. HAP-python republie en
  incrémentant le **config number (c#)** → HomeKit relit la structure.
- **Activer** un service après coup : propre (nouvelle tuile).
- **Désactiver** un service déjà appairé : peut laisser une **tuile fantôme**.
  Dans le pire cas, retirer puis ré-ajouter l'accessoire dans Maison.

> Astuce : si vous figez la liste (p. ex. 2 capteurs) **dès l'installation**,
> vous évitez complètement ce piège.

---

## Source de données & robustesse

- **Cloud uniquement** : Cozytouch n'expose pas d'API locale → dépendance
  Internet assumée.
- **Rate-limit Overkiz** : polling **espacé (45 s par défaut, 30–60 s
  recommandé)**, **backoff exponentiel** sur erreur (plafond configurable),
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
  binaires au lieu de compiler `cryptography` & co depuis les sources.
- ⚠️ **Appairage lent** : le handshake crypto sur single-core ARMv6 peut
  prendre **plusieurs minutes**. Ce n'est pas un plantage — laissez tourner.

### Reproductibilité de l'install — le vrai enjeu

`requirements.txt` est **épinglé** (`==`). Avant de figer une version, on
vérifie qu'une **wheel ARMv6 existe sur piwheels** (surtout `cryptography`,
dont les dernières versions n'ont pas forcément de wheel ARMv6 et tomberaient
sur une compilation Rust).

> Les versions de `requirements.txt` sont un **point de départ** validé
> fonctionnellement (API HAP-python + pyoverkiz vérifiées). Elles **doivent
> être re-testées sur une carte fraîchement flashée** avant publication : page
> piwheels `https://www.piwheels.org/project/<paquet>/`, onglet `armv6l`.

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

## Secrets

`config.yaml`, `.env`, `accessory.state` et les dumps `explore_dump*.json` sont
`.gitignore`. **Ne les commitez jamais** : ils contiennent identifiants
Cozytouch, clés d'appairage HAP et/ou l'ID de votre passerelle.

---

## Feuille de route

- **V1 (actuel)** : install reproductible + `configure` + accessoire standalone
  (capteurs ambiante / extérieure / ECS) + QR code + polling espacé + systemd.
- **V2** : écriture — `Thermostat`/`HeaterCooler` (consigne + mode), `Switch`
  boost ECS. ⚠️ HomeKit n'a pas de type « PAC pilotée par loi d'eau » : mapping
  à assumer et documenter.
- **V3 (option)** : cache offline, healthcheck, procédure de nettoyage des
  tuiles fantômes.

## Licence

GPL-3.0 — voir [LICENSE](LICENSE).
